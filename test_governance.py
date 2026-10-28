"""Behavior tests: python3 -m unittest -v"""

import contextlib
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from demo import run_demo
from governance import ControlError, Governance, encode


def supplier(tax="SYN-CN-1001", **changes):
    data = dict(supplier_name="Example Components", country="CN", tax_id=tax,
                payment_terms="NET30", bank_account="DEMO-0001")
    data.update(changes)
    return data


class GovernanceTests(unittest.TestCase):
    def setUp(self):
        self.g = Governance()

    def tearDown(self):
        self.g.close()

    def approved(self, kind="CREATE", target="SUP-001", data=None, actor="alice"):
        rid = self.g.submit(actor, kind, target, data or supplier(), "Test request", "SIM-VERIFIED")
        self.assertTrue(self.g.validate(rid, "bob"))
        self.g.approve(rid, "diana", "Independent review")
        return rid

    def create(self, target="SUP-001", data=None):
        rid = self.approved(target=target, data=data)
        self.g.apply(rid, "bob")
        return rid

    def test_cleaning_does_not_activate_data(self):
        rid = self.g.submit("alice", "CREATE", "SUP-001", supplier(
            supplier_name="  Example   Components ", country="cn"), "Normalize", "SIM")
        self.assertEqual(json.loads(self.g.request(rid)["payload"])["supplier_name"], "Example Components")
        self.assertEqual(self.g.rows("SELECT * FROM suppliers"), [])

    def test_invalid_data_opens_issues_and_cannot_be_approved(self):
        rid = self.g.submit("alice", "CREATE", "SUP-001", supplier(tax=""), "Missing tax", "SIM")
        self.assertFalse(self.g.validate(rid, "bob"))
        self.assertEqual({r["rule_id"] for r in self.g.rows("SELECT * FROM issues")}, {"DQ01", "DQ03"})
        with self.assertRaises(ControlError):
            self.g.approve(rid, "carol", "Should fail")
        self.assertEqual(self.g.rows("SELECT * FROM suppliers"), [])

    def test_approver_cannot_approve_their_own_request(self):
        rid = self.g.submit("carol", "CREATE", "SUP-001", supplier(), "Own request", "SIM")
        self.g.validate(rid, "bob")
        with self.assertRaisesRegex(ControlError, "independent"):
            self.g.approve(rid, "carol", "Self approval")
        self.assertEqual(self.g.request(rid)["status"], "VALIDATED")

    def test_unapproved_and_repeat_application_are_blocked(self):
        rid = self.g.submit("alice", "CREATE", "SUP-001", supplier(), "Create", "SIM")
        self.g.validate(rid, "bob")
        with self.assertRaises(ControlError):
            self.g.apply(rid, "bob")
        self.g.approve(rid, "carol", "Reviewed")
        self.g.apply(rid, "bob")
        with self.assertRaises(ControlError):
            self.g.apply(rid, "bob")
        self.assertEqual(self.g.supplier("SUP-001")["version"], 1)

    def test_role_and_unknown_actor_are_rejected(self):
        with self.assertRaises(ControlError):
            self.g.submit("unknown", "CREATE", "SUP-001", supplier(), "Create", "SIM")
        rid = self.g.submit("alice", "CREATE", "SUP-001", supplier(), "Create", "SIM")
        with self.assertRaises(ControlError):
            self.g.validate(rid, "alice")
        self.g.validate(rid, "bob")
        with self.assertRaises(ControlError):
            self.g.approve(rid, "bob", "Wrong role")

    def test_duplicate_identity_is_blocked(self):
        self.create()
        rid = self.g.submit("alice", "CREATE", "SUP-002", supplier(), "Duplicate", "SIM")
        self.assertFalse(self.g.validate(rid, "bob"))
        self.assertEqual(self.g.rows("SELECT rule_id FROM issues"), [{"rule_id": "MDM01"}])

    def test_duplicate_created_after_approval_is_rechecked(self):
        first = self.approved(target="SUP-001")
        second = self.approved(target="SUP-002")
        self.g.apply(first, "bob")
        with self.assertRaisesRegex(ControlError, "MDM01"):
            self.g.apply(second, "bob")
        self.assertEqual(len(self.g.rows("SELECT * FROM suppliers")), 1)
        self.assertEqual(self.g.request(second)["status"], "APPROVED")

    def test_stale_approved_update_cannot_overwrite_newer_master(self):
        self.create()
        first = self.approved("UPDATE", data=supplier(payment_terms="NET45"))
        second = self.approved("UPDATE", data=supplier(payment_terms="NET60"))
        self.g.apply(first, "bob")
        with self.assertRaisesRegex(ControlError, "CTL02"):
            self.g.apply(second, "bob")
        self.assertEqual(self.g.supplier("SUP-001")["payment_terms"], "NET45")
        self.assertEqual(self.g.supplier("SUP-001")["version"], 2)

    def test_bank_change_requires_verification_reference(self):
        self.create()
        rid = self.g.submit("alice", "UPDATE", "SUP-001", supplier(bank_account="DEMO-9001"), "New bank")
        self.assertFalse(self.g.validate(rid, "bob"))
        self.assertEqual(self.g.rows("SELECT rule_id FROM issues"), [{"rule_id": "CTL01"}])

    def test_unchanged_bank_does_not_require_new_verification(self):
        self.create()
        rid = self.g.submit("alice", "UPDATE", "SUP-001", supplier(payment_terms="NET45"), "Terms only")
        self.assertTrue(self.g.validate(rid, "bob"))

    def test_proposal_and_event_history_cannot_be_overwritten_by_sql(self):
        rid = self.approved()
        with self.assertRaises(sqlite3.IntegrityError), self.g.db:
            self.g.db.execute("UPDATE requests SET payload=? WHERE request_id=?", (encode(supplier()), rid))
        with self.assertRaises(sqlite3.IntegrityError), self.g.db:
            self.g.db.execute("DELETE FROM audit_events")
        with self.assertRaises(sqlite3.IntegrityError), self.g.db:
            self.g.db.execute("UPDATE audit_events SET action='EDITED'")

    def test_issue_needs_investigation_replacement_and_erp_retest(self):
        failed = self.g.submit("alice", "CREATE", "SUP-001", supplier(payment_terms="NET90"), "Invalid", "SIM")
        self.g.validate(failed, "bob")
        issue = self.g.rows("SELECT issue_id FROM issues")[0]["issue_id"]
        with self.assertRaises(ControlError):
            self.g.close_issue(issue, "carol")
        self.g.investigate(issue, "bob", "Incorrect terms selected", "Use approved NET30")
        replacement = self.create()
        self.g.remediate(issue, "bob", replacement)
        with self.assertRaisesRegex(ControlError, "ERP"):
            self.g.close_issue(issue, "carol")
        self.g.sync("SUP-001", "bob")
        self.g.close_issue(issue, "carol")
        self.assertEqual(self.g.rows("SELECT status FROM issues"), [{"status": "CLOSED"}])

    def test_issue_cannot_use_an_unrelated_supplier_as_remediation(self):
        failed = self.g.submit("alice", "CREATE", "SUP-001", supplier(payment_terms="NET90"), "Invalid", "SIM")
        self.g.validate(failed, "bob")
        issue = self.g.rows("SELECT issue_id FROM issues")[0]["issue_id"]
        self.g.investigate(issue, "bob", "Bad terms", "Correct terms")
        other = self.create("SUP-002", supplier(tax="SYN-CN-2002"))
        with self.assertRaises(ControlError):
            self.g.remediate(issue, "bob", other)

    def test_sync_failure_keeps_mismatch_visible_until_retry(self):
        self.create()
        self.assertFalse(self.g.sync("SUP-001", "bob", simulate_failure=True))
        self.assertEqual(len(self.g.monitoring()["erp_mismatches"]), 1)
        self.g.sync("SUP-001", "bob")
        self.assertEqual(self.g.monitoring()["erp_mismatches"], [])

    def test_monitoring_detects_same_version_field_drift(self):
        self.create()
        self.g.sync("SUP-001", "bob")
        # Direct database write is a test-only corruption injection.
        with self.g.db:
            self.g.db.execute("UPDATE erp_snapshot SET payload=?", (encode(supplier(bank_account="DEMO-9999")),))
        self.assertEqual(len(self.g.monitoring()["erp_mismatches"]), 1)

    def test_demo_outputs_are_reproducible_and_refuse_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / "evidence"
            with contextlib.redirect_stdout(io.StringIO()):
                results = run_demo(folder)
            self.assertEqual(results["summary"]["master_suppliers"], 4)
            self.assertEqual(results["summary"]["source_mappings"], 8)
            self.assertEqual(results["summary"]["issues_closed"], 3)
            self.assertEqual(results["summary"]["blocked_attempts"], 5)
            self.assertTrue((folder / "audit_events.csv").exists())
            self.assertTrue(all(not rows for rows in results["monitoring_stages"]["final"].values()))
            with self.assertRaises(FileExistsError):
                run_demo(folder)


if __name__ == "__main__":
    unittest.main()
