import hashlib
import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone

import jwt as pyjwt
import requests

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)


class EnablebankingApplication(models.Model):
    _name = "enablebanking.application"
    _description = "Application for Enablebanking authentication"
    _order = "application_id"
    _rec_name = "application_id"

    online_bank_statement_provider_ids = fields.One2many(
        comodel_name="online.bank.statement.provider",
        inverse_name="enablebanking_application_id",
        string="Bank statement providers",
    )
    application_id = fields.Char(
        string="Application ID",
        help="Application ID from your EnableBanking.com portal",
        required=True,
    )
    bank_bic = fields.Char(
        string="Bank BIC",
        required=True,
        help="BIC of the bank, as the bank account of the journal carries it (Odoo 20 has no res.bank).",
    )
    redirect_url = fields.Char(
        string="Redirect URL", default=lambda self: self._default_redirect_url()
    )
    api_origin = fields.Char(
        string="API Origin", default="https://api.enablebanking.com", readonly=True
    )
    auth_code = fields.Char(string="Auth code", readonly=False, copy=False)
    session = fields.Char(string="Latest session", readonly=True)
    valid_until = fields.Datetime(
        string="Valid until",
        help="Authentication valid until",
        readonly=True,
        compute="_compute_session_data",
    )
    authenticated_bank_accounts = fields.Char(
        help="List of bank accounts that are authenticated",
        compute="_compute_session_data",
    )

    aspsp_name = fields.Char(string="ASPSP name")
    aspsp_country = fields.Many2one(string="ASPSP country", comodel_name="res.country")
    psu_type = fields.Selection(
        string="Account type",
        selection=[("business", "Business"), ("personal", "Personal")],
        default="personal",
    )
    enablebanking_state = fields.Char(
        help="SHA-256 of the state sent to the bank with the request of consent; it names the application on the way back"
    )
    user_id = fields.Many2one(
        comodel_name="res.users",
        string="Responsible",
        help="The person to be notified about expired authentication or other problems",
    )
    company_id = fields.Many2one(
        comodel_name="res.company",
        string="Company",
        required=True,
        default=lambda self: self.env.company,
    )

    @api.constrains("company_id")
    def _check_company_of_providers(self):
        """An application does not move to another company under a provider of the first: its session is the bank access of one firm."""
        for application in self:
            foreign = application.online_bank_statement_provider_ids.filtered(
                lambda provider, application=application: provider.company_id != application.company_id
            )
            if foreign:
                raise ValidationError(
                    self.env._(
                        "The application %(application)s is used by the journal(s) %(journals)s of another company: "
                        "change those providers first.",
                        application=application.application_id,
                        journals=", ".join(foreign.mapped("journal_id.display_name")),
                    )
                )

    @api.onchange("user_id")
    def onchange_user_id(self):
        """
        When responsible user is changed, add them to follower
        """
        for record in self:
            if record.user_id:
                for provider in record.online_bank_statement_provider_ids:
                    provider.message_subscribe(
                        partner_ids=[record.user_id.partner_id.id]
                    )

    def _default_redirect_url(self):
        url = self.env["ir.config_parameter"].sudo().get_str("web.base.url")
        url += "/enablebanking_auth"

        return url

    def _get_session_dict(self):
        """
        Get session as dict
        """
        self.ensure_one()
        if not self.session:
            return {}
        return json.loads(self.session)

    @staticmethod
    def _enablebanking_utc(text):
        """A date-time of the API as the naive UTC moment Odoo keeps.

        The API writes moments with an offset (Z, +02:00, -02:00); dropping the offset as it is changes the moment. A moment
        without an offset is read as UTC, as the API reference says of its dates.
        """
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return moment.astimezone(timezone.utc).replace(tzinfo=None)

    def _compute_session_data(self):
        for record in self:
            session = record._get_session_dict()

            vals = {
                "valid_until": False,
                "authenticated_bank_accounts": False,
            }

            if session:
                _logger.debug(
                    "Enable Banking %s: session with %s account(s)",
                    record.application_id,
                    len(session.get("accounts") or []),
                )

                # Get valid until from session data
                valid_until = session.get("access", {}).get("valid_until")
                if valid_until:
                    vals["valid_until"] = self._enablebanking_utc(valid_until)

                accounts = []
                for account in session.get("accounts", []):
                    iban = account.get("account_id", {}).get("iban")
                    account_name = account.get("name")
                    accounts.append(f"{iban} - {account_name}")

                vals["authenticated_bank_accounts"] = ", ".join(accounts)

            record.update(vals)

    # Enablebanking
    def action_enablebanking_authenticate(self):
        return self._enablebanking_authorize()

    def action_enablebanking_deauthorize(self):
        """
        Deauthorize the application by clearing the session.
        This will require re-authentication.
        """
        self.write({"session": {}, "enablebanking_state": False})

    def action_enablebanking_get_aspsp(self):
        """
        Fetch ASPSP information using bank BIC in bank account
        """
        self.ensure_one()

        if not self.bank_bic:
            raise ValidationError(self.env._("Please add the BIC of the bank"))

        bic = self.bank_bic

        jwt = self._enablebanking_get_jwt_token()
        base_headers = self._enablebanking_get_basic_headers(jwt)

        if not self.company_id.country_code:
            raise ValidationError(
                self.env._(
                    "Country code is missing! Please add a country for your company."
                )
            )

        body = {"psu_type": self.psu_type, "country": self.company_id.country_code}
        r = self._enablebanking_body(
            requests.get(
                f"{self.api_origin}/aspsps",
                params=body,
                headers=base_headers,
                timeout=10,
            ),
            "GET /aspsps",
        )

        aspsps = r.get("aspsps", [])
        # Banks of one group may share a BIC (the FAQ of Enable Banking says so): one match is a choice, none or several
        # are a refusal, and the bank chosen earlier is neither kept nor replaced behind the owner's back.
        bic = bic.strip().upper()
        candidates = [a for a in aspsps if (a.get("bic") or "").strip().upper() == bic]
        if not candidates:
            raise ValidationError(
                self.env._(
                    "Could not find ASPSP info for the BIC %(bic)s. "
                    "Please check account type and your bank BIC. \n"
                    "Found ASPSPs: %(aspsp_names)s",
                    bic=bic,
                    aspsp_names=", ".join(a.get("name") or "-" for a in aspsps),
                )
            )
        if len(candidates) > 1:
            raise ValidationError(
                self.env._(
                    "%(count)s banks of Enable Banking have the BIC %(bic)s: %(aspsp_names)s. "
                    "Set the ASPSP name and country of the application by hand.",
                    count=len(candidates),
                    bic=bic,
                    aspsp_names=", ".join(f"{a.get('name')} ({a.get('country')})" for a in candidates),
                )
            )
        self.aspsp_name = candidates[0].get("name")
        self.aspsp_country = self.env["res.country"].search(
            [("code", "=", candidates[0].get("country"))], limit=1
        )

    def _enablebanking_get_basic_headers(self, jwt):
        base_headers = {"Authorization": f"Bearer {jwt}"}

        # Requesting application details
        # This doesn't really do anything but fetch and print the details
        r = requests.get(
            f"{self.api_origin}/application",
            headers=base_headers,
            timeout=10,
        )
        if r.status_code != 200:
            raise ValidationError(self._enablebanking_error_text(r))
        app = self._enablebanking_body(r, "GET /application")

        if not app.get("active"):
            raise ValidationError(
                self.env._(
                    "This application is not yet activated. "
                    "Please do that before continuing"
                )
            )

        if self.redirect_url not in app.get("redirect_urls", []):
            raise ValidationError(
                self.env._(
                    "Redirect URL is not registered with your application. \n"
                    "Please add '%s' to allowed redirect URLs.",
                    self.redirect_url,
                )
            )

        return base_headers

    def _enablebanking_private_key(self):
        """The private key of the application: the file the stand names, never a column of the database.

        The key of the application is a secret of the stand. A column of the database goes into every copy of it (the copy taken
        before each deploy, the dump a head restores to try a change), so the key is read from the file whose path the
        environment of Odoo holds in ENABLEBANKING_KEY_FILE.
        """
        path = os.environ.get("ENABLEBANKING_KEY_FILE")
        if not path:
            raise UserError(
                self.env._(
                    "The private key of the Enable Banking application is a file of the stand: "
                    "set ENABLEBANKING_KEY_FILE in the environment of Odoo to its path."
                )
            )
        try:
            with open(path, "rb") as key_file:
                return key_file.read()
        except OSError as error:
            raise UserError(
                self.env._(
                    "Cannot read the private key of the Enable Banking application from %(path)s (ENABLEBANKING_KEY_FILE): %(error)s",
                    path=path,
                    error=error.strerror or error,
                )
            ) from error

    def _enablebanking_get_jwt_token(self):
        self.ensure_one()
        iat = int(datetime.now().timestamp())

        # Generate JWT token
        jwt_body = {
            "iss": "enablebanking.com",
            "aud": "api.enablebanking.com",
            "iat": iat,
            "exp": iat + 3600,
        }
        try:
            jwt = pyjwt.encode(
                jwt_body,
                self._enablebanking_private_key(),
                algorithm="RS256",
                headers={"kid": self.application_id},
            )
        except (ValueError, pyjwt.PyJWTError) as error:
            raise UserError(
                self.env._(
                    "The file named by ENABLEBANKING_KEY_FILE is not an RSA private key in PEM form: %(error)s",
                    error=error,
                )
            ) from error

        if not isinstance(jwt, str):
            jwt = jwt.decode("utf-8")

        return jwt

    def _enablebanking_max_validity(self, headers):
        """The longest consent the bank allows, less an hour so that a slow clock cannot make the request exceed it.

        valid_until cannot exceed now + maximum_consent_validity of the bank in GET /aspsps (180 days for most banks): the longer
        the consent, the more rarely the owner has to go to the bank to renew it.
        """
        self.ensure_one()
        r = requests.get(
            f"{self.api_origin}/aspsps",
            params={"country": self.aspsp_country.code, "psu_type": self.psu_type},
            headers=headers,
            timeout=10,
        )
        if r.status_code != 200:
            raise ValidationError(self._enablebanking_error_text(r))
        named = [aspsp for aspsp in r.json().get("aspsps", []) if aspsp.get("name") == self.aspsp_name]
        if len(named) > 1:
            raise UserError(
                self.env._(
                    "The bank %(bank)s is listed %(count)s times for %(country)s: the term of the consent cannot be told. "
                    "Check the ASPSP name and country of the application.",
                    bank=self.aspsp_name,
                    count=len(named),
                    country=self.aspsp_country.code,
                )
            )
        if named and named[0].get("maximum_consent_validity"):
            return timedelta(seconds=named[0]["maximum_consent_validity"]) - timedelta(hours=1)
        raise UserError(
            self.env._(
                "The bank %(bank)s is not in the list of banks of Enable Banking for %(country)s (or gives no maximum consent validity): "
                "get the ASPSP info again.",
                bank=self.aspsp_name,
                country=self.aspsp_country.code,
            )
        )

    def _enablebanking_authorize(self):
        """
        Enable Banking: authorization
        """

        jwt = self._enablebanking_get_jwt_token()
        base_headers = self._enablebanking_get_basic_headers(jwt)
        enablebanking_state = str(uuid.uuid4())

        # Starting authorization, for as long as the bank allows
        validity = self._enablebanking_max_validity(base_headers)

        body = {
            "access": {
                "valid_until": (datetime.now(timezone.utc) + validity).isoformat()
            },
            "aspsp": {"name": self.aspsp_name, "country": self.aspsp_country.code},
            "state": enablebanking_state,
            "redirect_url": self.redirect_url,
            "psu_type": self.psu_type,
        }
        r = requests.post(
            f"{self.api_origin}/auth",
            json=body,
            headers=base_headers,
            timeout=10,
        )
        if r.status_code != 200:
            raise ValidationError(self._enablebanking_error_text(r))
        self._enablebanking_remember_state(enablebanking_state)
        return {
            "type": "ir.actions.act_url",
            "url": r.json()["url"],
            "target": "self",
        }

    @staticmethod
    def _enablebanking_state_hash(state):
        return hashlib.sha256(state.encode()).hexdigest()

    def _enablebanking_remember_state(self, state):
        """Keep only the hash of the state sent to the bank: a reader of the application cannot forge the way back."""
        self.ensure_one()
        self.sudo().enablebanking_state = self._enablebanking_state_hash(state)

    @api.model
    def _enablebanking_by_state(self, state):
        """The application the bank returns to, found as the current user so that the rule of the companies holds."""
        if not state:
            return self.browse()
        return self.search([("enablebanking_state", "=", self._enablebanking_state_hash(state))])

    def _enablebanking_open_session(self, auth_code, headers, send=None):
        """Exchange the code the bank returned for a session (POST /sessions) and return the session.

        `send` is the function that posts the request, `requests.post` when not given.
        """
        self.ensure_one()
        send = send or requests.post
        r = send(f"{self.api_origin}/sessions", json={"code": auth_code}, headers=headers, timeout=10)
        if r.status_code != 200:
            _logger.info("Enable Banking %s: the bank refused the code of the consent, status %s", self.application_id, r.status_code)
            raise ValidationError(self._enablebanking_error_text(r))
        session = self._enablebanking_body(r, "POST /sessions")
        _logger.info(
            "Enable Banking %s: a new session with %s account(s) has been created",
            self.application_id,
            len(session.get("accounts") or []),
        )
        return session

    @api.model
    def _enablebanking_body(self, answer, request_name):
        """The parsed body of an answer of the API; the log says that it came and its status, never what it holds."""
        _logger.debug("Enable Banking %s: status %s", request_name, answer.status_code)
        return answer.json()

    @api.model
    def _enablebanking_error_text(self, answer):
        """The text of an error for an answer of the API: its status and the message and error code the bank gives.

        The rest of the body stays out: the text goes to the screen, the chatter of the provider and the log.
        """
        try:
            body = answer.json()
        except ValueError:
            body = None
        said = []
        if isinstance(body, dict):
            said = [str(body[key]) for key in ("message", "error") if body.get(key)]
        if said:
            return self.env._(
                "Error response %(status_code)s: %(message)s",
                status_code=answer.status_code,
                message=" / ".join(said),
            )
        return self.env._("Error response %(status_code)s", status_code=answer.status_code)

    def _enablebanking_get_account_ids(self):
        """
        Enablebanking: get available accounts
        """
        _logger.warning("Fetching accounts is not implemented")
        pass
