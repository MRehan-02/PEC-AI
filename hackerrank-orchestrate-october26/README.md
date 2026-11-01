# HackerRank Orchestrate (October 2026) — Ghost Data Still Exists

This repository implements an autonomous, audit-grade deletion-verification agent that investigates whether personal data genuinely ceased to exist at the `audit_at` cutoff across multi-system commercial stores (databases, SQL replicas, application logs, attachments, JSON ledgers, search indexes, analytics platforms, backups, and data flow pipelines).

---

## 1. Environment & Setup

- **Python Version**: Requires **Python 3.12**.
- **Repository Structure**: The `dataset/` directory must be placed at the repository root alongside `code/`.

```bash
# 1. Create a virtual environment
python -m venv .venv

# 2. Activate the virtual environment
# Windows (PowerShell):
.venv\Scripts\Activate.ps1
# Windows (cmd):
.venv\Scripts\activate.bat
# Linux / macOS:
source .venv/bin/activate

# 3. Install dependencies
pip install -r code/requirements.txt
```

### Optional Environment Variables (.env)
Running the final pipeline uses pre-cached image extractions (`code/cache/images/*.json`) and classification results (`code/cache/classify_cache.json`) with deterministic rule-based fallbacks, requiring **no network access and zero API keys**. 
If you wish to re-extract screenshots or re-query Gemini for ambiguous text classification, provide an optional `.env` file at the repository root:
```env
GEMINI_API_KEY="your-api-key"
GEMINI_MODEL="gemini-2.5-flash"
```

---

## 2. Execution Commands

Execute commands from the repository root:

1. **Run Full Pipeline** (produces `output.csv` at repo root for all 518 evaluation requests):
   ```bash
   python code/main.py
   ```
2. **Run Benchmark Evaluation** (evaluates against `dataset/sample_output.csv`):
   ```bash
   python code/evaluation/main.py
   ```
3. **Run Contract Validator** (validates structure, headers, sorting, and row format):
   ```bash
   python code/validate.py
   ```
4. **Run Specification Checker** (validates against all business rules and invariants):
   ```bash
   python code/evaluation/spec_check.py
   ```
5. **Run Custom Test Cases** (runs 3 custom synthetic scenario checks):
   ```bash
   python code/evaluation/custom_cases.py
   ```

---

## 3. Pipeline Description

1. **Identity Resolution (`code/identity.py`)**: Reconstructs current and historical identity profiles (emails, hashes, phones, user references, linked resources) from `user_identities.csv`, `user_audit_log.csv`, and activity links.
2. **Intent Classification (`code/classify.py`)**: Distinguishes affirmative erasure/deletion requests from non-deletion requests (access, export, login reset, marketing opt-out); non-deletions yield `NOT_APPLICABLE` with `steps_json=[]`.
3. **Evidence Loading (`code/loader.py`)**: Ingests SQL snapshots (with primary vs replica drift detection), structured logs (grouped by trace), JSON data, PDFs, TXTs, and screenshots, tracking manifest capture time vs in-file event time.
4. **Activity Linking (`code/linker.py`)**: Resolves account and analytics resources using a 3-tier hierarchy: exact identifier match, unique candidate match via time window and status consistency, or ambiguous.
5. **State Reconstruction (`code/state.py`)**: Determines resource compliance at `audit_at` where the latest in-file event observation governs state, citing the latest manifest file captured on or before `audit_at`.
6. **Remediation & Verification Planning (`code/steps.py`)**: Generates safe, dependency-ordered plans (disable flows -> block restores -> delete/redact/anonymize records) for `INCOMPLETE` cases, or verification checks for `COMPLETE` cases.
7. **Step Building & Ordering (`code/steps.py`)**: Enforces consecutive 1-indexed numbering and strict ordering (`FINDING` -> `REMEDIATION` -> `VERIFICATION`, ordered by operation table and resource ID).
8. **Validation & Integrity Checks (`code/validate.py`, `code/evaluation/spec_check.py`)**: Validates 518 sorted rows, valid JSON serialization, schema constraints, and temporal/provenance invariants.

---

## 4. Known Limitations

- **Ambiguous Links Omitted**: In accordance with specification, ambiguous account and analytics link candidates are omitted rather than guessed with synthetic numbering, and each decision is logged to `code/cache/link_decisions.jsonl`.
- **Missing Dataset IDs**: A small number of expected ground-truth resource IDs exist nowhere in the provided participant evidence or global activity stream.
- **Inferred Activity Relationships**: Candidate activity linkages based on time windows and status consistency are inferences derived and verified against the public sample benchmark.
