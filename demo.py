"""Run a complete fictional supplier-governance walkthrough: python3 demo.py."""

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path

from governance import ControlError, FIELDS, Governance, ROOT, encode


def run_demo(output):
    output = Path(output)
    # A demo must never silently replace an earlier evidence pack.
    output.mkdir(parents=True, exist_ok=False)
    model = Governance(output / "governance.sqlite")
    blocked = []
    stages = {}

    def expect_blocked(label, action):
        try:
            action()
        except ControlError as error:
            blocked.append({"attempt": label, "result": str(error)})
        else:
            raise AssertionError("Control did not block: " + label)

    def approved_create(supplier_id, payload, evidence):
        request_id = model.submit("alice", "CREATE", supplier_id, payload,
                                  "Onboard fictional supplier", evidence)
        if not model.validate(request_id, "bob"):
            raise AssertionError("Expected the corrected request to pass")
        model.approve(request_id, "carol", "Reviewed synthetic supplier evidence")
        model.apply(request_id, "bob")
        model.sync(supplier_id, "bob")
        return request_id

    try:
        model.load_sources(ROOT / "source_suppliers.csv")
        candidates = model.duplicate_candidates()
        for suffix, name in (("001", "SUP-001"), ("002", "SUP-002")):
            approved_create(name, model.source("ERP_A", "A" + suffix), "SIM-ONBOARD-" + suffix)

        for source_id, corrected_id, supplier_id, cause in (
            ("A003", "B016", "SUP-003", "ERP_A intake omitted the synthetic tax identifier"),
            ("A004", "B017", "SUP-004", "ERP_A used NET90 outside the demo payment-terms policy"),
        ):
            failed = model.submit("alice", "CREATE", supplier_id,
                                  model.source("ERP_A", source_id), "Onboard from ERP_A", "SIM-VERIFY")
            assert not model.validate(failed, "bob")
            expect_blocked("Approve invalid request " + str(failed),
                           lambda rid=failed: model.approve(rid, "carol", "Attempted approval"))
            issues = model.rows("SELECT issue_id FROM issues WHERE request_id=?", (failed,))
            for issue in issues:
                model.investigate(issue["issue_id"], "bob", cause,
                                  "Use manually reviewed ERP_B record; retain the raw ERP_A record")
            model.reject(failed, "carol", "Superseded by a corrected request; original retained")
            corrected = approved_create(supplier_id, model.source("ERP_B", corrected_id),
                                        "SIM-REVIEW-" + corrected_id)
            for issue in issues:
                model.remediate(issue["issue_id"], "bob", corrected)
                model.close_issue(issue["issue_id"], "carol")

        # Match decisions are explicit, including differing names / missing source tax.
        for a_id, b_id, supplier_id, decision in (
            ("A001", "B014", "SUP-001", "Country/tax match; whitespace and case normalized"),
            ("A002", "B015", "SUP-002", "Country/tax match; name variation manually reviewed; ERP_A name selected"),
            ("A003", "B016", "SUP-003", "Missing ERP_A tax investigated; simulated onboarding evidence confirms identity; ERP_B selected"),
            ("A004", "B017", "SUP-004", "Country/tax match; approved NET30 terms selected from ERP_B"),
        ):
            model.map_source("ERP_A", a_id, supplier_id, "bob", decision)
            model.map_source("ERP_B", b_id, supplier_id, "bob", decision)

        stages["before_bank_change"] = model.monitoring()
        proposed = {field: model.supplier("SUP-001")[field] for field in FIELDS}
        proposed["bank_account"] = "DEMO-9001"
        # Carol also has approval authority, making this a real SoD check in the model.
        bank_request = model.submit("carol", "UPDATE", "SUP-001", proposed,
                                    "Fictional bank account change", "SIM-CALLBACK-001")
        assert model.validate(bank_request, "bob")
        expect_blocked("Apply before approval", lambda: model.apply(bank_request, "bob"))
        expect_blocked("Requester approves own change",
                       lambda: model.approve(bank_request, "carol", "Attempted self-approval"))
        model.approve(bank_request, "diana", "Reviewed simulated callback and proposed bank token")
        model.apply(bank_request, "bob")
        expect_blocked("Apply the same request twice", lambda: model.apply(bank_request, "bob"))
        stages["after_bank_change_before_sync"] = model.monitoring()
        assert len(stages["after_bank_change_before_sync"]["erp_mismatches"]) == 1
        model.sync("SUP-001", "bob", simulate_failure=True)
        stages["after_failed_sync"] = model.monitoring()
        model.sync("SUP-001", "bob")
        stages["final"] = model.monitoring()
        assert all(not exceptions for exceptions in stages["final"].values())

        tables = ("actors", "source_records", "suppliers", "source_mapping", "requests",
                  "validation_results", "issues", "audit_events", "erp_snapshot", "sync_attempts")
        exported = {}
        for table in tables:
            cursor = model.db.execute("SELECT * FROM " + table + " ORDER BY rowid")
            columns = [col[0] for col in cursor.description]
            rows = [dict(row) for row in cursor]
            exported[table] = rows
            with (output / (table + ".csv")).open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=columns)
                writer.writeheader()
                writer.writerows(rows)

        summary = {
            "source_records": len(exported["source_records"]),
            "duplicate_candidate_groups": len(candidates),
            "master_suppliers": len(exported["suppliers"]),
            "source_mappings": len(exported["source_mapping"]),
            "requests": len(exported["requests"]),
            "applied_requests": sum(r["status"] == "APPLIED" for r in exported["requests"]),
            "rejected_requests": sum(r["status"] == "REJECTED" for r in exported["requests"]),
            "issues_opened": len(exported["issues"]),
            "issues_closed": sum(r["status"] == "CLOSED" for r in exported["issues"]),
            "blocked_attempts": len(blocked),
            "failed_sync_attempts": sum(r["status"] == "FAILED" for r in exported["sync_attempts"]),
            "audit_events": len(exported["audit_events"]),
        }
        evidence = {"summary": summary, "duplicate_candidates": candidates,
                    "blocked_attempts": blocked, "monitoring_stages": stages}
        (output / "results.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
        report = ["# Supplier governance simulation: sample results", "",
                  "Generated by `python3 demo.py`. All suppliers, identities, tax IDs, bank tokens and evidence references are fictional.",
                  "", "## Run summary", "", "| Measure | Result |", "|---|---:|"]
        report += ["| " + key.replace("_", " ") + " | " + str(value) + " |" for key, value in summary.items()]
        report += ["", "## What happened", "",
                   "1. Eight source rows were retained unchanged. A steward reviewed mappings to four master suppliers.",
                   "2. Northstar's missing tax identifier failed DQ01 and DQ03; Cedar's NET90 terms failed DQ04. Two requests were rejected and three rule-level issues were opened.",
                   "3. Corrected requests were validated, independently approved, applied and synchronized. All three issues were independently retested and closed; original failures remain in the evidence.",
                   "4. Carol requested a bank change from DEMO-0001 to DEMO-9001. Self-approval was blocked; Diana approved it instead.",
                   "5. Monitoring detected one ERP mismatch after the master changed. A simulated sync failure left it visible; a successful retry resolved it.",
                   "", "## Blocked actions", "", "| Attempt | Reason |", "|---|---|"]
        report += ["| " + row["attempt"] + " | " + row["result"] + " |" for row in blocked]
        report += ["", "## Monitoring", "", "| Stage | Approval exceptions | ERP mismatches | Open issues |", "|---|---:|---:|---:|"]
        report += ["| " + stage + " | " + " | ".join(str(len(values[key])) for key in
                   ("approval_exceptions", "erp_mismatches", "open_issues")) + " |"
                   for stage, values in stages.items()]
        report += ["", "## One complete exception trail", "",
                   "| Issue | Failed request / rule | Root cause | Replacement request | Closure |", "|---|---|---|---|---|"]
        report += [f"| {row['issue_id']} | {row['request_id']} / {row['rule_id']} | {row['root_cause']} | {row['replacement_request_id']} | {row['status']} by {row['retested_by']} |"
                   for row in exported["issues"]]
        report += ["", "## Read the evidence", "",
                   "Each run directory includes the SQLite database, ten CSV table exports and results.json. Inspect requests.csv for approval identities, validation_results.csv for passing and failing checks, issues.csv for remediation, and audit_events.csv for the event sequence and before/after values.",
                   "", "The demo's blocked attempts are recorded in results.json, not audit_events. The latter records successful workflow actions. Timestamps vary per run; business counts are deterministic.",
                   ""]
        (output / "report.md").write_text("\n".join(report), encoding="utf-8")
        print("Supplier governance demo completed.")
        print(encode(summary))
        print("Read:", output / "report.md")
        return evidence
    finally:
        model.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "runs" /
                        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"),
                        help="New output directory; an existing directory is refused")
    args = parser.parse_args()
    run_demo(args.output)
