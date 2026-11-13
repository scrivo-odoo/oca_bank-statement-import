# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl.html).
"""A CAMT document written without the ISO 20022 namespace.

``samples/financelatvia-camt052-no-namespace`` is the account report example
of the Finance Latvia ISO 20022 guideline: a ``BkToCstmrAcctRpt`` document
whose ``Document`` root carries no namespace.
"""

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase
from odoo.tools import BinaryBytes
from odoo.tools.misc import file_path

NO_NAMESPACE = (
    "account_statement_import_camt/tests/samples/financelatvia-camt052-no-namespace"
)


class TestNoNamespace(TransactionCase):
    def _import(self, data, filename):
        return (
            self.env["account.statement.import"]
            .create(
                {"statement_filename": filename, "statement_file": BinaryBytes(data)}
            )
            .import_file_button()
        )

    def test_document_without_namespace_says_so(self):
        """A well-formed CAMT document without the ISO 20022 namespace is
        refused with a message naming the missing namespace, not with the
        message for a file of an unknown format."""
        with open(file_path(NO_NAMESPACE), "rb") as sample:
            data = sample.read()
        with self.assertRaises(UserError) as caught:
            self._import(data, "financelatvia.xml")
        message = str(caught.exception)
        self.assertIn("namespace", message)
        self.assertNotIn("format is not supported", message)

    def test_other_xml_without_namespace_is_left_to_other_formats(self):
        """An XML file without a namespace that is no CAMT document is not
        claimed by the CAMT parser: it gets the message for an unknown format."""
        with self.assertRaises(UserError) as caught:
            self._import(b"<?xml version='1.0'?><Report><Row/></Report>", "other.xml")
        self.assertIn("format is not supported", str(caught.exception))
