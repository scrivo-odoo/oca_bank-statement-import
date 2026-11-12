# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl.html).
"""Import probes on a hand-written QIF sample with Latvian payees.

The expected values below are copied from ``samples/lv-probe.qif``: one
credit and two debits (250.50, -120.00, -35.40) on 2 and 3 November 2026.
``samples/lv-probe-memo.qif`` gives two items a payee (``P``) and a memo
(``M``), in both orders: the payee is the counterparty, the memo the purpose.
"""

from datetime import date

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase
from odoo.tools import BinaryBytes
from odoo.tools.misc import file_path

SAMPLE = "account_statement_import_qif/tests/samples/lv-probe.qif"
SAMPLE_MEMO = "account_statement_import_qif/tests/samples/lv-probe-memo.qif"


class TestLvProbeQif(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.journal = cls.env["account.journal"].create(
            {
                "name": "Bank LV probe",
                "code": "LVQIF",
                "type": "bank",
                "currency_id": cls.env.company.currency_id.id,
            }
        )
        with open(file_path(SAMPLE), "rb") as sample:
            cls.sample_bytes = sample.read()

    def _import(self, data):
        return (
            self.env["account.statement.import"]
            .with_context(journal_id=self.journal.id)
            .create(
                {
                    "statement_filename": "lv-probe.qif",
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

    def test_valid_qif_creates_exact_lines(self):
        self._import(self.sample_bytes)
        lines = self._journal_lines().sorted(lambda line: (line.date, -line.amount))
        self.assertEqual(len(lines), 3)
        self.assertEqual(lines.mapped("amount"), [250.50, -120.00, -35.40])
        self.assertEqual(
            lines.mapped("date"),
            [date(2026, 11, 2), date(2026, 11, 2), date(2026, 11, 3)],
        )
        self.assertEqual(len(self._journal_statements()), 1)
        self.assertAlmostEqual(sum(lines.mapped("amount")), 95.10, places=2)

    def test_truncated_qif_is_rejected(self):
        # The file stops inside the second item: its date and amount are
        # there, its end marker "^" is not.
        cut = self.sample_bytes.index(b"PAS Elektrotikls")
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

    def test_not_qif_is_rejected(self):
        try:
            self._import(b"\x00\x01 this is not a bank statement \xff")
        except UserError:
            pass
        else:
            self.fail("a file that is not a statement was imported")
        self.assertFalse(self._journal_statements())
        self.assertFalse(self._journal_lines())

    def test_payee_and_memo_are_both_kept(self):
        paraugs = self.env["res.partner"].create({"name": "SIA Paraugs"})
        elektrotikls = self.env["res.partner"].create({"name": "AS Elektrotikls"})
        with open(file_path(SAMPLE_MEMO), "rb") as sample:
            self._import(sample.read())
        lines = self._journal_lines().sorted(lambda line: -line.amount)
        self.assertEqual(lines.mapped("amount"), [250.50, -120.00])
        # Payee first, then memo, whatever the order of P and M in the item.
        self.assertEqual(
            lines.mapped("payment_ref"),
            ["SIA Paraugs: Rekins 2026/117", "AS Elektrotikls: Elektriba oktobris"],
        )
        self.assertEqual(
            lines.mapped("partner_name"), ["SIA Paraugs", "AS Elektrotikls"]
        )
        self.assertEqual(lines.mapped("partner_id"), paraugs + elektrotikls)
