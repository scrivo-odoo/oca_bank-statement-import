import json

import werkzeug

from odoo import http
from odoo.exceptions import ValidationError
from odoo.http import request


class EnableBankingController(http.Controller):
    @http.route("/enablebanking_auth", type="http", auth="user")
    def enablebanking_auth(self, *args, **kwargs):
        auth_code = kwargs.get("code")
        enablebanking_state = kwargs.get("state")
        # The application is found as the user who comes back from the bank, so the rule of the companies holds: a user
        # sees only the applications of the own company. No state names no application, and one state names exactly one.
        enablebanking = request.env["enablebanking.application"]._enablebanking_by_state(enablebanking_state)

        if len(enablebanking) != 1:
            raise ValidationError(
                request.env._(
                    "Could not find EnableBanking application for state: %s",
                    enablebanking_state,
                )
            )

        jwt = enablebanking._enablebanking_get_jwt_token()
        session = enablebanking._enablebanking_open_session(auth_code, {"Authorization": f"Bearer {jwt}"})

        # An accountant reads the application but does not write it; storing the session is this callback's job.
        enablebanking.sudo().session = json.dumps(session)

        action = request.env.ref("account.action_account_journal_form")
        base_url = request.env["ir.config_parameter"].sudo().get_str("web.base.url")

        # Redirect back to journals view
        redirect_url = f"{base_url}/odoo/action-{action.id}"

        return werkzeug.utils.redirect(redirect_url, 303)
