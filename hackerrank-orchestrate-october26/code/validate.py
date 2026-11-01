"""
Ghost Data Still Exists - Output Validator
Validates output.csv format, row count, sorting, step numbering, and compliance.
"""

import os
import sys
import csv
import json


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def validate_output(
    output_path: str = None,
    requests_path: str = None
):
    if output_path is None:
        output_path = os.path.join(REPO_ROOT, "output.csv")
    elif not os.path.isabs(output_path):
        output_path = os.path.abspath(output_path) if os.path.exists(output_path) else os.path.join(REPO_ROOT, output_path)

    if requests_path is None:
        requests_path = os.path.join(REPO_ROOT, "dataset", "deletion_requests.csv")
    elif not os.path.isabs(requests_path):
        requests_path = os.path.abspath(requests_path) if os.path.exists(requests_path) else os.path.join(REPO_ROOT, requests_path)

    print("=" * 70)
    print("Validating output submission file...")
    print("=" * 70)

    if not os.path.exists(output_path):
        print(f"FAILED: Output file {output_path} does not exist!")
        sys.exit(1)

    if not os.path.exists(requests_path):
        print(f"FAILED: Reference file {requests_path} does not exist!")
        sys.exit(1)

    with open(requests_path, "r", encoding="utf-8") as f:
        req_rows = list(csv.DictReader(f))
    expected_count = len(req_rows)

    with open(output_path, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader, None)

        if header != ["case_id", "deletion_status", "steps_json"]:
            print(f"FAILED: Header mismatch! Expected ['case_id', 'deletion_status', 'steps_json'], got {header}")
            sys.exit(1)

        rows = list(reader)

    # 1. Row count check
    if len(rows) != expected_count:
        print(f"FAILED: Row count mismatch! Expected {expected_count}, got {len(rows)}")
        sys.exit(1)

    # 2. Sorted check
    case_ids = [r[0] for r in rows]
    if case_ids != sorted(case_ids):
        print("FAILED: Rows are not sorted by case_id!")
        sys.exit(1)

    # 3. Row-level schema, valid JSON, step numbering, and NOT_APPLICABLE check
    for idx, (cid, status, steps_raw) in enumerate(rows, 1):
        if status not in ("COMPLETE", "INCOMPLETE", "NOT_APPLICABLE"):
            print(f"FAILED: Invalid deletion_status '{status}' at row {idx} ({cid})")
            sys.exit(1)

        try:
            steps = json.loads(steps_raw)
        except Exception as e:
            print(f"FAILED: Invalid JSON at row {idx} ({cid}): {e}")
            sys.exit(1)

        if not isinstance(steps, list):
            print(f"FAILED: steps_json is not an array at row {idx} ({cid})")
            sys.exit(1)

        if status == "NOT_APPLICABLE" and steps != []:
            print(f"FAILED: NOT_APPLICABLE case {cid} has non-empty steps_json: {steps}")
            sys.exit(1)

        # Check consecutive step numbering
        for step_idx, st in enumerate(steps, 1):
            if st.get("step_no") != step_idx:
                print(f"FAILED: Step numbering error in {cid} step {step_idx}: got {st.get('step_no')}")
                sys.exit(1)

    print(f"SUCCESS: {output_path} passed all validation checks!")
    print(f"  - Rows: {len(rows)} (matches {expected_count} deletion requests)")
    print("  - Header: case_id,deletion_status,steps_json")
    print("  - Sorted by case_id: YES")
    print("  - Valid JSON and consecutive step numbers: YES")
    print("  - NOT_APPLICABLE steps_json == []: YES")
    print("=" * 70)


if __name__ == "__main__":
    validate_output()
