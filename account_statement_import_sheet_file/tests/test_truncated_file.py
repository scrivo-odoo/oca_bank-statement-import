# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl.html).
"""A CSV file cut inside a row is refused."""

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase
from odoo.tools import BinaryBytes
from odoo.tools.misc import file_path

SAMPLE = "account_statement_import_sheet_file/tests/samples/csv-three-rows.csv"


class TestTruncatedFile(TransactionCase):
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
