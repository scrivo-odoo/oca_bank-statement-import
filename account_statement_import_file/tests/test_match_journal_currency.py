# License AGPL-3 - See http://www.gnu.org/licenses/agpl-3.0.html
"""Journal of a statement on an account held in several currencies.

One bank account (one IBAN) may carry a journal per currency; a statement
goes to the journal of its own currency.
"""

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase


class TestMatchJournalCurrency(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.eur = cls.env.ref("base.EUR")
        cls.usd = cls.env.ref("base.USD")
        (cls.eur | cls.usd).write({"active": True})
        company = cls.env.company
        cls.bank_account = cls.env["res.partner.bank"].create(
            {
                "account_number": "LV97HABA0012345678910",
                "partner_id": company.partner_id.id,
                "company_id": company.id,
            }
        )

        def journal(code, currency):
            return cls.env["account.journal"].create(
                {
                    "name": f"Bank {code}",
                    "code": code,
                    "type": "bank",
                    "bank_account_id": cls.bank_account.id,
                    "currency_id": currency.id,
                }
            )

        cls.journal_eur = journal("MCEUR", cls.eur)
        cls.journal_usd = journal("MCUSD", cls.usd)
        cls.wizard = cls.env["account.statement.import"].create(
            {"statement_filename": "multi-currency"}
        )

    def test_statement_goes_to_the_journal_of_its_currency(self):
        """Both journals hold the IBAN: a USD statement gets the USD journal
        and a EUR statement the EUR journal, whichever was created first."""
        self.assertEqual(
            self.wizard._match_journal("LV97HABA0012345678910", self.usd),
            self.journal_usd,
        )
        self.assertEqual(
            self.wizard._match_journal("LV97HABA0012345678910", self.eur),
            self.journal_eur,
        )

    def test_currency_without_a_journal_is_refused(self):
        """No journal of the IBAN is in GBP: the statement is refused with the
        currency message instead of going to a journal of another currency."""
        gbp = self.env.ref("base.GBP")
        gbp.write({"active": True})
        with self.assertRaises(UserError) as caught:
            self.wizard._match_journal("LV97HABA0012345678910", gbp)
        self.assertIn("GBP", str(caught.exception))
