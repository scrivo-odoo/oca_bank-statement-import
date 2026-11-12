# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl.html).
"""Import probes on a hand-written CSV sample with Latvian accounts.

The expected values below are copied from ``samples/lv-probe.csv``: one
credit and two debits (250.50, -120.00, -35.40) on 2 and 3 November 2026,
with the counterparty names and IBANs of the rows.
"""

from datetime import date

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase
from odoo.tools import BinaryBytes
from odoo.tools.misc import file_path

SAMPLE = "account_statement_import_sheet_file/tests/samples/lv-probe.csv"


class TestLvProbeSheet(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.eur = cls.env.ref("base.EUR")
        cls.eur.write({"active": True})
        cls.mapping = cls.env["account.statement.import.sheet.mapping"].create(
            {
                "name": "LV probe CSV",
                "header_lines_skip_count": 1,
                "footer_lines_skip_count": 0,
                "float_thousands_sep": "none",
                "float_decimal_sep": "dot",
                "delimiter": "comma",
                "quotechar": '"',
                "timestamp_format": "%Y-%m-%d",
                "timestamp_column": "Date",
                "amount_type": "simple_value",
                "amount_column": "Amount",
                "description_column": "Label",
                "partner_name_column": "Partner Name",
                "bank_account_column": "Bank Account",
            }
        )
        cls.journal = cls.env["account.journal"].create(
            {
                "name": "Bank LV probe",
                "code": "LVCSV",
                "type": "bank",
                "currency_id": cls.eur.id,
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
                    "statement_filename": "lv-probe.csv",
                    "statement_file": BinaryBytes(data),
                    "sheet_mapping_id": self.mapping.id,
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

    def test_valid_csv_creates_exact_lines(self):
        self._import(self.sample_bytes)
        self.assertEqual(len(self._journal_statements()), 1)
        lines = self._journal_lines().sorted(lambda line: (line.date, -line.amount))
        self.assertEqual(len(lines), 3)
        self.assertEqual(lines.mapped("amount"), [250.50, -120.00, -35.40])
        self.assertEqual(
            lines.mapped("date"),
            [date(2026, 11, 2), date(2026, 11, 2), date(2026, 11, 3)],
        )
        self.assertEqual(
            lines.mapped("partner_name"),
            ["SIA Paraugs", "AS Elektrotikls", "SIA Birojs"],
        )
        self.assertEqual(
            lines.mapped("account_number"),
            [
                "LV10RIKO0002013000000",
                "LV41HABA0551000000001",
                "LV62PARX0000000000002",
            ],
        )

    def test_truncated_csv_is_rejected(self):
        # The file stops inside the second row: its date and label are there,
        # its amount and counterparty are not.
        cut = self.sample_bytes.index(b'"-120.00"')
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

    def test_not_csv_is_rejected(self):
        try:
            self._import(b"\x00\x01 this is not a bank statement \xff")
        except UserError:
            pass
        else:
            self.fail("a file that is not a statement was imported")
        self.assertFalse(self._journal_statements())
        self.assertFalse(self._journal_lines())
