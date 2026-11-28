# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl.html).
"""An amount written with a separator the mapping does not name is refused."""

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase
from odoo.tools import BinaryBytes

HEADER = '"Date","Label","Amount"\n'


class TestAmountSeparators(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.eur = cls.env.ref("base.EUR")
        cls.eur.write({"active": True})
        cls.journal = cls.env["account.journal"].create(
            {
                "name": "Bank separators probe",
                "code": "SEPCS",
                "type": "bank",
                "currency_id": cls.eur.id,
            }
        )

    def _mapping(self, thousands, decimal):
        return self.env["account.statement.import.sheet.mapping"].create(
            {
                "name": f"separators {thousands}/{decimal}",
                "header_lines_skip_count": 1,
                "footer_lines_skip_count": 0,
                "float_thousands_sep": thousands,
                "float_decimal_sep": decimal,
                "delimiter": "comma",
                "quotechar": '"',
                "timestamp_format": "%Y-%m-%d",
                "timestamp_column": "Date",
                "amount_type": "simple_value",
                "amount_column": "Amount",
                "description_column": "Label",
            }
        )

    def _import(self, mapping, amounts):
        rows = "".join(
            f'"2026-11-0{n}","Row {n}","{amount}"\n'
            for n, amount in enumerate(amounts, start=1)
        )
        return (
            self.env["account.statement.import"]
            .with_context(journal_id=self.journal.id)
            .create(
                {
                    "statement_filename": "separators-probe.csv",
                    "statement_file": BinaryBytes((HEADER + rows).encode()),
                    "sheet_mapping_id": mapping.id,
                }
            )
            .import_file_button()
        )

    def _amounts_in_the_journal(self):
        lines = self.env["account.bank.statement.line"].search(
            [("journal_id", "=", self.journal.id)]
        )
        return sorted(lines.mapped("amount"))

    def _assert_refused(self, mapping, amounts, cell):
        with self.assertRaises(UserError) as refusal:
            self._import(mapping, amounts)
        self.assertIn(cell, str(refusal.exception))
        self.assertEqual(self._amounts_in_the_journal(), [])

    def test_decimal_comma_under_a_decimal_point_mapping_is_refused(self):
        # 40,50 was read as 4050.0: the comma is dropped, no error is raised.
        self._assert_refused(self._mapping("none", "dot"), ["12.00", "40,50"], "40,50")

    def test_decimal_point_under_a_decimal_comma_mapping_is_refused(self):
        self._assert_refused(self._mapping("none", "comma"), ["12,00", "40.50"], "40.50")

    def test_unnamed_thousands_comma_is_refused(self):
        # The mapping names no thousands separator: the file is not the layout
        # the mapping describes, and the cell is not guessed.
        self._assert_refused(self._mapping("none", "dot"), ["1,004.13"], "1,004.13")

    def test_amounts_in_the_form_of_the_mapping_are_read(self):
        self._import(self._mapping("none", "dot"), ["40.50", "-1004.13"])
        self.assertEqual(self._amounts_in_the_journal(), [-1004.13, 40.5])

    def test_the_thousands_separator_of_the_mapping_is_read(self):
        self._import(self._mapping("comma", "dot"), ["1,004.13"])
        self.assertEqual(self._amounts_in_the_journal(), [1004.13])

    def test_the_decimal_comma_of_the_mapping_is_read(self):
        self._import(self._mapping("dot", "comma"), ["1.004,13", "-40,50"])
        self.assertEqual(self._amounts_in_the_journal(), [-40.5, 1004.13])

    def test_a_currency_mark_and_spaces_around_the_amount_are_still_ignored(self):
        self._import(self._mapping("none", "dot"), ["EUR 40.50", " 12.00 "])
        self.assertEqual(self._amounts_in_the_journal(), [12.0, 40.5])
