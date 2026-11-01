"""
Ghost Data Still Exists - Image Evidence Extractor
Extracts structured observation JSON from screenshot evidence via Gemini.
"""

import os
import sys
import csv
import json
import re
from typing import Dict, Any, List

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from code.llm import call_model


def strip_code_fences(text: str) -> str:
    """Removes markdown code fences like ```json and ```."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    return text.strip()


def extract_single_image(
    file_id: str,
    img_path: str,
    prompt_text: str,
    cache_dir: str = "code/cache/images"
) -> Dict[str, Any]:
    """
    Extracts and caches parsed JSON for an image file.
    Skips API call if cache file already exists.
    """
    os.makedirs(cache_dir, exist_ok=True)
    cache_file = os.path.join(cache_dir, f"{file_id}.json")
    error_file = os.path.join(cache_dir, f"{file_id}_error.txt")

    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass  # Re-extract if cache is corrupted

    raw_text = call_model(prompt_text, img_path, purpose=f"image_extract_{file_id}")
    cleaned = strip_code_fences(raw_text)

    try:
        data = json.loads(cleaned)
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        return data
    except Exception as e:
        with open(error_file, "w", encoding="utf-8") as f:
            f.write(f"ERROR: {e}\n\nRAW TEXT:\n{raw_text}")
        print(f"FAILED to parse JSON for {file_id}: {e}", file=sys.stderr)
        return {"error": str(e), "raw_text": raw_text, "records": []}


def process_all_images(
    manifest_path: str = "dataset/evidence_files.csv",
    dataset_dir: str = "dataset",
    prompt_path: str = "code/prompts/image_prompt.md",
    cache_dir: str = "code/cache/images"
) -> Dict[str, Dict[str, Any]]:
    """
    Processes all PNGs listed in manifest.
    Tests evidence_04 first, prints its JSON, then processes the rest.
    Prints one line per file: file_id, record count.
    """
    with open(prompt_path, "r", encoding="utf-8") as f:
        prompt_text = f.read()

    png_files = []
    with open(manifest_path, "r", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["file_path"].lower().endswith(".png"):
                full_path = os.path.join(dataset_dir, r["file_path"])
                png_files.append((r["file_id"], full_path))

    results = {}

    # 1. Process evidence_04 first
    ev04 = [p for p in png_files if p[0] == "evidence_04"]
    if ev04:
        fid, fpath = ev04[0]
        data = extract_single_image(fid, fpath, prompt_text, cache_dir)
        results[fid] = data
        print(f"=== Extracted JSON for {fid} ===")
        print(json.dumps(data, indent=2))
        print("=" * 40)

    # 2. Process all other PNG files
    for fid, fpath in png_files:
        if fid not in results:
            data = extract_single_image(fid, fpath, prompt_text, cache_dir)
            results[fid] = data

    print("\nImage Extraction Summary:")
    for fid, _ in png_files:
        rec_count = len(results.get(fid, {}).get("records", []))
        print(f"{fid}: {rec_count} records")

    return results


if __name__ == "__main__":
    process_all_images()

