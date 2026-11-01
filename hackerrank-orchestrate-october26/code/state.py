"""
Ghost Data Still Exists - State Evaluator
Evaluates the true compliance state of a deletion subject at audit_at cutoff.
"""

import re
from typing import List, Dict, Any, Tuple, Optional
from code.loader import Observation, FileScope
from code.identity import UserProfile
from code.classify import classify_request
from code.steps import sort_steps


def evaluate_case(
    case_row: Dict[str, str],
    user: Optional[UserProfile],
    all_obs: List[Observation],
    scopes: Dict[str, FileScope],
    activity_account_map: Optional[Dict[str, str]] = None,
    linker: Optional[Any] = None
) -> Tuple[str, List[Dict[str, Any]]]:
    """
    Evaluates a single deletion request row and returns (deletion_status, steps_list).
    """
    req_text = case_row.get("request_text", "")
    audit_at = case_row.get("audit_at", "")
    cid = case_row.get("case_id", "")

    # 1. Classification check
    intent = classify_request(req_text)
    if intent == "NOT_DELETE":
        return "NOT_APPLICABLE", []

    if not user:
        return "NOT_APPLICABLE", []

    # 2. Rule B.1 & B.2: Match observations to user using exact trimmed/lowercased matching
    # and discovery/state cutoff at in-file event time <= audit_at
    user_emails = {e.strip().lower() for e in user.emails if e}
    user_hashes = {h.strip().lower() for h in user.hashes if h}
    user_phones = {p.strip() for p in user.phones if p}
    user_ref = user.user_id.strip().lower()

    # Determine user's primary account resource_id and linked analytics record
    account_id = None
    linked_an_id = None
    if linker is not None:
        account_id = linker.get_account_id(cid)
        linked_an_id = linker.get_analytics_id(cid)
    else:
        # Search evidence matching user
        for o in all_obs:
            is_u = (
                (o.email and o.email.strip().lower() in user_emails) or
                (o.email_hash and o.email_hash.strip().lower() in user_hashes) or
                (o.phone and o.phone.strip() in user_phones) or
                (o.user_reference and o.user_reference.strip().lower() == user_ref)
            )
            if is_u and (o.system_id == "account_service" or o.resource_id.startswith("account_")):
                account_id = o.resource_id
                break

        # If none exists, use inferred naming convention
        if not account_id:
            m_num = re.search(r"\d+", user.user_id)
            if m_num:
                account_id = f"account_{int(m_num.group(0)):03d}"

    matched_obs: List[Observation] = []
    for o in all_obs:
        is_user = (
            (o.email and o.email.strip().lower() in user_emails) or
            (o.email_hash and o.email_hash.strip().lower() in user_hashes) or
            (o.phone and o.phone.strip() in user_phones) or
            (o.user_reference and o.user_reference.strip().lower() == user_ref) or
            (o.resource_id in user.resource_ids) or
            (account_id and o.resource_id == account_id) or
            (linked_an_id and o.resource_id == linked_an_id)
        )
        if not is_user:
            continue

        in_file_ts = o.captured_at if o.captured_at else o.file_captured_at
        if in_file_ts <= audit_at:
            matched_obs.append(o)

    # 3. Rule B.3: The newest observation by in-file time decides each resource's state;
    # a later absent/redacted/anonymized/blocked observation overrides an earlier present one.
    res_latest: Dict[str, Observation] = {}
    for o in sorted(matched_obs, key=lambda x: (x.captured_at or x.file_captured_at, x.file_captured_at)):
        res_latest[o.resource_id] = o

    # Helper: Rule B.2: The cited evidence_id is the latest file with manifest file_captured_at <= audit_at
    # that contains the record or covers its system. Never cite a file with manifest time after audit_at.
    def get_citable_evidence_id(resource_id: str, system_id: str) -> str:
        # Check files containing the record with manifest time <= audit_at
        cands = [o for o in all_obs if o.resource_id == resource_id and o.file_captured_at <= audit_at]
        if cands:
            return sorted(cands, key=lambda x: x.file_captured_at)[-1].file_id

        # Fallback to snapshots/files covering the system with manifest time <= audit_at
        cov = [s for s in scopes.values() if s.file_captured_at <= audit_at and system_id in s.covered_systems]
        if cov:
            return sorted(cov, key=lambda s: s.file_captured_at)[-1].file_id
        return ""

    findings = []
    remediations = []
    verifications = []

    # A. Account Service Resource
    if account_id:
        acc_obs = res_latest.get(account_id)
        if acc_obs and (acc_obs.state == "PRESENT" or acc_obs.is_drift):
            ev_id = get_citable_evidence_id(account_id, "account_service")
            findings.append({
                "phase": "FINDING",
                "system_id": "account_service",
                "resource_id": account_id,
                "operation": "REPORT_REMAINING_RECORD",
                "state": "PRESENT",
                "evidence_id": ev_id
            })
            remediations.append({
                "phase": "REMEDIATION",
                "system_id": "account_service",
                "resource_id": account_id,
                "operation": "DELETE_RECORD",
                "state": "ABSENT",
                "evidence_id": ""
            })
            verifications.append({
                "phase": "VERIFICATION",
                "system_id": "account_service",
                "resource_id": account_id,
                "operation": "VERIFY_RECORD_ABSENT",
                "state": "ABSENT",
                "evidence_id": ""
            })
        else:
            # Verified absent
            ev_id = get_citable_evidence_id(account_id, "account_service")
            verifications.append({
                "phase": "VERIFICATION",
                "system_id": "account_service",
                "resource_id": account_id,
                "operation": "VERIFY_RECORD_ABSENT",
                "state": "ABSENT",
                "evidence_id": ev_id
            })

    # B. Other Matched Resources
    for r_id, o in res_latest.items():
        if r_id == account_id:
            continue
        sys_id = o.system_id

        # Skip legal_service dispute orders (ORD-...) as they are not subject deletion targets
        if sys_id == "legal_service" or r_id.startswith("ord-"):
            continue

        ev_id = get_citable_evidence_id(r_id, sys_id)

        # 1. Rule B.4: Support platform
        # An attachment belonging only to the user -> REPORT_REMAINING_RECORD / DELETE_RECORD / VERIFY_RECORD_ABSENT.
        # A message with shared context -> REPORT_EXPOSED_PII / REDACT_RECORD / VERIFY_RECORD_REDACTED.
        if sys_id == "support_platform" or r_id.startswith("support_") or r_id.startswith("object_"):
            raw_str = str(o.raw_details or "").lower()
            rec_type = o.raw_details.get("record_type") if isinstance(o.raw_details, dict) else ""
            is_attachment = (
                r_id.startswith("object_") or
                rec_type == "SUPPORT_ATTACHMENT" or
                "attachment" in raw_str or
                "delivery_correction" in raw_str or
                "contact_query" in raw_str or
                "support_replica" in raw_str
            )
            if is_attachment:
                if o.state == "PRESENT" or o.is_drift:
                    findings.append({
                        "phase": "FINDING",
                        "system_id": sys_id,
                        "resource_id": r_id,
                        "operation": "REPORT_REMAINING_RECORD",
                        "state": "PRESENT",
                        "evidence_id": ev_id
                    })
                    remediations.append({
                        "phase": "REMEDIATION",
                        "system_id": sys_id,
                        "resource_id": r_id,
                        "operation": "DELETE_RECORD",
                        "state": "ABSENT",
                        "evidence_id": ""
                    })
                    verifications.append({
                        "phase": "VERIFICATION",
                        "system_id": sys_id,
                        "resource_id": r_id,
                        "operation": "VERIFY_RECORD_ABSENT",
                        "state": "ABSENT",
                        "evidence_id": ""
                    })
                elif o.state == "ABSENT":
                    verifications.append({
                        "phase": "VERIFICATION",
                        "system_id": sys_id,
                        "resource_id": r_id,
                        "operation": "VERIFY_RECORD_ABSENT",
                        "state": "ABSENT",
                        "evidence_id": ev_id
                    })
            else:
                # Message with shared context
                if o.state == "PRESENT":
                    findings.append({
                        "phase": "FINDING",
                        "system_id": sys_id,
                        "resource_id": r_id,
                        "operation": "REPORT_EXPOSED_PII",
                        "state": "PRESENT",
                        "evidence_id": ev_id
                    })
                    remediations.append({
                        "phase": "REMEDIATION",
                        "system_id": sys_id,
                        "resource_id": r_id,
                        "operation": "REDACT_RECORD",
                        "state": "REDACTED",
                        "evidence_id": ""
                    })
                    verifications.append({
                        "phase": "VERIFICATION",
                        "system_id": sys_id,
                        "resource_id": r_id,
                        "operation": "VERIFY_RECORD_REDACTED",
                        "state": "REDACTED",
                        "evidence_id": ""
                    })
                elif o.state == "REDACTED":
                    verifications.append({
                        "phase": "VERIFICATION",
                        "system_id": sys_id,
                        "resource_id": r_id,
                        "operation": "VERIFY_RECORD_REDACTED",
                        "state": "REDACTED",
                        "evidence_id": ev_id
                    })

        # 2. Rule B.5: Marketing / Contact records vs Analytics platform
        # Marketing/contact records -> REPORT_REMAINING_RECORD / DELETE_RECORD / VERIFY_RECORD_ABSENT.
        # Analytics profiles and telemetry still attributable -> ANONYMIZE.
        elif sys_id == "analytics_platform" or r_id.startswith("analytics_"):
            raw_str = str(o.raw_details or "").lower()
            evt = o.raw_details.get("event") if isinstance(o.raw_details, dict) else ""
            is_marketing_contact = (
                evt in ("contact_lookup_completed", "contact_lookup_started") or
                "contact_lookup" in raw_str or
                "email_opt_in" in raw_str or
                "marketing_profile" in raw_str
            )
            if is_marketing_contact:
                if o.state == "PRESENT" or o.is_drift:
                    findings.append({
                        "phase": "FINDING",
                        "system_id": sys_id,
                        "resource_id": r_id,
                        "operation": "REPORT_REMAINING_RECORD",
                        "state": "PRESENT",
                        "evidence_id": ev_id
                    })
                    remediations.append({
                        "phase": "REMEDIATION",
                        "system_id": sys_id,
                        "resource_id": r_id,
                        "operation": "DELETE_RECORD",
                        "state": "ABSENT",
                        "evidence_id": ""
                    })
                    verifications.append({
                        "phase": "VERIFICATION",
                        "system_id": sys_id,
                        "resource_id": r_id,
                        "operation": "VERIFY_RECORD_ABSENT",
                        "state": "ABSENT",
                        "evidence_id": ""
                    })
                elif o.state == "ABSENT":
                    verifications.append({
                        "phase": "VERIFICATION",
                        "system_id": sys_id,
                        "resource_id": r_id,
                        "operation": "VERIFY_RECORD_ABSENT",
                        "state": "ABSENT",
                        "evidence_id": ev_id
                    })
            else:
                # Analytics profile and product telemetry
                if o.state == "PRESENT":
                    findings.append({
                        "phase": "FINDING",
                        "system_id": sys_id,
                        "resource_id": r_id,
                        "operation": "REPORT_EXPOSED_PII",
                        "state": "PRESENT",
                        "evidence_id": ev_id
                    })
                    remediations.append({
                        "phase": "REMEDIATION",
                        "system_id": sys_id,
                        "resource_id": r_id,
                        "operation": "ANONYMIZE_RECORD",
                        "state": "ANONYMIZED",
                        "evidence_id": ""
                    })
                    verifications.append({
                        "phase": "VERIFICATION",
                        "system_id": sys_id,
                        "resource_id": r_id,
                        "operation": "VERIFY_RECORD_ANONYMIZED",
                        "state": "ANONYMIZED",
                        "evidence_id": ""
                    })
                elif o.state == "ANONYMIZED":
                    verifications.append({
                        "phase": "VERIFICATION",
                        "system_id": sys_id,
                        "resource_id": r_id,
                        "operation": "VERIFY_RECORD_ANONYMIZED",
                        "state": "ANONYMIZED",
                        "evidence_id": ev_id
                    })

        # 3. Search Service / Export Service (Should be absent)
        elif sys_id in ("search_service", "export_service") or r_id.startswith("search_") or r_id.startswith("export_"):
            if o.state == "PRESENT" or o.is_drift:
                findings.append({
                    "phase": "FINDING",
                    "system_id": sys_id,
                    "resource_id": r_id,
                    "operation": "REPORT_REMAINING_RECORD",
                    "state": "PRESENT",
                    "evidence_id": ev_id
                })
                remediations.append({
                    "phase": "REMEDIATION",
                    "system_id": sys_id,
                    "resource_id": r_id,
                    "operation": "DELETE_RECORD",
                    "state": "ABSENT",
                    "evidence_id": ""
                })
                verifications.append({
                    "phase": "VERIFICATION",
                    "system_id": sys_id,
                    "resource_id": r_id,
                    "operation": "VERIFY_RECORD_ABSENT",
                    "state": "ABSENT",
                    "evidence_id": ""
                })
            elif o.state == "ABSENT":
                verifications.append({
                    "phase": "VERIFICATION",
                    "system_id": sys_id,
                    "resource_id": r_id,
                    "operation": "VERIFY_RECORD_ABSENT",
                    "state": "ABSENT",
                    "evidence_id": ev_id
                })

        # 4. Identity Service / Tombstones (Redacted)
        elif sys_id == "identity_service" or r_id.startswith("tombstone_"):
            if o.state == "PRESENT":
                findings.append({
                    "phase": "FINDING",
                    "system_id": sys_id,
                    "resource_id": r_id,
                    "operation": "REPORT_EXPOSED_PII",
                    "state": "PRESENT",
                    "evidence_id": ev_id
                })
                remediations.append({
                    "phase": "REMEDIATION",
                    "system_id": sys_id,
                    "resource_id": r_id,
                    "operation": "REDACT_RECORD",
                    "state": "REDACTED",
                    "evidence_id": ""
                })
                verifications.append({
                    "phase": "VERIFICATION",
                    "system_id": sys_id,
                    "resource_id": r_id,
                    "operation": "VERIFY_RECORD_REDACTED",
                    "state": "REDACTED",
                    "evidence_id": ""
                })
            elif o.state == "REDACTED":
                verifications.append({
                    "phase": "VERIFICATION",
                    "system_id": sys_id,
                    "resource_id": r_id,
                    "operation": "VERIFY_RECORD_REDACTED",
                    "state": "REDACTED",
                    "evidence_id": ev_id
                })

        # 5. Integration Service (Disabled)
        elif sys_id == "integration_service" or r_id.startswith("data_flow_"):
            if o.enabled is True or o.state == "ACTIVE":
                findings.append({
                    "phase": "FINDING",
                    "system_id": sys_id,
                    "resource_id": r_id,
                    "operation": "REPORT_ACTIVE_DATA_FLOW",
                    "state": "ACTIVE",
                    "evidence_id": ev_id
                })
                remediations.append({
                    "phase": "REMEDIATION",
                    "system_id": sys_id,
                    "resource_id": r_id,
                    "operation": "DISABLE_DATA_FLOW",
                    "state": "DISABLED",
                    "evidence_id": ""
                })
                verifications.append({
                    "phase": "VERIFICATION",
                    "system_id": sys_id,
                    "resource_id": r_id,
                    "operation": "VERIFY_DATA_FLOW_DISABLED",
                    "state": "DISABLED",
                    "evidence_id": ""
                })
            elif o.enabled is False or o.state == "DISABLED":
                verifications.append({
                    "phase": "VERIFICATION",
                    "system_id": sys_id,
                    "resource_id": r_id,
                    "operation": "VERIFY_DATA_FLOW_DISABLED",
                    "state": "DISABLED",
                    "evidence_id": ev_id
                })

        # 6. Backup Service (Blocked)
        elif sys_id == "backup_service" or r_id.startswith("backup_"):
            if o.restore_allowed is True or o.state == "ACTIVE":
                findings.append({
                    "phase": "FINDING",
                    "system_id": sys_id,
                    "resource_id": r_id,
                    "operation": "REPORT_RESTORE_RISK",
                    "state": "ACTIVE",
                    "evidence_id": ev_id
                })
                remediations.append({
                    "phase": "REMEDIATION",
                    "system_id": sys_id,
                    "resource_id": r_id,
                    "operation": "BLOCK_RESTORE",
                    "state": "BLOCKED",
                    "evidence_id": ""
                })
                verifications.append({
                    "phase": "VERIFICATION",
                    "system_id": sys_id,
                    "resource_id": r_id,
                    "operation": "VERIFY_RESTORE_BLOCKED",
                    "state": "BLOCKED",
                    "evidence_id": ""
                })
            elif o.restore_allowed is False or o.state == "BLOCKED":
                verifications.append({
                    "phase": "VERIFICATION",
                    "system_id": sys_id,
                    "resource_id": r_id,
                    "operation": "VERIFY_RESTORE_BLOCKED",
                    "state": "BLOCKED",
                    "evidence_id": ev_id
                })

        # 7. Security Service (Retained Audit Record)
        elif sys_id == "security_service" or r_id.startswith("audit_record_"):
            verifications.append({
                "phase": "VERIFICATION",
                "system_id": sys_id,
                "resource_id": r_id,
                "operation": "VERIFY_RECORD_PRESENT",
                "state": "PRESENT",
                "evidence_id": ev_id
            })

    # 8. Linked analytics record with null identifiers
    if linked_an_id and linked_an_id not in res_latest:
        ev_id = get_citable_evidence_id(linked_an_id, "analytics_platform")
        verifications.append({
            "phase": "VERIFICATION",
            "system_id": "analytics_platform",
            "resource_id": linked_an_id,
            "operation": "VERIFY_RECORD_ANONYMIZED",
            "state": "ANONYMIZED",
            "evidence_id": ev_id
        })

    # Status decision: any finding => INCOMPLETE, else COMPLETE
    is_incomplete = len(findings) > 0
    status = "INCOMPLETE" if is_incomplete else "COMPLETE"

    # If INCOMPLETE, all future remediation and verification steps must use evidence_id ""
    if is_incomplete:
        for v in verifications:
            v["evidence_id"] = ""

    all_steps = findings + remediations + verifications
    sorted_steps = sort_steps(all_steps)

    return status, sorted_steps
