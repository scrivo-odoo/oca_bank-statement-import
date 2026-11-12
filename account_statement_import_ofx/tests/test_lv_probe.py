# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl.html).
"""Import probes on a hand-written OFX sample with a Latvian account.

The expected values below are copied from ``samples/lv-probe.ofx``: account
LV80BANK0000435195001 in EUR, one credit and two debits (250.50, -120.00,
-35.40) on 2 and 3 November 2026, ledger balance 1095.10, hence an opening
balance of 1000.00.
"""

from datetime import date

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase
from odoo.tools import BinaryBytes
from odoo.tools.misc import file_path

SAMPLE = "account_statement_import_ofx/tests/samples/lv-probe.ofx"


class TestLvProbeOfx(TransactionCase):
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
                "name": "Bank LV probe",
                "code": "LVOFX",
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
                    "statement_filename": "lv-probe.ofx",
                    "statement_file": BinaryBytes(data),
                }
            )
            .import_file_button()
        )

    def _journal_lines(self):
        return self.env["account.bank.statement.line"].search(
            [("journal_id", "=", self.journal.id)]
        )

    def _journal_statements(self):
        return self.env["account.bank.statement"].search(
            [("journal_id", "=", self.journal.id)]
        )

    def test_valid_ofx_creates_exact_lines(self):
        self._import(self.sample_bytes)
        statement = self._journal_statements()
        self.assertEqual(len(statement), 1)
        self.assertAlmostEqual(statement.balance_start, 1000.00, places=2)
        self.assertAlmostEqual(statement.balance_end_real, 1095.10, places=2)
        lines = statement.line_ids.sorted(lambda line: (line.date, -line.amount))
        self.assertEqual(len(lines), 3)
        self.assertEqual(lines.mapped("amount"), [250.50, -120.00, -35.40])
        self.assertEqual(
            lines.mapped("date"),
            [date(2026, 11, 2), date(2026, 11, 2), date(2026, 11, 3)],
        )
        self.assertEqual(
            lines.mapped("payment_ref"),
            ["SIA Paraugs", "AS Elektrotikls", "SIA Birojs"],
        )

    def test_truncated_ofx_is_rejected(self):
        # The file stops inside the second transaction.
        cut = self.sample_bytes.index(b"<NAME>AS Elektrotikls")
        try:
            self._import(self.sample_bytes[:cut])
        except UserError:
            pass
        else:
            lines = self._journal_lines()
            self.fail(
                f"truncated file was imported: {len(lines)} line(s), "
                f"amounts {sorted(lines.mapped('amount'))}"
            )
        self.assertFalse(self._journal_statements())
        self.assertFalse(self._journal_lines())

    def test_not_ofx_is_rejected(self):
        try:
            self._import(b"\x00\x01 this is not a bank statement \xff")
        except UserError:
            pass
        else:
            self.fail("a file that is not a statement was imported")
        self.assertFalse(self._journal_statements())
        self.assertFalse(self._journal_lines())
