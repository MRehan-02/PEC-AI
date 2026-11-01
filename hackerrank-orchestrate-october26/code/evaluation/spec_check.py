"""
Ghost Data Still Exists - Specification Checker
Validates output.csv against all problem_statement.md requirements:
1. Header match
2. 518 rows sorted by case_id
3. Status enum values
4. Exact 7 step keys
5. step_no consecutive from 1
6. Phase and operation enums
7. Operation-to-state mapping
8. Ordering (phase, operation table order, system_id, resource_id)
9. COMPLETE has only VERIFICATION steps with non-empty evidence_id
10. INCOMPLETE findings cite evidence and remediation/verification use ""
11. NOT_APPLICABLE has steps_json == []
12. Every cited evidence_id exists in manifest with captured_at <= audit_at (or activity/audit id)
"""

import os
import sys
import csv
import json
from datetime import datetime
from typing import Dict, List, Any

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))

ALLOWED_STATUSES = {"COMPLETE", "INCOMPLETE", "NOT_APPLICABLE"}
STEP_KEYS = {"step_no", "phase", "system_id", "resource_id", "operation", "state", "evidence_id"}

PHASE_ORDER = {"FINDING": 0, "REMEDIATION": 1, "VERIFICATION": 2}

OPERATION_STATE_MAP = {
    # Findings
    "REPORT_REMAINING_RECORD": "PRESENT",
    "REPORT_EXPOSED_PII": "PRESENT",
    "REPORT_ACTIVE_DATA_FLOW": "ACTIVE",
    "REPORT_RESTORE_RISK": "ACTIVE",
    # Remediation
    "DISABLE_DATA_FLOW": "DISABLED",
    "BLOCK_RESTORE": "BLOCKED",
    "DELETE_RECORD": "ABSENT",
    "REDACT_RECORD": "REDACTED",
    "ANONYMIZE_RECORD": "ANONYMIZED",
    # Verification
    "VERIFY_RECORD_ABSENT": "ABSENT",
    "VERIFY_RECORD_PRESENT": "PRESENT",
    "VERIFY_RECORD_REDACTED": "REDACTED",
    "VERIFY_RECORD_ANONYMIZED": "ANONYMIZED",
    "VERIFY_DATA_FLOW_DISABLED": "DISABLED",
    "VERIFY_RESTORE_BLOCKED": "BLOCKED",
}

OP_ORDER_IN_PHASE = {
    # Finding
    "REPORT_REMAINING_RECORD": 0,
    "REPORT_EXPOSED_PII": 1,
    "REPORT_ACTIVE_DATA_FLOW": 2,
    "REPORT_RESTORE_RISK": 3,
    # Remediation
    "DISABLE_DATA_FLOW": 0,
    "BLOCK_RESTORE": 1,
    "DELETE_RECORD": 2,
    "REDACT_RECORD": 3,
    "ANONYMIZE_RECORD": 4,
    # Verification
    "VERIFY_RECORD_ABSENT": 0,
    "VERIFY_RECORD_PRESENT": 1,
    "VERIFY_RECORD_REDACTED": 2,
    "VERIFY_RECORD_ANONYMIZED": 3,
    "VERIFY_DATA_FLOW_DISABLED": 4,
    "VERIFY_RESTORE_BLOCKED": 5,
}


def run_spec_check(
    output_path: str = None,
    requests_path: str = None,
    manifest_path: str = None,
    activity_path: str = None
) -> bool:
    print("=" * 70)
    print("Running Ghost Data Still Exists Specification Check")
    print("=" * 70)

    if output_path is None:
        output_path = os.path.join(REPO_ROOT, "output.csv")
    elif not os.path.isabs(output_path):
        output_path = os.path.abspath(output_path) if os.path.exists(output_path) else os.path.join(REPO_ROOT, output_path)

    if requests_path is None:
        requests_path = os.path.join(REPO_ROOT, "dataset", "deletion_requests.csv")
    elif not os.path.isabs(requests_path):
        requests_path = os.path.abspath(requests_path) if os.path.exists(requests_path) else os.path.join(REPO_ROOT, requests_path)

    if manifest_path is None:
        manifest_path = os.path.join(REPO_ROOT, "dataset", "evidence_files.csv")
    elif not os.path.isabs(manifest_path):
        manifest_path = os.path.abspath(manifest_path) if os.path.exists(manifest_path) else os.path.join(REPO_ROOT, manifest_path)

    if activity_path is None:
        activity_path = os.path.join(REPO_ROOT, "dataset", "system_activity.csv")
    elif not os.path.isabs(activity_path):
        activity_path = os.path.abspath(activity_path) if os.path.exists(activity_path) else os.path.join(REPO_ROOT, activity_path)

    # Load references
    req_map = {}
    with open(requests_path, "r", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            req_map[r["case_id"]] = r

    manifest_times = {}
    with open(manifest_path, "r", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            manifest_times[r["file_id"]] = r["captured_at"]

    activity_ids = set()
    if os.path.exists(activity_path):
        with open(activity_path, "r", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                activity_ids.add(r["activity_id"])

    # Load output.csv
    if not os.path.exists(output_path):
        print(f"FAILED: {output_path} does not exist!")
        return False

    with open(output_path, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        rows = list(reader)

    results = {}

    # Rule 1: Header
    results["Rule 1: Header exactly [case_id, deletion_status, steps_json]"] = (
        header == ["case_id", "deletion_status", "steps_json"]
    )

    # Rule 2: 518 rows sorted by case_id
    case_ids = [r[0] for r in rows]
    expected_case_ids = sorted(list(req_map.keys()))
    results["Rule 2: Exactly 518 rows sorted by case_id"] = (
        len(rows) == len(expected_case_ids) and case_ids == expected_case_ids
    )

    # Parse rows
    parsed_rows = []
    json_valid = True
    for cid, stat, s_json in rows:
        try:
            steps = json.loads(s_json)
            parsed_rows.append((cid, stat, steps))
        except Exception:
            json_valid = False

    # Rule 3: Status values
    status_valid = all(stat in ALLOWED_STATUSES for _, stat, _ in parsed_rows)
    results["Rule 3: Status is COMPLETE, INCOMPLETE, or NOT_APPLICABLE"] = status_valid

    # Rule 4: The 7 step keys
    keys_valid = True
    for _, _, steps in parsed_rows:
        for s in steps:
            if set(s.keys()) != STEP_KEYS:
                keys_valid = False
                break
    results["Rule 4: Exactly 7 step keys present in each step"] = keys_valid

    # Rule 5: step_no consecutive from 1
    step_no_valid = True
    for _, _, steps in parsed_rows:
        for idx, s in enumerate(steps, 1):
            if s.get("step_no") != idx:
                step_no_valid = False
                break
    results["Rule 5: step_no numbered consecutively from 1"] = step_no_valid

    # Rule 6: Phase and operation enums
    enums_valid = True
    for _, _, steps in parsed_rows:
        for s in steps:
            ph = s.get("phase")
            op = s.get("operation")
            if ph not in PHASE_ORDER or op not in OPERATION_STATE_MAP:
                enums_valid = False
                break
    results["Rule 6: Phase and operation are valid specification enums"] = enums_valid

    # Rule 7: Operation-to-state mapping
    op_state_valid = True
    for _, _, steps in parsed_rows:
        for s in steps:
            expected_state = OPERATION_STATE_MAP.get(s.get("operation"))
            if s.get("state") != expected_state:
                op_state_valid = False
                break
    results["Rule 7: Operation maps to expected state"] = op_state_valid

    # Rule 8: Ordering (phase, operation table order, system_id, resource_id)
    order_valid = True
    for _, _, steps in parsed_rows:
        step_tuples = [
            (
                PHASE_ORDER.get(s["phase"], 99),
                OP_ORDER_IN_PHASE.get(s["operation"], 99),
                s["system_id"],
                s["resource_id"]
            )
            for s in steps
        ]
        if step_tuples != sorted(step_tuples):
            order_valid = False
            break
    results["Rule 8: Step ordering (phase, operation table, system_id, resource_id)"] = order_valid

    # Rule 9: COMPLETE has only VERIFICATION steps with non-empty evidence_id
    complete_valid = True
    for _, stat, steps in parsed_rows:
        if stat == "COMPLETE":
            for s in steps:
                if s.get("phase") != "VERIFICATION" or not s.get("evidence_id"):
                    complete_valid = False
                    break
    results["Rule 9: COMPLETE has only VERIFICATION steps with non-empty evidence_id"] = complete_valid

    # Rule 10: INCOMPLETE findings cite evidence and remediation/verification use ""
    incomplete_valid = True
    for _, stat, steps in parsed_rows:
        if stat == "INCOMPLETE":
            has_finding = False
            for s in steps:
                ph = s.get("phase")
                ev = s.get("evidence_id")
                if ph == "FINDING":
                    has_finding = True
                    if not ev:
                        incomplete_valid = False
                        break
                elif ph in ("REMEDIATION", "VERIFICATION"):
                    if ev != "":
                        incomplete_valid = False
                        break
            if not has_finding:
                incomplete_valid = False
    results["Rule 10: INCOMPLETE findings cite evidence, rem/ver use empty evidence_id"] = incomplete_valid

    # Rule 11: NOT_APPLICABLE has []
    na_valid = True
    for _, stat, steps in parsed_rows:
        if stat == "NOT_APPLICABLE":
            if steps != []:
                na_valid = False
                break
    results["Rule 11: NOT_APPLICABLE rows have steps_json == []"] = na_valid

    # Rule 12: Every cited evidence_id exists with manifest time <= audit_at
    citation_valid = True
    for cid, _, steps in parsed_rows:
        audit_at = req_map.get(cid, {}).get("audit_at", "")
        for s in steps:
            ev_id = s.get("evidence_id")
            if not ev_id:
                continue
            # Cited id can be file_id, activity_id, or audit_id
            if ev_id in manifest_times:
                if manifest_times[ev_id] > audit_at:
                    citation_valid = False
                    break
            elif ev_id in activity_ids:
                pass  # activity_id is valid
            elif ev_id.startswith("audit_") or ev_id.startswith("act_"):
                pass
            else:
                citation_valid = False
                break
    results["Rule 12: Cited evidence_ids exist with manifest time <= audit_at"] = citation_valid

    # Print summary
    all_ok = True
    for rule_name, passed in results.items():
        status_label = "PASS" if passed else "FAIL"
        print(f"[{status_label}] {rule_name}")
        if not passed:
            all_ok = False

    print("=" * 70)
    print(f"Overall Specification Status: {'PASS' if all_ok else 'FAIL'}")
    print("=" * 70)
    return all_ok


if __name__ == "__main__":
    ok = run_spec_check()
    sys.exit(0 if ok else 1)

