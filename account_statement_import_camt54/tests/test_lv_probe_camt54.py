# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl.html).
"""Import probes on a hand-written CAMT.054 sample with Latvian accounts.

The expected values below are copied from ``samples/lv-probe-camt054``:
a notification on account LV80BANK0000435195001 with a credit of 80.00
carrying the structured reference RF18539007547034 and a debit of 12.30
with an unstructured remittance text; a notification has no balances.
"""

from datetime import date

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase
from odoo.tools import BinaryBytes
from odoo.tools.misc import file_path

SAMPLE = "account_statement_import_camt54/tests/samples/lv-probe-camt054"
NOTIFICATION = "LVPROBE054/2026-11-04"


class TestLvProbeCamt54(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.eur = cls.env.ref("base.EUR")
        cls.eur.write({"active": True})
        company = cls.env.company
        own_account = cls.env["res.partner.bank"].create(
            {
                "account_number": "LV80BANK0000435195001",
                "partner_id": company.partner_id.id,
                "company_id": company.id,
            }
        )
        cls.journal = cls.env["account.journal"].create(
            {
                "name": "Bank LV probe 054",
                "code": "LV054",
                "type": "bank",
                "bank_account_id": own_account.id,
                "currency_id": cls.eur.id,
            }
        )
        with open(file_path(SAMPLE), "rb") as sample:
            cls.sample_bytes = sample.read()

    def _import(self, data):
        return (
            self.env["account.statement.import"]
            .create(
                {
                    "statement_filename": "lv-probe-054.xml",
                    "statement_file": BinaryBytes(data),
                }
            )
            .import_file_button()
        )

    def _statement(self):
        return self.env["account.bank.statement"].search(
            [("journal_id", "=", self.journal.id)]
        )

    def _check_file_lines(self, lines):
        self.assertEqual(lines.mapped("amount"), [80.00, -12.30])
        self.assertEqual(lines.mapped("date"), [date(2026, 11, 4)] * 2)
        self.assertEqual(lines.mapped("partner_name"), ["SIA Paraugs", "SIA Birojs"])
        self.assertEqual(
            lines.mapped("account_number"),
            ["LV10RIKO0002013000000", "LV62PARX0000000000002"],
        )
        # The remittance reference becomes the label of the line.
        self.assertEqual(lines[0].payment_ref, "RF18539007547034")
        self.assertIn("Kancelejas preces novembris", lines[1].payment_ref)

    def test_notification_end_balance_follows_the_lines(self):
        self.journal.transfer_line = False
        self._import(self.sample_bytes)
        statement = self._statement()
        self.assertEqual(len(statement), 1)
        self.assertEqual(statement.name, NOTIFICATION)
        lines = statement.line_ids.sorted("sequence")
        self.assertEqual(len(lines), 2)
        self._check_file_lines(lines)
        self.assertAlmostEqual(statement.balance_start, 0.0, places=2)
        self.assertAlmostEqual(statement.balance_end_real, 67.70, places=2)

    def test_notification_with_balancing_line_totals_zero(self):
        self.journal.transfer_line = True
        self._import(self.sample_bytes)
        statement = self._statement()
        self.assertEqual(len(statement), 1)
        lines = statement.line_ids.sorted("sequence")
        self.assertEqual(len(lines), 3)
        file_lines = lines.filtered(lambda line: line.payment_ref != NOTIFICATION)
        self._check_file_lines(file_lines)
        balancing = lines - file_lines
        self.assertEqual(len(balancing), 1)
        self.assertAlmostEqual(balancing.amount, -67.70, places=2)
        self.assertEqual(balancing.date, date(2026, 11, 4))
        self.assertAlmostEqual(sum(lines.mapped("amount")), 0.0, places=2)
        self.assertAlmostEqual(statement.balance_end_real, 0.0, places=2)

    def test_reimporting_the_same_notification_adds_no_lines(self):
        self.journal.transfer_line = True
        self._import(self.sample_bytes)
        try:
            self._import(self.sample_bytes)
        except UserError:
            pass  # refused as already imported
        lines = self.env["account.bank.statement.line"].search(
            [("journal_id", "=", self.journal.id)]
        )
        # Two lines from the file and one balancing line, once.
        self.assertEqual(len(lines), 3)
        self.assertAlmostEqual(sum(lines.mapped("amount")), 0.0, places=2)

    def test_truncated_notification_is_rejected(self):
        self.journal.transfer_line = True
        cut = self.sample_bytes.index(b"<Nm>SIA Birojs")
        with self.assertRaises(UserError):
            self._import(self.sample_bytes[:cut])
        self.assertFalse(self._statement())
        self.assertFalse(
            self.env["account.bank.statement.line"].search(
                [("journal_id", "=", self.journal.id)]
            )
        )
