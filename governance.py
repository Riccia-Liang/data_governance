"""Small supplier-governance learning model using Python's standard library.

Actor names model roles; they are NOT authenticated identities. The SQLite
owner can bypass this model. Never use it for real bank details or SOX controls.
"""

import csv
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FIELDS = ("supplier_name", "country", "tax_id", "payment_terms", "bank_account")


class ControlError(ValueError):
    """An attempted action did not satisfy a workflow control."""


def now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def encode(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def normalize(payload):
    """Basic cleaning only: whitespace and case, with no invented business data."""
    if set(payload) - set(FIELDS):
        raise ControlError("Unexpected supplier fields")
    if any(not isinstance(v, str) for v in payload.values()):
        raise ControlError("Supplier values must be strings")
    cleaned = {field: " ".join(payload.get(field, "").split()) for field in FIELDS}
    for field in FIELDS[1:]:
        cleaned[field] = cleaned[field].upper()
    return cleaned


class Governance:
    def __init__(self, path=":memory:"):
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        if not self.db.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'actors'"
        ).fetchone():
            self.db.executescript((ROOT / "schema.sql").read_text())

    def close(self):
        self.db.close()

    def rows(self, sql, params=()):
        return [dict(row) for row in self.db.execute(sql, params)]

    def _one(self, sql, params):
        row = self.db.execute(sql, params).fetchone()
        if row is None:
            raise ControlError("Record does not exist")
        return dict(row)

    def _actor(self, actor, roles=("requester", "steward", "approver")):
        row = self._one("SELECT * FROM actors WHERE actor_id = ?", (actor,))
        if row["role"] not in roles:
            raise ControlError("Actor does not have the required role")

    @staticmethod
    def _required(text, label):
        if not isinstance(text, str) or not text.strip():
            raise ControlError(label + " is required")

    def _event(self, actor, action, object_ref, detail):
        self.db.execute(
            "INSERT INTO audit_events(event_at,actor,action,object_ref,detail) "
            "VALUES (?,?,?,?,?)", (now(), actor, action, str(object_ref), encode(detail))
        )

    def request(self, request_id):
        return self._one("SELECT * FROM requests WHERE request_id = ?", (request_id,))

    def supplier(self, supplier_id):
        return self._one("SELECT * FROM suppliers WHERE supplier_id = ?", (supplier_id,))

    def load_sources(self, csv_path):
        """Keep raw source values unchanged. Re-imports are refused by the key."""
        with open(csv_path, newline="", encoding="utf-8") as stream:
            records = list(csv.DictReader(stream))
        with self.db:
            for row in records:
                payload = {field: row[field] for field in FIELDS}
                self.db.execute("INSERT INTO source_records VALUES (?,?,?)", (
                    row["source_system"], row["source_id"], encode(payload)))

    def source(self, system, source_id):
        return json.loads(self._one(
            "SELECT payload FROM source_records WHERE source_system=? AND source_id=?",
            (system, source_id))["payload"])

    def duplicate_candidates(self):
        """Exact nonempty country/tax matches are candidates, not automatic merges."""
        groups = {}
        for row in self.rows("SELECT * FROM source_records ORDER BY source_system,source_id"):
            data = normalize(json.loads(row["payload"]))
            if data["country"] and data["tax_id"]:
                key = (data["country"], data["tax_id"])
                groups.setdefault(key, []).append(row["source_system"] + ":" + row["source_id"])
        return [{"country": key[0], "tax_id": key[1], "source_records": refs}
                for key, refs in groups.items() if len(refs) > 1]

    def submit(self, actor, kind, supplier_id, payload, reason, verification_ref=""):
        self._actor(actor)
        self._required(reason, "Reason")
        self._required(supplier_id, "Supplier ID")
        if kind not in ("CREATE", "UPDATE"):
            raise ControlError("Kind must be CREATE or UPDATE")
        existing = self.rows("SELECT * FROM suppliers WHERE supplier_id=?", (supplier_id,))
        if (kind == "CREATE" and existing) or (kind == "UPDATE" and not existing):
            raise ControlError("Target does not match request kind")
        before = {key: existing[0][key] for key in FIELDS} if existing else {}
        version = existing[0]["version"] if existing else 0
        with self.db:
            cursor = self.db.execute(
                "INSERT INTO requests(kind,supplier_id,requester,reason,verification_ref,"
                "payload,before_payload,expected_version,status,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,'SUBMITTED',?)",
                (kind, supplier_id, actor, reason.strip(), verification_ref.strip(),
                 encode(normalize(payload)), encode(before), version, now()))
            request_id = cursor.lastrowid
            self._event(actor, "SUBMIT", "request:" + str(request_id), {
                "before": before, "proposed": normalize(payload), "reason": reason})
        return request_id

    def _checks(self, request):
        data = json.loads(request["payload"])
        before = json.loads(request["before_payload"])
        existing = self.rows("SELECT version FROM suppliers WHERE supplier_id=?",
                             (request["supplier_id"],))
        version = existing[0]["version"] if existing else 0
        duplicate = self.rows(
            "SELECT supplier_id FROM suppliers WHERE country=? AND tax_id=? AND supplier_id!=?",
            (data["country"], data["tax_id"], request["supplier_id"]))
        needs_verification = not before or before["bank_account"] != data["bank_account"]
        return [
            ("DQ01", all(data.values()), "All five supplier fields must be populated"),
            ("DQ02", data["country"] in {"CN", "US", "DE"}, "Country in demo allowlist CN/US/DE"),
            ("DQ03", bool(re.fullmatch(r"SYN-" + re.escape(data["country"]) + r"-\d{4}", data["tax_id"])),
             "Synthetic tax ID must follow SYN-<country>-1234"),
            ("DQ04", data["payment_terms"] in {"NET30", "NET45", "NET60"}, "Allowed terms NET30/NET45/NET60"),
            ("DQ05", bool(re.fullmatch(r"DEMO-\d{4}", data["bank_account"])), "Fictional bank token DEMO-1234"),
            ("MDM01", not duplicate, "Country and tax ID must identify one active supplier"),
            ("CTL01", not needs_verification or bool(request["verification_ref"]),
             "Creation or bank change requires a verification reference (simulated)"),
            ("CTL02", version == request["expected_version"], "Target version must match submission snapshot"),
        ]

    def validate(self, request_id, actor):
        self._actor(actor, ("steward",))
        request = self.request(request_id)
        if request["status"] != "SUBMITTED":
            raise ControlError("Only a submitted request can be validated")
        checks = self._checks(request)
        passed = all(check[1] for check in checks)
        with self.db:
            for rule, ok, detail in checks:
                self.db.execute(
                    "INSERT INTO validation_results(request_id,rule_id,passed,detail,checked_at) "
                    "VALUES (?,?,?,?,?)", (request_id, rule, int(ok), detail, now()))
                if not ok:
                    self.db.execute(
                        "INSERT INTO issues(request_id,rule_id,owner,status,opened_at) "
                        "VALUES (?,?,?,'OPEN',?)", (request_id, rule, actor, now()))
            self.db.execute("UPDATE requests SET status=?,validator=? WHERE request_id=?",
                            ("VALIDATED" if passed else "VALIDATION_FAILED", actor, request_id))
            self._event(actor, "VALIDATE", "request:" + str(request_id),
                        {"passed": passed, "failed_rules": [r for r, ok, _ in checks if not ok]})
        return passed

    def _fresh(self, request):
        failed = [rule for rule, ok, _ in self._checks(request) if not ok]
        if failed:
            raise ControlError("Current validation failed: " + ", ".join(failed))

    def approve(self, request_id, actor, note):
        self._actor(actor, ("approver",))
        self._required(note, "Approval note")
        with self.db:
            # Reserve the write transaction before checking the current master.
            self.db.execute("BEGIN IMMEDIATE")
            request = self.request(request_id)
            if actor in (request["requester"], request["validator"]):
                raise ControlError("An independent approver is required")
            if request["status"] != "VALIDATED":
                raise ControlError("Only a validated request can be approved")
            self._fresh(request)
            self.db.execute("UPDATE requests SET status='APPROVED',approver=?,decision_note=?,"
                            "approved_at=? WHERE request_id=?", (actor, note, now(), request_id))
            self._event(actor, "APPROVE", "request:" + str(request_id), {"note": note})

    def reject(self, request_id, actor, note):
        self._actor(actor, ("approver",))
        self._required(note, "Rejection note")
        request = self.request(request_id)
        if request["status"] not in ("SUBMITTED", "VALIDATION_FAILED", "VALIDATED"):
            raise ControlError("This request can no longer be rejected")
        with self.db:
            self.db.execute("UPDATE requests SET status='REJECTED',decision_note=? WHERE request_id=?",
                            (note, request_id))
            self._event(actor, "REJECT", "request:" + str(request_id), {"note": note})

    def apply(self, request_id, actor):
        self._actor(actor, ("steward",))
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            request = self.request(request_id)
            if request["status"] != "APPROVED":
                raise ControlError("Only an approved request can be applied")
            self._fresh(request)
            data = json.loads(request["payload"])
            values = [data[field] for field in FIELDS]
            if request["kind"] == "CREATE":
                self.db.execute("INSERT INTO suppliers VALUES (?,?,?,?,?,?,1)",
                                [request["supplier_id"]] + values)
            else:
                self.db.execute("UPDATE suppliers SET supplier_name=?,country=?,tax_id=?,"
                                "payment_terms=?,bank_account=?,version=version+1 WHERE supplier_id=?",
                                values + [request["supplier_id"]])
            self.db.execute("UPDATE requests SET status='APPLIED',applied_at=? WHERE request_id=?",
                            (now(), request_id))
            self._event(actor, "APPLY", "request:" + str(request_id), {
                "before": json.loads(request["before_payload"]), "after": data,
                "supplier_id": request["supplier_id"], "version": request["expected_version"] + 1})

    def map_source(self, system, source_id, supplier_id, actor, decision):
        self._actor(actor, ("steward",))
        self._required(decision, "Manual match decision")
        self.source(system, source_id)
        self.supplier(supplier_id)
        with self.db:
            self.db.execute("INSERT INTO source_mapping VALUES (?,?,?,?,?,?)",
                            (system, source_id, supplier_id, actor, decision, now()))
            self._event(actor, "MAP_SOURCE", system + ":" + source_id,
                        {"supplier_id": supplier_id, "decision": decision})

    def sync(self, supplier_id, actor, simulate_failure=False):
        self._actor(actor, ("steward",))
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            supplier = self.supplier(supplier_id)
            payload = {field: supplier[field] for field in FIELDS}
            status = "FAILED" if simulate_failure else "SUCCESS"
            if not simulate_failure:
                self.db.execute("INSERT INTO erp_snapshot VALUES (?,?,?,?) "
                                "ON CONFLICT(supplier_id) DO UPDATE SET payload=excluded.payload,"
                                "version=excluded.version,synced_at=excluded.synced_at",
                                (supplier_id, encode(payload), supplier["version"], now()))
            self.db.execute("INSERT INTO sync_attempts(supplier_id,version,status,detail,attempted_at) "
                            "VALUES (?,?,?,?,?)", (supplier_id, supplier["version"], status,
                            "Simulated connection failure" if simulate_failure else "Snapshot copied", now()))
            self._event(actor, "SYNC_" + status, supplier_id, {"version": supplier["version"]})
        return not simulate_failure

    def investigate(self, issue_id, actor, root_cause, remediation):
        self._actor(actor, ("steward",))
        self._required(root_cause, "Root cause")
        self._required(remediation, "Remediation")
        issue = self._one("SELECT * FROM issues WHERE issue_id=?", (issue_id,))
        if issue["owner"] != actor or issue["status"] != "OPEN":
            raise ControlError("Only the owner may investigate an open issue")
        with self.db:
            self.db.execute("UPDATE issues SET status='INVESTIGATING',root_cause=?,remediation=? "
                            "WHERE issue_id=?", (root_cause, remediation, issue_id))
            self._event(actor, "INVESTIGATE", "issue:" + str(issue_id),
                        {"root_cause": root_cause, "remediation": remediation})

    def remediate(self, issue_id, actor, replacement_request_id):
        self._actor(actor, ("steward",))
        issue = self._one("SELECT * FROM issues WHERE issue_id=?", (issue_id,))
        original = self.request(issue["request_id"])
        replacement = self.request(replacement_request_id)
        if issue["owner"] != actor or issue["status"] != "INVESTIGATING":
            raise ControlError("Only an investigated issue may be remediated by its owner")
        if replacement["status"] != "APPLIED" or replacement["supplier_id"] != original["supplier_id"]:
            raise ControlError("Remediation needs an applied replacement for the same supplier")
        with self.db:
            self.db.execute("UPDATE issues SET status='REMEDIATED',replacement_request_id=? WHERE issue_id=?",
                            (replacement_request_id, issue_id))
            self._event(actor, "REMEDIATE", "issue:" + str(issue_id),
                        {"replacement_request_id": replacement_request_id})

    def close_issue(self, issue_id, actor):
        self._actor(actor, ("approver",))
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            issue = self._one("SELECT * FROM issues WHERE issue_id=?", (issue_id,))
            if issue["status"] != "REMEDIATED" or actor == issue["owner"]:
                raise ControlError("Independent retest of a remediated issue is required")
            replacement = self.request(issue["replacement_request_id"])
            current = self.supplier(replacement["supplier_id"])
            payload = {field: current[field] for field in FIELDS}
            if encode(payload) != replacement["payload"] or current["version"] != replacement["expected_version"] + 1:
                raise ControlError("Master changed after remediation; investigate again")
            retest = dict(replacement, payload=encode(payload), expected_version=current["version"])
            if not all(ok for _, ok, _ in self._checks(retest)):
                raise ControlError("Current data failed retesting")
            erp = self.rows("SELECT * FROM erp_snapshot WHERE supplier_id=?", (current["supplier_id"],))
            if not erp or erp[0]["payload"] != encode(payload) or erp[0]["version"] != current["version"]:
                raise ControlError("ERP must agree with current master before closure")
            self.db.execute("UPDATE issues SET status='CLOSED',retested_by=?,closed_at=? WHERE issue_id=?",
                            (actor, now(), issue_id))
            self._event(actor, "RETEST_AND_CLOSE", "issue:" + str(issue_id),
                        {"master_and_erp_agree": True, "failed_rule_retested": issue["rule_id"]})

    def monitoring(self):
        """Execute the actual SQL file so exported monitoring is reproducible."""
        names = ("approval_exceptions", "erp_mismatches", "open_issues")
        statements = [s.strip() for s in (ROOT / "monitoring.sql").read_text().split(";") if s.strip()]
        if len(statements) != len(names):
            raise ControlError("Unexpected monitoring query count")
        return {name: self.rows(statement) for name, statement in zip(names, statements)}
