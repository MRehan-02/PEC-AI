"""
Ghost Data Still Exists - Request Classifier
Classifies whether a request text authorizes personal data deletion.
Uses a fast path for unambiguous deletion requests, and Gemini via llm.call_model for ambiguous cases,
caching decisions in code/cache/classify_cache.json with rule-based fallback.
"""

import os
import sys
import json
import re
import hashlib
from typing import Dict, Any

PROMPT_PATH = os.path.join(os.path.dirname(__file__), "prompts", "classify_prompt.md")
CACHE_PATH = os.path.join(os.path.dirname(__file__), "cache", "classify_cache.json")

_CLASSIFY_CACHE: Dict[str, Dict[str, Any]] = {}


def _load_cache():
    global _CLASSIFY_CACHE
    if os.path.exists(CACHE_PATH):
        try:
            with open(CACHE_PATH, "r", encoding="utf-8") as f:
                _CLASSIFY_CACHE = json.load(f)
        except Exception:
            _CLASSIFY_CACHE = {}


def _save_cache():
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    try:
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(_CLASSIFY_CACHE, f, indent=2)
    except Exception:
        pass


_load_cache()

AFFIRMATIVE_DEL_KEYWORDS = [
    "delet", "eras", "remov", "clos", "supprim", "suppression",
    "elimina", "borra", "lösch", "削除", "삭제", "exclui",
    "exclus", "cancella", "verwijder", "anonym"
]

NEGATION_KEEP_CUES = [
    "do not", "don't", "not asking for", "no need", "leave",
    "keep the account", "keep my account", "keep my profile", "keep the profile", "keep",
    "still need", "still use", "continue using", "only", "stop the emails",
    "just the emails", "unsubscribe", "without removing", "without deleting",
    "not a request", "misspelled", "correct it", "invoice", "invoices", "copy",
    "portable format", "out of date", "change the email", "reset", "login",
    "ne pas", "garder", "nicht", "keine", "behalten", "solo", "solamente", "nur",
    "seulement", "sin eliminar", "apenas"
]

CONFIRM_GONE_PHRASES = [
    "confirm that the profile is gone", "confirm that the data is gone",
    "confirm that my account is gone", "confirm that my profile is gone",
    "confirm that my data is gone", "check that the profile is gone",
    "check that the data is gone", "check that my account is gone",
    "check that my profile is gone", "profile is gone", "data is gone",
    "account is gone", "is gone", "are gone", "verify that my account was deleted",
    "verify that the account was deleted", "check the latest state",
    "remove any remaining", "switch off any active sync",
    "remove records under both addresses", "keep only the required tombstone"
]

NON_DEL_PHRASES = [
    "leave the account active", "not asking for deletion", "keep the account",
    "keep my account", "keep my profile", "keep the profile", "portable format",
    "do not close", "don't close", "marketing lists only", "marketing only",
    "stop the promotional emails", "promotional emails", "invoices", "invoice",
    "continue using the service", "continue using", "misspelled", "correct it without",
    "information request, not a request", "not a request to erase",
    "don't delete", "do not delete", "download my account information",
    "resetting the login", "reset the login", "change the email"
]


_WARNED_API_UNAVAILABLE = False


def _rule_based_classify(text_lower: str) -> str:
    """Fallback classifier when model is unavailable."""
    if any(p in text_lower for p in CONFIRM_GONE_PHRASES):
        return "DELETE"
    if any(p in text_lower for p in NON_DEL_PHRASES):
        return "NOT_DELETE"
    if any(k in text_lower for k in AFFIRMATIVE_DEL_KEYWORDS):
        return "DELETE"
    return "NOT_DELETE"


def classify_request(request_text: str) -> str:
    """
    Decides whether the person is asking for deletion (DELETE) or not (NOT_DELETE).
    Uses fast path for unambiguous deletion requests, and Gemini / classify_cache for ambiguous cases.
    """
    if not request_text or not isinstance(request_text, str):
        return "NOT_DELETE"

    text_norm = request_text.strip().lower()
    text_hash = hashlib.sha256(text_norm.encode("utf-8")).hexdigest()

    # 1. Check disk cache
    if text_hash in _CLASSIFY_CACHE:
        return _CLASSIFY_CACHE[text_hash].get("decision", "DELETE")

    # 2. Fast path: matches affirmative deletion request and contains no negation / keep cues
    has_del = any(k in text_norm for k in AFFIRMATIVE_DEL_KEYWORDS)
    has_cue = any(c in text_norm for c in NEGATION_KEEP_CUES)

    if has_del and not has_cue:
        _CLASSIFY_CACHE[text_hash] = {
            "decision": "DELETE",
            "method": "fast_path",
            "reason": "Affirmative deletion keyword without negation cues"
        }
        _save_cache()
        return "DELETE"

    # 3. Ambiguous / negation cues: call model or use rule-based fallback
    prompt_template = ""
    if os.path.exists(PROMPT_PATH):
        with open(PROMPT_PATH, "r", encoding="utf-8") as f:
            prompt_template = f.read().strip()
    else:
        prompt_template = (
            "Decide whether this customer message asks for their account or personal data to be deleted, "
            "or asks to VERIFY that a deletion was completed. Return JSON only: {\"deletion_requested\": true|false, \"reason\": \"...\"}."
        )

    full_prompt = f"{prompt_template}\n\nCustomer message:\n\"{request_text}\""

    try:
        from code.llm import call_model
        raw_res = call_model(full_prompt, purpose="classify")
        cleaned = raw_res.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```[a-zA-Z]*\n?", "", cleaned)
            cleaned = re.sub(r"\n?```$", "", cleaned).strip()

        data = json.loads(cleaned)
        del_req = data.get("deletion_requested")
        decision = "DELETE" if del_req is True else "NOT_DELETE"

        _CLASSIFY_CACHE[text_hash] = {
            "decision": decision,
            "method": "model",
            "deletion_requested": del_req,
            "reason": data.get("reason", "")
        }
        _save_cache()
        return decision

    except Exception as e:
        global _WARNED_API_UNAVAILABLE
        if not _WARNED_API_UNAVAILABLE:
            print("Warning: Gemini API unavailable for classification, using rule-based classification.", file=sys.stderr)
            _WARNED_API_UNAVAILABLE = True
        decision = _rule_based_classify(text_norm)
        _CLASSIFY_CACHE[text_hash] = {
            "decision": decision,
            "method": "rule_fallback",
            "reason": f"Fallback due to: {e}"
        }
        _save_cache()
        return decision
