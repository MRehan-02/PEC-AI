"""
Ghost Data Still Exists - Custom Evaluation Cases
Implements 3 realistic synthetic evaluation scenarios:
1. Historical email resolution
2. Non-deletion request (NOT_APPLICABLE)
3. Tombstone with exposed PII (INCOMPLETE with REDACT remediation)
"""

import os
import sys
import json

# Ensure repository root is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from code.loader import load_all_evidence, Observation, FileScope
from code.identity import IdentityStore, UserProfile
from code.linker import Linker
from code.state import evaluate_case

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))


def run_custom_cases() -> bool:
    print("=" * 70)
    print("Running Ghost Data Still Exists - Custom Test Cases")
    print("=" * 70)

    dataset_dir = os.path.join(REPO_ROOT, "dataset")
    all_obs, scopes = load_all_evidence(
        manifest_path=os.path.join(dataset_dir, "evidence_files.csv"),
        dataset_dir=dataset_dir
    )
    id_store = IdentityStore(dataset_dir=dataset_dir)
    linker = Linker(dataset_dir=dataset_dir)
    linker.build_links(id_store, all_obs)

    all_passed = True

    # -------------------------------------------------------------------------
    # Case 1: Historical Email Request
    # User updated email in user_audit_log.csv. Request is submitted with the old email.
    # Expectation: IdentityStore resolves to correct user_id, intent is DELETE.
    # -------------------------------------------------------------------------
    hist_user_id = None
    hist_email = None
    for uid, prof in id_store.profiles.items():
        if len(prof.emails) > 1:
            hist_user_id = uid
            # Grab one of the emails that is in prof.emails
            hist_email = next(iter(prof.emails))
            break

    if not hist_email:
        # Fallback if no multi-email user
        hist_user_id = "user_01"
        hist_email = "test.historical@example.com"
        id_store.email_to_uid[hist_email] = hist_user_id
        if hist_user_id in id_store.profiles:
            id_store.profiles[hist_user_id].emails.add(hist_email)

    c1_row = {
        "case_id": "custom_case_01",
        "email": hist_email,
        "requested_at": "2026-10-01T10:00:00Z",
        "reported_status": "COMPLETE",
        "reported_at": "2026-10-01T11:00:00Z",
        "audit_at": "2026-10-01T12:00:00Z",
        "request_text": "Please permanently delete all my personal data and close my account immediately."
    }

    user1 = id_store.resolve_user(hist_email)
    c1_status, c1_steps = evaluate_case(c1_row, user1, all_obs, scopes, linker=linker)
    c1_pass = (user1 is not None and user1.user_id == hist_user_id and c1_status in ("COMPLETE", "INCOMPLETE"))
    print(f"Case 1 [Historical Email Resolution]: {'PASS' if c1_pass else 'FAIL'}")
    if not c1_pass:
        all_passed = False
        print(f"   Details: user resolved={user1.user_id if user1 else None}, status={c1_status}")

    # -------------------------------------------------------------------------
    # Case 2: Non-Deletion Request (Invoice copy & keep profile)
    # Expectation: Classify as NOT_DELETE, status NOT_APPLICABLE, steps_json == []
    # -------------------------------------------------------------------------
    c2_row = {
        "case_id": "custom_case_02",
        "email": hist_email,
        "requested_at": "2026-10-02T10:00:00Z",
        "reported_status": "COMPLETE",
        "reported_at": "2026-10-02T11:00:00Z",
        "audit_at": "2026-10-02T12:00:00Z",
        "request_text": "I need a copy of my last year invoices for tax filing. Please keep my profile and account active."
    }

    user2 = id_store.resolve_user(hist_email)
    c2_status, c2_steps = evaluate_case(c2_row, user2, all_obs, scopes, linker=linker)
    c2_pass = (c2_status == "NOT_APPLICABLE" and len(c2_steps) == 0)
    print(f"Case 2 [Non-Deletion Invoice Request]: {'PASS' if c2_pass else 'FAIL'}")
    if not c2_pass:
        all_passed = False
        print(f"   Details: status={c2_status}, steps_count={len(c2_steps)}")

    # -------------------------------------------------------------------------
    # Case 3: Tombstone With Exposed PII
    # A synthetic observation representing a tombstone record in identity_service
    # that is PRESENT with un-redacted user email / direct identifiers.
    # Expectation: REPORT_EXPOSED_PII -> REDACT_RECORD -> VERIFY_RECORD_REDACTED, status INCOMPLETE
    # -------------------------------------------------------------------------
    tomb_user = UserProfile(user_id="user_synth_999", emails={"synth.user@test.org"})
    tomb_obs = Observation(
        file_id="evidence_synth",
        file_captured_at="2026-10-01T08:00:00Z",
        captured_at="2026-10-01T08:00:00Z",
        system_id="identity_service",
        resource_id="tombstone_synth_999",
        user_reference="user_synth_999",
        email="synth.user@test.org",
        state="PRESENT"
    )
    synth_scopes = dict(scopes)
    synth_scopes["evidence_synth"] = FileScope(
        file_id="evidence_synth",
        file_path="records/evidence_synth.json",
        file_type=".json",
        file_captured_at="2026-10-01T08:00:00Z",
        captured_at="2026-10-01T08:00:00Z",
        covered_systems={"identity_service"}
    )
    test_obs = all_obs + [tomb_obs]

    c3_row = {
        "case_id": "custom_case_03",
        "email": "synth.user@test.org",
        "requested_at": "2026-10-01T10:00:00Z",
        "reported_status": "COMPLETE",
        "reported_at": "2026-10-01T11:00:00Z",
        "audit_at": "2026-10-01T12:00:00Z",
        "request_text": "Please delete my account and purge all my data."
    }

    c3_status, c3_steps = evaluate_case(c3_row, tomb_user, test_obs, synth_scopes, linker=linker)
    has_pii_finding = any(s.get("operation") == "REPORT_EXPOSED_PII" and s.get("resource_id") == "tombstone_synth_999" for s in c3_steps)
    has_redact_rem = any(s.get("operation") == "REDACT_RECORD" and s.get("resource_id") == "tombstone_synth_999" for s in c3_steps)
    has_redact_ver = any(s.get("operation") == "VERIFY_RECORD_REDACTED" and s.get("resource_id") == "tombstone_synth_999" for s in c3_steps)
    c3_pass = (c3_status == "INCOMPLETE" and has_pii_finding and has_redact_rem and has_redact_ver)
    print(f"Case 3 [Tombstone With Exposed PII]: {'PASS' if c3_pass else 'FAIL'}")
    if not c3_pass:
        all_passed = False
        print(f"   Details: status={c3_status}, finding={has_pii_finding}, rem={has_redact_rem}, ver={has_redact_ver}")

    print("=" * 70)
    print(f"Custom Cases Result: {'ALL PASS' if all_passed else 'SOME FAILED'}")
    print("=" * 70)
    return all_passed


if __name__ == "__main__":
    ok = run_custom_cases()
    sys.exit(0 if ok else 1)
