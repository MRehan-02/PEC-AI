"""
Ghost Data Still Exists - Identity Indexer
Reconstructs current and historical identity fields for all users and links resources.
"""

import os
import csv
import re
from dataclasses import dataclass, field
from typing import Dict, Set, Optional, Tuple
from collections import defaultdict


@dataclass
class UserProfile:
    user_id: str
    emails: Set[str] = field(default_factory=set)
    phones: Set[str] = field(default_factory=set)
    hashes: Set[str] = field(default_factory=set)
    resource_ids: Set[str] = field(default_factory=set)
    account_id: Optional[str] = None


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


class IdentityStore:
    def __init__(self, dataset_dir: str = "dataset"):
        if not os.path.isabs(dataset_dir):
            self.dataset_dir = os.path.abspath(dataset_dir) if os.path.exists(dataset_dir) else os.path.join(REPO_ROOT, dataset_dir)
        else:
            self.dataset_dir = dataset_dir
        self.email_to_uid: Dict[str, str] = {}
        self.phone_to_uid: Dict[str, str] = {}
        self.hash_to_uid: Dict[str, str] = {}
        self.profiles: Dict[str, UserProfile] = {}
        self._build_index()

    def _build_index(self):
        # 1. Load user_identities.csv
        identities_path = os.path.join(self.dataset_dir, "user_identities.csv")
        if os.path.exists(identities_path):
            with open(identities_path, "r", encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    uid = r["user_id"]
                    if uid not in self.profiles:
                        self.profiles[uid] = UserProfile(user_id=uid)
                    prof = self.profiles[uid]

                    em = r["email"].strip().lower() if r.get("email") else None
                    ph = r["phone"].strip() if r.get("phone") else None
                    h = r["email_hash"].strip().lower() if r.get("email_hash") else None

                    if em and em != "null":
                        self.email_to_uid[em] = uid
                        prof.emails.add(em)
                    if ph and ph != "null":
                        self.phone_to_uid[ph] = uid
                        prof.phones.add(ph)
                    if h and h != "null":
                        self.hash_to_uid[h] = uid
                        prof.hashes.add(h)

        # 2. Load user_audit_log.csv for historical identifiers
        audit_path = os.path.join(self.dataset_dir, "user_audit_log.csv")
        if os.path.exists(audit_path):
            with open(audit_path, "r", encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    uid = r["user_id"]
                    if uid not in self.profiles:
                        self.profiles[uid] = UserProfile(user_id=uid)
                    prof = self.profiles[uid]

                    field = r.get("field_name")
                    for val in [r.get("old_value"), r.get("new_value")]:
                        if not val or val.strip().lower() in ("null", ""):
                            continue
                        clean_val = val.strip()
                        if field == "email":
                            em = clean_val.lower()
                            self.email_to_uid[em] = uid
                            prof.emails.add(em)
                        elif field == "email_hash":
                            h = clean_val.lower()
                            self.hash_to_uid[h] = uid
                            prof.hashes.add(h)
                        elif field == "phone":
                            self.phone_to_uid[clean_val] = uid
                            prof.phones.add(clean_val)

        # 3. Load LINK_RESOURCE_IDENTITY from system_activity.csv
        act_path = os.path.join(self.dataset_dir, "system_activity.csv")
        if os.path.exists(act_path):
            with open(act_path, "r", encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    if r.get("event_type") == "LINK_RESOURCE_IDENTITY":
                        res_id = r.get("resource_id")
                        details = r.get("details", "")
                        m_u = re.search(r"\b(user_\d+)\b", details)
                        m_em = re.search(r"([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)", details)
                        uid = m_u.group(1) if m_u else (self.email_to_uid.get(m_em.group(1).lower()) if m_em else None)
                        if uid and uid in self.profiles:
                            self.profiles[uid].resource_ids.add(res_id)

    def resolve_user(self, email: str) -> Optional[UserProfile]:
        if not email:
            return None
        uid = self.email_to_uid.get(email.strip().lower())
        if uid:
            return self.profiles.get(uid)
        return None
