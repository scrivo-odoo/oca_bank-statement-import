# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Only an administrator asks the bank for a consent: on the form of the application, on the journal and by a direct call."""
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from .test_classes_of_review import FirmsCase


@tagged("post_install", "-at_install")
class TestOnlyAnAdministratorAsksForAConsent(FirmsCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # The journal is opened by an administrator who also keeps the books; the administrator of FirmsCase does not see it.
        cls.accounting_admin = cls._user(
            "test-eb-accounting-admin",
            cls.own_company,
            cls.env.ref("base.group_user") | cls.env.ref("base.group_system") | cls.env.ref("account.group_account_user"),
        )

    def refusal_of(self, call):
        with self.assertRaises(UserError) as caught:
            call()
        return caught.exception

    def test_an_accountant_is_refused_on_the_method_of_the_application(self):
        error = self.refusal_of(self.application.with_user(self.accountant).action_enablebanking_authenticate)
        self.assertIsInstance(error, AccessError)
        self.assertFalse(self.application.enablebanking_state)

    def test_an_accountant_is_refused_on_the_method_of_the_journal(self):
        error = self.refusal_of(self.journal.with_user(self.accountant).action_enablebanking_authenticate)
        self.assertIsInstance(error, AccessError)
        self.assertFalse(self.application.enablebanking_state)

    def test_an_administrator_goes_on_to_the_request_to_the_bank_on_the_method_of_the_application(self):
        error = self.refusal_of(self.application.with_user(self.admin).action_enablebanking_authenticate)
        self.assertNotIsInstance(error, AccessError)
        self.assertIn("ENABLEBANKING_KEY_FILE", str(error))

    def test_an_administrator_goes_on_to_the_request_to_the_bank_on_the_method_of_the_journal(self):
        error = self.refusal_of(self.journal.with_user(self.accounting_admin).action_enablebanking_authenticate)
        self.assertNotIsInstance(error, AccessError)
        self.assertIn("ENABLEBANKING_KEY_FILE", str(error))
