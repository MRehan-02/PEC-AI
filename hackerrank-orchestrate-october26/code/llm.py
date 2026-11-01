"""
Ghost Data Still Exists - LLM Interface
Wraps google-genai client for multimodal extraction and logs token usage.
"""

import os
import sys
import time
import json
from datetime import datetime
from typing import Optional
from PIL import Image
from google import genai
from google.genai import errors


def load_config():
    """Reads GEMINI_API_KEY and GEMINI_MODEL from env or .env at repo root."""
    api_key = os.environ.get("GEMINI_API_KEY")
    model = os.environ.get("GEMINI_MODEL")

    if not api_key or not model:
        env_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".env"))
        if os.path.exists(env_path):
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip('"').strip("'")
                    if k == "GEMINI_API_KEY" and not api_key:
                        api_key = v
                    elif k == "GEMINI_MODEL" and not model:
                        model = v

    model = model or "gemini-2.5-flash"
    return api_key, model


_API_KEY, _MODEL = load_config()
_CLIENT = genai.Client(api_key=_API_KEY) if _API_KEY else None


def call_model(prompt_text: str, image_path: Optional[str] = None, purpose: str = "image_extract") -> str:
    """
    Calls the Gemini model with prompt_text and optional image_path.
    Retries up to 3 times with 5s, 15s, and 30s backoff on 429 / 5xx errors.
    Logs usage metadata to code/cache/usage.jsonl.
    """
    global _CLIENT, _API_KEY, _MODEL
    if not _API_KEY or _CLIENT is None:
        _API_KEY, _MODEL = load_config()
        if not _API_KEY:
            raise RuntimeError("GEMINI_API_KEY is missing from environment and .env")
        _CLIENT = genai.Client(api_key=_API_KEY)
    contents = []
    if image_path:
        img = Image.open(image_path)
        contents.append(img)
    contents.append(prompt_text)

    delays = [5, 15, 30]
    last_exc = None

    for attempt in range(len(delays) + 1):
        try:
            response = _CLIENT.models.generate_content(
                model=_MODEL,
                contents=contents
            )
            # Log usage
            input_tokens = 0
            output_tokens = 0
            if hasattr(response, "usage_metadata") and response.usage_metadata:
                input_tokens = getattr(response.usage_metadata, "prompt_token_count", 0) or 0
                output_tokens = getattr(response.usage_metadata, "candidates_token_count", 0) or 0

            cache_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "cache"))
            os.makedirs(cache_dir, exist_ok=True)
            usage_file = os.path.join(cache_dir, "usage.jsonl")

            entry = {
                "timestamp": datetime.now().astimezone().isoformat(),
                "model": _MODEL,
                "purpose": purpose,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens
            }
            with open(usage_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")

            return response.text or ""

        except Exception as e:
            err_str = str(e)
            if "per day limit exceeded" in err_str or "per_day" in err_str:
                raise e
            is_retryable = (
                "429" in err_str or "500" in err_str or "503" in err_str or
                "504" in err_str or "RESOURCE_EXHAUSTED" in err_str or
                "overloaded" in err_str.lower() or "rate" in err_str.lower()
            )
            last_exc = e
            if attempt < len(delays) and is_retryable:
                wait_sec = delays[attempt]
                print(f"[call_model] Retrying after {wait_sec}s due to error: {err_str[:120]}...", file=sys.stderr)
                time.sleep(wait_sec)
            else:
                raise last_exc

