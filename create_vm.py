import asyncio
import json
import os
import sys
import time
from datetime import datetime, timedelta
from dotenv import load_dotenv
load_dotenv()  # This loads environment variables from a .env file in the current directory
import subprocess
import shutil
import platform
import webbrowser
import random
import string
import dns.resolver
from azure.core.exceptions import ClientAuthenticationError
from azure.identity import ClientSecretCredential
from azure.storage.blob import BlobServiceClient, generate_blob_sas, BlobSasPermissions

from azure.mgmt.resource import ResourceManagementClient
from azure.mgmt.network import NetworkManagementClient
from azure.mgmt.network.models import NetworkSecurityGroup, SecurityRule, NetworkInterface
from azure.mgmt.compute import ComputeManagementClient
from azure.mgmt.compute.models import (
    VirtualMachine, HardwareProfile, StorageProfile,
    OSProfile, NetworkProfile, NetworkInterfaceReference,
    LinuxConfiguration
)
from azure.mgmt.dns import DnsManagementClient
from azure.mgmt.dns.models import RecordSet,TxtRecord,MxRecord
from azure.mgmt.storage import StorageManagementClient
from azure.mgmt.storage import StorageManagementClient
 
import generate_setup  # Your sh setup generator module

#UBUNTU IMAGE
image_reference = {
    'publisher': 'canonical',
    'offer': 'ubuntu-24_04-lts',
    'sku': 'server',
    'version': 'latest',
    'exactVersion': '24.04.202409120'
}

#MAILCOW PORST
INBOUND_PORTS_TO_OPEN = [
    22,     # SSH
    80,     # HTTP
    443,    # HTTPS
    8000,   # Optional app port (if used)
    3000,   # Optional app port (if used)
    25,     # Postfix SMTP
    465,    # Postfix SMTPS (SMTP over SSL)
    587,    # Postfix Submission (SMTP with STARTTLS)
    110,    # Dovecot POP3
    995,    # Dovecot POP3S (POP3 over SSL)
    143,    # Dovecot IMAP
    993,    # Dovecot IMAPS (IMAP over SSL)
    4190    # Dovecot ManageSieve (for Sieve email filtering)
]
OUTBOUND_PORTS_TO_ALLOW = [
    25,    # Outbound SMTP
    53,    # DNS resolution
    80,    # HTTP (Let's Encrypt)
    443,   # HTTPS (Let's Encrypt, updates)
]
OS_DISK_SSD_GB = '128'

# Console colors for logs
class bcolors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKORANGE = '\033[38;5;214m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
 
async def main():
    print_info("Welcome to the Azure Windows VM provisioning tool")

    subscription_id = os.environ.get("AZURE_SUBSCRIPTION_ID")

    username = prompt_input("Enter VM username", "azureuser")
    password = prompt_input("Enter VM password", "azurepassword1234!", secret=True)
    domain = prompt_input("Enter main domain", "example.com")
    subdomain = prompt_input("Enter subdomain (e.g., 'smtp')", "smtp")
    if subdomain:
        subdomain = subdomain.strip().strip('.')
        fqdn = f"{subdomain}.{domain}"
    else:
        fqdn = domain
    print_info(f"Full domain to configure: {fqdn}")
    resource_group = prompt_input("Enter resource group name", "smtpgroup")
    pc_name = 'smtp'#''.join(random.choices(string.ascii_lowercase, k=6))
    vm_name = prompt_input("Enter VM name", pc_name)
    location = prompt_input("Enter Azure region", "uksouth")
    vm_size = prompt_input("Enter VM size", "Standard_B2s")
    #storage_account_base = prompt_input("Enter base storage account name (globally unique). Storage account name must be between 3 and 24 characters in length and use numbers and lower-case letters only", "vmstorage")
    storage_account_base = vm_name
    admin_email =  prompt_input("Enter admin email", f"admin@{domain}")
    random_admin_password = 'smtppass123!'#''.join(random.choices(string.ascii_lowercase, k=6))
    admin_password = prompt_input("Enter admin password", random_admin_password)
    OS_DISK_SSD_GB = prompt_input("Enter disk size in GB", '128')


    try:
        credentials = ClientSecretCredential(
            client_id=os.environ['AZURE_APP_CLIENT_ID'],
            client_secret=os.environ['AZURE_APP_CLIENT_SECRET'],
            tenant_id=os.environ['AZURE_APP_TENANT_ID']
        )
    except KeyError:
        print_error("Set AZURE_APP_CLIENT_ID, AZURE_APP_CLIENT_SECRET, and AZURE_APP_TENANT_ID environment variables.")
        sys.exit(1)

    subscription_id = os.environ.get('AZURE_SUBSCRIPTION_ID')
    if not subscription_id:
        print_error("Set AZURE_SUBSCRIPTION_ID environment variable.")
        sys.exit(1)

    compute_client = ComputeManagementClient(credentials, subscription_id)
    storage_client = StorageManagementClient(credentials, subscription_id)
    network_client = NetworkManagementClient(credentials, subscription_id)
    resource_client = ResourceManagementClient(credentials, subscription_id)
    dns_client = DnsManagementClient(credentials, subscription_id)
 
    # Resource group
    try:
        print_info(f"Creating or updating resource group '{resource_group}' in '{location}'...")
        resource_client.resource_groups.create_or_update(resource_group, {'location': location})
        print_success(f"Resource group '{resource_group}' created or updated successfully.")
    except Exception as e:
        print_error(f"Failed to create or update resource group '{resource_group}': {e}")
        sys.exit(1)

    # Container storage
    storage_account_name = f"{storage_account_base}{int(time.time()) % 10000}"
    storage_config = await create_storage_account(storage_client, resource_group, storage_account_name, location)
    global AZURE_STORAGE_ACCOUNT_KEY
    AZURE_STORAGE_ACCOUNT_KEY = storage_config["AZURE_STORAGE_KEY"]
    AZURE_STORAGE_URL = storage_config["AZURE_STORAGE_URL"]

    # Autoinstall script generation
    print_info("Generating installation setup script...")
    # Generate Auto-setup setup script
    sh_script = generate_setup.generate_setup(
        fqdn, admin_email, admin_password
    )

    print(sh_script)

    blob_service_client = BlobServiceClient(account_url=AZURE_STORAGE_URL, credential=credentials)
    container_name = 'vm-startup-scripts'
    blob_name = f'{vm_name}-setup.sh'


    # Uploading generated script to storage
    blob_url_with_sas = await upload_blob_and_generate_sas(blob_service_client, container_name, blob_name, sh_script, sas_expiry_hours=2)

    print_success(f"Uploaded setup script to Blob Storage: {blob_url_with_sas}")

    # Create VNet and subnet
    vnet_name = f'{vm_name}-vnet'
    subnet_name = f'{vm_name}-subnet'
    print_info(f"Creating VNet '{vnet_name}' with subnet '{subnet_name}'.")

    network_client.virtual_networks.begin_create_or_update(
        resource_group,
        vnet_name,
        {
            'location': location,
            'address_space': {'address_prefixes': ['10.1.0.0/16']},
            'subnets': [{'name': subnet_name, 'address_prefix': '10.1.0.0/24'}]
        }
    ).result()
    print_success(f"Created VNet '{vnet_name}' with subnet '{subnet_name}'.")

    # Create Public IP
    public_ip_name = f'{vm_name}-public-ip'
    print_info(f"Creating Public IP '{public_ip_name}'.")
    public_ip_params = {
        'location': location,
        'public_ip_allocation_method': 'Dynamic'
    }
    public_ip = network_client.public_ip_addresses.begin_create_or_update(
        resource_group,
        public_ip_name,
        public_ip_params
    ).result()
    print_success(f"Created Public IP '{public_ip_name}'.")

    subnet_id = f'/subscriptions/{subscription_id}/resourceGroups/{resource_group}/providers/Microsoft.Network/virtualNetworks/{vnet_name}/subnets/{subnet_name}'
    public_ip_id = f'/subscriptions/{subscription_id}/resourceGroups/{resource_group}/providers/Microsoft.Network/publicIPAddresses/{public_ip_name}'

    # Create or get NSG
    nsg_name = f'{vm_name}-nsg'
    print_info(f"Creating NSG '{nsg_name}'.")
    try:
        nsg = network_client.network_security_groups.get(resource_group, nsg_name)
        print_info(f"Found existing NSG '{nsg_name}'.")
    except Exception:
        nsg_params = NetworkSecurityGroup(location=location, security_rules=[])
        nsg = network_client.network_security_groups.begin_create_or_update(resource_group, nsg_name, nsg_params).result()
        print_success(f"Created NSG '{nsg_name}'.")

    # Add NSG rules for required ports
    print_info(f"Updating NSG '{nsg_name}' with required port rules.")
    existing_rules = {rule.name for rule in nsg.security_rules} if nsg.security_rules else set()
    priority = 100
    for port in INBOUND_PORTS_TO_OPEN:
        rule_name = f'AllowAnyCustom{port}Inbound' 
        if rule_name not in existing_rules:
            rule = SecurityRule(
                name=rule_name,
                access='Allow',
                direction='Inbound',
                priority=priority,
                protocol='*',
                source_address_prefix='*',
                destination_address_prefix='*',
                destination_port_range=str(port),
                source_port_range='*'
            )
            nsg.security_rules.append(rule)
            priority += 1
    for port in OUTBOUND_PORTS_TO_ALLOW:
        rule_name = f'AllowAnyCustom{port}Outbound' 
        if rule_name not in existing_rules:
            rule = SecurityRule(
                name=rule_name,
                access='Allow',
                direction='Outbound',
                priority=priority,
                protocol='*',
                source_address_prefix='*',
                destination_address_prefix='*',
                destination_port_range=str(port),
                source_port_range='*'
            )
            nsg.security_rules.append(rule)
            priority += 1
    network_client.network_security_groups.begin_create_or_update(resource_group, nsg_name, nsg).result()
    print_success(f"Updated NSG '{nsg_name}' with required port rules.")

    # Create NIC
    print_info(f"Creating Network Interface '{vm_name}-nic'.")
    nic_params = NetworkInterface(
        location=location,
        ip_configurations=[{
            'name': f'{vm_name}-ip-config',
            'subnet': {'id': subnet_id},
            'public_ip_address': {'id': public_ip_id}
        }],
        network_security_group={'id': nsg.id}
    )
    nic = network_client.network_interfaces.begin_create_or_update(resource_group, f'{vm_name}-nic', nic_params).result()
    print_success(f"Created Network Interface '{vm_name}-nic'.")

    # Create VM
    print_info(f"Creating VM '{vm_name}'.")
    os_disk = {
        'name': f'{vm_name}-os-disk',
        'managed_disk': {
            'storage_account_type': 'Standard_LRS'
            },
        'create_option': 'FromImage',
        'disk_size_gb': f"{int(OS_DISK_SSD_GB)}"
    }
    os_profile = OSProfile(
        computer_name=vm_name,
        admin_username=username,
        admin_password=password,
        linux_configuration=LinuxConfiguration(
            disable_password_authentication=False
        )
    )
    vm_parameters = VirtualMachine(
        location=location,
        hardware_profile=HardwareProfile(vm_size=vm_size),
        storage_profile=StorageProfile(os_disk=os_disk, 
                                       image_reference=image_reference),
        os_profile=os_profile,
        network_profile=NetworkProfile(network_interfaces=[NetworkInterfaceReference(id=nic.id)]),
        zones=None
    )
    vm = compute_client.virtual_machines.begin_create_or_update(resource_group, vm_name, vm_parameters).result()
    print_success(f"Created VM '{vm_name}'.")

    # Wait for VM to be ready before extension
    print_info("Waiting 5 seconds for VM to initialize...")
    time.sleep(5)

    # Get public IP
    print_info(f"Retrieving VM Public IP: {public_ip}")
    nic_client = network_client.network_interfaces.get(resource_group, f'{vm_name}-nic')
    if not nic_client.ip_configurations or not nic_client.ip_configurations[0].public_ip_address:
        print_error("No public IP found on NIC.")
        sys.exit(1)
    public_ip_name = nic_client.ip_configurations[0].public_ip_address.id.split('/')[-1]
    public_ip_info = network_client.public_ip_addresses.get(resource_group, public_ip_name)
    public_ip = public_ip_info.ip_address
    print_success(f"VM Public IP: {public_ip}")

    # Create DNS Zone
    print_info(f"Creating DNS zone '{domain}'.")
    try:
        dns_zone = dns_client.zones.get(resource_group, domain)
        print_info(f"Found DNS zone '{domain}'.")
    except Exception:
        dns_zone = dns_client.zones.create_or_update(resource_group, domain, {'location': 'global'})
        print_success(f"Created DNS zone '{domain}'.")

    # Create DNS A record
    print_info(f"Creating DNS A record for {subdomain}.{domain} -> {public_ip}")
    a_record_set = RecordSet(ttl=3600, a_records=[{'ipv4_address': public_ip}])
    record_name = subdomain.rstrip('.') if subdomain else '@' 
    dns_client.record_sets.create_or_update(resource_group, domain, record_name, 'A', a_record_set)
    print_success(f"Created DNS A record for {subdomain}.{domain} -> {public_ip}")

    # Wait for DNS Zone to be ready before extension
    print_info("Waiting 5 seconds for DNS Zone to initialize...")
    time.sleep(5)
    if not check_ns_delegation_with_retries(dns_client, resource_group, domain):
        print_error("Stopping provisioning due to incorrect NS delegation.")
        await cleanup_resources_on_failure(
            network_client,
            compute_client,
            storage_client,
            blob_service_client,
            container_name,
            blob_name,
            dns_client,
            resource_group,
            domain,
            a_records,
            vm_name=vm_name,
            storage_account_name=storage_account_name
        )

        print_warn("-----------------------------------------------------")
        print_warn("Azure Windows VM provisioning failed with error")
        print_warn("-----------------------------------------------------")
        sys.exit(1)

    #A-RECORDS
    a_records = [subdomain]
    for a_record in a_records:
        print_info(f"Creating DNS A record for {a_record} for DNS Zone {domain} -> {public_ip}")
        a_record_set = RecordSet(ttl=3600, a_records=[{'ipv4_address': public_ip}])
        dns_client.record_sets.create_or_update(resource_group, domain, a_record, 'A', a_record_set)
        print_success(f"Created DNS  A record for {a_record} for DNS Zone {domain} -> {public_ip}")

    #CNAME-RECORDS 
    cname_records = ['autodiscover', 'autoconfig']
    for cname in cname_records:
        print_info(f"Creating DNS CNAME record for {cname} for DNS Zone {domain} -> {domain}")
        cname_record_set = RecordSet(
            ttl=3600,
            cname_record={'cname': f"{subdomain}.{domain}"}
        )
        dns_client.record_sets.create_or_update(resource_group, domain, cname, 'CNAME', cname_record_set)
        print_success(f"Created DNS CNAME record for {cname} for DNS Zone {domain} -> {domain}")

    #TXT-RECORDS
    spf_value = f"v=spf1 ip4:{public_ip} -all"
    print_info(f"Creating DNS TXT record for {spf_value} for DNS Zone {domain} -> {public_ip}")
    txt_record_set = RecordSet(ttl=3600, txt_records=[TxtRecord(value=[spf_value])])
    dns_client.record_sets.create_or_update(
        resource_group_name=resource_group,
        zone_name=domain,
        relative_record_set_name='@',
        record_type='TXT',
        parameters=txt_record_set
    )
    print_success(f"Created DNS TXT record for {spf_value} for DNS Zone {domain} -> {public_ip}")

    # Create DMARC record
    dmarc_value = f"v=DMARC1; p=quarantine; rua=mailto:admin@{domain}; ruf=mailto:admin@{domain}; fo=1; adkim=s; aspf=s"
    print_info(f"Creating DNS TXT record for DMARC: {dmarc_value} for DNS Zone {domain}")
    dmarc_record_set = RecordSet(ttl=3600, txt_records=[TxtRecord(value=[dmarc_value])])
    dns_client.record_sets.create_or_update(
        resource_group_name=resource_group,
        zone_name=domain,
        relative_record_set_name='_dmarc',
        record_type='TXT',
        parameters=dmarc_record_set
    )
    print_success(f"Created DNS TXT record for {spf_value} for DNS Zone {domain} -> {public_ip}")

    # --- MX Record with priority 10 pointing to smtp.domain ---
    print_info(f"Creating DNS MX record for DNS Zone {domain} -> {subdomain}.{domain} (priority 10)")
    mx_record_set = RecordSet(ttl=3600, mx_records=[MxRecord(preference=10, exchange=f"{subdomain}.{domain}")])
    dns_client.record_sets.create_or_update(
        resource_group_name=resource_group,
        zone_name=domain,
        relative_record_set_name='@',  # Root of the zone
        record_type='MX',
        parameters=mx_record_set
    )
    print_success(f"Created DNS MX record for DNS Zone {domain} -> {subdomain}.{domain} with priority 10")

    # ACME challenge record
    acme_value = f"_acme-challenge.{domain}"
    print_info(f"Creating DNS TXT record for acme-challenge: {acme_value} for DNS Zone {domain}")
    acme_record_set = RecordSet(ttl=3600, txt_records=[TxtRecord(value=[acme_value])])
    dns_client.record_sets.create_or_update(
    resource_group_name=resource_group,
    zone_name=domain,
    relative_record_set_name='_acme-challenge',
    record_type='TXT',
    parameters=acme_record_set
    )        
    print_success(f"Creating DNS TXT record for acme-challenge: {acme_value} for DNS Zone {domain} -> {public_ip}")

    # Deploy Custom Script Extension to run PowerShell setup script
    print_info(f"Deploying Custom Script Extension to install script on VM.")
    # Create Extension for script setup .sh
    ext_params = {
        'location': location,
        'publisher': 'Microsoft.Azure.Extensions',
        'type': 'CustomScript',
        'type_handler_version': '2.0',
        'settings': {
            'fileUris': [blob_url_with_sas],
            'commandToExecute': f'bash {blob_name}',  # Update command accordingly
        },
    }
    extension = None
    try:
        extension = compute_client.virtual_machine_extensions.begin_create_or_update(
            resource_group,
            vm_name,
            'customScriptExtension',
            ext_params
        ).result(timeout=600)
    except Exception as e:
        print_error(f"Failed to deploy Custom Script Extension: {e}")

    if extension:
        print_success(f"Deployed Custom Script Extension '{extension.name}'.")
        await cleanup_temp_storage_on_success(resource_group, storage_client, storage_account_name, blob_service_client, container_name, blob_name)

        print_success("-----------------------------------------------------")
        print_success("Azure Windows VM provisioning completed successfully!")
        print_success("-----------------------------------------------------")
        print_success("Access your service at:-----------------------------")
        print_success(f"https://{fqdn}/admin/")
        print_success("-----------------------------------------------------")
        print_success("Login credentials:")
        print_success("  Username: admin")
        print_success("  Password: moohoo")
        print_success("Please change this password immediately after logging in for security reasons.")
        print_success("-----------------------------------------------------")
        print_success("Additional Setup Instructions:")
        print_success(f"1. Add your domain at https://{fqdn}/admin/mailbox")
        print_success("2. Get the 'dkim._domainkey' TXT record value from Mailcow admin panel")
        print_success("3. Paste the DKIM TXT record in your DNS zone to enable email signing")
        print_success("-----------------------------------------------------")
        # Construct the URL
        url = f"https://{fqdn}/admin/"
        # Open the default browser with the URL
        webbrowser.open(url)
    else:
        print_warn("Custom Script Extension deployment did not complete successfully.")
        await cleanup_resources_on_failure(
            network_client,
            compute_client,
            storage_client,
            blob_service_client,
            container_name,
            blob_name,
            dns_client,
            resource_group,
            domain,
            a_records,
            vm_name=vm_name,
            storage_account_name=storage_account_name
        )

        print_warn("-----------------------------------------------------")
        print_warn("Azure Windows VM provisioning failed with error")
        print_warn("-----------------------------------------------------")

#####
def print_info(msg):
    print(f"{bcolors.OKBLUE}[INFO]{bcolors.ENDC} {msg}")

def print_build(msg):
    print(f"{bcolors.OKORANGE}[BUILD]{bcolors.ENDC} {msg}")

def print_success(msg):
    print(f"{bcolors.OKGREEN}[SUCCESS]{bcolors.ENDC} {msg}")

def print_warn(msg):
    print(f"{bcolors.WARNING}[WARNING]{bcolors.ENDC} {msg}")

def print_error(msg):
    print(f"{bcolors.FAIL}[ERROR]{bcolors.ENDC} {msg}")

def prompt_input(prompt, default=None, secret=False):
    if default:
        prompt_full = f"{prompt} [{default}]: "
    else:
        prompt_full = f"{prompt}: "
    if secret:
        import getpass
        value = getpass.getpass(prompt_full)
        if not value and default:
            return default
        return value
    else:
        value = input(prompt_full)
        if not value and default:
            return default
        return value

async def create_storage_account(storage_client, resource_group_name, storage_name, location):
    print_info(f"Creating storage account '{storage_name}' in '{location}'...")
    try:
        try:
            storage_client.storage_accounts.get_properties(resource_group_name, storage_name)
            print_info(f"Storage account '{storage_name}' already exists.")
        except:
            poller = storage_client.storage_accounts.begin_create(
                resource_group_name,
                storage_name,
                {
                    "sku": {"name": "Standard_LRS"},
                    "kind": "StorageV2",
                    "location": location,
                    "enable_https_traffic_only": True
                }
            )
            poller.result()
            print_success(f"Storage account '{storage_name}' created.")

        keys = storage_client.storage_accounts.list_keys(resource_group_name, storage_name)
        storage_key = keys.keys[0].value
        storage_url = f"https://{storage_name}.blob.core.windows.net"

        return {
            "AZURE_STORAGE_URL": storage_url,
            "AZURE_STORAGE_NAME": storage_name,
            "AZURE_STORAGE_KEY": storage_key
        }
    except Exception as e:
        print_error(f"Failed to create storage account: {e}")
        raise

def ensure_container_exists(blob_service_client, container_name):
    print_info(f"Checking container '{container_name}'.")
    container_client = blob_service_client.get_container_client(container_name)
    try:
        container_client.create_container()
        print_success(f"Created container '{container_name}'.")
    except Exception as e:
        print_info(f"Container '{container_name}' likely exists or could not be created: {e}")
    return container_client

async def upload_blob_and_generate_sas(blob_service_client, container_name, blob_name, data, sas_expiry_hours=1):
    print_info(f"Uploading blob '{blob_name}' to container '{container_name}'.")
    container_client = ensure_container_exists(blob_service_client, container_name)
    blob_client = container_client.get_blob_client(blob_name)
    blob_client.upload_blob(data, overwrite=True)
    print_success(f"Uploaded blob '{blob_name}' to container '{container_name}'.")
    print_info(f"SAS URL generating for blob '{blob_name}'.")
    sas_token = generate_blob_sas(
        blob_service_client.account_name,
        container_name,
        blob_name,
        permission=BlobSasPermissions(read=True),
        expiry=datetime.utcnow() + timedelta(hours=sas_expiry_hours),
        account_key=AZURE_STORAGE_ACCOUNT_KEY
    )
    blob_url = f"https://{blob_service_client.account_name}.blob.core.windows.net/{container_name}/{blob_name}"
    blob_url_with_sas = f"{blob_url}?{sas_token}"
    print_success(f"SAS URL generated for blob '{blob_name}'.")
    return blob_url_with_sas

  
def check_vm_size_compatibility(vm_size):
    gen2_compatible_vm_sizes = ['Standard_B2s']
    return vm_size in gen2_compatible_vm_sizes

async def cleanup_resources_on_failure(network_client, compute_client, storage_client, blob_service_client, container_name, blob_name, dns_client, resource_group, domain, a_records, vm_name, storage_account_name):
    print_warn("Starting cleanup of Azure resources due to failure...")

    # Delete VM
    try:
        vm = compute_client.virtual_machines.get(resource_group, vm_name)
        os_disk_name = vm.storage_profile.os_disk.name
        compute_client.virtual_machines.begin_delete(resource_group, vm_name).result()
        print_info(f"Deleted VM '{vm_name}'.")
    except Exception as e:
        print_warn(f"Could not delete VM '{vm_name}': {e}")
        os_disk_name = None

    # Delete OS disk if available
    if os_disk_name:
        try:
            compute_client.disks.begin_delete(resource_group, os_disk_name).result()
            print_info(f"Deleted OS disk '{os_disk_name}'.")
        except Exception as e:
            print_warn(f"Could not delete OS disk '{os_disk_name}': {e}")

    # Delete NIC
    try:
        network_client.network_interfaces.begin_delete(resource_group, f"{vm_name}-nic").result()
        print_info(f"Deleted NIC '{vm_name}-nic'.")
    except Exception as e:
        print_warn(f"Could not delete NIC '{vm_name}-nic': {e}")

    # Delete NSG
    try:
        network_client.network_security_groups.begin_delete(resource_group, f"{vm_name}-nsg").result()
        print_info(f"Deleted NSG '{vm_name}-nsg'.")
    except Exception as e:
        print_warn(f"Could not delete NSG '{vm_name}-nsg': {e}")

    # Delete Public IP
    try:
        network_client.public_ip_addresses.begin_delete(resource_group, f"{vm_name}-public-ip").result()
        print_info(f"Deleted Public IP '{vm_name}-public-ip'.")
    except Exception as e:
        print_warn(f"Could not delete Public IP '{vm_name}-public-ip': {e}")

    # Delete VNet
    try:
        network_client.virtual_networks.begin_delete(resource_group, f"{vm_name}-vnet").result()
        print_info(f"Deleted VNet '{vm_name}-vnet'.")
    except Exception as e:
        print_warn(f"Could not delete VNet '{vm_name}-vnet': {e}")

    # Delete Storage Account
    try:
        print_info(f"Deleting blob '{blob_name}' from container '{container_name}'.")
        container_client = blob_service_client.get_container_client(container_name)
        container_client.delete_blob(blob_name)
        print_success(f"Deleted blob '{blob_name}' from container '{container_name}'.")
        print_info(f"Deleting container '{container_name}'.")
        blob_service_client.delete_container(container_name)
        print_success(f"Deleted container '{container_name}'.")
        print_info(f"Deleting storage account '{storage_account_name}'.")
        storage_client.storage_accounts.delete(resource_group, storage_account_name)
        print_success(f"Deleted storage account '{storage_account_name}'.")
    except Exception as e:
        print_warn(f"Could not delete Storage Account '{storage_account_name}': {e}")

    # Delete DNS A record (keep DNS zone)
    for record_name in a_records:
        record_to_delete = record_name if record_name else '@'  # handle root domain with '@'
        print_info(f"Deleting DNS A record '{record_to_delete}' in zone '{domain}'.")
        try:
            dns_client.record_sets.delete(resource_group, domain, record_to_delete, 'A')
            print_success(f"Deleted DNS A record '{record_to_delete}' in zone '{domain}'.")
        except Exception as e:
            print_warn(f"Could not delete DNS A record '{record_to_delete}' in zone '{domain}': {e}")

    # Delete old TXT records if needed
    txt_records_to_clean = ['@', '_dmarc','_acme-challenge']  # Root and DMARC records
    for record_name in txt_records_to_clean:
        print_info(f"Deleting DNS TXT record '{record_name}' in zone '{domain}'.")
        try:
            dns_client.record_sets.delete(resource_group, domain, record_name, 'TXT')
            print_success(f"Deleted DNS TXT record '{record_name}' in zone '{domain}'.")
        except Exception as e:
            print_warn(f"Could not delete DNS TXT record '{record_name}' in zone '{domain}': {e}")

    # Delete MX record
    print_info(f"Deleting DNS MX record '@' in zone '{domain}'.")
    try:
        dns_client.record_sets.delete(resource_group, domain, '@', 'MX')
        print_success(f"Deleted DNS MX record '@' in zone '{domain}'.")
    except Exception as e:
        print_warn(f"Could not delete DNS MX record '@' in zone '{domain}': {e}")

    # Delete CNAME records
    cname_records = ['autodiscover', 'autoconfig']
    for cname in cname_records:
        print_info(f"Deleting DNS CNAME record '{cname}' in zone '{domain}'.")
        try:
            dns_client.record_sets.delete(resource_group, domain, cname, 'CNAME')
            print_success(f"Deleted DNS CNAME record '{cname}' in zone '{domain}'.")
        except Exception as e:
            print_warn(f"Could not delete DNS CNAME record '{cname}' in zone '{domain}': {e}")

    print_success("Cleanup completed.")

async def cleanup_temp_storage_on_success(resource_group, storage_client, storage_account_name, blob_service_client, container_name, blob_name):
    print_info("Starting cleanup of Azure resources on success...")

    # Delete Storage Account
    try:
        print_info(f"Deleting blob '{blob_name}' from container '{container_name}'.")
        container_client = blob_service_client.get_container_client(container_name)
        container_client.delete_blob(blob_name)
        print_success(f"Deleted blob '{blob_name}' from container '{container_name}'.")
        print_info(f"Deleting container '{container_name}'.")
        blob_service_client.delete_container(container_name)
        print_success(f"Deleted container '{container_name}'.")
        print_info(f"Deleting storage account '{storage_account_name}'.")
        storage_client.storage_accounts.delete(resource_group, storage_account_name)
        print_success(f"Deleted storage account '{storage_account_name}'.")
    except Exception as e:
        print_warn(f"Could not delete Storage Account '{storage_account_name}': {e}")

    print_success("Temp storage cleanup completed.")

def check_ns_delegation_with_retries(dns_client, resource_group, domain, retries=5, delay=10):
    for attempt in range(1, retries + 1):
        if check_ns_delegation(dns_client, resource_group, domain):
            return True
        print_warn(f"\n⚠️ Retrying NS delegation check in {delay} seconds... (Attempt {attempt}/{retries})")
        time.sleep(delay)
    return False


def check_ns_delegation(dns_client, resource_group, domain):
    print_warn(
        "\nIMPORTANT: You must update your domain registrar's nameserver (NS) records "
        "to exactly match the Azure DNS nameservers. Without this delegation, "
        "your domain will NOT resolve correctly, and your application will NOT work as expected.\n"
        "Please log into your domain registrar (e.g., Namecheap, GoDaddy) and set the NS records "
        "for your domain to the above nameservers.\n"
        "DNS changes may take up to 24–48 hours to propagate globally.\n"
    )

    try:
        print_info("\n----------------------------")
        print_info("🔍 Checking Azure DNS zone for NS servers...")
        dns_zone = dns_client.zones.get(resource_group, domain)
        azure_ns = sorted(ns.lower().rstrip('.') for ns in dns_zone.name_servers)
        print_info(f"✅ Azure DNS zone NS servers for '{domain}':")
        for ns in azure_ns:
            print(f"  - {ns}")
    except Exception as e:
        print_error(f"\n❌ Failed to get Azure DNS zone NS servers: {e}")
        return False

    try:
        print_info("\n🌐 Querying public DNS to verify delegation...")
        resolver = dns.resolver.Resolver()
        resolver.nameservers = ['8.8.8.8', '8.8.4.4']  # Google DNS
        answers = resolver.resolve(domain, 'NS')
        public_ns = sorted(str(rdata.target).lower().rstrip('.') for rdata in answers)
        print_info(f"🌍 Publicly visible NS servers for '{domain}':")
        for ns in public_ns:
            print(f"  - {ns}")
    except Exception as e:
        print_error(f"\n❌ Failed to resolve public NS records for domain '{domain}': {e}")
        return False

    if set(azure_ns).issubset(set(public_ns)):
        print_success("\n✅✅✅ NS delegation is correctly configured ✅✅✅")
        return True
    else:
        print_error("\n❌ NS delegation mismatch detected!")
        print_error("\nAzure DNS NS servers:")
        for ns in azure_ns:
            print_error(f"  - {ns}")
        print_error("\nPublicly visible NS servers:")
        for ns in public_ns:
            print_error(f"  - {ns}")

        print_warn(
            "\nACTION REQUIRED: Update your domain registrar's NS records to match the Azure DNS NS servers.\n"
            "Provisioning will stop until this is fixed.\n"
        )
        return False
    
if __name__ == "__main__":
    asyncio.run(main())
