# Copyright 2026 Konstantins Deruks
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Five classes of defect of the provider, each held by what a person sees, not by the shape of the code.

F1 two different operations of the bank are never one line; F2 a line is found by the window that takes it; F3 the dates
with a time of the API are read with their zone; F4 a firm sees only the applications of its own; F5 an empty or an
ambiguous choice is a refusal in words, never a silent pick. The API is `FakeApi` of test_enablebanking: booking date
decides what a window returns, as the Mock ASPSP does.
"""
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import HttpCase, tagged

from .test_enablebanking import OWN_IBAN, EnableBankingCase, account, line, session

OCTOBER = (datetime(2026, 10, 1), datetime(2026, 10, 31))


# ---------------------------------------------------------------------------------------------------------------- F1
# Every case is a pair of lines that differ in one thing the line shows, and a way to read that thing back from the book.
# The first pull brings A, the second B and A again (the bank may answer in another order or with more lines).
DIFFERENT_OPERATIONS = [
    ("payment_reference", {"reference_number": "INVOICE-A"}, {"reference_number": "INVOICE-B"}, lambda l: l.ref),
    (
        "remittance_text",
        {"remittance_information": ["Invoice 1"]},
        {"remittance_information": ["Invoice 2"]},
        lambda l: l.narration,
    ),
    ("creditor_name", {"creditor": {"name": "Party Alpha"}}, {"creditor": {"name": "Party Beta"}}, lambda l: l.partner_name),
    (
        "creditor_iban",
        {"creditor": {"name": "Party"}, "creditor_account": {"iban": "LV00TEST0000000000001"}},
        {"creditor": {"name": "Party"}, "creditor_account": {"iban": "LV00TEST0000000000002"}},
        lambda l: l.account_number,
    ),
    (
        "debtor_name",
        {"direction": "CRDT", "debtor": {"name": "Payer Alpha"}},
        {"direction": "CRDT", "debtor": {"name": "Payer Beta"}},
        lambda l: l.partner_name,
    ),
    (
        "debtor_iban",
        {"direction": "CRDT", "debtor": {"name": "Payer"}, "debtor_account": {"iban": "LT00TEST000000000001"}},
        {"direction": "CRDT", "debtor": {"name": "Payer"}, "debtor_account": {"iban": "LT00TEST000000000002"}},
        lambda l: l.account_number,
    ),
    ("booking_date", {"booking": "2026-10-10"}, {"booking": "2026-10-11"}, lambda l: str(l.date)),
    (
        "value_date",
        {"booking": "2026-10-10", "value": "2026-10-10"},
        {"booking": "2026-10-10", "value": "2026-10-12"},
        lambda l: str(l.date),
    ),
    ("amount", {"amount": "10.00"}, {"amount": "10.01"}, lambda l: round(abs(l.amount), 2)),
]


def _two_operations_case(reference, spec_a, spec_b, read):
    def test(self):
        a = line(reference, **{"amount": "10.00", **spec_a})
        b = line(reference, **{"amount": "10.00", **spec_b})
        self.pull({"uid-eur": [a]}, *OCTOBER)
        self.pull({"uid-eur": [b, a]}, *OCTOBER)
        lines = self.lines()
        self.assertEqual(len(lines), 2, "two different operations of the bank must be two lines of the book")
        self.assertEqual(len({read(l) for l in lines}), 2, "the two lines must keep what tells them apart")

    return test


@tagged("post_install", "-at_install")
class TestDifferentOperationsAreDifferentLines(EnableBankingCase):
    def test_the_same_operation_in_every_field_is_one_line_however_often_it_comes(self):
        a = line(None, "10.00", reference_number="R-1", creditor={"name": "Party"}, creditor_account={"iban": "LV00TEST0000000000001"})
        for _ in range(3):
            self.pull({"uid-eur": [a]}, *OCTOBER)
        self.assertEqual(len(self.lines()), 1)


for _name, _spec_a, _spec_b, _read in DIFFERENT_OPERATIONS:
    for _reference in (None, "SAME-REFERENCE"):
        setattr(
            TestDifferentOperationsAreDifferentLines,
            f"test_two_lines_differing_only_in_{_name}_with_reference_{'none' if _reference is None else 'repeated'}_stay_two",
            _two_operations_case(_reference, _spec_a, _spec_b, _read),
        )


# ---------------------------------------------------------------------------------------------------------------- F2
@tagged("post_install", "-at_install")
class TestAWindowFindsTheLineThatItTakes(EnableBankingCase):
    MONTHS = [
        (datetime(2026, 10, 1), datetime(2026, 10, 31)),
        (datetime(2026, 11, 1), datetime(2026, 11, 30)),
        (datetime(2026, 12, 1), datetime(2026, 12, 31)),
    ]

    def pull_months(self, transactions):
        for since, until in self.MONTHS:
            self.pull({"uid-eur": transactions}, since, until)

    def test_a_line_whose_value_date_is_up_to_a_week_after_its_booking_is_in_the_book_once_whatever_the_day(self):
        """Booking on each of the last days of October, value 0 to 7 days later: the month borders must lose none."""
        transactions = []
        for day in range(24, 32):
            for lag in range(0, 8):
                booking = date(2026, 10, day)
                transactions.append(
                    line(
                        f"E-{day}-{lag}",
                        f"{day}.{lag:02d}",
                        booking=booking.isoformat(),
                        value=(booking + timedelta(days=lag)).isoformat(),
                    )
                )
        self.pull_months(transactions)
        self.assertEqual(len(self.lines()), len(transactions))
        self.assertEqual(len({l.unique_import_id for l in self.lines()}), len(transactions))

    def test_a_line_valued_far_after_its_booking_is_not_lost_it_is_dated_a_week_after_the_booking(self):
        """The value date is taken up to a week after the booking, no further: a window can only reach back so far."""
        self.pull_months([line("FAR", "5.00", booking="2026-10-10", value="2026-11-20")])
        self.assertEqual([str(l.date) for l in self.lines()], ["2026-10-17"])

    def test_a_value_date_before_the_booking_gives_the_booking_date(self):
        self.pull_months([line("EARLY", "5.00", booking="2026-10-12", value="2026-10-09")])
        self.assertEqual([str(l.date) for l in self.lines()], ["2026-10-12"])

    def test_the_booking_date_is_taken_when_the_bank_gives_no_value_date(self):
        no_value = line("NOVAL", "5.00", booking="2026-10-12")
        del no_value["value_date"]
        self.pull_months([no_value])
        self.assertEqual([str(l.date) for l in self.lines()], ["2026-10-12"])

    def test_a_line_without_any_date_is_an_error_that_names_it_not_a_crash_or_a_silent_drop(self):
        undated = line("NODATE", "5.00")
        del undated["value_date"]
        del undated["booking_date"]
        with self.assertRaises(UserError) as caught:
            self.pull({"uid-eur": [undated]}, *OCTOBER)
        self.assertIn("NODATE", str(caught.exception))


# ---------------------------------------------------------------------------------------------------------------- F3
@tagged("post_install", "-at_install")
class TestDatesOfTheApiHaveTheirZone(EnableBankingCase):
    OFFSETS = ["Z", "+00:00", "-02:00", "-12:00", "+05:30", "+14:00"]

    def consent_until(self, instant, offset):
        """The moment as the API writes it: the same instant, in the given offset."""
        zone = timezone.utc if offset in ("Z", "+00:00") else timezone(_offset(offset))
        text = instant.astimezone(zone).isoformat()
        return text.replace("+00:00", "Z") if offset == "Z" else text

    def errors_for(self, instant, offset):
        self.set_session([account("uid-eur")], valid_until=self.consent_until(instant, offset))
        self.env.invalidate_all()
        return self.provider._enablebanking_errors()

    def test_a_consent_that_lasts_another_half_hour_is_valid_in_any_offset(self):
        instant = datetime.now(timezone.utc) + timedelta(minutes=30)
        for offset in self.OFFSETS:
            with self.subTest(offset=offset):
                self.assertEqual(self.errors_for(instant, offset), "")

    def test_a_consent_that_ended_half_an_hour_ago_is_expired_in_any_offset(self):
        instant = datetime.now(timezone.utc) - timedelta(minutes=30)
        for offset in self.OFFSETS:
            with self.subTest(offset=offset):
                self.assertIn("expired", self.errors_for(instant, offset))

    def test_valid_until_is_the_utc_moment_of_the_api_whatever_the_offset(self):
        instant = datetime(2099, 1, 1, 12, 0, tzinfo=timezone.utc)
        for offset in self.OFFSETS:
            with self.subTest(offset=offset):
                self.set_session([account("uid-eur")], valid_until=self.consent_until(instant, offset))
                self.env.invalidate_all()
                self.assertEqual(self.application.valid_until, datetime(2099, 1, 1, 12, 0))

    def test_a_moment_without_a_zone_is_read_as_utc(self):
        self.set_session([account("uid-eur")], valid_until="2099-01-01T12:00:00")
        self.env.invalidate_all()
        self.assertEqual(self.application.valid_until, datetime(2099, 1, 1, 12, 0))


def _offset(text):
    sign = -1 if text[0] == "-" else 1
    hours, minutes = text[1:].split(":")
    return sign * timedelta(hours=int(hours), minutes=int(minutes))


# ---------------------------------------------------------------------------------------------------------------- F4
class FirmsCase(EnableBankingCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.own_company = cls.env.company
        cls.foreign_company = cls.env["res.company"].create({"name": "Foreign company of the test"})
        cls.foreign = cls.env["enablebanking.application"].create(
            {
                "application_id": "foreign-test-app",
                "bank_bic": "TESTLV22",
                "company_id": cls.foreign_company.id,
                "session": session([account("foreign-uid", iban="LV00FOREIGN000000001")]),
                "enablebanking_state": "foreign-state",
            }
        )
        group_user = cls.env.ref("base.group_user")
        cls.accountant = cls._user("test-eb-accountant", cls.own_company, group_user | cls.env.ref("account.group_account_user"))
        cls.admin = cls._user("test-eb-admin", cls.own_company, group_user | cls.env.ref("base.group_system"))
        cls.foreign_accountant = cls._user(
            "test-eb-foreign-accountant", cls.foreign_company, group_user | cls.env.ref("account.group_account_user")
        )

    @classmethod
    def _user(cls, login, company, groups):
        return (
            cls.env["res.users"]
            .with_context(no_reset_password=True)
            .create(
                {
                    "name": login,
                    "login": login,
                    "password": f"{login}-Test-Password-1",
                    "company_id": company.id,
                    "company_ids": [(6, 0, company.ids)],
                    "group_ids": [(6, 0, groups.ids)],
                }
            )
        )


@tagged("post_install", "-at_install")
class TestAFirmSeesOnlyItsOwnApplications(FirmsCase):
    def test_an_accountant_finds_the_application_of_the_own_firm_and_not_of_the_other(self):
        found = self.env["enablebanking.application"].with_user(self.accountant).search([])
        self.assertIn(self.application, found)
        self.assertNotIn(self.foreign, found)

    def test_an_administrator_of_one_firm_does_not_find_the_application_of_another(self):
        found = self.env["enablebanking.application"].with_user(self.admin).search([])
        self.assertIn(self.application, found)
        self.assertNotIn(self.foreign, found)

    def test_the_session_of_another_firm_cannot_be_read_by_id(self):
        with self.assertRaises(AccessError):
            self.foreign.with_user(self.accountant).read(["session"])

    def test_the_session_of_the_own_firm_can_be_read_and_gives_its_accounts(self):
        own = self.application.with_user(self.accountant)
        self.assertIn("uid-eur", own.session)
        self.assertIn(OWN_IBAN, own.authenticated_bank_accounts)

    def test_an_accountant_cannot_change_an_application_of_the_own_firm(self):
        with self.assertRaises(AccessError):
            self.application.with_user(self.accountant).write({"aspsp_name": "Other"})

    def test_a_provider_cannot_use_the_application_of_another_firm(self):
        with self.assertRaises(ValidationError):
            self.provider.enablebanking_application_id = self.foreign

    def test_an_application_cannot_move_to_another_firm_while_a_provider_of_the_first_uses_it(self):
        with self.assertRaises(ValidationError):
            self.application.company_id = self.foreign_company


@tagged("post_install", "-at_install")
class TestTheCallbackOfTheBank(FirmsCase, HttpCase):
    """The page the bank sends the owner back to: /enablebanking_auth?state=...&code=..."""

    SESSION_BODY = {"session_id": "new-session", "access": {"valid_until": "2099-01-01T00:00:00Z"}, "accounts": []}

    def call(self, login, query):
        self.authenticate(login, f"{login}-Test-Password-1")
        posted = []

        def post(url, json=None, headers=None, timeout=None):
            posted.append(url)
            return SimpleNamespace(status_code=200, text="ok", json=lambda: dict(self.SESSION_BODY))

        application = self.registry["enablebanking.application"]
        with mock.patch("requests.post", side_effect=post), mock.patch.object(
            application, "_enablebanking_get_jwt_token", return_value="test-jwt"
        ):
            response = self.url_open(f"/enablebanking_auth?{query}", allow_redirects=False)
        return response, posted

    def session_of(self, application):
        application.invalidate_recordset()
        return application.sudo().session

    def test_the_owner_of_the_firm_that_asked_for_the_consent_stores_the_session(self):
        self.application.enablebanking_state = "own-state"
        self.env.flush_all()
        response, posted = self.call("test-eb-accountant", "state=own-state&code=abc")
        self.assertEqual(response.status_code, 303)
        self.assertEqual(len(posted), 1)
        self.assertIn("new-session", self.session_of(self.application))

    def test_a_user_of_another_firm_cannot_store_a_session_with_the_state_of_this_firm(self):
        before = self.session_of(self.foreign)
        self.env.flush_all()
        response, posted = self.call("test-eb-accountant", "state=foreign-state&code=abc")
        self.assertNotEqual(response.status_code, 303)
        self.assertEqual(posted, [])
        self.assertEqual(self.session_of(self.foreign), before)

    def test_a_call_without_a_state_picks_no_application(self):
        self.assertFalse(self.application.enablebanking_state)
        before = self.session_of(self.application)
        self.env.flush_all()
        response, posted = self.call("test-eb-accountant", "code=abc")
        self.assertNotEqual(response.status_code, 303)
        self.assertEqual(posted, [])
        self.assertEqual(self.session_of(self.application), before)


# ---------------------------------------------------------------------------------------------------------------- F5
@tagged("post_install", "-at_install")
class TestAnEmptyOrAmbiguousChoiceIsARefusal(EnableBankingCase):
    def get_aspsps(self, banks):
        self.env.company.country_id = self.env.ref("base.lv")
        cls = self.registry["enablebanking.application"]
        with mock.patch.object(cls, "_enablebanking_get_jwt_token", return_value="test-jwt"), mock.patch.object(
            cls, "_enablebanking_get_basic_headers", return_value={}
        ), mock.patch("requests.get", return_value=SimpleNamespace(status_code=200, json=lambda: {"aspsps": banks})):
            self.application.action_enablebanking_get_aspsp()

    def test_one_bank_with_the_bic_is_chosen_with_its_country(self):
        self.get_aspsps([{"name": "Bank Alpha", "bic": "BANKLV22", "country": "LV"}, {"name": "Bank Gamma", "bic": "OTHERLV22", "country": "LV"}])
        self.assertEqual((self.application.aspsp_name, self.application.aspsp_country.code), ("Bank Alpha", "LV"))

    def test_the_bic_is_compared_without_case_and_spaces(self):
        self.application.bank_bic = " banklv22 "
        self.get_aspsps([{"name": "Bank Alpha", "bic": "BANKLV22", "country": "LV"}])
        self.assertEqual(self.application.aspsp_name, "Bank Alpha")

    def test_no_bank_with_the_bic_is_an_error_that_names_the_bic_and_keeps_no_pick(self):
        self.application.aspsp_name = "Earlier Bank"
        self.application.bank_bic = "OTHERLV22"
        with self.assertRaises(UserError) as caught:
            self.get_aspsps([{"name": "Bank Alpha", "bic": "BANKLV22", "country": "LV"}])
        self.assertIn("OTHERLV22", str(caught.exception))

    def test_two_banks_with_one_bic_are_an_error_that_names_both_not_a_pick_of_the_last(self):
        with self.assertRaises(UserError) as caught:
            self.get_aspsps(
                [
                    {"name": "Bank Alpha", "bic": "BANKLV22", "country": "LV"},
                    {"name": "Bank Beta", "bic": "BANKLV22", "country": "LV"},
                ]
            )
        self.assertIn("Bank Alpha", str(caught.exception))
        self.assertIn("Bank Beta", str(caught.exception))

    def test_a_list_without_banks_is_an_error(self):
        with self.assertRaises(UserError):
            self.get_aspsps([])

    def authorize_with(self, aspsps):
        def get(url, params=None, headers=None, timeout=None):
            body = {"active": True, "redirect_urls": [self.application.redirect_url]} if url.endswith("/application") else {"aspsps": aspsps}
            return SimpleNamespace(status_code=200, text="ok", json=lambda: body)

        self.application.aspsp_country = self.env.ref("base.lv")
        cls = self.registry["enablebanking.application"]
        with mock.patch("requests.get", side_effect=get), mock.patch(
            "requests.post", return_value=SimpleNamespace(status_code=200, text="ok", json=lambda: {"url": "https://bank.test/consent"})
        ) as post, mock.patch.object(cls, "_enablebanking_get_jwt_token", return_value="test-jwt"):
            try:
                self.application.action_enablebanking_authenticate()
            finally:
                self.posted = post.call_count

    def test_two_entries_of_the_bank_with_one_name_and_country_are_an_error_and_no_consent_is_asked(self):
        with self.assertRaises(UserError):
            self.authorize_with(
                [
                    {"name": "Test ASPSP", "country": "LV", "maximum_consent_validity": 15552000},
                    {"name": "Test ASPSP", "country": "LV", "maximum_consent_validity": 86400},
                ]
            )
        self.assertEqual(self.posted, 0)

    def test_one_partner_with_the_name_of_the_counterparty_is_taken(self):
        partner = self.env["res.partner"].create({"name": "Unique Counterparty Test"})
        self.pull({"uid-eur": [line("P-1", "5.00", creditor={"name": "Unique Counterparty Test"})]}, *OCTOBER)
        self.assertEqual(self.lines().partner_id, partner)

    def test_two_partners_with_the_name_of_the_counterparty_are_taken_by_none(self):
        self.env["res.partner"].create([{"name": "Twin Counterparty Test"}, {"name": "Twin Counterparty Test"}])
        self.pull({"uid-eur": [line("P-2", "5.00", creditor={"name": "Twin Counterparty Test"})]}, *OCTOBER)
        self.assertEqual(len(self.lines()), 1)
        self.assertFalse(self.lines().partner_id)
