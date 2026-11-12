# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl.html).
"""Import probes on a hand-written CAMT.053 sample with Latvian accounts.

The expected values below are copied from ``samples/lv-probe-camt053``:
one credit and two debits on account LV80BANK0000435195001, opening
balance 1000.00, closing balance 1095.10 EUR.
"""

from datetime import date

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase
from odoo.tools import BinaryBytes
from odoo.tools.misc import file_path

SAMPLE = "account_statement_import_camt/tests/samples/lv-probe-camt053"


class TestLvProbeImport(TransactionCase):
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
                "code": "LVPRB",
                "type": "bank",
                "bank_account_id": own_account.id,
                "currency_id": cls.eur.id,
            }
        )
        cls.customer = cls.env["res.partner"].create({"name": "SIA Paraugs"})
        cls.env["res.partner.bank"].create(
            {
                "account_number": "LV10RIKO0002013000000",
                "partner_id": cls.customer.id,
            }
        )
        with open(file_path(SAMPLE), "rb") as sample:
            cls.sample_bytes = sample.read()

    def _import(self, data):
        return (
            self.env["account.statement.import"]
            .create(
                {
                    "statement_filename": "lv-probe.xml",
                    "statement_file": BinaryBytes(data),
                }
            )
            .import_file_button()
        )

    def _journal_statements(self):
        return self.env["account.bank.statement"].search(
            [("journal_id", "=", self.journal.id)]
        )

    def _journal_lines(self):
        return self.env["account.bank.statement.line"].search(
            [("journal_id", "=", self.journal.id)]
        )

    def test_valid_camt053_creates_exact_lines(self):
        self._import(self.sample_bytes)

        statement = self._journal_statements()
        self.assertEqual(len(statement), 1)
        self.assertEqual(statement.name, "LVPROBE/2026-11-03")
        self.assertEqual(statement.currency_id, self.eur)
        self.assertAlmostEqual(statement.balance_start, 1000.00, places=2)
        self.assertAlmostEqual(statement.balance_end_real, 1095.10, places=2)

        lines = statement.line_ids.sorted("sequence")
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
        for line, label in zip(
            lines,
            ["Rekins 2026/117", "Elektriba oktobris", "Kancelejas preces"],
            strict=True,
        ):
            self.assertIn(label, line.payment_ref)
        # The debtor's IBAN is known in Odoo: the line is matched to its partner.
        self.assertEqual(lines[0].partner_id, self.customer)
        self.assertAlmostEqual(sum(lines.mapped("amount")), 95.10, places=2)

    def test_truncated_camt053_is_rejected(self):
        # The file stops in the middle of the second entry.
        cut = self.sample_bytes.index(b"<Nm>AS Elektrotikls")
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

    def test_every_truncation_point_is_rejected_by_the_parser(self):
        # Banks send both pretty-printed and single-line files.
        one_line = b"".join(line.strip() for line in self.sample_bytes.splitlines())
        parser = self.env["account.statement.import.camt.parser"]
        for layout, data in (("multi-line", self.sample_bytes), ("one-line", one_line)):
            end = data.index(b"</Document>")
            accepted = []
            for cut in range(1, end):
                try:
                    parser.parse(data[:cut])
                except (UserError, ValueError):
                    continue
                accepted.append(cut)
            self.assertFalse(
                accepted,
                f"{layout}: {len(accepted)} of {end - 1} truncated copies parsed, "
                f"first cuts {accepted[:5]}",
            )

    def test_dirty_but_complete_camt053_is_imported(self):
        # A bare ampersand is malformed XML that the recovering parser tolerates;
        # nothing is lost, so the file must still import in full.
        dirty = self.sample_bytes.replace(b"Rekins 2026/117", b"Rekins & Co 2026/117")
        self._import(dirty)
        lines = self._journal_lines()
        self.assertEqual(sorted(lines.mapped("amount")), [-120.00, -35.40, 250.50])

    def test_not_xml_is_rejected(self):
        with self.assertRaises(UserError):
            self._import(b"\x00\x01 this is not a bank statement \xff")
        self.assertFalse(self._journal_statements())
        self.assertFalse(self._journal_lines())
