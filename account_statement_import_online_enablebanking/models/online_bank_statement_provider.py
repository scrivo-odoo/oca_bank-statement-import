import hashlib
import logging
from collections import Counter
from datetime import datetime
from decimal import Decimal

import requests

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)


class OnlineBankStatementProviderEnablebanking(models.Model):
    _inherit = "online.bank.statement.provider"

    enablebanking_application_id = fields.Many2one(
        comodel_name="enablebanking.application", string="EnableBanking application"
    )
    enablebanking_valid_until = fields.Datetime(
        related="enablebanking_application_id.valid_until",
    )
    bank_bic = fields.Char(related="journal_id.bank_bic")

    application_id = fields.Char(
        related="enablebanking_application_id.application_id", readonly=True
    )
    redirect_url = fields.Char(
        related="enablebanking_application_id.redirect_url", readonly=True
    )

    aspsp_name = fields.Char(
        related="enablebanking_application_id.aspsp_name", readonly=True
    )
    aspsp_country = fields.Many2one(
        related="enablebanking_application_id.aspsp_country", readonly=True
    )
    psu_type = fields.Selection(
        related="enablebanking_application_id.psu_type", readonly=True
    )

    enablebanking_user_id = fields.Many2one(
        related="enablebanking_application_id.user_id", readonly=True
    )

    @api.onchange("enablebanking_application_id")
    def onchange_enablebanking_application_id(self):
        """
        When EnableBanking application is changed, update related fields
        """
        for record in self:
            application = record.enablebanking_application_id
            if application:
                record.message_subscribe(
                    partner_ids=[application.user_id.partner_id.id]
                )

    @api.model
    def _get_available_services(self):
        return super()._get_available_services() + [
            ("enablebanking", "EnableBanking"),
        ]

    def _enablebanking_errors(self) -> str:
        """
        Check for any errors related to the EnableBanking application
        """
        self.ensure_one()
        if not self.service or self.service != "enablebanking":
            return ""

        errors = []
        application = self.enablebanking_application_id
        valid_until = application.valid_until

        if not application:
            errors.append(
                self.env._(
                    "EnableBanking application is not configured for this provider. "
                    "Please configure it."
                )
            )
        elif not application._get_session_dict():
            # The session is missing
            errors.append(
                self.env._("Your bank authentication is invalid. Please authenticate.")
            )
        elif not valid_until or valid_until < fields.Datetime.now():
            errors.append(
                self.env._(
                    "Bank authentication is invalid or expired.Please re-authenticate."
                )
            )

        return " \n".join(errors)

    def _obtain_statement_data(self, date_since, date_until):
        self.ensure_one()
        if self.service != "enablebanking":
            return super()._obtain_statement_data(date_since, date_until)
        return self._enablebanking_obtain_statement_data(date_since, date_until)

    def _enablebanking_account_uid(self, session):
        """The uid of the account of this journal in the session.

        Banks give one IBAN to the accounts of several currencies (SEB: EUR and NOK, Revolut: EUR, GBP and USD), so the IBAN alone
        does not name an account: it is the IBAN and the currency of the journal (that of the company when the journal has none).
        The uid of an account changes with every new consent, so it is not kept on the provider. No account or more than one
        is an error: reading the wrong account of the same IBAN loads the lines of another currency into the books.
        """
        self.ensure_one()

        def iban(account):
            return (account.get("account_id") or {}).get("iban", "").replace(" ", "").upper()

        own_iban = (self.account_number or "").replace(" ", "").upper()
        currency = (self.journal_id.currency_id or self.company_id.currency_id).name
        candidates = [a for a in session.get("accounts", []) if own_iban and iban(a) == own_iban]
        if any(a.get("currency") for a in candidates):
            candidates = [a for a in candidates if a.get("currency") == currency]
        if len(candidates) != 1:
            raise UserError(
                self.env._(
                    "The consent holds %(count)s account(s) with the IBAN %(iban)s in %(currency)s for the journal %(journal)s; "
                    "exactly one is needed. Check the bank account of the journal and re-authenticate.",
                    count=len(candidates),
                    iban=own_iban or "-",
                    currency=currency,
                    journal=self.journal_id.display_name,
                )
            )
        return candidates[0]["uid"]

    def _enablebanking_obtain_statement_data(self, date_since, date_until):
        """
        EnableBanking: get bank statement data
        """
        self.ensure_one()
        enablebanking = self.enablebanking_application_id

        # A request that cannot succeed is not made: many banks answer four requests a day while the client is away.
        errors = self._enablebanking_errors()
        if errors:
            raise UserError(errors)

        session = enablebanking._get_session_dict()
        # Using the first available account for the following API calls
        if not session.get("accounts"):
            raise ValidationError(
                self.env._(
                    "Did not find any accounts. "
                    "Please check linked accounts in Enable Banking management "
                    "and re-authenticate"
                )
            )

        account_uid = self._enablebanking_account_uid(session)

        _logger.info(f"Using account {self.account_number}")

        if date_until > datetime.now():
            # API won't allow using a future date here
            date_until = datetime.now()

        date_from = date_since.date().isoformat()
        date_to = date_until.date().isoformat()
        query = {
            "date_from": date_from,
            "date_to": date_to,
        }
        _logger.info(f"Fetching transactions from {date_from} to {date_to}")

        jwt = enablebanking._enablebanking_get_jwt_token()
        base_headers = {"Authorization": f"Bearer {jwt}"}

        raw_transactions = []
        continuation_key = None
        while True:
            if continuation_key:
                query["continuation_key"] = continuation_key
            r = requests.get(
                f"{enablebanking.api_origin}/accounts/{account_uid}/transactions",
                params=query,
                headers=base_headers,
                timeout=30,
            )
            if r.status_code == 401:
                # Not authorized: expired, withdrawn. An error and not an empty answer: a pull that ends in silence moves the
                # date of the last good run forward and the days it skipped never come back.
                _logger.info(f"Error response {r.status_code}: {r.text}")
                raise UserError(
                    self.env._(
                        "Bank authentication is invalid. "
                        "Please re-authenticate to fetch bank statements."
                    )
                )
            if r.status_code != 200:
                raise ValidationError(
                    self.env._(
                        "Error response %(status_code)s: %(response_text)s",
                        status_code=r.status_code,
                        response_text=r.text,
                    )
                )
            resp_data = r.json()
            page = resp_data["transactions"]
            _logger.info(f"Found {len(page)} transactions")
            raw_transactions += page
            continuation_key = resp_data.get("continuation_key")
            if not continuation_key:
                _logger.info(
                    f"No continuation key. "
                    f"All transactions were fetched for {self.account_number}"
                )
                break
            _logger.info(
                f"Fetching more transactions with continuation key "
                f"{continuation_key}"
            )

        return self._enablebanking_lines(raw_transactions), {}

    def _enablebanking_lines(self, raw_transactions):
        """Statement line values of the booked, non-zero transactions of one answer, each with a key of its own."""
        self.ensure_one()
        twins = Counter()
        skipped_zero = []
        lines = []
        for transaction in raw_transactions:
            if transaction.get("status") not in (None, "BOOK"):
                # Pending lines have no stable reference and may change; they come when the bank has booked them.
                continue
            if Decimal(str(transaction["transaction_amount"]["amount"])) == 0:
                # Nothing moves in the books and the file of the bank has no such line.
                skipped_zero.append(
                    f"{transaction.get('entry_reference') or '-'} ({transaction.get('booking_date')})"
                )
                continue
            digest = self._enablebanking_digest(transaction)
            twin = (transaction.get("entry_reference"), digest)
            twins[twin] += 1
            vals = self._get_transaction_vals(transaction, len(lines) + 1)
            vals["unique_import_id"] = self._enablebanking_key(transaction, digest, twins[twin])
            lines.append(vals)
        if skipped_zero:
            _logger.info(
                "Enable Banking %s: %s line(s) with a zero amount skipped: %s",
                self.journal_id.display_name,
                len(skipped_zero),
                ", ".join(skipped_zero),
            )
        return lines

    @staticmethod
    def _enablebanking_digest(transaction):
        """What a line says, whatever the bank calls it: dates, signed amount, currency, text."""
        amount = Decimal(str(transaction["transaction_amount"]["amount"])).normalize()
        parts = [
            transaction.get("booking_date") or "",
            transaction.get("value_date") or "",
            transaction.get("credit_debit_indicator") or "",
            format(amount, "f"),
            transaction["transaction_amount"].get("currency") or "",
            " ".join(transaction.get("remittance_information") or []),
        ]
        return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]

    @staticmethod
    def _enablebanking_key(transaction, digest, ordinal):
        """The key a line is known by: the reference of the bank and what the line says, and its number among identical ones.

        The reference alone is not one: banks repeat it for lines of other days and amounts (the sandbox answer has 126 different
        in 150 lines), and the base module drops a line whose key it already knows, so a repeated reference would lose lines
        without a word. What the line says does not change between pulls, so the same line gets the same key in any window.
        """
        key = f"{transaction.get('entry_reference') or 'h'}:{digest}"
        return key if ordinal == 1 else f"{key}:{ordinal}"

    def _get_transaction_vals(self, transaction, sequence):
        """
        Map EnableBanking transaction data to Odoo bank statement line values.
        """
        _logger.debug(f"Mapping transaction data to bank statement: {transaction}")
        transaction_type = transaction.get("credit_debit_indicator")
        if transaction_type == "DBIT":
            multiplier = -1
        else:
            multiplier = 1

        value_date = transaction.get("value_date")
        booking_date = transaction.get("booking_date")

        if not value_date or value_date < booking_date:
            # Some banks don't seem to return a value date at all
            # If value date is missing, we use booking date as value date.

            # As value date may be some banking dates before booking date,
            # we may also encounter a situation
            # where we receive a transaction some days after the value date.
            # This can be even 3-4 days on weekends.
            # If a bank statement for that date is already handled in Odoo,
            # the transaction is silently dropped.
            # To avoid this, we set booking date as value date.
            # The value date in Odoo will be wrong and might cause
            # a slight miscalculation in overdue interest fee
            value_date = booking_date

        # The other side of the payment: whom we paid when the money went out, who paid us when it came in.
        side = "creditor" if transaction_type == "DBIT" else "debtor"
        partner_name = (transaction.get(side) or {}).get("name")
        counterparty_iban = (transaction.get(f"{side}_account") or {}).get("iban")

        ref = transaction.get("reference_number")
        remittance_information = " ".join(transaction.get("remittance_information") or [])
        payment_ref = ref or remittance_information

        if payment_ref:
            # Strip leading zeros to make sure the reconciliation works
            payment_ref = payment_ref.lstrip("0") or "-"
        else:
            payment_ref = "-"

        vals = {
            "sequence": sequence,
            "date": value_date,
            "payment_ref": payment_ref,
            "ref": ref,
            "amount": float(transaction.get("transaction_amount").get("amount"))
            * multiplier,  # TODO: currency
            "account_number": counterparty_iban,
            "partner_name": partner_name,
            "narration": remittance_information,
        }

        # Try to find partner with exact name match
        if partner_name != "" and partner_name is not None:
            partner_id = (
                self.env["res.partner"]
                .sudo()
                .search([("name", "=ilike", partner_name)], limit=1)
            )
            if partner_id:
                vals["partner_id"] = partner_id.id

        return vals
