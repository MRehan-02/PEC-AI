"""
Ghost Data Still Exists - Evidence Loader
Parses all evidence files listed in dataset/evidence_files.csv into normalized observations
and records file coverage / scopes (including empty scopes).
"""

import os
import csv
import json
import sqlite3
import re
from dataclasses import dataclass, field
from typing import List, Dict, Set, Optional, Tuple, Any
from collections import Counter, defaultdict

try:
    import pypdf
except ImportError:
    pypdf = None


@dataclass
class Observation:
    file_id: str
    file_captured_at: str  # Taken from dataset/evidence_files.csv manifest (used for audit_at cutoffs)
    captured_at: str       # In-file timestamp (used for ordering records within one file)
    system_id: str
    resource_id: str
    email: Optional[str] = None
    email_hash: Optional[str] = None
    phone: Optional[str] = None
    user_reference: Optional[str] = None
    state: str = "PRESENT"
    restore_allowed: Optional[bool] = None
    enabled: Optional[bool] = None
    is_drift: bool = False
    raw_details: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "file_id": self.file_id,
            "file_captured_at": self.file_captured_at,
            "captured_at": self.captured_at,
            "system_id": self.system_id,
            "resource_id": self.resource_id,
            "email": self.email,
            "email_hash": self.email_hash,
            "phone": self.phone,
            "user_reference": self.user_reference,
            "state": self.state,
            "restore_allowed": self.restore_allowed,
            "enabled": self.enabled,
            "is_drift": self.is_drift,
        }


@dataclass
class FileScope:
    file_id: str
    file_path: str
    file_type: str
    file_captured_at: str
    captured_at: str
    covered_systems: Set[str] = field(default_factory=set)
    covered_tables: List[str] = field(default_factory=list)
    empty_scopes: List[Tuple[str, str]] = field(default_factory=list)  # (system_id, table_or_collection)


def normalize_email(email: Optional[str]) -> Optional[str]:
    if not email or not isinstance(email, str):
        return None
    val = email.strip()
    if not val or val.lower() == "null" or "@" not in val:
        return None
    return val.lower()


def normalize_phone(phone: Optional[str]) -> Optional[str]:
    if not phone or not isinstance(phone, str):
        return None
    val = phone.strip()
    if not val or val.lower() == "null":
        return None
    return val


def normalize_hash(h: Optional[str]) -> Optional[str]:
    if not h or not isinstance(h, str):
        return None
    val = h.strip()
    if not val or val.lower() == "null":
        return None
    return val.lower()


def normalize_user_ref(u: Optional[str]) -> Optional[str]:
    if not u or not isinstance(u, str):
        return None
    val = u.strip()
    if not val or val.lower() == "null":
        return None
    return val


def parse_bool_val(val: Any) -> Optional[bool]:
    if val is None:
        return None
    if isinstance(val, bool):
        return val
    s = str(val).strip().strip('"\'').lower()
    if s in ("true", "1", "yes"):
        return True
    if s in ("false", "0", "no"):
        return False
    return None


def map_resource_to_system(resource_id: str, default: Optional[str] = None) -> str:
    res = resource_id.lower()
    if res.startswith("account_"):
        return "account_service"
    if res.startswith("search_"):
        return "search_service"
    if res.startswith("analytics_"):
        return "analytics_platform"
    if res.startswith("support_"):
        return "support_platform"
    if res.startswith("export_"):
        return "export_service"
    if res.startswith("data_flow_"):
        return "integration_service"
    if res.startswith("backup_"):
        return "backup_service"
    if res.startswith("audit_record_"):
        return "security_service"
    if res.startswith("tombstone_"):
        return "identity_service"
    if res.startswith("legal_hold_") or res.startswith("ord-"):
        return "legal_service"

    if default:
        d = default.lower()
        if "account" in d or "profile" in d:
            return "account_service"
        if "search" in d or "index" in d:
            return "search_service"
        if "analytic" in d:
            return "analytics_platform"
        if "support" in d or "ticket" in d:
            return "support_platform"
        if "export" in d:
            return "export_service"
        if "integration" in d or "connector" in d or "flow" in d:
            return "integration_service"
        if "backup" in d or "restore" in d:
            return "backup_service"
        if "security" in d or "audit" in d:
            return "security_service"
        if "identity" in d or "tombstone" in d:
            return "identity_service"
        if "legal" in d or "tax" in d:
            return "legal_service"
        return default

    return "unknown_service"


# --- SQL Parser ---

def parse_sql_file(file_id: str, file_path: str, manifest_captured_at: str, full_path: str) -> Tuple[List[Observation], FileScope]:
    observations: List[Observation] = []

    with open(full_path, "r", encoding="utf-8") as f:
        sql_content = f.read()

    # Capture in-file point-in-time timestamp from SQL comment if available
    time_match = re.search(r"-- Captured at:\s*([^\r\n]+)", sql_content)
    in_file_captured_at = time_match.group(1).strip() if time_match else manifest_captured_at

    scope = FileScope(
        file_id=file_id,
        file_path=file_path,
        file_type=".sql",
        file_captured_at=manifest_captured_at,
        captured_at=in_file_captured_at
    )

    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(sql_content)
    except Exception as e:
        conn.close()
        raise RuntimeError(f"Failed executing {file_path} in sqlite: {e}")

    cursor = conn.cursor()
    tables = [row[0] for row in cursor.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    scope.covered_tables = tables

    # Check for snapshot_scope table
    has_snapshot_scope = ("snapshot_scope" in tables)
    snapshot_scope_systems: Set[str] = set()
    if has_snapshot_scope:
        for s_row in cursor.execute("SELECT origin_system, captured_at FROM snapshot_scope").fetchall():
            orig_sys = s_row[0]
            snapshot_scope_systems.add(orig_sys)
        scope.covered_systems = set(snapshot_scope_systems)

    # Check for replica drift between primary and replica tables
    primary_tables = [t for t in tables if t.endswith("_primary")]
    replica_tables = [t for t in tables if "replica" in t or "failover" in t]

    primary_resources: Set[str] = set()
    for pt in primary_tables:
        cols = [c[1] for c in cursor.execute(f'PRAGMA table_info("{pt}")').fetchall()]
        if "resource_id" in cols:
            for r_row in cursor.execute(f'SELECT resource_id FROM "{pt}"').fetchall():
                if r_row[0]:
                    primary_resources.add(r_row[0])

    table_to_system = {
        "customer_profiles": "account_service",
        "customer_search_documents": "search_service",
        "customer_analytics_profiles": "analytics_platform",
        "support_records": "support_platform",
        "customer_exports": "export_service",
        "connector_subscriptions": "integration_service",
        "backup_restore_entries": "backup_service",
        "security_audit_records": "security_service",
        "customer_profiles_primary": "account_service",
        "customer_profiles_failover_replica": "account_service",
        "search_documents_primary": "search_service"
    }

    seen_resources: Set[str] = set()

    for tbl in tables:
        if tbl == "snapshot_scope":
            continue

        cols = [c[1] for c in cursor.execute(f'PRAGMA table_info("{tbl}")').fetchall()]
        rows = cursor.execute(f'SELECT * FROM "{tbl}"').fetchall()
        col_idx = {name: idx for idx, name in enumerate(cols)}

        sys_for_tbl = table_to_system.get(tbl)
        if has_snapshot_scope:
            # Rule (2): Coverage must strictly come from snapshot_scope.origin_system.
            # Never treat an empty table as covering a system that is not in the scope.
            if sys_for_tbl and sys_for_tbl in snapshot_scope_systems:
                if len(rows) == 0:
                    scope.empty_scopes.append((sys_for_tbl, tbl))
        else:
            if sys_for_tbl:
                scope.covered_systems.add(sys_for_tbl)
                if len(rows) == 0:
                    scope.empty_scopes.append((sys_for_tbl, tbl))

        # Check if table has standard JSON structure (origin_system, collection_name, record_json)
        if "record_json" in col_idx and "resource_id" in col_idx:
            for row in rows:
                r_id = row[col_idx["resource_id"]]
                raw_orig = row[col_idx["origin_system"]] if "origin_system" in col_idx else None
                orig_sys = map_resource_to_system(r_id, default=raw_orig)
                if not has_snapshot_scope:
                    scope.covered_systems.add(orig_sys)
                raw_json = row[col_idx["record_json"]]
                data = json.loads(raw_json) if raw_json else {}

                is_drift = False
                if primary_tables and tbl in replica_tables:
                    if r_id not in primary_resources:
                        is_drift = True

                # Determine state and explicit flags
                state = "PRESENT"
                restore_allowed = None
                enabled = None

                if orig_sys == "backup_service":
                    if "restore_allowed" in data:
                        restore_allowed = bool(data["restore_allowed"])
                    state = "ACTIVE" if restore_allowed else "BLOCKED"
                elif orig_sys == "integration_service":
                    if "enabled" in data:
                        enabled = bool(data["enabled"])
                    state = "ACTIVE" if enabled else "DISABLED"
                elif orig_sys == "analytics_platform":
                    has_id = (data.get("user_id") or data.get("email") or data.get("phone") or data.get("email_hash"))
                    state = "PRESENT" if has_id else "ANONYMIZED"
                elif orig_sys == "support_platform":
                    msg = str(data.get("message_text", ""))
                    has_id = (data.get("email") or data.get("phone") or "@" in msg)
                    state = "REDACTED" if (msg == "[REDACTED]" or not has_id) else "PRESENT"
                elif orig_sys == "identity_service" or r_id.startswith("tombstone_"):
                    has_pii = (data.get("name") or data.get("email") or data.get("phone") or data.get("email_hash"))
                    state = "PRESENT" if has_pii else "REDACTED"

                obs = Observation(
                    file_id=file_id,
                    file_captured_at=manifest_captured_at,
                    captured_at=in_file_captured_at,
                    system_id=orig_sys,
                    resource_id=r_id,
                    email=normalize_email(data.get("email") or (data.get("document", {}).get("email") if isinstance(data.get("document"), dict) else None) or data.get("subject_selector")),
                    email_hash=normalize_hash(data.get("email_hash")),
                    phone=normalize_phone(data.get("phone")),
                    user_reference=normalize_user_ref(data.get("user_id") or data.get("customer_reference") or data.get("subject_reference") or (data.get("document", {}).get("customer_reference") if isinstance(data.get("document"), dict) else None)),
                    state=state,
                    restore_allowed=restore_allowed,
                    enabled=enabled,
                    is_drift=is_drift,
                    raw_details=data
                )
                observations.append(obs)
                seen_resources.add(r_id)

        else:
            # Explicit column structure
            is_replica = (tbl in replica_tables)
            for row in rows:
                r_id = row[col_idx["resource_id"]] if "resource_id" in col_idx else None
                if not r_id:
                    continue

                is_drift = False
                if primary_tables and is_replica and r_id not in primary_resources:
                    is_drift = True

                sys_id = map_resource_to_system(r_id, default=sys_for_tbl)
                if not has_snapshot_scope:
                    scope.covered_systems.add(sys_id)

                email_val = None
                for ef in ["email", "requester_email", "owner_email", "customer_email"]:
                    if ef in col_idx and row[col_idx[ef]]:
                        email_val = row[col_idx[ef]]
                        break

                phone_val = None
                for pf in ["phone", "requester_phone", "owner_phone"]:
                    if pf in col_idx and row[col_idx[pf]]:
                        phone_val = row[col_idx[pf]]
                        break

                user_ref = None
                for uf in ["customer_reference", "user_id", "subject_reference"]:
                    if uf in col_idx and row[col_idx[uf]]:
                        user_ref = row[col_idx[uf]]
                        break

                hash_val = row[col_idx["email_hash"]] if "email_hash" in col_idx else None

                # State determination and explicit flags
                state = "PRESENT"
                restore_allowed = None
                enabled = None

                if sys_id == "backup_service" and "restore_allowed" in col_idx:
                    val = row[col_idx["restore_allowed"]]
                    restore_allowed = bool(val in (1, True, "1"))
                    state = "ACTIVE" if restore_allowed else "BLOCKED"
                elif sys_id == "integration_service" and "enabled" in col_idx:
                    val = row[col_idx["enabled"]]
                    enabled = bool(val in (1, True, "1"))
                    state = "ACTIVE" if enabled else "DISABLED"
                elif sys_id == "analytics_platform":
                    has_pii = (email_val or phone_val or hash_val or user_ref)
                    state = "PRESENT" if has_pii else "ANONYMIZED"
                elif sys_id == "support_platform":
                    msg = str(row[col_idx["message_excerpt"]]) if "message_excerpt" in col_idx else ""
                    has_pii = (email_val or phone_val or (msg and "[REDACTED]" not in msg and "@" in msg))
                    state = "REDACTED" if (msg == "[REDACTED]" or not has_pii) else "PRESENT"

                obs = Observation(
                    file_id=file_id,
                    file_captured_at=manifest_captured_at,
                    captured_at=in_file_captured_at,
                    system_id=sys_id,
                    resource_id=r_id,
                    email=normalize_email(email_val),
                    email_hash=normalize_hash(hash_val),
                    phone=normalize_phone(phone_val),
                    user_reference=normalize_user_ref(user_ref),
                    state=state,
                    restore_allowed=restore_allowed,
                    enabled=enabled,
                    is_drift=is_drift,
                    raw_details={"table": tbl}
                )
                observations.append(obs)
                seen_resources.add(r_id)

    conn.close()
    return observations, scope


# --- JSON Parser ---

def parse_json_file(file_id: str, file_path: str, manifest_captured_at: str, full_path: str) -> Tuple[List[Observation], FileScope]:
    observations: List[Observation] = []

    with open(full_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    in_file_captured_at = data.get("exported_at") or data.get("captured_at") or manifest_captured_at

    scope = FileScope(
        file_id=file_id,
        file_path=file_path,
        file_type=".json",
        file_captured_at=manifest_captured_at,
        captured_at=in_file_captured_at
    )

    # 1. Snapshot bundle format
    if "snapshots" in data:
        for s in data["snapshots"]:
            raw_store = s.get("data_store", "unknown")
            coll_name = s.get("collection", "")
            records = s.get("records", [])
            canonical_store = map_resource_to_system("", default=raw_store)
            
            # Rule (2): Coverage must strictly come from the data_store/collection entries of JSON bundles
            scope.covered_systems.add(canonical_store)
            scope.covered_tables.append(coll_name)

            if len(records) == 0:
                scope.empty_scopes.append((canonical_store, coll_name))

            for r in records:
                r_id = r.get("resource_id") or ""
                rec_sys = r.get("system")
                sys_id = map_resource_to_system(r_id, default=rec_sys or raw_store)
                scope.covered_systems.add(sys_id)

                # State logic and explicit flags
                res_status = r.get("result", "")
                state = "PRESENT"
                restore_allowed = None
                enabled = None

                if sys_id == "backup_service":
                    if "restore_allowed" in r:
                        restore_allowed = bool(r["restore_allowed"])
                    elif res_status == "ACTIVE":
                        restore_allowed = True
                    elif res_status == "BLOCKED":
                        restore_allowed = False
                    state = "ACTIVE" if restore_allowed else "BLOCKED"
                elif sys_id == "integration_service":
                    if "enabled" in r:
                        enabled = bool(r["enabled"])
                    elif res_status == "ACTIVE":
                        enabled = True
                    elif res_status == "DISABLED":
                        enabled = False
                    state = "ACTIVE" if enabled else "DISABLED"
                elif res_status == "NOT_FOUND":
                    state = "ABSENT"
                elif res_status in ("FOUND", "IDENTIFIABLE"):
                    state = "PRESENT"
                elif res_status == "ANONYMIZED":
                    state = "ANONYMIZED"
                elif res_status == "REDACTED":
                    state = "REDACTED"
                elif sys_id == "analytics_platform":
                    has_id = any(
                        r.get(k) and str(r.get(k)).strip().lower() not in ("null", "", "none")
                        for k in ("user_id", "email", "phone", "email_hash", "customer_reference")
                    )
                    state = "PRESENT" if has_id else "ANONYMIZED"
                elif sys_id == "support_platform":
                    msg = str(r.get("message_text") or r.get("message_excerpt") or "")
                    has_id = (r.get("email") or r.get("phone") or "@" in msg)
                    state = "REDACTED" if (msg == "[REDACTED]" or not has_id) else "PRESENT"
                elif sys_id == "identity_service" or r_id.startswith("tombstone_"):
                    has_pii = (r.get("name") or r.get("email") or r.get("phone") or r.get("email_hash"))
                    state = "PRESENT" if has_pii else "REDACTED"

                lv = r.get("lookup_value")
                email_val = (
                    r.get("email") or r.get("requester_email") or r.get("owner_email") or r.get("customer_email") or
                    (r.get("document", {}).get("email") if isinstance(r.get("document"), dict) else None) or
                    (lv if lv and "@" in str(lv) else None)
                )
                user_ref = (
                    r.get("user_id") or r.get("customer_reference") or r.get("subject_reference") or
                    (r.get("document", {}).get("customer_reference") if isinstance(r.get("document"), dict) else None) or
                    (lv if lv and str(lv).startswith("user_") else None)
                )

                obs = Observation(
                    file_id=file_id,
                    file_captured_at=manifest_captured_at,
                    captured_at=s.get("captured_at", in_file_captured_at),
                    system_id=sys_id,
                    resource_id=r_id,
                    email=normalize_email(email_val),
                    email_hash=normalize_hash(r.get("email_hash")),
                    phone=normalize_phone(r.get("phone") or r.get("requester_phone") or r.get("owner_phone")),
                    user_reference=normalize_user_ref(user_ref),
                    state=state,
                    restore_allowed=restore_allowed,
                    enabled=enabled,
                    raw_details=r
                )
                observations.append(obs)

    # 2. Service resource export format
    elif "service_exports" in data:
        for raw_sys, export_data in data["service_exports"].items():
            canonical_sys = map_resource_to_system("", default=raw_sys)
            scope.covered_systems.add(canonical_sys)
            if isinstance(export_data, dict):
                for list_key, items in export_data.items():
                    scope.covered_tables.append(f"{canonical_sys}:{list_key}")
                    if isinstance(items, list) and len(items) == 0:
                        scope.empty_scopes.append((canonical_sys, list_key))
                    elif isinstance(items, list):
                        for r in items:
                            r_id = r.get("resource_id", "")
                            sys_id = map_resource_to_system(r_id, default=canonical_sys)
                            scope.covered_systems.add(sys_id)

                            # determine state and explicit flags
                            state = "PRESENT"
                            restore_allowed = None
                            enabled = None

                            if sys_id == "backup_service":
                                if "restore_allowed" in r:
                                    restore_allowed = bool(r["restore_allowed"])
                                state = "ACTIVE" if restore_allowed else "BLOCKED"
                            elif sys_id == "integration_service":
                                if "enabled" in r:
                                    enabled = bool(r["enabled"])
                                state = "ACTIVE" if enabled else "DISABLED"
                            elif sys_id == "account_service":
                                state = "ABSENT" if r.get("status_code") == 404 else "PRESENT"
                            elif sys_id == "search_service":
                                total_hits = r.get("hits", {}).get("total", 1) if isinstance(r.get("hits"), dict) else 1
                                state = "ABSENT" if total_hits == 0 else "PRESENT"
                            elif sys_id == "analytics_platform":
                                has_id = any(
                                    r.get(k) and str(r.get(k)).strip().lower() not in ("null", "", "none")
                                    for k in ("email", "phone", "customer_reference", "user_id", "email_hash")
                                )
                                state = "PRESENT" if has_id else "ANONYMIZED"
                            elif sys_id == "support_platform":
                                msg = str(r.get("message_excerpt", ""))
                                has_id = (r.get("requester_email") or r.get("requester_phone") or "@" in msg)
                                state = "REDACTED" if (msg == "[REDACTED]" or not has_id) else "PRESENT"

                            email_val = (
                                r.get("email") or r.get("customer_email") or r.get("owner_email") or r.get("requester_email") or
                                (r.get("profile", {}).get("email") if isinstance(r.get("profile"), dict) else None) or
                                (r.get("hits", {}).get("documents", [{}])[0].get("email") if isinstance(r.get("hits"), dict) and r.get("hits", {}).get("documents") else None) or
                                (r.get("query") if (r.get("query") and "@" in str(r.get("query"))) else None)
                            )

                            obs = Observation(
                                file_id=file_id,
                                file_captured_at=manifest_captured_at,
                                captured_at=r.get("observed_at", in_file_captured_at),
                                system_id=sys_id,
                                resource_id=r_id,
                                email=normalize_email(email_val),
                                email_hash=normalize_hash(r.get("email_hash")),
                                phone=normalize_phone(r.get("phone") or r.get("owner_phone") or r.get("requester_phone")),
                                user_reference=normalize_user_ref(r.get("subject_reference") or r.get("customer_reference") or r.get("user_id")),
                                state=state,
                                restore_allowed=restore_allowed,
                                enabled=enabled,
                                raw_details=r
                            )
                            observations.append(obs)

    # 3. Records list format (connector configs, export manifest, audit ledger, export jobs, analytics report)
    elif "records" in data:
        scope.covered_tables.append(data.get("collection") or data.get("origin") or "records")
        records = data.get("records", [])
        if len(records) == 0:
            scope.empty_scopes.append(("unknown", "records"))

        for r in records:
            r_id = r.get("resource_id", "")
            raw_sys = r.get("system") or data.get("source") or data.get("origin")
            sys_id = map_resource_to_system(r_id, default=raw_sys)
            scope.covered_systems.add(sys_id)

            res_status = r.get("result", "")
            state = "PRESENT"
            restore_allowed = None
            enabled = None

            if sys_id == "backup_service":
                if "restore_allowed" in r:
                    restore_allowed = bool(r["restore_allowed"])
                elif res_status == "ACTIVE":
                    restore_allowed = True
                elif res_status == "BLOCKED":
                    restore_allowed = False
                state = "ACTIVE" if restore_allowed else "BLOCKED"
            elif sys_id == "integration_service":
                if "enabled" in r:
                    enabled = bool(r["enabled"])
                elif res_status == "ACTIVE":
                    enabled = True
                elif res_status == "DISABLED":
                    enabled = False
                state = "ACTIVE" if enabled else "DISABLED"
            elif res_status == "NOT_FOUND":
                state = "ABSENT"
            elif res_status in ("FOUND", "IDENTIFIABLE"):
                state = "PRESENT"
            elif res_status == "ANONYMIZED":
                state = "ANONYMIZED"
            elif res_status == "REDACTED":
                state = "REDACTED"
            elif sys_id == "analytics_platform":
                has_id = (r.get("user_id") or r.get("email") or r.get("phone") or r.get("email_hash"))
                state = "PRESENT" if has_id else "ANONYMIZED"

            subj_ref = r.get("subject_reference", "")
            email_val = r.get("email") or r.get("owner_email") or (subj_ref if "@" in str(subj_ref) else None)
            user_ref = r.get("user_id") or (subj_ref if str(subj_ref).startswith("user_") else None)

            obs = Observation(
                file_id=file_id,
                file_captured_at=manifest_captured_at,
                captured_at=r.get("recorded_at") or r.get("created_at") or in_file_captured_at,
                system_id=sys_id,
                resource_id=r_id,
                email=normalize_email(email_val),
                email_hash=normalize_hash(r.get("email_hash")),
                phone=normalize_phone(r.get("phone")),
                user_reference=normalize_user_ref(user_ref),
                state=state,
                restore_allowed=restore_allowed,
                enabled=enabled,
                raw_details=r
            )
            observations.append(obs)

    return observations, scope


# --- TXT Parser ---

def parse_txt_file(file_id: str, file_path: str, manifest_captured_at: str, full_path: str) -> Tuple[List[Observation], FileScope]:
    observations: List[Observation] = []
    scope = FileScope(
        file_id=file_id,
        file_path=file_path,
        file_type=".txt",
        file_captured_at=manifest_captured_at,
        captured_at=manifest_captured_at
    )
    scope.covered_tables.append("shift_handover")

    with open(full_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line.startswith("- "):
                continue
            parts = [p.strip() for p in line[2:].split("|")]
            if len(parts) >= 6:
                ts, raw_sys, r_id, rec_type, status, subj = parts[0], parts[1], parts[2], parts[3], parts[4], parts[5]
                sys_id = map_resource_to_system(r_id, default=raw_sys)
                scope.covered_systems.add(sys_id)

                state = "PRESENT"
                restore_allowed = None
                enabled = None

                if sys_id == "backup_service":
                    if status == "ACTIVE":
                        restore_allowed = True
                        state = "ACTIVE"
                    elif status == "BLOCKED":
                        restore_allowed = False
                        state = "BLOCKED"
                elif sys_id == "integration_service":
                    if status == "ACTIVE":
                        enabled = True
                        state = "ACTIVE"
                    elif status == "DISABLED":
                        enabled = False
                        state = "DISABLED"
                elif status == "NOT_FOUND":
                    state = "ABSENT"
                elif status in ("FOUND", "IDENTIFIABLE"):
                    state = "PRESENT"
                elif status == "ANONYMIZED":
                    state = "ANONYMIZED"
                elif status == "REDACTED":
                    state = "REDACTED"

                email_val = subj if "@" in subj else None
                user_ref = subj if subj.startswith("user_") else None

                obs = Observation(
                    file_id=file_id,
                    file_captured_at=manifest_captured_at,
                    captured_at=ts,
                    system_id=sys_id,
                    resource_id=r_id,
                    email=normalize_email(email_val),
                    user_reference=normalize_user_ref(user_ref),
                    state=state,
                    restore_allowed=restore_allowed,
                    enabled=enabled,
                    raw_details={"record_type": rec_type, "raw_status": status}
                )
                observations.append(obs)

    return observations, scope


# --- PDF Parser ---

def parse_image_cache_file(
    file_id: str,
    file_rel_path: str,
    manifest_captured_at: str,
    cache_path: str
) -> Tuple[List[Observation], FileScope]:
    """
    Loads observations from cached image extraction JSON (generated via Gemini).
    """
    obs_list: List[Observation] = []
    covered_systems: Set[str] = set()

    if not os.path.exists(cache_path):
        return obs_list, FileScope(
            file_id=file_id,
            file_path=file_rel_path,
            file_type=".png",
            file_captured_at=manifest_captured_at,
            captured_at=manifest_captured_at,
            covered_systems=covered_systems
        )

    with open(cache_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    records = data.get("records", [])

    for r in records:
        r_id = r.get("resource_id") or ""
        if not r_id:
            continue
        u_ref = r.get("user_reference")
        email = r.get("email")
        email_hash = r.get("email_hash")
        phone = r.get("phone")
        status_words = str(r.get("status_words") or r.get("status") or "").lower()
        rec_dates = str(r.get("dates") or r.get("visible_time") or data.get("visible_time") or manifest_captured_at)

        # Infer system_id
        sys_id = None
        if r_id.startswith("account_"):
            sys_id = "account_service"
        elif r_id.startswith("support_") or r_id.startswith("object_"):
            sys_id = "support_platform"
        elif r_id.startswith("analytics_"):
            sys_id = "analytics_platform"
        elif r_id.startswith("search_"):
            sys_id = "search_service"
        elif r_id.startswith("export_"):
            sys_id = "export_service"
        elif r_id.startswith("data_flow_"):
            sys_id = "integration_service"
        elif r_id.startswith("backup_"):
            sys_id = "backup_service"
        elif r_id.startswith("audit_record_"):
            sys_id = "security_service"
        elif r_id.startswith("tombstone_"):
            sys_id = "identity_service"
        else:
            fl = file_rel_path.lower()
            if "search" in fl: sys_id = "search_service"
            elif "account" in fl: sys_id = "account_service"
            elif "connector" in fl: sys_id = "integration_service"
            elif "analytics" in fl: sys_id = "analytics_platform"
            elif "export" in fl: sys_id = "export_service"
            elif "support" in fl: sys_id = "support_platform"
            elif "backup" in fl: sys_id = "backup_service"
            elif "audit" in fl or "security" in fl: sys_id = "security_service"
            elif "market" in fl: sys_id = "analytics_platform"
            else: sys_id = "unknown_service"

        if sys_id:
            covered_systems.add(sys_id)

        # Determine state
        state = "PRESENT"
        enabled = None
        restore_allowed = None

        if "deleted" in status_words or "absent" in status_words:
            state = "ABSENT"
        elif "anonymized" in status_words:
            state = "ANONYMIZED"
        elif "redacted" in status_words:
            state = "REDACTED"
        elif sys_id == "integration_service" or r_id.startswith("data_flow_"):
            if "disabled" in status_words or "inactive" in status_words:
                state = "DISABLED"
                enabled = False
            else:
                state = "ACTIVE"
                enabled = True
        elif sys_id == "backup_service" or r_id.startswith("backup_"):
            if "blocked" in status_words or "prohibited" in status_words:
                state = "BLOCKED"
                restore_allowed = False
            else:
                state = "ACTIVE"
                restore_allowed = True

        obs = Observation(
            file_id=file_id,
            file_captured_at=manifest_captured_at,
            captured_at=rec_dates,
            system_id=sys_id,
            resource_id=r_id,
            email=normalize_email(email),
            email_hash=normalize_hash(email_hash),
            phone=normalize_phone(phone),
            user_reference=normalize_user_ref(u_ref),
            state=state,
            restore_allowed=restore_allowed,
            enabled=enabled,
            raw_details=r
        )
        obs_list.append(obs)

    scope = FileScope(
        file_id=file_id,
        file_path=file_rel_path,
        file_type=".png",
        file_captured_at=manifest_captured_at,
        captured_at=manifest_captured_at,
        covered_systems=covered_systems
    )
    return obs_list, scope


def parse_pdf_file(file_id: str, file_path: str, manifest_captured_at: str, full_path: str) -> Tuple[List[Observation], FileScope]:
    observations: List[Observation] = []
    scope = FileScope(
        file_id=file_id,
        file_path=file_path,
        file_type=".pdf",
        file_captured_at=manifest_captured_at,
        captured_at=manifest_captured_at
    )

    if pypdf is None:
        raise ImportError("pypdf is required to parse PDF evidence files.")

    reader = pypdf.PdfReader(full_path)
    full_text = "\n".join([page.extract_text() or "" for page in reader.pages])

    base_name = os.path.basename(file_path)

    # 1. Delivery address correction attachment (support_platform)
    if "delivery_correction_attachment" in base_name:
        scope.covered_systems.add("support_platform")
        scope.covered_tables.append("support_attachments")

        m_res = re.search(r"\b(support_\d+)\b", full_text)
        m_email = re.search(r"([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)", full_text)
        m_phone = re.search(r"(\+\d{1,3}[\s\-]?\(?\d{1,4}\)?[\s\-]?\d{1,4}[\s\-]?\d{3,5})", full_text)

        if m_res:
            r_id = m_res.group(1)
            obs = Observation(
                file_id=file_id,
                file_captured_at=manifest_captured_at,
                captured_at=manifest_captured_at,
                system_id="support_platform",
                resource_id=r_id,
                email=normalize_email(m_email.group(1) if m_email else None),
                phone=normalize_phone(m_phone.group(1) if m_phone else None),
                state="PRESENT",
                raw_details={"record_type": "SUPPORT_ATTACHMENT", "pdf_type": "delivery_correction_attachment"}
            )
            observations.append(obs)

    # 2. Security retention extract (security_service)
    elif "security_retention_extract" in base_name:
        scope.covered_systems.add("security_service")
        scope.covered_tables.append("security_journal")

        m_res = re.search(r"\b(audit_record_\d+)\b", full_text)
        m_user = re.search(r"\b(user_\d+)\b", full_text)

        if m_res:
            r_id = m_res.group(1)
            obs = Observation(
                file_id=file_id,
                file_captured_at=manifest_captured_at,
                captured_at=manifest_captured_at,
                system_id="security_service",
                resource_id=r_id,
                user_reference=normalize_user_ref(m_user.group(1) if m_user else None),
                state="PRESENT",
                raw_details={"pdf_type": "security_retention_extract"}
            )
            observations.append(obs)

    # 3. Legal retention register (legal_service)
    elif "legal_retention_register" in base_name:
        scope.covered_systems.add("legal_service")
        scope.covered_tables.append("legal_hold_register")

        # Disputed order entries like: ORD-<number> | user_<number> | ...
        for line in full_text.splitlines():
            line = line.strip()
            if "ORD-" in line and "user_" in line:
                m_ord = re.search(r"\b(ORD-\d+)\b", line)
                m_u = re.search(r"\b(user_\d+)\b", line)
                if m_ord and m_u:
                    obs = Observation(
                        file_id=file_id,
                        file_captured_at=manifest_captured_at,
                        captured_at=manifest_captured_at,
                        system_id="legal_service",
                        resource_id=m_ord.group(1),
                        user_reference=normalize_user_ref(m_u.group(1)),
                        state="PRESENT",
                        raw_details={"line": line}
                    )
                    observations.append(obs)

    # 4. Account history report (account_service)
    elif "account_history_report" in base_name:
        scope.covered_systems.add("account_service")
        scope.covered_tables.append("customer_statements")

        m_user = re.search(r"\b(user_\d+)\b", full_text)
        m_email = re.search(r"([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)", full_text)
        m_stmt = re.search(r"\b(NST-\d{4}-\d{4}-\d{4})\b", full_text)

        obs = Observation(
            file_id=file_id,
            file_captured_at=manifest_captured_at,
            captured_at=manifest_captured_at,
            system_id="account_service",
            resource_id=m_stmt.group(1) if m_stmt else "statement_report",
            email=normalize_email(m_email.group(1) if m_email else None),
            user_reference=normalize_user_ref(m_user.group(1) if m_user else None),
            state="PRESENT",
            raw_details={"pdf_type": "account_history_report"}
        )
        observations.append(obs)

    return observations, scope


# --- Log Parser ---

def parse_log_file(file_id: str, file_path: str, manifest_captured_at: str, full_path: str) -> Tuple[List[Observation], FileScope]:
    observations: List[Observation] = []
    scope = FileScope(
        file_id=file_id,
        file_path=file_path,
        file_type=".log",
        file_captured_at=manifest_captured_at,
        captured_at=manifest_captured_at
    )

    trace_re = re.compile(r"\b(tr-[a-zA-Z0-9_-]+|rb-[a-zA-Z0-9_-]+)\b")
    res_re = re.compile(r'''resource_id\?["'\s]*[=:]\?["'\s]*([a-zA-Z0-9_-]+)''')
    service_re = re.compile(r'''(?:service|logger|function)\?["'\s]*[=:]\?["'\s]*([a-zA-Z0-9_-]+)''')
    ts_re = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z?)\s*")
    kv_pattern = re.compile(r"""([a-zA-Z0-9_-]+)\s*[:=]\s*(?:"([^"]*)"|'([^']*)'|([^\s,{}]+))""")
    email_re = re.compile(r"([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)")
    hash_re = re.compile(r"\b([a-fA-F0-9]{64})\b")
    user_re = re.compile(r"\b(user_\d+)\b")
    phone_re = re.compile(r"(\+\d{1,3}[\s\-]?(?:\(?\d{1,4}\)?)?[\s\-]?\d{1,4}[\s\-]?\d{3,5})")

    with open(full_path, "r", encoding="utf-8") as f:
        raw_lines = f.readlines()

    # Pass 1: Parse all lines, collect trace identifiers and group lines by trace
    trace_identifiers: Dict[str, Dict[str, Set[str]]] = defaultdict(lambda: {
        "emails": set(),
        "hashes": set(),
        "phones": set(),
        "user_refs": set()
    })

    parsed_lines = []
    for idx, raw_line in enumerate(raw_lines):
        line = raw_line.strip()
        if not line:
            continue

        m_ts = ts_re.search(line)
        line_ts = m_ts.group(1) if m_ts else manifest_captured_at
        line_no_ts = line[m_ts.end():] if m_ts else line

        fields: Dict[str, Any] = {}
        start_brace = line.find('{')
        end_brace = line.rfind('}')
        if start_brace != -1 and end_brace != -1 and start_brace < end_brace:
            try:
                fields.update(json.loads(line[start_brace:end_brace+1]))
            except Exception:
                pass

        for m_kv in kv_pattern.finditer(line_no_ts):
            k = m_kv.group(1)
            v = m_kv.group(2) if m_kv.group(2) is not None else (m_kv.group(3) if m_kv.group(3) is not None else m_kv.group(4))
            if k not in fields:
                fields[k] = v

        # Trace ID: accept trace_id, requestId, request_id, trace, or regex match
        tr = fields.get("trace_id") or fields.get("requestId") or fields.get("request_id") or fields.get("trace")
        if not tr:
            m_tr = trace_re.search(line)
            if m_tr:
                tr = m_tr.group(1)

        # Identifiers in line
        em_list = [e.lower() for e in email_re.findall(line)]
        h_list = [h.lower() for h in hash_re.findall(line)]
        u_list = user_re.findall(line)
        ph_list = phone_re.findall(line)

        for k_id in ("lookup_key", "subject_key", "subject_ref", "correlation_key", "owner_key", "subject_selector", "match_key"):
            v = fields.get(k_id)
            if v and isinstance(v, str):
                v_clean = v.strip().strip('"\'')
                if "@" in v_clean:
                    em_list.append(v_clean.lower())
                elif re.fullmatch(r"[a-fA-F0-9]{64}", v_clean):
                    h_list.append(v_clean.lower())
                elif v_clean.startswith("user_"):
                    u_list.append(v_clean)
                elif v_clean.startswith("+"):
                    ph_list.append(v_clean)

        if tr:
            for em in em_list: trace_identifiers[tr]["emails"].add(em)
            for h in h_list: trace_identifiers[tr]["hashes"].add(h)
            for u in u_list: trace_identifiers[tr]["user_refs"].add(u)
            for ph in ph_list: trace_identifiers[tr]["phones"].add(ph)

        # Resource ID
        r_id = fields.get("resource_id")
        if not r_id:
            m_r = res_re.search(line)
            if m_r:
                r_id = m_r.group(1).strip()

        raw_srv = fields.get("service") or fields.get("logger") or fields.get("function")
        if not raw_srv:
            m_srv = service_re.search(line)
            if m_srv:
                raw_srv = m_srv.group(1)

        parsed_lines.append({
            "line": line,
            "line_ts": line_ts,
            "fields": fields,
            "trace_id": tr,
            "resource_id": r_id,
            "raw_srv": raw_srv,
            "emails": set(em_list),
            "hashes": set(h_list),
            "user_refs": set(u_list),
            "phones": set(ph_list)
        })

    # Pass 2: Group lines by (trace_id, resource_id) or resource_id
    trace_resource_groups = defaultdict(list)
    no_trace_items = []

    for pl in parsed_lines:
        r_id = pl["resource_id"]
        if not r_id:
            continue
        tr = pl["trace_id"]
        if tr:
            trace_resource_groups[(tr, r_id)].append(pl)
        else:
            no_trace_items.append(pl)

    def process_group(r_id, group_lines, tr_id=None):
        # Ignore *_started events for state, use *_completed events
        comp_lines = [
            pl for pl in group_lines
            if "_completed" in str(pl["fields"].get("event", ""))
            or "_completed" in pl["line"]
            or "rows" in pl["fields"]
            or "http_status" in pl["fields"]
            or 'result="miss"' in pl["line"]
            or "NOT_FOUND" in pl["line"]
        ]
        rep = comp_lines[-1] if comp_lines else group_lines[-1]

        line = rep["line"]
        fields = rep["fields"]
        line_ts = rep["line_ts"]
        raw_srv = rep["raw_srv"]

        sys_id = map_resource_to_system(r_id, default=raw_srv)
        scope.covered_systems.add(sys_id)

        # Identifiers: attach all identifiers collected in that trace
        if tr_id and tr_id in trace_identifiers:
            ids = trace_identifiers[tr_id]
            em_val = sorted(ids["emails"])[0] if ids["emails"] else None
            h_val = sorted(ids["hashes"])[0] if ids["hashes"] else None
            u_val = sorted(ids["user_refs"])[0] if ids["user_refs"] else None
            ph_val = sorted(ids["phones"])[0] if ids["phones"] else None
        else:
            em_val = sorted(rep["emails"])[0] if rep["emails"] else None
            h_val = sorted(rep["hashes"])[0] if rep["hashes"] else None
            u_val = sorted(rep["user_refs"])[0] if rep["user_refs"] else None
            ph_val = sorted(rep["phones"])[0] if rep["phones"] else None

        # State determination
        state = "PRESENT"
        restore_allowed = None
        enabled = None

        el_bool = parse_bool_val(fields.get("eligible"))
        db_bool = parse_bool_val(fields.get("deletion_block"))
        ra_bool = parse_bool_val(fields.get("restore_allowed"))

        # Backup / restore mapping: eligible=true with deletion_block=false means restore_allowed=True,
        # and deletion_block=true or eligible=false means False.
        if sys_id == "backup_service" or r_id.startswith("backup_"):
            if el_bool is not None or db_bool is not None:
                if el_bool is True and db_bool is False:
                    restore_allowed = True
                elif db_bool is True or el_bool is False:
                    restore_allowed = False
            elif ra_bool is not None:
                restore_allowed = ra_bool
            elif 'restore_allowed="true"' in line or "restore_allowed=true" in line:
                restore_allowed = True
            elif 'restore_allowed="false"' in line or "restore_allowed=false" in line or "BLOCKED" in line:
                restore_allowed = False

            if restore_allowed is True:
                state = "ACTIVE"
            elif restore_allowed is False:
                state = "BLOCKED"

        # Integration / connector mapping
        elif sys_id == "integration_service" or r_id.startswith("data_flow_"):
            en_bool = parse_bool_val(fields.get("enabled"))
            last_deliv = str(fields.get("last_delivery_status", "")).strip().strip('"\'').lower()
            if en_bool is not None:
                enabled = en_bool
            elif last_deliv:
                if last_deliv in ("delivered", "active", "running", "success"):
                    enabled = True
                elif last_deliv in ("stopped", "disabled", "failed", "blocked"):
                    enabled = False
            elif 'enabled="true"' in line or "enabled=true" in line or "ACTIVE" in line:
                enabled = True
            elif 'enabled="false"' in line or "enabled=false" in line or "DISABLED" in line:
                enabled = False

            if enabled is True:
                state = "ACTIVE"
            elif enabled is False:
                state = "DISABLED"

        # Other systems:
        else:
            event_str = str(fields.get("event", "")).strip().strip('"\'')
            rows_val = fields.get("rows")
            status_code = fields.get("http_status")

            is_rows_zero = False
            if rows_val is not None:
                try:
                    is_rows_zero = (int(rows_val) == 0)
                except ValueError:
                    is_rows_zero = (str(rows_val).strip() == "0")
            elif 'rows="0"' in line or "rows=0" in line:
                is_rows_zero = True

            is_rows_pos = False
            if rows_val is not None and not is_rows_zero:
                try:
                    is_rows_pos = (int(rows_val) >= 1)
                except ValueError:
                    pass

            is_404 = (status_code in (404, "404") or 'http_status="404"' in line or "http_status=404" in line)
            is_not_found = (
                event_str.endswith("_not_found") or
                'result="miss"' in line or "result=miss" in line or
                'result="NOT_FOUND"' in line or "NOT_FOUND" in line
            )

            if is_rows_zero or is_404 or is_not_found:
                state = "ABSENT"
            elif is_rows_pos or (status_code in (200, "200") and not is_rows_zero and "_started" not in event_str):
                state = "PRESENT"
            elif 'direct_identifiers="0"' in line or fields.get("direct_identifiers") in (0, "0") or 'result="ANONYMIZED"' in line:
                state = "ANONYMIZED"
            elif 'result="REDACTED"' in line or 'message_text="[REDACTED]"' in line or fields.get("message_text") == "[REDACTED]":
                state = "REDACTED"

        raw_details = dict(fields)
        raw_details["line"] = line

        return Observation(
            file_id=file_id,
            file_captured_at=manifest_captured_at,
            captured_at=line_ts,
            system_id=sys_id,
            resource_id=r_id,
            email=normalize_email(em_val),
            email_hash=normalize_hash(h_val),
            phone=normalize_phone(ph_val),
            user_reference=normalize_user_ref(u_val),
            state=state,
            restore_allowed=restore_allowed,
            enabled=enabled,
            raw_details=raw_details
        )

    for (tr, r_id), group_lines in trace_resource_groups.items():
        obs = process_group(r_id, group_lines, tr_id=tr)
        observations.append(obs)

    for pl in no_trace_items:
        obs = process_group(pl["resource_id"], [pl], tr_id=None)
        observations.append(obs)

    return observations, scope


# --- Main Loader ---

def load_all_evidence(
    manifest_path: str = "dataset/evidence_files.csv",
    dataset_dir: str = "dataset"
) -> Tuple[List[Observation], Dict[str, FileScope]]:
    """
    Parses every file listed in evidence_files.csv into normalized observations
    and file scopes. Skips .png files for now.
    """
    all_observations: List[Observation] = []
    scopes: Dict[str, FileScope] = {}

    file_type_counts: Counter = Counter()
    system_counts: Counter = Counter()
    skipped_counts: Counter = Counter()

    if not os.path.exists(manifest_path):
        raise FileNotFoundError(f"Manifest not found: {manifest_path}")

    with open(manifest_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            file_id = row["file_id"]
            file_rel_path = row["file_path"]
            manifest_captured_at = row["captured_at"]

            full_path = os.path.join(dataset_dir, file_rel_path)
            ext = os.path.splitext(file_rel_path)[1].lower()

            if ext == ".png":
                code_dir = os.path.dirname(os.path.abspath(__file__))
                cache_file = os.path.join(code_dir, "cache", "images", f"{file_id}.json")
                if os.path.exists(cache_file):
                    obs_list, scope = parse_image_cache_file(file_id, file_rel_path, manifest_captured_at, cache_file)
                    scopes[file_id] = scope
                    all_observations.extend(obs_list)
                    for obs in obs_list:
                        system_counts[obs.system_id] += 1
                    file_type_counts[ext] += 1
                else:
                    skipped_counts[ext] += 1
                continue

            if not os.path.exists(full_path):
                print(f"Warning: File {full_path} not found on disk, skipping.")
                continue

            file_type_counts[ext] += 1

            if ext == ".sql":
                obs_list, scope = parse_sql_file(file_id, file_rel_path, manifest_captured_at, full_path)
            elif ext == ".json":
                obs_list, scope = parse_json_file(file_id, file_rel_path, manifest_captured_at, full_path)
            elif ext == ".txt":
                obs_list, scope = parse_txt_file(file_id, file_rel_path, manifest_captured_at, full_path)
            elif ext == ".pdf":
                obs_list, scope = parse_pdf_file(file_id, file_rel_path, manifest_captured_at, full_path)
            elif ext == ".log":
                obs_list, scope = parse_log_file(file_id, file_rel_path, manifest_captured_at, full_path)
            else:
                skipped_counts[ext] += 1
                continue

            scopes[file_id] = scope
            all_observations.extend(obs_list)

            for obs in obs_list:
                system_counts[obs.system_id] += 1

    # Print summary statistics
    print("=" * 60)
    print("Evidence Loading Summary")
    print("=" * 60)
    print(f"Total processed files: {sum(file_type_counts.values())}")
    print(f"Total skipped files:   {sum(skipped_counts.values())} (e.g. PNGs)")
    print(f"Total observations:    {len(all_observations)}")
    print("\nCounts per file type:")
    for ft, cnt in sorted(file_type_counts.items()):
        obs_ft = sum(1 for o in all_observations if os.path.splitext(scopes[o.file_id].file_path)[1].lower() == ft)
        print(f"  {ft:6s}: {cnt:3d} files, {obs_ft:5d} observations")

    print("\nCounts per system:")
    for sys_id, cnt in sorted(system_counts.items(), key=lambda x: -x[1]):
        print(f"  {sys_id:22s}: {cnt:5d} observations")

    total_empty_scopes = sum(len(s.empty_scopes) for s in scopes.values())
    print(f"\nTotal recorded empty scopes across snapshots: {total_empty_scopes}")
    print("=" * 60)

    return all_observations, scopes


if __name__ == "__main__":
    load_all_evidence()
