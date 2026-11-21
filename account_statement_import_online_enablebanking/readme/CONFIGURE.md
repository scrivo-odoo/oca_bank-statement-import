## Creating EnableBanking application
1. Create an Application in https://enablebanking.com/cp/applications
 - Your redirect URL should be https://your-odoo.com/enablebanking_auth
 - Fill in email for data protection matters, Privacy URL and Terms URL
2. Save the Application ID and Private key (.pem -file) for the next step.
3. Link your accounts, unless you plan to request general availability

## The private key
The private key is a file of the server, not a field of the database: put the .pem file where Odoo can read it (and nobody else)
and name it in the environment of Odoo: `ENABLEBANKING_KEY_FILE=/path/to/key.pem`. Odoo reads it for every request to the API.

## Configuring Odoo
1. Enable "Show Full Accounting Features" group/permission to your user
2. Go to Invoicing->Configuration->EnableBanking Applications
3. Create a new application and provide the following information:
 - Application ID (from previous step)
 - Bank BIC (the BIC of the bank; the bank account of the journal carries it)
 - Responsible user for this application
 - Account type (Personal/Business)
 - Redirect URL. This needs to be allowed in the previous step
 - Company
4. Click "Get ASPSP info" to fetch bank information from EnableBanking. If your credentials are correct, you should see bank name and ASPSP name filled in.
5. Click "Bank authentication" and follow instructions to complete the authentication. The consent is asked for as long as the bank allows.

Basic configuration is now ready. You can proceed to authenticate and fetch bank statements.


## Authenticating to bank
1. Go to Invoicing->Configuration->Journals
2. Create (or edit) a journal belonging to a bank account
3. Fill the "Bank Account Number" with your IBAN number and the BIC of the bank
4. Select "Online (OCA)" from Bank Feeds
5. Select "EnableBanking" for Provider
6. Click "Configuration"-button next to provider, and select the application you created in previous steps.
7. Configure transactions interval here: Day/Week/Month
8. If you want to fetch old transactions, click "Pull Online Bank Statement" and define the range for fetching

The account that is read is the one of the consent with the IBAN of the journal in the currency of the journal
(the currency of the company when the journal has none).

Done! A re-authentication will be required when the consent ends (up to 180 days, depending on the bank).
You can wait for the authentication to expire, or renew it while it's still active.
