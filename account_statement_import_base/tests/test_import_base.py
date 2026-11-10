# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).

from psycopg2 import IntegrityError

from odoo.tests import common
from odoo.tools import mute_logger


class TestImportBase(common.TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.journal = cls.env["account.journal"].create(
            {"name": "Import base bank", "code": "IMPB", "type": "bank"}
        )
        cls.partner = cls.env["res.partner"].create({"name": "Known payer"})
        # Stored with separators, as a user types an IBAN
        cls.partner_bank = cls.env["res.partner.bank"].create(
            {"account_number": "DE89 3704 0044 0532 0130 00", "partner_id": cls.partner.id}
        )

    def _line(self, **vals):
        base = {
            "journal_id": self.journal.id,
            "amount": 10.0,
            "payment_ref": "Test line",
        }
        base.update(vals)
        return self.env["account.bank.statement.line"].create(base)

    # unique import id

    def test_unique_import_id_gets_account_and_journal_prefix(self):
        vals = {"unique_import_id": "TX1"}
        self.journal._statement_line_import_update_unique_import_id(
            vals, "DE89 3704 0044 0532 0130 00"
        )
        self.assertEqual(
            vals["unique_import_id"], f"DE89370400440532013000-{self.journal.id}-TX1"
        )

    def test_unique_import_id_without_account_keeps_only_journal(self):
        vals = {"unique_import_id": "TX1"}
        self.journal._statement_line_import_update_unique_import_id(vals, False)
        self.assertEqual(vals["unique_import_id"], f"{self.journal.id}-TX1")

    def test_line_without_unique_import_id_is_left_alone(self):
        vals = {"payment_ref": "no id"}
        self.journal._statement_line_import_update_unique_import_id(vals, "123")
        self.assertNotIn("unique_import_id", vals)

    def test_same_transaction_cannot_be_imported_twice(self):
        self._line(unique_import_id="DUP-1")
        with self.assertRaises(IntegrityError), mute_logger("odoo.sql_db"):
            with self.env.cr.savepoint():
                self._line(unique_import_id="DUP-1")

    def test_different_transactions_are_both_imported(self):
        self._line(unique_import_id="A-1")
        line = self._line(unique_import_id="A-2")
        self.assertTrue(line.id)

    # partner lookup by account number

    def test_partner_found_by_account_number_written_differently(self):
        speeddict = self.journal._statement_line_import_speeddict()
        vals = {"account_number": "de89370400440532013000"}
        self.journal._statement_line_import_update_hook(vals, speeddict)
        self.assertEqual(vals["partner_id"], self.partner.id)
        self.assertEqual(vals["partner_bank_id"], self.partner_bank.id)
        self.assertEqual(vals["account_number"], "DE89370400440532013000")

    def test_unknown_account_number_gets_no_partner(self):
        speeddict = self.journal._statement_line_import_speeddict()
        vals = {"account_number": "FR7630006000011234567890189"}
        self.journal._statement_line_import_update_hook(vals, speeddict)
        self.assertNotIn("partner_id", vals)

    def test_partner_given_by_the_file_is_not_replaced(self):
        other = self.env["res.partner"].create({"name": "Other payer"})
        speeddict = self.journal._statement_line_import_speeddict()
        vals = {"account_number": "DE89370400440532013000", "partner_id": other.id}
        self.journal._statement_line_import_update_hook(vals, speeddict)
        self.assertEqual(vals["partner_id"], other.id)

    def test_line_without_account_number_gets_no_partner(self):
        speeddict = self.journal._statement_line_import_speeddict()
        vals = {"payment_ref": "no account"}
        self.journal._statement_line_import_update_hook(vals, speeddict)
        self.assertNotIn("partner_id", vals)

    def test_bank_account_of_another_company_is_not_offered(self):
        other_company = self.env["res.company"].create({"name": "Other company"})
        other_partner = self.env["res.partner"].create(
            {"name": "Other co payer", "company_id": other_company.id}
        )
        self.env["res.partner.bank"].create(
            {
                "account_number": "NL91ABNA0417164300",
                "partner_id": other_partner.id,
                "company_id": other_company.id,
            }
        )
        speeddict = self.journal._statement_line_import_speeddict()
        vals = {"account_number": "NL91ABNA0417164300"}
        self.journal._statement_line_import_update_hook(vals, speeddict)
        self.assertNotIn("partner_id", vals)
