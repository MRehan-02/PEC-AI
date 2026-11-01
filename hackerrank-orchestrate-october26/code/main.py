"""
Ghost Data Still Exists - Main Pipeline Runner
Processes deletion requests from dataset/deletion_requests.csv,
computes audit decisions and remediation/verification steps,
and generates output.csv at the repository root.
"""

import os
import sys
import csv
import json
from collections import Counter

# Ensure repository root is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from code.loader import load_all_evidence
from code.identity import IdentityStore
from code.linker import Linker
from code.state import evaluate_case


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def run_pipeline(
    dataset_dir: str = None,
    requests_csv: str = None,
    output_csv: str = None
):
    if dataset_dir is None:
        dataset_dir = os.path.join(REPO_ROOT, "dataset")
    elif not os.path.isabs(dataset_dir):
        dataset_dir = os.path.abspath(dataset_dir) if os.path.exists(dataset_dir) else os.path.join(REPO_ROOT, dataset_dir)

    if requests_csv is None:
        requests_csv = os.path.join(dataset_dir, "deletion_requests.csv")
    elif not os.path.isabs(requests_csv):
        requests_csv = os.path.abspath(requests_csv) if os.path.exists(requests_csv) else os.path.join(REPO_ROOT, requests_csv)

    if output_csv is None:
        output_csv = os.path.join(REPO_ROOT, "output.csv")
    elif not os.path.isabs(output_csv):
        output_csv = os.path.abspath(output_csv)
    print("Initializing Ghost Data Still Exists pipeline...")
    all_obs, scopes = load_all_evidence(
        manifest_path=os.path.join(dataset_dir, "evidence_files.csv"),
        dataset_dir=dataset_dir
    )
    identity_store = IdentityStore(dataset_dir=dataset_dir)
    linker = Linker(dataset_dir=dataset_dir)
    linker.build_links(identity_store, all_obs)

    with open(requests_csv, "r", encoding="utf-8") as f:
        cases = list(csv.DictReader(f))

    # Sort cases by case_id
    cases.sort(key=lambda r: r["case_id"])

    results = []
    status_counts = Counter()

    for r in cases:
        cid = r["case_id"]
        em = r["email"].strip().lower()
        user = identity_store.resolve_user(em)

        status, steps = evaluate_case(
            case_row=r,
            user=user,
            all_obs=all_obs,
            scopes=scopes,
            linker=linker
        )
        status_counts[status] += 1
        steps_json = json.dumps(steps, separators=(",", ":"))
        results.append((cid, status, steps_json))

    # Write output.csv
    with open(output_csv, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["case_id", "deletion_status", "steps_json"])
        for row in results:
            writer.writerow(row)

    print("\nPipeline execution complete:")
    print(f"Total requests processed: {len(results)}")
    for status, cnt in sorted(status_counts.items()):
        print(f"  {status:18s}: {cnt}")
    print(f"Output saved to: {output_csv}")

    return status_counts


if __name__ == "__main__":
    run_pipeline()
