# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl.html).
"""Import probes on the CAMT.053 example published by Swedbank.

``samples/swedbank-camt053-two-currencies`` is the CAMT.053 example of the
Swedbank B2C message guide: one account EE062200221055091966 with two
statements, 539631915-EUR-1 (7 entries, opening 0.00, closing 263.35 EUR)
and 539631915-USD-1 (2 entries, opening and closing 0.00 USD).
"""

import zipfile
from io import BytesIO

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase
from odoo.tools import BinaryBytes
from odoo.tools.misc import file_path

TWO_CURRENCIES = (
    "account_statement_import_camt/tests/samples/swedbank-camt053-two-currencies"
)
BATCH = "account_statement_import_camt/tests/samples/lv-probe-camt053-batch"
IBAN = "EE062200221055091966"


def _read(path):
    with open(file_path(path), "rb") as sample:
        return sample.read()


class TestBankSamples(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.eur = cls.env.ref("base.EUR")
        cls.usd = cls.env.ref("base.USD")
        (cls.eur | cls.usd).write({"active": True})
        cls.company = cls.env.company
        cls.own_account = cls.env["res.partner.bank"].create(
            {
                "account_number": IBAN,
                "partner_id": cls.company.partner_id.id,
                "company_id": cls.company.id,
            }
        )
        cls.parser = cls.env["account.statement.import.camt.parser"]

    def _journal(self, code, currency):
        return self.env["account.journal"].create(
            {
                "name": f"Bank {code}",
                "code": code,
                "type": "bank",
                "bank_account_id": self.own_account.id,
                "currency_id": currency.id,
            }
        )

    def _import(self, data, filename):
        return (
            self.env["account.statement.import"]
            .create(
                {"statement_filename": filename, "statement_file": BinaryBytes(data)}
            )
            .import_file_button()
        )

    def _parsed_statements(self, data):
        """Every statement the parser returns, with the currency and account
        it was returned under, whether it answers one triplet or a list."""
        parsed = self.parser.parse(data)
        triplets = parsed if isinstance(parsed, list) else [parsed]
        return [
            (currency, account, statement)
            for currency, account, statements in triplets
            for statement in statements
        ]

    # ---- one statement per currency ------------------------------------------

    def test_each_statement_keeps_its_own_currency(self):
        """The two statements of the file are in EUR and USD (Acct/Ccy of each
        Stmt): each must come out of the parser under its own currency."""
        by_name = {
            statement["name"]: (currency, account)
            for currency, account, statement in self._parsed_statements(
                _read(TWO_CURRENCIES)
            )
        }
        self.assertEqual(
            by_name,
            {"539631915-EUR-1": ("EUR", IBAN), "539631915-USD-1": ("USD", IBAN)},
        )

    def test_eur_statement_does_not_land_on_a_usd_journal(self):
        """With only a USD journal on the account, the EUR statement has no
        journal of its currency: the import must refuse instead of booking
        the EUR amounts as USD, and must leave nothing behind."""
        journal_usd = self._journal("SWUSD", self.usd)
        with self.assertRaises(UserError):
            self._import(_read(TWO_CURRENCIES), "swedbank.xml")
        self.assertFalse(
            self.env["account.bank.statement"].search(
                [("journal_id", "=", journal_usd.id)]
            )
        )

    def _statements_of(self, journal):
        return self.env["account.bank.statement"].search(
            [("journal_id", "=", journal.id)]
        )

    def _assert_two_currencies_imported(self, journal_eur, journal_usd):
        statement_eur = self._statements_of(journal_eur)
        statement_usd = self._statements_of(journal_usd)
        self.assertEqual(statement_eur.name, "539631915-EUR-1")
        self.assertEqual(len(statement_eur.line_ids), 7)
        self.assertAlmostEqual(
            sum(statement_eur.line_ids.mapped("amount")), 263.35, places=2
        )
        self.assertAlmostEqual(statement_eur.balance_end_real, 263.35, places=2)
        self.assertEqual(statement_usd.name, "539631915-USD-1")
        self.assertEqual(len(statement_usd.line_ids), 2)

    def test_two_currencies_land_on_the_journals_of_their_currency(self):
        """The account has a EUR and a USD journal: each statement of the file
        goes to the journal of its currency, the EUR lines add up to 263.35."""
        journal_eur = self._journal("SWEUR", self.eur)
        journal_usd = self._journal("SWUSD", self.usd)
        self._import(_read(TWO_CURRENCIES), "swedbank.xml")
        self._assert_two_currencies_imported(journal_eur, journal_usd)

    def test_two_currencies_inside_a_zip_land_the_same_way(self):
        """A zip holding the same file imports the same two statements."""
        journal_eur = self._journal("SWEUR", self.eur)
        journal_usd = self._journal("SWUSD", self.usd)
        archive = BytesIO()
        with zipfile.ZipFile(archive, "w") as zipped:
            zipped.writestr("swedbank.xml", _read(TWO_CURRENCIES))
        self._import(archive.getvalue(), "swedbank.zip")
        self._assert_two_currencies_imported(journal_eur, journal_usd)

    # ---- the booked amount of an entry ---------------------------------------

    def test_booked_amount_is_the_line_amount_of_a_single_transaction(self):
        """Entry 20131008-1 is booked 0.60 DBIT; its only TxDtls carries a
        TxAmt of 1.00. The account moved by the booked amount, so the line is
        -0.60 and the EUR lines add up to closing minus opening, 263.35."""
        statement = next(
            statement
            for _currency, _account, statement in self._parsed_statements(
                _read(TWO_CURRENCIES)
            )
            if statement["name"] == "539631915-EUR-1"
        )
        by_ref = {t["unique_import_id"]: t["amount"] for t in statement["transactions"]}
        self.assertAlmostEqual(by_ref["20131008-1-20131008-1"], -0.60, places=2)
        self.assertAlmostEqual(
            sum(t["amount"] for t in statement["transactions"]),
            statement["balance_end_real"] - statement["balance_start"],
            places=2,
        )

    def test_batch_whose_transactions_do_not_add_up_is_one_booked_line(self):
        """The batch entry is booked 250.50 CRDT with TxAmt 200.00 and 50.50.
        With the second TxAmt changed to 40.50 the transactions no longer add
        up to the booked amount: the entry becomes one line of 250.50, the
        amount the account moved by."""
        data = _read(BATCH).replace(
            b'<Amt Ccy="EUR">50.50</Amt>', b'<Amt Ccy="EUR">40.50</Amt>', 1
        )
        statements = self._parsed_statements(data)
        amounts = [
            t["amount"]
            for _c, _a, statement in statements
            for t in statement["transactions"]
        ]
        self.assertIn(250.50, amounts)
        self.assertNotIn(200.00, amounts)
        self.assertNotIn(40.50, amounts)

    def test_batch_whose_transactions_add_up_keeps_one_line_each(self):
        """The unchanged batch: 200.00 + 50.50 = 250.50 booked, two lines."""
        amounts = [
            t["amount"]
            for _c, _a, statement in self._parsed_statements(_read(BATCH))
            for t in statement["transactions"]
        ]
        self.assertIn(200.00, amounts)
        self.assertIn(50.50, amounts)
        self.assertNotIn(250.50, amounts)
