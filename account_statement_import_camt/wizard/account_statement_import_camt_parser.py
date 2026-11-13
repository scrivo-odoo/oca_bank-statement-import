"""Class to parse camt files."""

# Copyright 2013-2016 Therp BV <https://therp.nl>
# Copyright 2017 Open Net Sàrl
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl.html).
import re

from lxml import etree

from odoo import models
from odoo.exceptions import UserError

RE_CAMT = re.compile(r"(^urn:iso:std:iso:20022:tech:xsd:camt." r"|^ISO:camt.)")


class AccountStatementImportCamtParser(models.AbstractModel):
    _name = "account.statement.import.camt.parser"
    _description = "Account Bank Statement Import CAMT parser"

    def parse_amount(self, ns, node):
        """Parse element that contains Amount and CreditDebitIndicator."""
        if node is None:
            return 0.0
        sign = 1
        amount = 0.0
        sign_node = node.xpath("ns:CdtDbtInd", namespaces={"ns": ns})
        if not sign_node:
            sign_node = node.xpath("../../ns:CdtDbtInd", namespaces={"ns": ns})
        if sign_node and sign_node[0].text == "DBIT":
            sign = -1
        amount_node = node.xpath("ns:Amt", namespaces={"ns": ns})
        if not amount_node:
            amount_node = node.xpath(
                "./ns:AmtDtls/ns:TxAmt/ns:Amt", namespaces={"ns": ns}
            )
        if amount_node:
            amount = sign * float(amount_node[0].text)
        return amount

    def add_value_from_node(self, ns, node, xpath_str, obj, attr_name, join_str=None):
        """Add value to object from first or all nodes found with xpath.

        If xpath_str is a list (or iterable), it will be seen as a series
        of search path's in order of preference. The first item that results
        in a found node will be used to set a value."""
        if not isinstance(xpath_str, list | tuple):
            xpath_str = [xpath_str]
        for search_str in xpath_str:
            found_node = node.xpath(search_str, namespaces={"ns": ns})
            if found_node:
                if isinstance(found_node[0], str):
                    attr_value = found_node[0]
                elif join_str is None:
                    attr_value = found_node[0].text
                else:
                    attr_value = join_str.join([x.text for x in found_node])
                obj[attr_name] = attr_value
                break

    def parse_transaction_details(self, ns, node, transaction):
        """Parse TxDtls node."""
        # message
        self.add_value_from_node(
            ns,
            node,
            [
                "./ns:RmtInf/ns:Ustrd|./ns:RtrInf/ns:AddtlInf",
                "./ns:AddtlNtryInf",
                "./ns:Refs/ns:InstrId",
            ],
            transaction,
            "payment_ref",
            join_str="\n",
        )

        self.add_value_from_node(
            ns,
            node,
            ["./ns:RmtInf/ns:Ustrd"],
            transaction["narration"],
            f"{self.env._('Unstructured Reference')} (RmtInf/Ustrd)",
            join_str=" ",
        )
        self.add_value_from_node(
            ns,
            node,
            ["./ns:RmtInf/ns:Strd/ns:CdtrRefInf/ns:Ref"],
            transaction["narration"],
            f"{self.env._('Structured Reference')} (RmtInf/Strd/CdtrRefInf/Ref)",
            join_str=" ",
        )
        self.add_value_from_node(
            ns,
            node,
            ["./ns:AddtlTxInf"],
            transaction["narration"],
            f"{self.env._('Additional Transaction Information')} (AddtlTxInf)",
            join_str=" ",
        )
        self.add_value_from_node(
            ns,
            node,
            ["./ns:RtrInf/ns:Rsn/ns:Cd"],
            transaction["narration"],
            f"{self.env._('Return Reason Code')} (RtrInf/Rsn/Cd)",
        )
        self.add_value_from_node(
            ns,
            node,
            ["./ns:RtrInf/ns:Rsn/ns:Cd"],
            transaction["narration"],
            f"{self.env._('Return Reason Code (Proprietary)')} (RtrInf/Rsn/Prtry)",
        )
        self.add_value_from_node(
            ns,
            node,
            ["./ns:RtrInf/ns:AddtlInf"],
            transaction["narration"],
            f"{self.env._('Return Reason Additional Information')} (RtrInf/AddtlInf)",
            join_str=" ",
        )
        self.add_value_from_node(
            ns,
            node,
            ["./ns:Refs/ns:MsgId"],
            transaction["narration"],
            f"{self.env._('Msg Id')} (Refs/MsgId)",
        )
        self.add_value_from_node(
            ns,
            node,
            ["./ns:Refs/ns:AcctSvcrRef"],
            transaction["narration"],
            f"{self.env._('Account Servicer Reference')} (Refs/AcctSvcrRef)",
        )
        self.add_value_from_node(
            ns,
            node,
            ["./ns:Refs/ns:EndToEndId"],
            transaction["narration"],
            f"{self.env._('End To End Id')} (Refs/EndToEndId)",
        )
        self.add_value_from_node(
            ns,
            node,
            ["./ns:Refs/ns:InstrId"],
            transaction["narration"],
            f"{self.env._('Instructed Id')} (Refs/InstrId)",
        )
        self.add_value_from_node(
            ns,
            node,
            ["./ns:Refs/ns:TxId"],
            transaction["narration"],
            f"{self.env._('Transaction Identification')} (Refs/TxId)",
        )
        self.add_value_from_node(
            ns,
            node,
            ["./ns:Refs/ns:MntId"],
            transaction["narration"],
            f"{self.env._('Mandate Id')} (Refs/MntId)",
        )
        self.add_value_from_node(
            ns,
            node,
            ["./ns:Refs/ns:ChqNb"],
            transaction["narration"],
            f"{self.env._('Cheque Number')} (Refs/ChqNb)",
        )

        self.add_value_from_node(
            ns, node, ["./ns:AddtlTxInf"], transaction, "payment_ref", join_str="\n"
        )
        # eref
        self.add_value_from_node(
            ns,
            node,
            [
                "./ns:RmtInf/ns:Strd/ns:CdtrRefInf/ns:Ref",
                "./ns:Refs/ns:EndToEndId",
                "./ns:Ntry/ns:AcctSvcrRef",
            ],
            transaction,
            "ref",
        )
        # remote party values
        party_type = "Dbtr"
        party_type_node = node.xpath("../../ns:CdtDbtInd", namespaces={"ns": ns})
        if party_type_node and party_type_node[0].text != "CRDT":
            party_type = "Cdtr"
        party_node = node.xpath(
            f"./ns:RltdPties/ns:{party_type}", namespaces={"ns": ns}
        )
        if party_node:
            name_node = node.xpath(
                f"./ns:RltdPties/ns:{party_type}/ns:Nm |"
                f"./ns:RltdPties/ns:{party_type}/ns:Pty/ns:Nm",
                namespaces={"ns": ns},
            )
            if name_node:
                transaction["partner_name"] = name_node[0].text
            else:
                self.add_value_from_node(
                    ns,
                    party_node[0],
                    "./ns:PstlAdr/ns:AdrLine",
                    transaction,
                    "partner_name",
                )
            self.add_value_from_node(
                ns,
                party_node[0],
                "./ns:PstlAdr/ns:StrtNm|"
                "./ns:PstlAdr/ns:BldgNb|"
                "./ns:PstlAdr/ns:BldgNm|"
                "./ns:PstlAdr/ns:PstBx|"
                "./ns:PstlAdr/ns:PstCd|"
                "./ns:PstlAdr/ns:TwnNm|"
                "./ns:PstlAdr/ns:TwnLctnNm|"
                "./ns:PstlAdr/ns:DstrctNm|"
                "./ns:PstlAdr/ns:CtrySubDvsn|"
                "./ns:PstlAdr/ns:Ctry|"
                "./ns:PstlAdr/ns:AdrLine",
                transaction["narration"],
                f"{self.env._('Postal Address')} (PstlAdr)",
                join_str=" | ",
            )
        # Get remote_account from iban or from domestic account:
        account_node = node.xpath(
            f"./ns:RltdPties/ns:{party_type}Acct/ns:Id", namespaces={"ns": ns}
        )
        if account_node:
            iban_node = account_node[0].xpath("./ns:IBAN", namespaces={"ns": ns})
            if iban_node:
                transaction["account_number"] = iban_node[0].text
            else:
                self.add_value_from_node(
                    ns,
                    account_node[0],
                    "./ns:Othr/ns:Id",
                    transaction,
                    "account_number",
                )

    def generate_narration(self, transaction):
        # this block ensure compatibility with v13
        transaction["narration"] = {
            "{} (RltdPties/Nm)".format(self.env._("Partner Name")): transaction.get(
                "partner_name", ""
            ),
            "{} (RltdPties/Acct)".format(
                self.env._("Partner Account Number")
            ): transaction.get("account_number", ""),
            "{} (BookgDt)".format(self.env._("Transaction Date")): transaction.get(
                "date", ""
            ),
            self.env._("Reference"): transaction.get("ref", ""),
            self.env._("Communication"): transaction.get("name", ""),
            "{} (BkTxCd)".format(self.env._("Transaction Type")): transaction.get(
                "transaction_type", ""
            ),
            **transaction["narration"],
        }

        transaction["narration"] = "\n".join(
            [f"{key}: {val}" for key, val in transaction["narration"].items()]
        )

    def parse_entry(self, ns, node):
        """Parse an Ntry node and yield transactions"""
        transaction = {
            "payment_ref": "/",
            "amount": 0.0,
            "narration": {},
            "transaction_type": {},
        }  # fallback defaults
        self.add_value_from_node(
            ns, node, "./ns:BookgDt/ns:Dt | ./ns:BookgDt/ns:DtTm", transaction, "date"
        )
        amount = self.parse_amount(ns, node)
        if amount != 0.0:
            transaction["amount"] = amount
        self.add_value_from_node(
            ns,
            node,
            [
                "./ns:NtryDtls/ns:RmtInf/ns:Strd/ns:CdtrRefInf/ns:Ref",
                "./ns:NtryDtls/ns:Btch/ns:PmtInfId",
                "./ns:NtryDtls/ns:TxDtls/ns:Refs/ns:AcctSvcrRef",
            ],
            transaction,
            "ref",
        )

        # enrich the notes with some more infos when they are available
        self.add_value_from_node(
            ns,
            node,
            "./ns:AddtlNtryInf",
            transaction["narration"],
            f"{self.env._('Additional Entry Information')} (AddtlNtryInf)",
        )
        self.add_value_from_node(
            ns,
            node,
            "./ns:RvslInd",
            transaction["narration"],
            f"{self.env._('Reversal Indicator')} (RvslInd)",
        )

        self.add_value_from_node(
            ns,
            node,
            "./ns:BkTxCd/ns:Domn/ns:Cd",
            transaction["transaction_type"],
            "Code",
        )
        self.add_value_from_node(
            ns,
            node,
            "./ns:BkTxCd/ns:Domn/ns:Fmly/ns:Cd",
            transaction["transaction_type"],
            "FmlyCd",
        )
        self.add_value_from_node(
            ns,
            node,
            "./ns:BkTxCd/ns:Domn/ns:Fmly/ns:SubFmlyCd",
            transaction["transaction_type"],
            "SubFmlyCd",
        )
        transaction["transaction_type"] = (
            "-".join(transaction["transaction_type"].values()) or ""
        )

        details_nodes = node.xpath("./ns:NtryDtls/ns:TxDtls", namespaces={"ns": ns})
        if len(details_nodes) == 0:
            self.generate_narration(transaction)
            yield transaction
            return
        # The booked amount (Ntry/Amt) is what moved the account. A batch entry
        # is split into one line per TxDtls only when the transaction amounts
        # add up to it; a single TxDtls, or a batch that does not add up, keeps
        # the booked amount on one line, so the lines match the balances.
        details_amounts = [self.parse_amount(ns, d) for d in details_nodes]
        if len(details_nodes) > 1 and (
            round(sum(details_amounts), 2) != round(transaction["amount"], 2)
            or 0.0 in details_amounts
        ):
            self.generate_narration(transaction)
            yield transaction
            return
        transaction_base = transaction
        for details_node, details_amount in zip(
            details_nodes, details_amounts, strict=True
        ):
            transaction = transaction_base.copy()
            self.parse_transaction_details(ns, details_node, transaction)
            if len(details_nodes) > 1 or (
                transaction["amount"] == 0.0 and details_amount != 0.0
            ):
                transaction["amount"] = details_amount
            self.generate_narration(transaction)
            yield transaction

    def get_balance_amounts(self, ns, node):
        """Return opening and closing balance.

        Depending on kind of balance and statement, the balance might be in a
        different kind of node:
        OPBD = OpeningBalance
        PRCD = PreviousClosingBalance
        ITBD = InterimBalance (first ITBD is start-, second is end-balance)
        CLBD = ClosingBalance
        """
        start_balance_node = None
        end_balance_node = None
        for node_name in ["OPBD", "PRCD", "CLBD", "ITBD"]:
            code_expr = (
                f'./ns:Bal/ns:Tp/ns:CdOrPrtry/ns:Cd[text()="{node_name}"]/../../..'
            )
            balance_node = node.xpath(code_expr, namespaces={"ns": ns})
            if balance_node:
                if node_name in ["OPBD", "PRCD"]:
                    start_balance_node = balance_node[0]
                elif node_name == "CLBD":
                    end_balance_node = balance_node[0]
                else:
                    if not start_balance_node:
                        start_balance_node = balance_node[0]
                    if not end_balance_node:
                        end_balance_node = balance_node[-1]
        return (
            self.parse_amount(ns, start_balance_node),
            self.parse_amount(ns, end_balance_node),
        )

    def _get_entry_import_id(self, ns, entry_node, statement_name, entry_index, used_ids):
        """Return a key that identifies an entry across imports of the same file.

        The bank's own reference of the entry (AcctSvcrRef, with NtryRef when
        given) identifies the operation on the account, so a re-sent file maps
        to the same keys while another operation with the same date and amount
        does not. Without a usable reference, or when it repeats within the
        statement, the key falls back to the statement id, booking date and the
        entry's position. The journal and account prefix is added by
        ``account.journal._statement_line_import_update_unique_import_id``.
        """
        bank_ref = "".join(
            entry_node.xpath("./ns:AcctSvcrRef/text()", namespaces={"ns": ns})
        ).strip()
        entry_ref = "".join(
            entry_node.xpath("./ns:NtryRef/text()", namespaces={"ns": ns})
        ).strip()
        if bank_ref.strip("0") and bank_ref.upper() != "NOTPROVIDED":
            entry_id = f"{bank_ref}-{entry_ref}" if entry_ref else bank_ref
            if entry_id not in used_ids:
                used_ids.add(entry_id)
                return entry_id
        booking_date = entry_node.xpath(
            "./ns:BookgDt/ns:Dt/text() | ./ns:BookgDt/ns:DtTm/text()",
            namespaces={"ns": ns},
        )
        entry_id = "-".join(
            [statement_name or "", booking_date[0] if booking_date else "", str(entry_index)]
        )
        used_ids.add(entry_id)
        return entry_id

    def parse_statement(self, ns, node):
        """Parse a single Stmt node."""
        result = {}
        self.add_value_from_node(
            ns,
            node,
            ["./ns:Acct/ns:Id/ns:IBAN", "./ns:Acct/ns:Id/ns:Othr/ns:Id"],
            result,
            "account_number",
        )
        self.add_value_from_node(ns, node, "./ns:Id", result, "name")
        self.add_value_from_node(
            ns,
            node,
            [
                "./ns:Acct/ns:Ccy",
                "./ns:Bal/ns:Amt/@Ccy",
                "./ns:Ntry/ns:Amt/@Ccy",
            ],
            result,
            "currency",
        )
        result["balance_start"], result["balance_end_real"] = self.get_balance_amounts(
            ns, node
        )
        entry_nodes = node.xpath("./ns:Ntry", namespaces={"ns": ns})
        transactions = []
        used_ids = set()
        for entry_index, entry_node in enumerate(entry_nodes, start=1):
            entry_transactions = list(self.parse_entry(ns, entry_node))
            entry_id = self._get_entry_import_id(
                ns, entry_node, result.get("name"), entry_index, used_ids
            )
            for tx_index, transaction in enumerate(entry_transactions, start=1):
                transaction["unique_import_id"] = (
                    f"{entry_id}-{tx_index}"
                    if len(entry_transactions) > 1
                    else entry_id
                )
            transactions.extend(entry_transactions)
        result["transactions"] = transactions
        result["date"] = None
        if transactions:
            result["date"] = max(t["date"] for t in transactions)
        return result

    def check_version(self, ns, root):
        """Validate validity of camt file."""
        # Check whether it is camt at all:
        if not RE_CAMT.search(ns):
            raise ValueError("no camt: " + ns)
        # Check whether version 052 ,053 or 054:
        re_camt_version = re.compile(
            r"(^urn:iso:std:iso:20022:tech:xsd:camt.054."
            r"|^urn:iso:std:iso:20022:tech:xsd:camt.053."
            r"|^urn:iso:std:iso:20022:tech:xsd:camt.052."
            r"|^ISO:camt.054."
            r"|^ISO:camt.053."
            r"|^ISO:camt.052.)"
        )
        if not re_camt_version.search(ns):
            raise ValueError("no camt 052 or 053 or 054:" + ns)
        # Check GrpHdr element:
        if not len(root) or not len(root[0]):
            raise ValueError("expected GrpHdr, got an empty document")
        root_0_0 = root[0][0].tag[len(ns) + 2 :]  # strip namespace
        if root_0_0 != "GrpHdr":
            raise ValueError("expected GrpHdr, got:", root_0_0)

    def _check_not_truncated(self, data, error_log):
        """Reject a file that ends before its document is complete.

        The recovering parser keeps whatever it read before the end of a
        truncated file, which would import part of a statement without any
        warning. Other malformations it recovers from (a stray ampersand, a
        bad byte) lose nothing and are tolerated. A truncated file is told
        apart by a fatal error reported at the very end of the input;
        libxml2 counts columns in characters, hence the decoded last line.
        """
        last_line = data.count(b"\n") + 1
        last_column = len(data.rsplit(b"\n", 1)[-1].decode("utf-8", errors="ignore"))
        for error in error_log:
            if (
                error.level_name == "FATAL"
                and error.type_name != "ERR_DOCUMENT_END"
                and (error.line, error.column) >= (last_line, last_column)
            ):
                raise UserError(
                    self.env._(
                        "The CAMT file is damaged: it ends before the statement "
                        "is complete (line %(line)s). Nothing was imported; "
                        "download the statement from the bank again.",
                        line=error.line,
                    )
                )

    def parse(self, data):
        """Parse a camt.052 or camt.053 or camt.054 file."""
        xml_parser = etree.XMLParser(recover=True)
        try:
            root = etree.fromstring(data, parser=xml_parser)
        except etree.XMLSyntaxError:
            xml_parser = None
            try:
                # ABNAmro is known to mix up encodings
                root = etree.fromstring(data.decode("iso-8859-15").encode("utf-8"))
            except etree.XMLSyntaxError:
                root = None
        if root is None:
            raise ValueError("Not a valid xml file, or not an xml file at all.")
        ns = root.tag[1 : root.tag.index("}")]
        if xml_parser is not None and RE_CAMT.search(ns):
            self._check_not_truncated(data, xml_parser.error_log)
        self.check_version(ns, root)
        # A file may hold statements of several currencies or accounts (one Stmt
        # per currency of a multi-currency account). Each (currency, account)
        # pair is returned as its own triplet so that it is matched to the
        # journal of that currency; a file with one pair keeps the single
        # triplet of the import contract.
        groups = {}
        for node in root[0][1:]:
            statement = self.parse_statement(ns, node)
            if len(statement["transactions"]):
                key = (
                    statement.pop("currency", None),
                    statement.pop("account_number", None),
                )
                groups.setdefault(key, []).append(statement)
        if not groups:
            return None, None, []
        triplets = [
            (currency, account, stmts) for (currency, account), stmts in groups.items()
        ]
        return triplets[0] if len(triplets) == 1 else triplets
