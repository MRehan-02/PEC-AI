"""
Debug Lookup Tool for Ghost Data Still Exists evidence loader.
Queries observations by resource_id and inspects SQL snapshot table coverage.
"""

import sys
import os
import sqlite3
import csv
from typing import List, Optional

# Ensure repository root is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from code.loader import load_all_evidence, Observation, FileScope


def get_table_or_source(obs: Observation) -> str:
    """Extracts table name or source origin from observation details."""
    details = obs.raw_details or {}
    if "table" in details:
        return details["table"]
    if "collection" in details:
        return details["collection"]
    if "collection_name" in details:
        return details["collection_name"]
    if "pdf_type" in details:
        return f"pdf:{details['pdf_type']}"
    if "line" in details:
        return "log_line"
    return "snapshot_record"


def get_table_topology(obs: Observation, scopes: dict) -> str:
    """Returns whether the observation came from a primary, replica, or non-database source."""
    scope = scopes.get(obs.file_id)
    if not scope or scope.file_type != ".sql":
        return "N/A (non-SQL)"
    
    tbl = get_table_or_source(obs).lower()
    if obs.is_drift:
        return f"REPLICA ONLY (Drift detected in {tbl})"
    if "replica" in tbl or "failover" in tbl:
        return f"Replica table ({tbl})"
    if "primary" in tbl:
        return f"Primary table ({tbl})"
    return f"Standard table ({tbl})"


def debug_lookup(resource_ids: List[str], print_sql_coverage: bool = False):
    print("Loading all evidence...")
    all_obs, scopes = load_all_evidence()
    
    # 1. SQL File Coverage Listing
    if print_sql_coverage:
        print("\n" + "=" * 80)
        print("SQL Evidence Files - Table Coverage & Row Counts")
        print("=" * 80)
        
        manifest_path = "dataset/evidence_files.csv"
        sql_rows = []
        with open(manifest_path, "r", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r["file_path"].endswith(".sql"):
                    sql_rows.append(r)

        for r in sql_rows:
            f_id = r["file_id"]
            rel_path = r["file_path"]
            full_path = os.path.join("dataset", rel_path)
            
            conn = sqlite3.connect(":memory:")
            with open(full_path, "r", encoding="utf-8") as sf:
                conn.executescript(sf.read())
            
            cursor = conn.cursor()
            tables = [row[0] for row in cursor.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
            table_stats = []
            for t in tables:
                cnt = cursor.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
                table_stats.append(f"{t} ({cnt} rows)")
            conn.close()
            
            print(f"[{f_id}] {os.path.basename(rel_path)}:")
            for ts in table_stats:
                print(f"    - {ts}")

    # 2. Lookup Resource IDs
    print("\n" + "=" * 80)
    print("Resource Observations Lookup")
    print("=" * 80)

    for r_id in resource_ids:
        matches = [o for o in all_obs if o.resource_id == r_id]
        print(f"\nResource ID: {r_id}  ({len(matches)} observation(s) found)")
        print("-" * 80)
        
        if not matches:
            print("  [NO OBSERVATIONS FOUND IN PROCESSED EVIDENCE]")
            print(f"  Note: Absence is proven when snapshots covering the system do not contain {r_id}.")
            continue

        for idx, m in enumerate(matches, 1):
            tbl_source = get_table_or_source(m)
            topology = get_table_topology(m, scopes)
            raw = m.raw_details or {}
            
            print(f"  [{idx}] File ID:               {m.file_id} ({scopes[m.file_id].file_path})")
            print(f"      File Captured At (manifest): {m.file_captured_at}")
            print(f"      In-File Captured At:         {m.captured_at}")
            print(f"      System ID:                   {m.system_id}")
            print(f"      Table/Source:                {tbl_source}")
            print(f"      Topology:                    {topology}")
            print(f"      State:                       {m.state}")
            print(f"      restore_allowed:             {m.restore_allowed}")
            print(f"      enabled:                     {m.enabled}")
            print(f"      Identifiers:                 email={m.email}, phone={m.phone}, hash={m.email_hash}, user_ref={m.user_reference}")
            
            state_flags = []
            if "account_status" in raw:
                state_flags.append(f"account_status={raw['account_status']}")
            if "result" in raw:
                state_flags.append(f"result={raw['result']}")
            if "raw_status" in raw:
                state_flags.append(f"raw_status={raw['raw_status']}")
            if "eligible" in raw:
                state_flags.append(f"eligible={raw['eligible']}")
            if "deletion_block" in raw:
                state_flags.append(f"deletion_block={raw['deletion_block']}")
            if "last_delivery_status" in raw:
                state_flags.append(f"last_delivery_status={raw['last_delivery_status']}")
            if "event" in raw:
                state_flags.append(f"event={raw['event']}")
            if state_flags:
                print(f"      Raw Flags:                   {', '.join(state_flags)}")
            
            if m.is_drift:
                print("      >>> REPLICATION DRIFT: Record is present in replica but ABSENT from primary!")

    print("=" * 80)


if __name__ == "__main__":
    target_ids = sys.argv[1:] if len(sys.argv) > 1 else []
    debug_lookup(target_ids, print_sql_coverage=False)
