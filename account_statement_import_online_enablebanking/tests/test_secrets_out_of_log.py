# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""What the bank gives in confidence stays out of the log and out of the text of an error.

Each case feeds an answer of the API that carries a known secret (the code of the consent, the session, the cursor of the pages,
a whole line of the bank, the body of a refusal) and reads every record the module writes: the secret is in none of them, and the
fact of the event is. The answer is an argument of the method that reads it; nothing of the module or of `requests` is replaced.
"""
import json
from types import SimpleNamespace

from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from .test_classes_of_review import FirmsCase
from .test_enablebanking import EnableBankingCase, account, line

LOGGER = "odoo.addons.account_statement_import_online_enablebanking"
AUTH_CODE = "SECRET-AUTH-CODE-7f3a9c"
SESSION_ID = "SECRET-SESSION-ID-51d0e2"
SECRET_IBAN = "LV99SECRETIBAN0000001"
CURSOR = "SECRET-CURSOR-0b44e1"
DETAIL = "SECRET-DETAIL-of-the-bank"


def answer(status_code, body):
    """An answer of the API as `requests` gives it: a status, the raw text and the parsed body."""
    return SimpleNamespace(status_code=status_code, text=json.dumps(body), json=lambda: body)


def unparsed_answer(status_code, text):
    def unreadable():
        raise ValueError("not JSON")

    return SimpleNamespace(status_code=status_code, text=text, json=unreadable)


def refusal(status_code=401):
    return answer(status_code, {"message": "Session expired", "error": "EXPIRED_SESSION", "detail": DETAIL, "code": AUTH_CODE})


@tagged("post_install", "-at_install")
class TestSecretsOutOfLog(EnableBankingCase):
    def logged(self, logs):
        return "\n".join(logs.output)

    def assertNoSecret(self, text):
        for secret in (AUTH_CODE, SESSION_ID, SECRET_IBAN, CURSOR, DETAIL):
            self.assertNotIn(secret, text)

    # ------------------------------------------------------------------------------------------------- the stored session
    def test_reading_a_session_does_not_log_the_session(self):
        self.application.session = json.dumps(
            {"session_id": SESSION_ID, "access": {"valid_until": "2099-01-01T00:00:00Z"}, "accounts": [account("uid-1", iban=SECRET_IBAN)]}
        )
        self.application.invalidate_recordset()
        with self.assertLogs(LOGGER, level="DEBUG") as logs:
            self.assertTrue(self.application.valid_until)
        self.assertNoSecret(self.logged(logs))

    def test_reading_a_session_logs_how_many_accounts_it_has(self):
        self.application.session = json.dumps(
            {"session_id": SESSION_ID, "access": {"valid_until": "2099-01-01T00:00:00Z"}, "accounts": [account("uid-1"), account("uid-2")]}
        )
        self.application.invalidate_recordset()
        with self.assertLogs(LOGGER, level="DEBUG") as logs:
            self.application.valid_until  # noqa: B018
        self.assertIn("2 account(s)", self.logged(logs))

    # ------------------------------------------------------------------------------------------------- a line of the bank
    def test_mapping_a_line_logs_its_reference_and_nothing_the_line_shows(self):
        transaction = line(
            "REF-MAP-1",
            "12.00",
            creditor={"name": "Secret Creditor Ltd"},
            creditor_account={"iban": SECRET_IBAN},
            remittance_information=["SECRET-REMITTANCE-text"],
        )
        with self.assertLogs(LOGGER, level="DEBUG") as logs:
            self.provider._get_transaction_vals(transaction, 1)
        text = self.logged(logs)
        self.assertIn("REF-MAP-1", text)
        self.assertNoSecret(text)
        self.assertNotIn("Secret Creditor Ltd", text)
        self.assertNotIn("SECRET-REMITTANCE-text", text)

    def test_a_name_of_two_partners_is_logged_by_the_reference_of_the_line_not_by_the_name(self):
        self.env["res.partner"].create([{"name": "Twin Person Name"}, {"name": "Twin Person Name"}])
        transaction = line("REF-TWIN-1", "5.00", creditor={"name": "Twin Person Name"})
        with self.assertLogs(LOGGER, level="INFO") as logs:
            vals = self.provider._get_transaction_vals(transaction, 1)
        text = self.logged(logs)
        self.assertNotIn("partner_id", vals)
        self.assertIn("REF-TWIN-1", text)
        self.assertIn("more than one partner", text)
        self.assertNotIn("Twin Person Name", text)

    # ------------------------------------------------------------------------------------------------- the text of an error
    def test_the_text_of_an_error_names_the_status_and_the_message_of_the_bank_only(self):
        text = self.application._enablebanking_error_text(refusal(403))
        self.assertIn("403", text)
        self.assertIn("Session expired", text)
        self.assertIn("EXPIRED_SESSION", text)
        self.assertNoSecret(text)

    def test_the_text_of_an_error_with_a_body_that_is_not_json_names_the_status_only(self):
        text = self.application._enablebanking_error_text(unparsed_answer(502, f"<html>{DETAIL}</html>"))
        self.assertIn("502", text)
        self.assertNoSecret(text)

    def test_a_body_of_the_api_is_logged_by_its_status_not_by_its_content(self):
        with self.assertLogs(LOGGER, level="DEBUG") as logs:
            body = self.application._enablebanking_body(answer(200, {"active": True, "detail": DETAIL}), "GET /application")
        text = self.logged(logs)
        self.assertEqual(body, {"active": True, "detail": DETAIL})
        self.assertIn("GET /application", text)
        self.assertIn("200", text)
        self.assertNoSecret(text)

    # ------------------------------------------------------------------------------------------------- the code of the consent
    def send_answering(self, given):
        sent = []

        def send(url, json=None, headers=None, timeout=None):
            sent.append(json)
            return given

        return send, sent

    def test_opening_a_session_sends_the_code_to_the_bank_and_logs_only_that_a_session_came(self):
        send, sent = self.send_answering(answer(200, {"session_id": SESSION_ID, "accounts": [account("uid-1", iban=SECRET_IBAN)]}))
        with self.assertLogs(LOGGER, level="DEBUG") as logs:
            session = self.application._enablebanking_open_session(AUTH_CODE, {"Authorization": "Bearer test"}, send=send)
        text = self.logged(logs)
        self.assertEqual(sent, [{"code": AUTH_CODE}])
        self.assertEqual(session["session_id"], SESSION_ID)
        self.assertIn("session", text.lower())
        self.assertNoSecret(text)

    def test_a_refused_code_is_an_error_that_carries_neither_the_code_nor_the_body(self):
        send, sent = self.send_answering(refusal(400))
        with self.assertLogs(LOGGER, level="DEBUG") as logs, self.assertRaises(ValidationError) as caught:
            self.application._enablebanking_open_session(AUTH_CODE, {"Authorization": "Bearer test"}, send=send)
        message = str(caught.exception)
        self.assertIn("400", message)
        self.assertIn("Session expired", message)
        self.assertNoSecret(message)
        self.assertNoSecret(self.logged(logs))

    # ------------------------------------------------------------------------------------------------- a page of transactions
    def test_a_page_with_more_to_come_logs_that_more_comes_not_the_cursor(self):
        with self.assertLogs(LOGGER, level="DEBUG") as logs:
            page = self.provider._enablebanking_transactions_page(
                answer(200, {"transactions": [line("P-1", "1.00")], "continuation_key": CURSOR})
            )
        text = self.logged(logs)
        self.assertEqual(page["continuation_key"], CURSOR)
        self.assertIn("1 transaction", text)
        self.assertIn("next page", text)
        self.assertNoSecret(text)

    def test_a_refused_page_logs_the_status_and_the_message_not_the_body(self):
        with self.assertLogs(LOGGER, level="INFO") as logs, self.assertRaises(UserError):
            self.provider._enablebanking_transactions_page(refusal(401))
        text = self.logged(logs)
        self.assertIn("401", text)
        self.assertIn("Session expired", text)
        self.assertNoSecret(text)

    def test_a_page_that_fails_otherwise_is_an_error_without_the_body(self):
        with self.assertRaises(ValidationError) as caught:
            self.provider._enablebanking_transactions_page(refusal(500))
        message = str(caught.exception)
        self.assertIn("500", message)
        self.assertNoSecret(message)


@tagged("post_install", "-at_install")
class TestTheStateOfTheConsent(FirmsCase):
    """The state sent to the bank names the application on the way back; whoever reads the application does not learn it."""

    def test_the_state_sent_to_the_bank_finds_its_application_for_a_user_of_its_firm(self):
        self.application._enablebanking_remember_state("state-sent-to-the-bank")
        found = self.env["enablebanking.application"].with_user(self.accountant)._enablebanking_by_state("state-sent-to-the-bank")
        self.assertEqual(found, self.application)

    def test_a_reader_of_the_application_does_not_read_the_state_sent_to_the_bank(self):
        self.application._enablebanking_remember_state("state-sent-to-the-bank")
        stored = self.application.with_user(self.accountant).read(["enablebanking_state"])[0]["enablebanking_state"]
        self.assertTrue(stored)
        self.assertNotIn("state-sent-to-the-bank", stored)

    def test_the_state_of_one_firm_finds_nothing_for_a_user_of_another(self):
        self.application._enablebanking_remember_state("state-sent-to-the-bank")
        found = self.env["enablebanking.application"].with_user(self.foreign_accountant)._enablebanking_by_state(
            "state-sent-to-the-bank"
        )
        self.assertFalse(found)

    def test_no_state_finds_no_application(self):
        self.application._enablebanking_remember_state("state-sent-to-the-bank")
        self.assertFalse(self.env["enablebanking.application"].with_user(self.accountant)._enablebanking_by_state(False))
