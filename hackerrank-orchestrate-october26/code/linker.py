"""
Ghost Data Still Exists - Activity Linker
Links activity rows to cases only when no identifier link exists.
"""

import os
import re
import csv
import json
from datetime import datetime
from typing import Dict, Set, List, Optional, Tuple, Any
from collections import defaultdict, Counter

AMBIGUOUS_ACCOUNT_POLICY = "omit"  # "omit" (default) or "padded_user_number"


def parse_iso(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    clean = ts.replace("Z", "+00:00")
    return datetime.fromisoformat(clean)


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


class Linker:
    def __init__(
        self,
        dataset_dir: str = "dataset",
        cache_dir: str = "code/cache",
        ambiguous_account_policy: str = AMBIGUOUS_ACCOUNT_POLICY
    ):
        if not os.path.isabs(dataset_dir):
            self.dataset_dir = os.path.abspath(dataset_dir) if os.path.exists(dataset_dir) else os.path.join(REPO_ROOT, dataset_dir)
        else:
            self.dataset_dir = dataset_dir

        if not os.path.isabs(cache_dir):
            self.cache_dir = os.path.abspath(cache_dir) if os.path.exists(cache_dir) else os.path.join(REPO_ROOT, cache_dir)
        else:
            self.cache_dir = cache_dir

        self.ambiguous_account_policy = ambiguous_account_policy
        self.decisions_path = os.path.join(self.cache_dir, "link_decisions.jsonl")

        self.account_links: Dict[str, Optional[str]] = {}
        self.account_tiers: Dict[str, str] = {}
        self.account_cand_counts: Dict[str, int] = {}

        self.analytics_links: Dict[str, Optional[str]] = {}
        self.analytics_tiers: Dict[str, str] = {}
        self.analytics_cand_counts: Dict[str, int] = {}

    def build_links(self, id_store: Any, all_obs: List[Any]):
        """
        Builds candidate sets for ALL requests in deletion_requests.csv and sample_deletion_requests.csv,
        filters exact identifier matches, performs iterative reduction, and logs decisions.
        """
        # 1. Load all requests
        all_reqs = []
        sample_path = os.path.join(self.dataset_dir, "sample_deletion_requests.csv")
        eval_path = os.path.join(self.dataset_dir, "deletion_requests.csv")

        if os.path.exists(sample_path):
            with open(sample_path, "r", encoding="utf-8") as f:
                all_reqs.extend(list(csv.DictReader(f)))
        if os.path.exists(eval_path):
            with open(eval_path, "r", encoding="utf-8") as f:
                all_reqs.extend(list(csv.DictReader(f)))

        # 2. Collect exact accounts and analytics from evidence observations
        user_exact_account: Dict[str, str] = {}
        user_exact_analytics: Dict[str, Set[str]] = defaultdict(set)

        # Treat every user_NN <-> resource_id pair found in observations (including images) as an exact link
        for o in all_obs:
            if o.user_reference and o.resource_id:
                uid = o.user_reference.lower()
                if uid in id_store.profiles:
                    id_store.profiles[uid].resource_ids.add(o.resource_id)
                    if o.system_id == "account_service" or o.resource_id.startswith("account_"):
                        if uid not in user_exact_account:
                            user_exact_account[uid] = o.resource_id
                    elif o.system_id == "analytics_platform" or o.resource_id.startswith("analytics_"):
                        user_exact_analytics[uid].add(o.resource_id)

        for uid, prof in id_store.profiles.items():
            user_emails = {e.lower() for e in prof.emails if e}
            user_hashes = {h.lower() for h in prof.hashes if h}
            user_phones = {p for p in prof.phones if p}
            user_ref = prof.user_id.lower()

            for o in all_obs:
                is_u = (
                    (o.email and o.email.lower() in user_emails) or
                    (o.email_hash and o.email_hash.lower() in user_hashes) or
                    (o.phone and o.phone in user_phones) or
                    (o.user_reference and o.user_reference.lower() == user_ref) or
                    (o.resource_id in prof.resource_ids)
                )
                if not is_u:
                    continue

                if o.system_id == "account_service" or o.resource_id.startswith("account_"):
                    if uid not in user_exact_account:
                        user_exact_account[uid] = o.resource_id

                if o.system_id == "analytics_platform" or o.resource_id.startswith("analytics_"):
                    has_id = (o.email or o.email_hash or o.phone or o.user_reference)
                    if has_id:
                        user_exact_analytics[uid].add(o.resource_id)

        exact_account_res_ids = set(user_exact_account.values())
        exact_analytics_res_ids = set()
        for s in user_exact_analytics.values():
            exact_analytics_res_ids.update(s)

        # 3. Load system_activity
        act_path = os.path.join(self.dataset_dir, "system_activity.csv")
        all_act = []
        if os.path.exists(act_path):
            with open(act_path, "r", encoding="utf-8") as f:
                all_act = list(csv.DictReader(f))

        # 4. Process Account Linking
        account_candidates_per_case: Dict[str, Set[str]] = {}

        for r in all_reqs:
            cid = r["case_id"]
            u = id_store.resolve_user(r["email"])
            if u and u.user_id in user_exact_account:
                self.account_links[cid] = user_exact_account[u.user_id]
                self.account_tiers[cid] = "exact"
                self.account_cand_counts[cid] = 1
                continue

            rep_at = parse_iso(r.get("reported_at"))
            rep_status = r.get("reported_status")
            cands = set()
            if rep_at:
                for act in all_act:
                    if act.get("event_type") == "DELETE_RECORD" and act.get("target_system") == "account_service":
                        res_id = act.get("resource_id")
                        if res_id in exact_account_res_ids:
                            continue
                        act_time = parse_iso(act.get("event_time"))
                        if not act_time:
                            continue
                        dt_min = (act_time - rep_at).total_seconds() / 60.0
                        # within reported_at -80..-40 minutes or -11..+11 minutes (widened 10%)
                        if (-88.0 <= dt_min <= -36.0) or (-12.1 <= dt_min <= 12.1):
                            # Keep only activity rows whose reported_result is consistent with request's reported_status
                            res_val = act.get("reported_result")
                            if rep_status == "COMPLETE" and res_val != "SUCCESS":
                                continue
                            elif rep_status == "NOT_STARTED" and res_val != "FAILED":
                                continue
                            elif rep_status == "PARTIALLY_COMPLETE" and res_val not in ("PARTIAL", "SUCCESS"):
                                continue
                            cands.add(res_id)
            account_candidates_per_case[cid] = cands

        # Iterative reduction for accounts:
        # Resolve a case only when its candidate set shrinks to exactly one row (repeat until no change)
        changed = True
        while changed:
            changed = False
            for cid, cands in list(account_candidates_per_case.items()):
                if len(cands) == 1:
                    chosen = next(iter(cands))
                    self.account_links[cid] = chosen
                    self.account_tiers[cid] = "unique"
                    self.account_cand_counts[cid] = 1
                    del account_candidates_per_case[cid]
                    for other_cid, other_cands in account_candidates_per_case.items():
                        if chosen in other_cands:
                            other_cands.remove(chosen)
                    changed = True
                    break

        # Remaining account cases are ambiguous (or empty)
        for cid, cands in account_candidates_per_case.items():
            self.account_tiers[cid] = "ambiguous"
            self.account_cand_counts[cid] = len(cands)
            u = None
            for req in all_reqs:
                if req["case_id"] == cid:
                    u = id_store.resolve_user(req["email"])
                    break
            if self.ambiguous_account_policy == "padded_user_number" and u:
                m_num = re.search(r"\d+", u.user_id)
                self.account_links[cid] = f"account_{int(m_num.group(0)):03d}" if m_num else None
            else:
                self.account_links[cid] = None

        # 5. Process Analytics Linking (null identifiers)
        # First measure on the sample cases how often the row's reported_result agrees with the request's reported_status
        sample_path = os.path.join(self.dataset_dir, "sample_deletion_requests.csv")
        sample_reqs = []
        if os.path.exists(sample_path):
            with open(sample_path, "r", encoding="utf-8") as f:
                sample_reqs = list(csv.DictReader(f))

        sample_an_agreed = 0
        sample_an_total = 0
        for r in sample_reqs:
            rep_at = parse_iso(r.get("reported_at"))
            rep_status = r.get("reported_status")
            if rep_at:
                for act in all_act:
                    if act.get("event_type") == "ANONYMIZE_RECORD":
                        act_time = parse_iso(act.get("event_time"))
                        if not act_time:
                            continue
                        dt_min = (act_time - rep_at).total_seconds() / 60.0
                        if 45.0 <= dt_min <= 126.5:
                            sample_an_total += 1
                            res_val = act.get("reported_result")
                            agrees = (
                                (rep_status == "COMPLETE" and res_val == "SUCCESS") or
                                (rep_status == "NOT_STARTED" and res_val == "FAILED") or
                                (rep_status == "PARTIALLY_COMPLETE" and res_val in ("PARTIAL", "SUCCESS"))
                            )
                            if agrees:
                                sample_an_agreed += 1

        print(f"Analytics agreement on sample cases: {sample_an_agreed}/{sample_an_total} ({sample_an_agreed/sample_an_total*100:.1f}%)" if sample_an_total else "Analytics agreement on sample cases: 0/0")
        apply_analytics_status_filter = (sample_an_total > 0 and (sample_an_agreed / sample_an_total) >= 0.90)

        analytics_candidates_per_case: Dict[str, Set[str]] = {}

        for r in all_reqs:
            cid = r["case_id"]
            u = id_store.resolve_user(r["email"])
            if u and u.user_id in user_exact_analytics:
                self.analytics_links[cid] = next(iter(user_exact_analytics[u.user_id]))
                self.analytics_tiers[cid] = "exact"
                self.analytics_cand_counts[cid] = 1
                continue

            rep_at = parse_iso(r.get("reported_at"))
            rep_status = r.get("reported_status")
            cands = set()
            if rep_at:
                for act in all_act:
                    if act.get("event_type") == "ANONYMIZE_RECORD":
                        res_id = act.get("resource_id")
                        if res_id in exact_analytics_res_ids:
                            continue
                        act_time = parse_iso(act.get("event_time"))
                        if not act_time:
                            continue
                        dt_min = (act_time - rep_at).total_seconds() / 60.0
                        # within reported_at +50..+115 minutes (widened 10%)
                        if 45.0 <= dt_min <= 126.5:
                            if apply_analytics_status_filter:
                                res_val = act.get("reported_result")
                                if rep_status == "COMPLETE" and res_val != "SUCCESS":
                                    continue
                                elif rep_status == "NOT_STARTED" and res_val != "FAILED":
                                    continue
                                elif rep_status == "PARTIALLY_COMPLETE" and res_val not in ("PARTIAL", "SUCCESS"):
                                    continue
                            cands.add(res_id)
            analytics_candidates_per_case[cid] = cands

        # Iterative reduction for analytics:
        changed = True
        while changed:
            changed = False
            for cid, cands in list(analytics_candidates_per_case.items()):
                if len(cands) == 1:
                    chosen = next(iter(cands))
                    self.analytics_links[cid] = chosen
                    self.analytics_tiers[cid] = "unique"
                    self.analytics_cand_counts[cid] = 1
                    del analytics_candidates_per_case[cid]
                    for other_cid, other_cands in analytics_candidates_per_case.items():
                        if chosen in other_cands:
                            other_cands.remove(chosen)
                    changed = True
                    break

        # Ambiguous analytics are always omitted
        for cid, cands in analytics_candidates_per_case.items():
            self.analytics_tiers[cid] = "ambiguous"
            self.analytics_cand_counts[cid] = len(cands)
            self.analytics_links[cid] = None

        # 6. Log every link decision to code/cache/link_decisions.jsonl
        # Never mark ambiguous as resolved
        os.makedirs(self.cache_dir, exist_ok=True)
        with open(self.decisions_path, "w", encoding="utf-8") as f:
            for r in all_reqs:
                cid = r["case_id"]
                # Account decision
                acc_tier = self.account_tiers.get(cid, "ambiguous")
                acc_res = self.account_links.get(cid) if acc_tier != "ambiguous" else None
                acc_count = self.account_cand_counts.get(cid, 0)
                entry_acc = {
                    "case_id": cid,
                    "resource_id": acc_res,
                    "tier": acc_tier,
                    "candidate_count": acc_count
                }
                f.write(json.dumps(entry_acc) + "\n")

                # Analytics decision
                an_tier = self.analytics_tiers.get(cid, "ambiguous")
                an_res = self.analytics_links.get(cid) if an_tier != "ambiguous" else None
                an_count = self.analytics_cand_counts.get(cid, 0)
                entry_an = {
                    "case_id": cid,
                    "resource_id": an_res,
                    "tier": an_tier,
                    "candidate_count": an_count
                }
                f.write(json.dumps(entry_an) + "\n")

    def get_account_id(self, case_id: str) -> Optional[str]:
        return self.account_links.get(case_id)

    def get_analytics_id(self, case_id: str) -> Optional[str]:
        return self.analytics_links.get(case_id)

    def get_tier_counts(self) -> Dict[str, Dict[str, int]]:
        return {
            "account": dict(Counter(self.account_tiers.values())),
            "analytics": dict(Counter(self.analytics_tiers.values()))
        }

