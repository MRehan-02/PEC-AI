"""
Ghost Data Still Exists - Step Serialization & Ordering
Handles formatting, phase ordering, operation sorting, and numbering for audit steps.
"""

from typing import List, Dict, Any

FINDING_OPS = [
    "REPORT_REMAINING_RECORD",
    "REPORT_EXPOSED_PII",
    "REPORT_ACTIVE_DATA_FLOW",
    "REPORT_RESTORE_RISK",
]

REMEDIATION_OPS = [
    "DISABLE_DATA_FLOW",
    "BLOCK_RESTORE",
    "DELETE_RECORD",
    "REDACT_RECORD",
    "ANONYMIZE_RECORD",
]

VERIFICATION_OPS = [
    "VERIFY_RECORD_ABSENT",
    "VERIFY_RECORD_PRESENT",
    "VERIFY_RECORD_REDACTED",
    "VERIFY_RECORD_ANONYMIZED",
    "VERIFY_DATA_FLOW_DISABLED",
    "VERIFY_RESTORE_BLOCKED",
]

FINDING_ORDER = {op: idx for idx, op in enumerate(FINDING_OPS)}
REMEDIATION_ORDER = {op: idx for idx, op in enumerate(REMEDIATION_OPS)}
VERIFICATION_ORDER = {op: idx for idx, op in enumerate(VERIFICATION_OPS)}


def sort_steps(steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Sorts steps by:
    1. Phase: FINDING -> REMEDIATION -> VERIFICATION
    2. Operation order within phase (defined in problem statement)
    3. system_id
    4. resource_id
    """
    findings = [s for s in steps if s["phase"] == "FINDING"]
    remediations = [s for s in steps if s["phase"] == "REMEDIATION"]
    verifications = [s for s in steps if s["phase"] == "VERIFICATION"]

    findings.sort(key=lambda s: (FINDING_ORDER.get(s["operation"], 99), s["system_id"], s["resource_id"]))
    remediations.sort(key=lambda s: (REMEDIATION_ORDER.get(s["operation"], 99), s["system_id"], s["resource_id"]))
    verifications.sort(key=lambda s: (VERIFICATION_ORDER.get(s["operation"], 99), s["system_id"], s["resource_id"]))

    all_sorted = findings + remediations + verifications
    for idx, s in enumerate(all_sorted, 1):
        s["step_no"] = idx

    return all_sorted
