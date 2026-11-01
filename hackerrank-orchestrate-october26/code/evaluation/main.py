"""
Ghost Data Still Exists - Evaluation Script
Runs evaluation pipeline on sample_deletion_requests.csv and measures accuracy
against the public benchmark sample_output.csv.
"""

import os
import sys
import csv
import json

# Ensure repository root is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from code.loader import load_all_evidence
from code.identity import IdentityStore
from code.linker import Linker
from code.state import evaluate_case


def lcs_length(seq1, seq2):
    m, n = len(seq1), len(seq2)
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(m):
        for j in range(n):
            if seq1[i] == seq2[j]:
                dp[i + 1][j + 1] = dp[i][j] + 1
            else:
                dp[i + 1][j + 1] = max(dp[i + 1][j], dp[i][j + 1])
    return dp[m][n]


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))


def run_evaluation(
    dataset_dir: str = None,
    sample_requests_csv: str = None,
    sample_output_csv: str = None,
    diff_report_path: str = None
):
    if dataset_dir is None:
        dataset_dir = os.path.join(REPO_ROOT, "dataset")
    elif not os.path.isabs(dataset_dir):
        dataset_dir = os.path.abspath(dataset_dir) if os.path.exists(dataset_dir) else os.path.join(REPO_ROOT, dataset_dir)

    if sample_requests_csv is None:
        sample_requests_csv = os.path.join(dataset_dir, "sample_deletion_requests.csv")
    elif not os.path.isabs(sample_requests_csv):
        sample_requests_csv = os.path.abspath(sample_requests_csv) if os.path.exists(sample_requests_csv) else os.path.join(REPO_ROOT, sample_requests_csv)

    if sample_output_csv is None:
        sample_output_csv = os.path.join(dataset_dir, "sample_output.csv")
    elif not os.path.isabs(sample_output_csv):
        sample_output_csv = os.path.abspath(sample_output_csv) if os.path.exists(sample_output_csv) else os.path.join(REPO_ROOT, sample_output_csv)

    if diff_report_path is None:
        diff_report_path = os.path.join(os.path.dirname(__file__), "diff_report.txt")
    elif not os.path.isabs(diff_report_path):
        diff_report_path = os.path.abspath(diff_report_path)
    print("=" * 70)
    print("Ghost Data Still Exists - Benchmark Evaluation")
    print("=" * 70)

    all_obs, scopes = load_all_evidence(
        manifest_path=os.path.join(dataset_dir, "evidence_files.csv"),
        dataset_dir=dataset_dir
    )
    identity_store = IdentityStore(dataset_dir=dataset_dir)
    linker = Linker(dataset_dir=dataset_dir)
    linker.build_links(identity_store, all_obs)

    with open(sample_requests_csv, "r", encoding="utf-8") as f:
        reqs = list(csv.DictReader(f))

    with open(sample_output_csv, "r", encoding="utf-8") as f:
        ground_truth = {r["case_id"]: r for r in csv.DictReader(f)}

    status_correct = 0
    row_exact_matches = 0
    total_expected_steps = 0
    matched_steps = 0
    seq_acc_list = []

    mismatch_counts = {
        "wrong evidence_id": 0,
        "missing step": 0,
        "extra step": 0,
        "wrong operation for the same resource": 0,
        "wrong order": 0,
        "wrong status": 0
    }

    diff_blocks = []

    for r in reqs:
        cid = r["case_id"]
        em = r["email"].strip().lower()
        user = identity_store.resolve_user(em)

        pred_status, pred_steps = evaluate_case(
            case_row=r,
            user=user,
            all_obs=all_obs,
            scopes=scopes,
            linker=linker
        )

        gt = ground_truth.get(cid)
        if not gt:
            continue

        gt_status = gt["deletion_status"]
        gt_steps = json.loads(gt["steps_json"])

        # 1. Status check
        if pred_status == gt_status:
            status_correct += 1
        else:
            mismatch_counts["wrong status"] += 1

        # 2. Step tuples
        total_expected_steps += len(gt_steps)
        gt_5t = [
            (s.get("phase", ""), s.get("operation", ""), s.get("system_id", ""), s.get("resource_id", ""), s.get("evidence_id", ""))
            for s in gt_steps
        ]
        pred_5t = [
            (s.get("phase", ""), s.get("operation", ""), s.get("system_id", ""), s.get("resource_id", ""), s.get("evidence_id", ""))
            for s in pred_steps
        ]

        gt_4t = [t[:4] for t in gt_5t]
        pred_4t = [t[:4] for t in pred_5t]

        # Matched steps (for Step-Level Field Accuracy)
        for gt_t in gt_4t:
            if gt_t in pred_4t:
                matched_steps += 1

        # Sequence Accuracy (LCS on 4-tuples divided by expected step count)
        if len(gt_4t) == 0:
            row_seq = 1.0 if len(pred_4t) == 0 else 0.0
        else:
            row_seq = lcs_length(gt_4t, pred_4t) / len(gt_4t)
        seq_acc_list.append(row_seq)

        # Exact row match
        is_exact = (pred_status == gt_status) and (gt_5t == pred_5t)
        if is_exact:
            row_exact_matches += 1

        # Order mismatches: count wrong order only for steps present in both lists whose relative order differs
        common_gt = [s for s in gt_5t if s in pred_5t]
        common_pred = [s for s in pred_5t if s in common_gt]
        for i in range(len(common_gt)):
            if i < len(common_pred) and common_gt[i] != common_pred[i]:
                mismatch_counts["wrong order"] += 1

        # Mismatch kind categorization (Pass 1: exact, Pass 2: wrong evidence_id, Pass 3: wrong op, Pass 4: missing/extra)
        matched_g = set()
        matched_p = set()

        for gi, gs in enumerate(gt_5t):
            for pi, ps in enumerate(pred_5t):
                if pi not in matched_p and gs == ps:
                    matched_g.add(gi)
                    matched_p.add(pi)
                    break

        for gi, gs in enumerate(gt_5t):
            if gi in matched_g:
                continue
            for pi, ps in enumerate(pred_5t):
                if pi not in matched_p and gs[:4] == ps[:4]:
                    matched_g.add(gi)
                    matched_p.add(pi)
                    mismatch_counts["wrong evidence_id"] += 1
                    break

        for gi, gs in enumerate(gt_5t):
            if gi in matched_g:
                continue
            g_res = (gs[0], gs[2], gs[3])  # (phase, system_id, resource_id)
            for pi, ps in enumerate(pred_5t):
                if pi not in matched_p and (ps[0], ps[2], ps[3]) == g_res:
                    matched_g.add(gi)
                    matched_p.add(pi)
                    mismatch_counts["wrong operation for the same resource"] += 1
                    break

        for gi in range(len(gt_5t)):
            if gi not in matched_g:
                mismatch_counts["missing step"] += 1

        for pi in range(len(pred_5t)):
            if pi not in matched_p:
                mismatch_counts["extra step"] += 1

        # Build diff block if not an exact match
        if not is_exact:
            block = []
            block.append(f"case_id: {cid}")
            block.append(f"expected status: {gt_status}")
            block.append(f"predicted status: {pred_status}")
            block.append("expected steps:")
            for idx, s in enumerate(gt_5t):
                if s in pred_5t:
                    tag = "OK" if pred_5t.index(s) == idx else "ORDER"
                else:
                    tag = "MISSING"
                block.append(f"  [{tag}] ({s[0]}, {s[1]}, {s[2]}, {s[3]}, {s[4]})")
            block.append("predicted steps:")
            for idx, s in enumerate(pred_5t):
                if s in gt_5t:
                    tag = "OK" if gt_5t.index(s) == idx else "ORDER"
                else:
                    tag = "EXTRA"
                block.append(f"  [{tag}] ({s[0]}, {s[1]}, {s[2]}, {s[3]}, {s[4]})")
            diff_blocks.append("\n".join(block))

    # Write diff_report.txt
    os.makedirs(os.path.dirname(diff_report_path), exist_ok=True)
    with open(diff_report_path, "w", encoding="utf-8") as f:
        f.write("\n\n".join(diff_blocks) + "\n")

    n = len(reqs)
    status_acc = (status_correct / n) * 100 if n else 0.0
    row_exact_acc = (row_exact_matches / n) * 100 if n else 0.0
    step_acc = (matched_steps / total_expected_steps) * 100 if total_expected_steps else 0.0
    seq_acc = (sum(seq_acc_list) / len(seq_acc_list)) * 100 if seq_acc_list else 0.0

    print("\nBenchmark Evaluation Results:")
    print(f"Total Sample Cases:         {n}")
    print(f"Deletion Status Accuracy:   {status_correct}/{n} ({status_acc:.1f}%)")
    print(f"Row Exact Match Accuracy:   {row_exact_matches}/{n} ({row_exact_acc:.1f}%)")
    print(f"Step-Level Field Accuracy:  {matched_steps}/{total_expected_steps} ({step_acc:.1f}%)")
    print(f"Sequence Accuracy:          {seq_acc:.1f}%")

    # Print first 8 mismatching cases to screen
    print(f"\nDiff Report (First 8 Mismatching Cases of {len(diff_blocks)} written to {diff_report_path}):")
    print("=" * 70)
    for block in diff_blocks[:8]:
        print(block)
        print("-" * 50)

    # Print count table of mismatch kinds
    print("\nMismatch Kind Counts Across All Rows:")
    print("+---------------------------------------+-------+")
    print("| Mismatch Kind                         | Count |")
    print("+---------------------------------------+-------+")
    print(f"| wrong evidence_id                     | {mismatch_counts['wrong evidence_id']:>5} |")
    print(f"| missing step                          | {mismatch_counts['missing step']:>5} |")
    print(f"| extra step                            | {mismatch_counts['extra step']:>5} |")
    print(f"| wrong operation for the same resource | {mismatch_counts['wrong operation for the same resource']:>5} |")
    print(f"| wrong order                           | {mismatch_counts['wrong order']:>5} |")
    print(f"| wrong status                          | {mismatch_counts['wrong status']:>5} |")
    print("+---------------------------------------+-------+")
    print("=" * 70)

    return status_acc, row_exact_acc, step_acc, seq_acc


if __name__ == "__main__":
    run_evaluation()
