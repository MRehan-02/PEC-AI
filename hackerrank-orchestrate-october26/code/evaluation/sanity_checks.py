"""
Ghost Data Still Exists - Submission Sanity Checks
Validates output.csv against challenge-specific business and temporal rules:
(a) No COMPLETE row has finding or remediation steps;
(b) Every cited evidence_id exists in evidence_files.csv and manifest time <= audit_at;
(c) NOT_APPLICABLE rows have steps_json == [];
(d) Every INCOMPLETE row has at least one finding step;
(e) Prints status counts.
"""

import os
import sys
import csv
import json
from collections import Counter


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))


def run_sanity_checks(
    output_path: str = None,
    requests_path: str = None,
    manifest_path: str = None
) -> bool:
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

    print("=" * 70)
    print("Running Ghost Data Still Exists Sanity Checks...")
    print("=" * 70)

    if not os.path.exists(output_path):
        print(f"FAILED: Output file {output_path} not found!", file=sys.stderr)
        return False

    # 1. Load requests map: case_id -> audit_at
    case_audit_at = {}
    with open(requests_path, "r", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            case_audit_at[r["case_id"]] = r["audit_at"]

    # 2. Load manifest map: file_id -> captured_at
    manifest_times = {}
    with open(manifest_path, "r", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            manifest_times[r["file_id"]] = r["captured_at"]

    # 3. Read output.csv
    with open(output_path, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if header != ["case_id", "deletion_status", "steps_json"]:
            print(f"FAILED: Header mismatch! Got {header}", file=sys.stderr)
            return False
        rows = list(reader)

    status_counts = Counter()
    errors = []

    for idx, (cid, status, steps_raw) in enumerate(rows, 1):
        status_counts[status] += 1
        steps = json.loads(steps_raw)
        audit_at = case_audit_at.get(cid, "")

        # (a) No COMPLETE row has finding or remediation steps
        if status == "COMPLETE":
            for s in steps:
                phase = s.get("phase")
                if phase in ("FINDING", "REMEDIATION"):
                    errors.append(f"Row {idx} ({cid}): COMPLETE status has {phase} step: {s}")

        # (b) Every cited evidence_id exists in manifest and manifest time <= audit_at
        for s in steps:
            ev_id = s.get("evidence_id")
            if ev_id:
                if ev_id not in manifest_times:
                    errors.append(f"Row {idx} ({cid}): cited evidence_id '{ev_id}' not found in manifest!")
                else:
                    m_time = manifest_times[ev_id]
                    if m_time > audit_at:
                        errors.append(f"Row {idx} ({cid}): evidence '{ev_id}' manifest time ({m_time}) is after audit_at ({audit_at})!")

        # (c) NOT_APPLICABLE rows have []
        if status == "NOT_APPLICABLE":
            if steps != []:
                errors.append(f"Row {idx} ({cid}): NOT_APPLICABLE has non-empty steps: {steps}")

        # (d) Every INCOMPLETE row has at least one finding
        if status == "INCOMPLETE":
            findings = [s for s in steps if s.get("phase") == "FINDING"]
            if not findings:
                errors.append(f"Row {idx} ({cid}): INCOMPLETE status has 0 FINDING steps!")

    print(f"Total Rows Verified: {len(rows)}")
    print("\nStatus Counts:")
    for st, count in sorted(status_counts.items()):
        print(f"  {st:18s}: {count}")

    if errors:
        print(f"\nFAILED: Found {len(errors)} sanity check violation(s):", file=sys.stderr)
        for err in errors[:10]:
            print(f"  - {err}", file=sys.stderr)
        if len(errors) > 10:
            print(f"  ... and {len(errors) - 10} more errors", file=sys.stderr)
        return False

    print("\nSanity Checks Passed:")
    print("  [PASS] (a) No COMPLETE row has finding or remediation steps.")
    print("  [PASS] (b) Every cited evidence_id exists in manifest and manifest time <= audit_at.")
    print("  [PASS] (c) NOT_APPLICABLE rows have empty steps_json [].")
    print("  [PASS] (d) Every INCOMPLETE row has at least one finding step.")
    print("=" * 70)
    return True


if __name__ == "__main__":
    success = run_sanity_checks()
    if not success:
        sys.exit(1)

