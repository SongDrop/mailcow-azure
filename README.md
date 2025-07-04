```
                                         ,--,
          ____                        ,---.'|                  ,----..
        ,'  , `.   ,---,         ,---,|   | :     ,----..     /   /   \             .---.
     ,-+-,.' _ |  '  .' \     ,`--.' |:   : |    /   /   \   /   .     :           /. ./|
  ,-+-. ;   , || /  ;    '.   |   :  :|   ' :   |   :     : .   /   ;.  \      .--'.  ' ;
 ,--.'|'   |  ;|:  :       \  :   |  ';   ; '   .   |  ;. /.   ;   /  ` ;     /__./ \ : |
|   |  ,', |  '::  |   /\   \ |   :  |'   | |__ .   ; /--` ;   |  ; \ ; | .--'.  '   \' .
|   | /  | |  |||  :  ' ;.   :'   '  ;|   | :.'|;   | ;    |   :  | ; | '/___/ \ |    ' '
'   | :  | :  |,|  |  ;/  \   \   |  |'   :    ;|   : |    .   |  ' ' ' :;   \  \;      :
;   . |  ; |--' '  :  | \  \ ,'   :  ;|   |  ./ .   | '___ '   ;  \; /  | \   ;  `      |
|   : |  | ,    |  |  '  '--' |   |  ';   : ;   '   ; : .'| \   \  ',  /   .   \    .\  ;
|   : '  |/     |  :  :       '   :  ||   ,/    '   | '/  :  ;   :    /     \   \   ' \ |
;   | |`-'      |  | ,'       ;   |.' '---'     |   :    /    \   \ .'       :   '  |--"
|   ;/          `--''         '---'              \   \ .'      `---`          \   \ ;
'---'                                             `---`                        '---"

```

# Mailcow Self-Hosted Email Server on Azure

This is an **automatic installation** on Azure to set up a Mailcow self-hosted email server.

---

## Step 1: Create the virtual machine and the resources needed

## Step 2: Upload and run setup script for automatic software installation on the virtual machine

---

## Required `.env` values for Azure:

You need to provide the following values in your `.env` file:

```
Azure subscription -> portal.azure.com

AZURE_SUBSCRIPTION_ID=''  # https://portal.azure.com/#view/Microsoft_Azure_Billing/SubscriptionsBladeV2
AZURE_TENANT_ID=''        # https://portal.azure.com/#view/Microsoft_AAD_IAM/ActiveDirectoryMenuBlade/~/Overview
AZURE_APP_CLIENT_ID=''
AZURE_APP_CLIENT_SECRET=''
AZURE_APP_TENANT_ID=''
```

You also need to create a new Azure Application in Azure Entra ID (Azure Active Directory) to get these credentials.

---

## To start:

```bash
python3 -m venv myenv
source myenv/bin/activate
pip install -r requirements.txt
python3 create_vm.py
```

---

## After that, just input these values when prompted, and your email service will be up and running within 5 minutes:

```
Enter VM username [azureuser]:
Enter VM password [azurepassword1234!]:
Enter main domain [example.com]:
Enter subdomain (e.g., 'smtp') [smtp]:
[INFO] Full domain to configure: smtp.example.com
Enter resource group name [smtpgroup]:
Enter VM name [stmp]:
Enter Azure region [uksouth]:
Enter VM size [Standard_B2s]:
Enter admin email [admin@example.com]:
Enter admin password [smtppass123!]:
```

---

Happy mailing with your new Mailcow setup on Azure! 🚀
