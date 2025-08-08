#!/usr/bin/env python3
import argparse
import json
import os
import pypandoc

def sanitize_filename(name: str) -> str:
    return "".join(c for c in name if c.isalnum() or c in " -_()").rstrip()

def main():
    ap = argparse.ArgumentParser(description="Convert existing FutureHouse answers to DOCX/TXT.")
    ap.add_argument("--responses", required=True, help="Path to responses JSON (e.g., ./futurehouse/responses_20250601_231801.json)")
    ap.add_argument("--out", required=True, help="Output directory for files (e.g., ./futurehouse/outputs)")
    ap.add_argument("--overwrite", action="store_true", help="Overwrite existing DOCX/TXT if present")
    args = ap.parse_args()

    with open(args.responses, "r", encoding="utf-8") as f:
        data = json.load(f)

    os.makedirs(args.out, exist_ok=True)

    marker = (
        "Strive for clarity, completeness, and adherence to the style and depth "
        "of the reference template"
    )

    for uid, entry in data.items():
        if "error" in entry:
            continue

        protein = entry.get("protein")
        answer_md = (entry.get("answer") or "").strip()
        if not protein or not answer_md:
            continue

        # Strip old template prefix if present
        if marker in answer_md:
            answer_md = answer_md.split(marker, 1)[1].lstrip("\n")

        fname = sanitize_filename(protein)
        docx_path = os.path.join(args.out, f"{fname}.docx")
        txt_path  = os.path.join(args.out, f"{fname}.txt")

        if not args.overwrite and os.path.exists(docx_path):
            print(f"✖ {docx_path} already exists, skipping. Use --overwrite to regenerate.")
            continue

        try:
            pypandoc.convert_text(answer_md, to="docx", format="md", outputfile=docx_path)
            print(f"✔ Saved DOCX {docx_path}")
            with open(txt_path, "w", encoding="utf-8") as t:
                t.write(answer_md)
            print(f"✔ Saved TXT  {txt_path}")
        except Exception as e:
            print(f"✖ Failed to convert {uid} ({protein}): {e}")

if __name__ == "__main__":
    main()
