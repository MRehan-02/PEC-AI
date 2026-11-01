# Token & Usage Report: Ghost Data Still Exists Deletion-Audit Agent

**HackerRank Orchestrate (October 2026)**  
**Submission**: Autonomous Deletion-Audit Pipeline  

---

## 1. Executive Summary

The production execution of the final pipeline (`python code/main.py`) processes all 518 evaluation requests with **0 model calls**, consuming **0 tokens** and incurring **$0.00 cost**, operating entirely as a deterministic, offline audit system.

All multimodal screenshot extractions (`code/cache/images/*.json`) and intent classifications (`code/cache/classify_cache.json`) were generated during the solution preparation phase using Google Gemini, cached locally to guarantee reproducibility and prevent runtime network failures.

---

## 2. Model Usage Breakdown (Offline Cache Generation Phase)

Logged from `code/cache/usage.jsonl`:

| Metric | Google Gemini |
|---|---|
| **Model Provider** | Google |
| **Model Name** | `gemini-3.5-flash` |
| **Model Call Count** | 19 calls |
| **Input Tokens** | 15,857 |
| **Output Tokens** | 4,258 |
| **Total Tokens** | 20,115 |
| **Estimated Total Cost** | **$0.0025** (< $0.01) |

### Pricing Assumptions
Based on published Gemini 2.5/3.5 Flash standard tier rates:
- **Input Prompt Tokens**: $0.075 per 1,000,000 tokens ($0.000000075 / token)
- **Output Candidate Tokens**: $0.300 per 1,000,000 tokens ($0.000000300 / token)
- **Calculation**:
  $$\text{Input Cost} = 15,857 \times \$0.000000075 = \$0.001189$$
  $$\text{Output Cost} = 4,258 \times \$0.000000300 = \$0.001277$$
  $$\text{Total Cost} = \$0.001189 + \$0.001277 = \$0.002466 \approx \$0.0025$$

---

## 3. Average Per-Request Statistics (Normalized Over 518 Requests)

| Metric | Offline Preparation (Amortized) | Production Execution (`python code/main.py`) |
|---|---|---|
| **Model Calls / Request** | 0.036 calls / request | **0 calls / request** |
| **Tokens / Request** | 38.8 tokens / request | **0 tokens / request** |
| **Estimated Cost / Request** | $0.00000476 / request | **$0.00 / request** |

---

## 4. Cache Provenance & Generation Calls

The 19 preparation calls recorded in `code/cache/usage.jsonl` produced the persistent cache files utilized by the pipeline:

1. **Multimodal Screenshot Extraction (11 calls)**:
   - Extracted visual text, user IDs, resource IDs, and toggle statuses from PNG screenshots:
     - `image_extract_evidence_04`: Evidence 04 screenshot
     - `image_extract_evidence_09`: Evidence 09 screenshot
     - `image_extract_evidence_14`: Evidence 14 screenshot
     - `image_extract_evidence_19`: Evidence 19 screenshot
     - `image_extract_evidence_23`: Evidence 23 screenshot
     - `image_extract_evidence_28`: Evidence 28 screenshot
     - `image_extract_evidence_32`: Evidence 32 screenshot
     - `image_extract_evidence_37`: Evidence 37 screenshot
     - `image_extract_evidence_42`: Evidence 42 screenshot
     - `image_extract_evidence_46`: Evidence 46 screenshot
     - `image_extract_evidence_60`: Evidence 60 screenshot
   - Saved to `code/cache/images/evidence_XX.json`.

2. **Multilingual Intent Classification (7 calls)**:
   - Evaluated customer request texts with ambiguous phrasing or negation markers:
     - 7 classification queries logged to `code/cache/usage.jsonl`
   - Saved to `code/cache/classify_cache.json`.

3. **Smoke Test (1 call)**:
   - Initial verification call for connectivity and schema validation (`image_extract`).
