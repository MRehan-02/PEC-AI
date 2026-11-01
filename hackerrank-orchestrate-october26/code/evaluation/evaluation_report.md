# Evaluation Report: Ghost Data Still Exists Deletion-Audit Agent

**HackerRank Orchestrate (October 2026)**  
**Submission**: Autonomous Deletion-Audit Pipeline  

---

## 1. Executive Summary

The solution implements an autonomous, audit-grade deletion verification system that reconstructs user identities, analyzes multi-system evidence (databases, SQL replicas, logs, PDFs, text records, and screenshots), applies statutory deletion rules, and generates dependency-ordered remediation and verification plans.

On the official public benchmark (`dataset/sample_deletion_requests.csv` against `dataset/sample_output.csv`):
- **Case-Level Deletion-Status Accuracy**: **100.0%** (32 / 32 cases)
- **Row Exact Match Accuracy**: **65.6%** (21 / 32 cases)
- **Step-Level Field Accuracy**: **93.3%** (180 / 193 step fields)
- **Sequence Accuracy (LCS Normalized)**: **90.4%**
- **Wrong Evidence IDs**: **0**
- **Extra Steps**: **0**
- **Wrong Statuses**: **0**

---

## 2. Benchmark Evaluation Metrics

| Metric | Measured Value | Benchmark Target / Notes |
|---|---|---|
| **Deletion Status Accuracy** | **100.0% (32/32)** | Perfect classification across all sample cases |
| **Row Exact Match Accuracy** | **65.6% (21/32)** | Complete step-for-step identity with ground truth |
| **Step-Level Field Accuracy** | **93.3% (180/193)** | Exact match across phase, operation, system_id, resource_id, evidence_id |
| **Sequence Accuracy (LCS)** | **90.4%** | Longest Common Subsequence normalized alignment |
| **Wrong Evidence Citations** | **0** | Strict cutoff at `audit_at` ensures 100% temporal validity |
| **Extra Steps Generated** | **0** | Zero false-positive hallucinated resources |

### Mismatch Breakdown Across All 32 Sample Cases:
| Mismatch Kind | Count | Root Cause Analysis |
|---|---|---|
| Wrong `evidence_id` | **0** | Every cited file existed and was captured on or before `audit_at` |
| Missing steps | **13** | Ground-truth citations for ambiguous/null analytics or unindexed resources |
| Extra steps | **0** | No spurious or ungrounded steps added |
| Wrong operation for same resource | **0** | Perfect rule-to-operation mapping |
| Wrong order | **20** | Ground truth variations in parallel verification ordering |
| Wrong status | **0** | 100% agreement on `COMPLETE` vs `INCOMPLETE` vs `NOT_APPLICABLE` |

---

## 3. Custom Evaluation Cases (`code/evaluation/custom_cases.py`)

Three comprehensive synthetic test scenarios were developed to verify core specification requirements:

1. **Scenario 1: Historical Email Resolution (`custom_case_01`)** — **[PASS]**
   - *Test Objective*: Validate that a deletion request submitted using an obsolete historical email (changed in `user_audit_log.csv`) successfully maps to the subject's stable `user_id`.
   - *Outcome*: Successfully resolved to canonical profile and evaluated state.

2. **Scenario 2: Non-Deletion Information Request (`custom_case_02`)** — **[PASS]**
   - *Test Objective*: Validate that an incoming request asking for an invoice copy with explicit instructions to "keep my profile and account active" is recognized as non-deletion.
   - *Outcome*: Correctly classified as `NOT_DELETE`, assigning status `NOT_APPLICABLE` with empty `steps_json = []`.

3. **Scenario 3: Tombstone With Exposed PII (`custom_case_03`)** — **[PASS]**
   - *Test Objective*: Validate that an account tombstone in `identity_service` that still retains direct identifiers (email/name) triggers required remediation.
   - *Outcome*: Generated `REPORT_EXPOSED_PII` finding, `REDACT_RECORD` remediation, and `VERIFY_RECORD_REDACTED` verification, yielding status `INCOMPLETE`.

---

## 4. Failures Found & Improvements Made

1. **Replication Drift In SQL Snapshots**:
   - *Failure*: Primary database tables showed customer profiles as absent, but failover replica tables still contained un-purged profile records.
   - *Improvement*: Enhanced `code/loader.py` to parse both primary and replica tables within SQLite in-memory snapshots, comparing record sets to detect drift and emit `REPORT_REMAINING_RECORD`.

2. **Trace Identifier Disconnection In Structured Logs**:
   - *Failure*: In asynchronous log streams, initial log lines contained request identifiers (`lookup_key`, `email_hash`, `user_ref`) while subsequent completion lines contained `resource_id` without inline identity tags.
   - *Improvement*: Implemented trace-level grouping in `code/loader.py`, propagating all identifiers discovered across any line of a trace to all completion events sharing that `trace_id`.

3. **Multi-lingual Affirmative vs Negation Intent**:
   - *Failure*: Keyword matching triggered false positives on requests asking to keep accounts while stopping marketing emails, or asking for invoice archives.
   - *Improvement*: Implemented a fast-path classifier in `code/classify.py` with multi-lingual affirmative keywords paired with negation and retention cues, routing genuine non-deletions to `NOT_APPLICABLE`.

4. **Multi-Request Candidate Ambiguity (Account Linking)**:
   - *Failure*: Global deletion activity candidate windows contained multiple candidate rows for several deletion cases.
   - *Improvement*: Introduced candidate consistency filtering in `code/linker.py` ensuring activity `reported_result` matches request `reported_status` (`COMPLETE -> SUCCESS`, `NOT_STARTED -> FAILED`, `PARTIALLY_COMPLETE -> PARTIAL/SUCCESS`). This increased uniquely resolved account links from 44 to 151 and improved Row Exact Match accuracy from 56.2% to 65.6%.

---

## 5. Known Limitations

- **Ambiguous Links Omitted by Design**: As specified in the challenge instructions, ambiguous account and analytics candidates are omitted rather than guessed with synthetic numbering conventions; all decisions are audited in `code/cache/link_decisions.jsonl`.
- **Missing Dataset Resources**: Certain ground-truth verification steps in the sample reference IDs that do not appear anywhere in participant evidence files or activity streams.
- **Activity Stream Inferences**: Activity linking relies on timestamp offsets and status-result consistency inferred and validated against the public sample benchmark.
