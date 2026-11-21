# Copyright 2026 Konstantins Deruks
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""The Enable Banking provider against recorded and made-up answers of the API.

`requests.get` is replaced by FakeApi, which serves the way Enable Banking does: newest first, in pages of ten with a
`continuation_key`, `date_from` and `date_to` inclusive on the booking date, 401 when asked to. What the tests expect is written
here from the input lines (each fixture is listed in its docstring or in the expected amounts), not read back from the code.

Fixtures: `mock_sandbox_eur_150.json` is the recorded answer of the Mock ASPSP of the sandbox (150 lines, 126 different
`entry_reference`); `seb_eur_october.json` and `revolut_eur_october.json` are made-up lines in the shape the production API
returned for the two banks in October 2026 (the fields a line carries, the side that carries the counterparty, a zero line, a credit).
"""
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

FIXTURES = Path(__file__).parent / "fixtures"
OWN_IBAN = "LV80BANK0000435195001"
APP_ID = "test-application-id"


def fixture(name):
    return json.loads((FIXTURES / name).read_text())


def line(entry_reference, amount, direction="DBIT", booking="2026-10-10", value=None, **extra):
    """One transaction as the API returns it; only what a test changes is named."""
    transaction = {
        "entry_reference": entry_reference,
        "transaction_amount": {"currency": "EUR", "amount": amount},
        "credit_debit_indicator": direction,
        "status": "BOOK",
        "booking_date": booking,
        "value_date": value or booking,
        "remittance_information": ["Payment"],
    }
    transaction.update(extra)
    return transaction


def session(accounts, valid_until="2099-01-01T00:00:00.000000Z"):
    """The answer of POST /sessions, as the application keeps it."""
    return json.dumps({"access": {"valid_until": valid_until}, "accounts": accounts, "session_id": "test-session"})


def account(uid, iban=OWN_IBAN, currency="EUR"):
    return {"uid": uid, "account_id": {"iban": iban}, "currency": currency}


class FakeApi:
    """requests.get of the API: transactions per account uid, paging and filters as the Mock ASPSP does them."""

    def __init__(self, transactions_by_uid, status=200):
        self.transactions_by_uid = transactions_by_uid
        self.status = status
        self.calls = []

    def __call__(self, url, params=None, headers=None, timeout=None):
        params = dict(params or {})
        self.calls.append({"url": url, "params": params, "headers": headers})
        if self.status != 200:
            return SimpleNamespace(status_code=self.status, text="denied", json=lambda: {"message": "denied"})
        uid = url.split("/accounts/")[1].split("/")[0]
        rows = sorted(self.transactions_by_uid.get(uid, []), key=lambda t: t["booking_date"], reverse=True)
        low, high = params.get("date_from", ""), params.get("date_to", "9999-12-31")
        rows = [t for t in rows if low <= t["booking_date"] <= high]
        start = int(params.get("continuation_key", 0))
        body = {"transactions": rows[start : start + 10]}
        if start + 10 < len(rows):
            body["continuation_key"] = str(start + 10)
        return SimpleNamespace(status_code=200, text="ok", json=lambda: body)


class EnableBankingCase(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.tz = "UTC"
        eur = cls.env.ref("base.EUR")
        eur.active = True
        cls.env.company.currency_id = eur
        suspense = cls.env["account.account"].create(
            {"name": "Bank Suspense Account", "code": "1014020", "account_type": "asset_current"}
        )
        partner_bank = cls.env["res.partner.bank"].create(
            {
                "account_number": OWN_IBAN,
                "partner_id": cls.env.company.partner_id.id,
                "bank_name": "Test Bank",
                "bank_bic": "BANKLV22",
            }
        )
        cls.journal = cls.env["account.journal"].create(
            {
                "name": "SEB",
                "code": "SEB",
                "type": "bank",
                "bank_account_id": partner_bank.id,
                "bank_statements_source": "online",
                "suspense_account_id": suspense.id,
            }
        )
        cls.application = cls.env["enablebanking.application"].create(
            {"application_id": APP_ID, "bank_bic": "BANKLV22", "psu_type": "business", "aspsp_name": "Test ASPSP"}
        )
        cls.set_session([account("uid-eur")])
        cls.provider = cls.env["online.bank.statement.provider"].create(
            {
                "service": "enablebanking",
                "journal_id": cls.journal.id,
                "statement_creation_mode": "monthly",
                "enablebanking_application_id": cls.application.id,
            }
        )

    @classmethod
    def set_session(cls, accounts, valid_until="2099-01-01T00:00:00.000000Z"):
        cls.application.session = session(accounts, valid_until)

    def pull(self, transactions_by_uid, since, until, status=200):
        """Pull the window through the whole path the scheduler takes: provider, statements, lines. Returns the fake API."""
        api = FakeApi(transactions_by_uid, status)
        application = self.registry["enablebanking.application"]
        with mock.patch("requests.get", side_effect=api), mock.patch.object(
            application, "_enablebanking_get_jwt_token", return_value="test-jwt"
        ):
            self.provider._pull(since, until)
        return api

    def lines(self):
        return self.env["account.bank.statement.line"].search([("journal_id", "=", self.journal.id)])

    def amounts(self):
        return sorted(round(a, 2) for a in self.lines().mapped("amount"))


@tagged("post_install", "-at_install")
class TestLinesOfTheBanks(EnableBankingCase):
    OCTOBER = (datetime(2026, 10, 1), datetime(2026, 10, 31))

    def test_seb_october_lands_with_the_amounts_of_the_input(self):
        """Seven debits; the amounts below are the seven of seb_eur_october.json, written out."""
        self.pull({"uid-eur": fixture("seb_eur_october.json")}, *self.OCTOBER)
        self.assertEqual(self.amounts(), [-2250.01, -120.0, -64.0, -33.1, -18.99, -12.0, -7.3])
        self.assertEqual(round(sum(self.lines().mapped("amount")), 2), -2505.40)

    def test_revolut_october_has_twelve_lines_without_the_zero_one(self):
        """Thirteen lines come, one of them 0.00: it moves nothing in the books and is not in the file of the bank."""
        self.pull({"uid-eur": fixture("revolut_eur_october.json")}, *self.OCTOBER)
        self.assertEqual(
            self.amounts(),
            [-310.0, -150.0, -90.0, -75.5, -64.8, -47.2, -23.45, -12.0, -9.99, -8.4, -5.0, 1500.0],
        )
        self.assertEqual(round(sum(self.lines().mapped("amount")), 2), 703.66)

    def test_a_zero_line_is_reported_in_the_log_not_silently_dropped(self):
        with self.assertLogs(level="INFO") as logs:
            self.pull({"uid-eur": fixture("revolut_eur_october.json")}, *self.OCTOBER)
        text = "\n".join(logs.output)
        self.assertIn("REV-0013", text)
        self.assertIn("zero", text.lower())

    def test_a_response_of_only_zero_lines_creates_nothing_and_does_not_fail(self):
        self.pull({"uid-eur": [line("Z-1", "0.00"), line("Z-2", "0")]}, *self.OCTOBER)
        self.assertEqual(len(self.lines()), 0)

    def test_the_date_is_the_value_date_unless_it_is_before_the_booking(self):
        """Revolut gives the day the money moved in value_date: it equals the date of the bank's own file on 12 of 12 lines of October."""
        self.pull(
            {
                "uid-eur": [
                    line("D-1", "10.00", booking="2026-10-10", value="2026-10-11"),
                    line("D-2", "20.00", booking="2026-10-12", value="2026-10-09"),
                ]
            },
            *self.OCTOBER,
        )
        dates = {round(l.amount, 2): str(l.date) for l in self.lines()}
        self.assertEqual(dates, {-10.0: "2026-10-11", -20.0: "2026-10-12"})

    def test_a_credit_takes_the_debtor_as_the_counterparty_and_a_debit_the_creditor(self):
        self.pull(
            {
                "uid-eur": [
                    line(
                        "C-1", "1500.00", "CRDT", debtor={"name": "Client Test Ltd"},
                        debtor_account={"iban": "LT00TEST000000000001"}, creditor={}, creditor_account={"iban": OWN_IBAN},
                    ),
                    line(
                        "C-2", "33.10", "DBIT", creditor={"name": "Fuel Station Test"},
                        creditor_account={"iban": "LV00TEST0000000000001"}, debtor={"name": "Test Firm"},
                        debtor_account={"iban": OWN_IBAN},
                    ),
                ]
            },
            *self.OCTOBER,
        )
        by_amount = {round(l.amount, 2): l for l in self.lines()}
        self.assertEqual(
            (by_amount[1500.0].partner_name, by_amount[1500.0].account_number),
            ("Client Test Ltd", "LT00TEST000000000001"),
        )
        self.assertEqual(
            (by_amount[-33.1].partner_name, by_amount[-33.1].account_number),
            ("Fuel Station Test", "LV00TEST0000000000001"),
        )

    def test_a_pending_line_is_not_taken_and_a_booked_one_beside_it_is(self):
        """A pending line has no stable reference and may change: it comes when the bank has booked it."""
        self.pull({"uid-eur": [line("P-1", "4.00", status="PDNG"), line("P-2", "5.00")]}, *self.OCTOBER)
        self.assertEqual(self.amounts(), [-5.0])

    def test_a_line_without_remittance_information_loads(self):
        no_text = line("N-1", "5.00")
        del no_text["remittance_information"]
        self.pull({"uid-eur": [no_text, line("N-2", "6.00")]}, *self.OCTOBER)
        self.assertEqual(self.amounts(), [-6.0, -5.0])


@tagged("post_install", "-at_install")
class TestOneLineOnce(EnableBankingCase):
    WINDOW = (datetime(2021, 6, 1), datetime(2021, 9, 30))

    def test_repeated_entry_reference_keeps_every_line(self):
        """The sandbox answer has 150 lines and 126 different entry_reference: the bank repeats it for lines of other days and amounts."""
        transactions = fixture("mock_sandbox_eur_150.json")
        self.assertEqual(len(transactions), 150)
        self.assertEqual(len({t["entry_reference"] for t in transactions}), 126)
        self.pull({"uid-eur": transactions}, *self.WINDOW)
        self.assertEqual(len(self.lines()), 150)

    def test_a_second_pull_of_the_same_days_adds_nothing(self):
        transactions = fixture("mock_sandbox_eur_150.json")
        self.pull({"uid-eur": transactions}, *self.WINDOW)
        self.pull({"uid-eur": transactions}, *self.WINDOW)
        self.assertEqual(len(self.lines()), 150)

    def test_a_narrower_window_after_a_wider_one_adds_nothing(self):
        transactions = fixture("mock_sandbox_eur_150.json")
        self.pull({"uid-eur": transactions}, *self.WINDOW)
        self.pull({"uid-eur": transactions}, datetime(2021, 7, 1), datetime(2021, 7, 31))
        self.assertEqual(len(self.lines()), 150)

    def test_two_identical_lines_with_one_reference_are_two_lines_and_stay_two(self):
        twins = [line("T-1", "9.99", booking="2026-10-10"), line("T-1", "9.99", booking="2026-10-10")]
        window = (datetime(2026, 10, 1), datetime(2026, 10, 31))
        self.pull({"uid-eur": twins}, *window)
        self.pull({"uid-eur": twins}, *window)
        self.assertEqual(self.amounts(), [-9.99, -9.99])

    def test_a_new_line_next_to_known_ones_is_the_only_one_added(self):
        window = (datetime(2026, 10, 1), datetime(2026, 10, 31))
        first = [line("K-1", "1.00"), line("K-2", "2.00")]
        self.pull({"uid-eur": first}, *window)
        self.pull({"uid-eur": first + [line("K-3", "3.00")]}, *window)
        self.assertEqual(self.amounts(), [-3.0, -2.0, -1.0])


@tagged("post_install", "-at_install")
class TestWhichAccount(EnableBankingCase):
    WINDOW = (datetime(2026, 10, 1), datetime(2026, 10, 31))

    def test_two_accounts_under_one_iban_the_one_of_the_journal_currency_is_read(self):
        """SEB and Revolut give one IBAN for several currency accounts; the journal is in euros, so only the euro account is read."""
        self.set_session([account("uid-eur", currency="EUR"), account("uid-nok", currency="NOK")])
        api = self.pull(
            {"uid-eur": [line("E-1", "11.00")], "uid-nok": [line("N-1", "999.00")]},
            *self.WINDOW,
        )
        self.assertEqual({call["url"].split("/accounts/")[1].split("/")[0] for call in api.calls}, {"uid-eur"})
        self.assertEqual(self.amounts(), [-11.0])

    def test_the_order_of_the_accounts_in_the_session_does_not_matter(self):
        self.set_session([account("uid-nok", currency="NOK"), account("uid-eur", currency="EUR")])
        api = self.pull({"uid-eur": [line("E-1", "11.00")], "uid-nok": [line("N-1", "999.00")]}, *self.WINDOW)
        self.assertEqual({call["url"].split("/accounts/")[1].split("/")[0] for call in api.calls}, {"uid-eur"})

    def test_an_iban_that_the_session_does_not_hold_is_an_error_naming_it(self):
        self.set_session([account("uid-other", iban="LT121000011101001000")])
        with self.assertRaises(UserError) as caught:
            self.pull({"uid-other": [line("O-1", "1.00")]}, *self.WINDOW)
        self.assertIn(OWN_IBAN, str(caught.exception))
        self.assertEqual(len(self.lines()), 0)

    def test_two_accounts_that_fit_the_journal_equally_are_an_error_not_a_guess(self):
        self.set_session([account("uid-a"), account("uid-b")])
        with self.assertRaises(UserError):
            self.pull({"uid-a": [line("A-1", "1.00")], "uid-b": [line("B-1", "2.00")]}, *self.WINDOW)
        self.assertEqual(len(self.lines()), 0)


@tagged("post_install", "-at_install")
class TestWhenTheBankSaysNo(EnableBankingCase):
    WINDOW = (datetime(2026, 10, 1), datetime(2026, 10, 31))

    def test_a_refusal_of_the_bank_is_an_error_not_an_empty_pull(self):
        """A pull that ends in silence moves the date of the last good run forward, and the days it skipped never come back."""
        with self.assertRaises(UserError):
            self.pull({"uid-eur": [line("R-1", "1.00")]}, *self.WINDOW, status=401)
        self.assertEqual(len(self.lines()), 0)

    def test_an_expired_consent_stops_before_the_bank_is_asked(self):
        """Many banks allow four requests a day while the client is away: a request that cannot succeed is not made."""
        self.set_session([account("uid-eur")], valid_until="2026-01-01T00:00:00.000000Z")
        api = FakeApi({"uid-eur": [line("X-1", "1.00")]})
        with mock.patch("requests.get", side_effect=api), mock.patch.object(
            self.registry["enablebanking.application"], "_enablebanking_get_jwt_token", return_value="test-jwt"
        ):
            with self.assertRaises(UserError):
                self.provider._obtain_statement_data(*self.WINDOW)
        self.assertEqual(api.calls, [])

    def test_a_valid_consent_is_asked_once_per_page(self):
        api = FakeApi({"uid-eur": fixture("mock_sandbox_eur_150.json")})
        with mock.patch("requests.get", side_effect=api), mock.patch.object(
            self.registry["enablebanking.application"], "_enablebanking_get_jwt_token", return_value="test-jwt"
        ):
            lines, _values = self.provider._obtain_statement_data(datetime(2021, 6, 1), datetime(2021, 10, 1))
        self.assertEqual(len(lines), 150)
        self.assertEqual(len(api.calls), 15)
        self.assertNotIn("continuation_key", api.calls[0]["params"])
        self.assertEqual([c["params"]["continuation_key"] for c in api.calls[1:3]], ["10", "20"])


@tagged("post_install", "-at_install")
class TestTheKeyOfTheApplication(EnableBankingCase):
    @classmethod
    def private_key(cls):
        return rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def write_key(self, key):
        handle = tempfile.NamedTemporaryFile("wb", suffix=".pem", delete=False)
        self.addCleanup(os.unlink, handle.name)
        handle.write(
            key.private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
            )
        )
        handle.close()
        return handle.name

    def test_the_token_is_signed_with_the_key_of_the_file_and_names_the_application(self):
        key = self.private_key()
        path = self.write_key(key)
        with mock.patch.dict(os.environ, {"ENABLEBANKING_KEY_FILE": path}):
            token = self.application._enablebanking_get_jwt_token()
        self.assertEqual(jwt.get_unverified_header(token)["kid"], APP_ID)
        claims = jwt.decode(token, key.public_key(), algorithms=["RS256"], audience="api.enablebanking.com")
        self.assertEqual(claims["iss"], "enablebanking.com")

    def test_a_token_signed_with_another_key_does_not_verify_with_this_one(self):
        path = self.write_key(self.private_key())
        with mock.patch.dict(os.environ, {"ENABLEBANKING_KEY_FILE": path}):
            token = self.application._enablebanking_get_jwt_token()
        with self.assertRaises(jwt.InvalidSignatureError):
            jwt.decode(token, self.private_key().public_key(), algorithms=["RS256"], audience="api.enablebanking.com")

    def test_no_file_named_is_an_error_that_names_the_setting(self):
        with mock.patch.dict(os.environ, clear=False) as env:
            env.pop("ENABLEBANKING_KEY_FILE", None)
            with self.assertRaises(UserError) as caught:
                self.application._enablebanking_get_jwt_token()
        self.assertIn("ENABLEBANKING_KEY_FILE", str(caught.exception))

    def test_a_file_that_is_not_a_key_is_an_error(self):
        with tempfile.NamedTemporaryFile("w", suffix=".pem") as handle:
            handle.write("not a key")
            handle.flush()
            with mock.patch.dict(os.environ, {"ENABLEBANKING_KEY_FILE": handle.name}):
                with self.assertRaises(UserError):
                    self.application._enablebanking_get_jwt_token()

    def test_a_missing_file_is_an_error(self):
        with mock.patch.dict(os.environ, {"ENABLEBANKING_KEY_FILE": "/nonexistent/enablebanking.pem"}):
            with self.assertRaises(UserError):
                self.application._enablebanking_get_jwt_token()

    def test_the_database_holds_no_key_and_no_token(self):
        """The key is a file of the stand; a column of the database goes into every copy of it."""
        fields = self.env["enablebanking.application"]._fields
        self.assertNotIn("key", fields)
        self.assertNotIn("key_name", fields)
        self.assertNotIn("jwt", fields)


@tagged("post_install", "-at_install")
class TestTheTermOfTheConsent(EnableBankingCase):
    """The consent is asked for as long as the bank allows: maximum_consent_validity of GET /aspsps, 180 days for most banks (faq.md of Enable Banking)."""

    def authorize(self, aspsps):
        posted = {}

        def get(url, params=None, headers=None, timeout=None):
            if url.endswith("/application"):
                body = {"active": True, "redirect_urls": [self.application.redirect_url]}
            elif url.endswith("/aspsps"):
                body = {"aspsps": aspsps}
            else:
                raise AssertionError(url)
            return SimpleNamespace(status_code=200, text="ok", json=lambda: body)

        def post(url, json=None, headers=None, timeout=None):
            posted.update(json)
            return SimpleNamespace(status_code=200, text="ok", json=lambda: {"url": "https://bank.test/consent"})

        self.application.aspsp_country = self.env.ref("base.lv")
        application = self.registry["enablebanking.application"]
        with mock.patch("requests.get", side_effect=get), mock.patch("requests.post", side_effect=post), mock.patch.object(
            application, "_enablebanking_get_jwt_token", return_value="test-jwt"
        ):
            self.application.action_enablebanking_authenticate()
        return posted

    def asked_days(self, posted):
        until = datetime.fromisoformat(posted["access"]["valid_until"])
        return (until - datetime.now(timezone.utc)).total_seconds() / 86400

    def test_a_bank_that_allows_a_hundred_and_eighty_days_is_asked_for_them(self):
        posted = self.authorize(
            [
                {"name": "Test ASPSP", "country": "LV", "maximum_consent_validity": 15552000},
                {"name": "Other Bank", "country": "LV", "maximum_consent_validity": 86400},
            ]
        )
        self.assertTrue(179 < self.asked_days(posted) <= 180)

    def test_a_bank_that_allows_ninety_days_is_asked_for_ninety(self):
        posted = self.authorize([{"name": "Test ASPSP", "country": "LV", "maximum_consent_validity": 7776000}])
        self.assertTrue(89 < self.asked_days(posted) <= 90)

    def test_a_bank_the_list_does_not_hold_is_an_error_and_no_consent_is_asked(self):
        with self.assertRaises(UserError):
            self.authorize([{"name": "Other Bank", "country": "LV", "maximum_consent_validity": 15552000}])
