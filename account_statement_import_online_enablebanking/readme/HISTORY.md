20.0.1.0.0 (2026-11-21)
Migration to 20.0 (Odoo 20 has no res.bank, so the application carries the BIC of the bank; access rules in ir.access.csv; ir.config_parameter.get_str).
Lines with a zero amount are skipped and named in the log; pending lines are not taken.
The account of a journal is the one with its IBAN in its currency (banks give one IBAN to several currency accounts).
The key of a line is the reference of the bank and what the line says, so repeated or missing references lose nothing.
The counterparty is the creditor of a debit and the debtor of a credit.
A refusal of the bank or an expired consent is an error, not an empty pull.
The private key of the application is a file named by ENABLEBANKING_KEY_FILE, not a column of the database.
The consent is asked for as long as the bank allows (maximum_consent_validity of GET /aspsps).

19.0.1.0.5 (2026-10-27)
Improve statement line information for better matching
Prefer ref over remittance information for payment reference, and move remittance information to narration.

19.0.1.0.4 (2026-10-23)
Fix message posting on statement fetch error

19.0.1.0.3 (2026-10-02)
Trim leading zeros to allow better reconciliation

19.0.1.0.2 (2026-09-25)
Improve usability and errors

19.0.1.0.1 (2026-09-02)
Don't search for a partner without name

19.0.1.0.0 (2026-08-18)
Migration to 19.0
