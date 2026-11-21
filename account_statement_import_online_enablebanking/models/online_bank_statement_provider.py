import hashlib
import json
import logging
from collections import Counter
from datetime import date, datetime, timedelta
from decimal import Decimal

import requests

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)

# The API takes a window by the booking date of a line, and a line is dated by its value date when that is later. For a window
# to find every line it takes, the value date counts up to this long after the booking and a pull reaches this far back before
# its start. Recorded answers of the two banks: the value date is 0 or 1 day after the booking.
VALUE_DATE_LAG = timedelta(days=7)


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

    @api.constrains("enablebanking_application_id", "journal_id")
    def _check_enablebanking_application_company(self):
        """The session of an application is the bank access of one firm: a provider of another firm does not use it."""
        for provider in self:
            application = provider.enablebanking_application_id.sudo()
            if application and application.company_id != provider.company_id:
                raise ValidationError(
                    self.env._(
                        "The Enable Banking application %(application)s belongs to the company %(application_company)s, "
                        "the journal %(journal)s to the company %(journal_company)s: a provider uses only the application of its own company.",
                        application=application.application_id,
                        application_company=application.company_id.display_name,
                        journal=provider.journal_id.display_name,
                        journal_company=provider.company_id.display_name,
                    )
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

        date_from = (date_since - VALUE_DATE_LAG).date().isoformat()
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
    def _enablebanking_counterparty(transaction):
        """The other side of the payment, as (name, IBAN): whom we paid when the money went out, who paid us when it came in."""
        side = "creditor" if transaction.get("credit_debit_indicator") == "DBIT" else "debtor"
        return (transaction.get(side) or {}).get("name"), (transaction.get(f"{side}_account") or {}).get("iban")

    def _enablebanking_digest(self, transaction):
        """What a line says, whatever the bank calls it: everything the line of the book shows, so that two different operations
        of one day, amount and text (two payers, two invoices) never get one key.

        The own side of the payment is left out: it is the account itself, and a bank that gives it in one answer and not in
        the next would change the key of a line it already delivered.
        """
        amount = Decimal(str(transaction["transaction_amount"]["amount"])).normalize()
        name, iban = self._enablebanking_counterparty(transaction)
        parts = [
            transaction.get("booking_date") or "",
            transaction.get("value_date") or "",
            transaction.get("credit_debit_indicator") or "",
            format(amount, "f"),
            transaction["transaction_amount"].get("currency") or "",
            transaction.get("reference_number") or "",
            transaction.get("remittance_information") or [],
            name or "",
            iban or "",
        ]
        return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()[:16]

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

        partner_name, counterparty_iban = self._enablebanking_counterparty(transaction)

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
            "date": self._enablebanking_line_date(transaction),
            "payment_ref": payment_ref,
            "ref": ref,
            "amount": float(transaction.get("transaction_amount").get("amount"))
            * multiplier,  # TODO: currency
            "account_number": counterparty_iban,
            "partner_name": partner_name,
            "narration": remittance_information,
        }

        # The partner is the one with exactly this name. A name of two partners does not name either of them: the line comes
        # without a partner, the name stays on it, and the log says why.
        if partner_name:
            partners = self.env["res.partner"].sudo().search([("name", "=ilike", partner_name)], limit=2)
            if len(partners) == 1:
                vals["partner_id"] = partners.id
            elif partners:
                _logger.info(
                    "Enable Banking %s: more than one partner is named %r, the line %s comes without a partner",
                    self.journal_id.display_name,
                    partner_name,
                    transaction.get("entry_reference") or "-",
                )

        return vals

    def _enablebanking_line_date(self, transaction):
        """The day of a line: the day the money moved (value date); the booking date when the bank gives no value date or an
        earlier one; never later than VALUE_DATE_LAG after the booking.

        The API takes a window by the booking date and the base module takes a line by this date, so the two must not drift
        apart: with the cap, a line dated in a window was booked at most VALUE_DATE_LAG before it, and a pull that reaches
        VALUE_DATE_LAG back finds it. Without it a line valued weeks after its booking is dropped by the window that
        returns it and never returned by the one that would take it.
        """
        booking = transaction.get("booking_date")
        value = transaction.get("value_date")
        if not booking and not value:
            raise UserError(
                self.env._(
                    "The line %(reference)s of the bank has neither a booking date nor a value date: it cannot be put on a day.",
                    reference=transaction.get("entry_reference") or "-",
                )
            )
        if not booking:
            return value
        if not value or value < booking:
            return booking
        return min(value, (date.fromisoformat(booking) + VALUE_DATE_LAG).isoformat())
