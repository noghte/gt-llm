#!/usr/bin/env python3
# format_outputs_gt.py
import argparse
import json
import os
from pathlib import Path
from typing import Dict, Optional, Iterable

import pypandoc
from dotenv import load_dotenv

try:
    from openai import OpenAI
except Exception:
    OpenAI = None  # type: ignore

PROMPTS_DIR = Path("./prompts")
FH_DIR      = Path("./futurehouse")
RESP_PATH   = FH_DIR / "responses.json"
DEFAULT_OUT = FH_DIR / "formatted"

def load_text(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"Missing file: {path}")
    return path.read_text(encoding="utf-8")

def ensure_openai():
    if OpenAI is None:
        raise RuntimeError("openai package is not installed")
    load_dotenv()
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY missing")
    return OpenAI()

def sanitize(name: str) -> str:
    return "".join(c for c in name if c.isalnum() or c in " -_()").strip()

def build_prompt(raw_answer: str, example_template: str, user_prompt_template: str) -> str:
    if "{RAW_ANSWER}" in user_prompt_template or "{TEMPLATE}" in user_prompt_template:
        return user_prompt_template.replace("{RAW_ANSWER}", raw_answer).replace("{TEMPLATE}", example_template)
    return f"{user_prompt_template}\n\n## Nomenclature\n{raw_answer}\n\n## Template\n{example_template}"

def summarize_one(client, model: str, temperature: float, system_msg: str, user_msg: str) -> str:
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ],
        temperature=temperature,
    )
    return resp.choices[0].message.content or ""

def run_formatter(
    model: str = "gpt-4o-mini",
    temperature: float = 0.0,
    only: Optional[Iterable[str]] = None,
    overwrite: bool = False,
    out_dir: str = str(DEFAULT_OUT),
    system_msg: str = "You are a precise scientific editor. Reformat to match the template. Do not invent content.",
    example_path: str = str(PROMPTS_DIR / "example_output.txt"),
    step2_path: str = str(PROMPTS_DIR / "summarization_prompt(step2).txt"),
    responses_path: str = str(RESP_PATH),
) -> Dict[str, str]:
    client = ensure_openai()
    out_root = Path(out_dir)
    out_root.mkdir(parents=True, exist_ok=True)

    rp = Path(responses_path)
    if not rp.exists():
        raise FileNotFoundError(f"Missing {rp}")
    responses: Dict[str, dict] = json.loads(rp.read_text(encoding="utf-8"))

    example_template = load_text(Path(example_path))
    user_prompt_tmpl = load_text(Path(step2_path))

    combined: Dict[str, str] = {}
    subset = set(only) if only else None

    for tid, rec in responses.items():
        if rec.get("status") != "success":
            continue
        protein = rec.get("protein") or ""
        if not protein:
            continue
        if subset and protein not in subset:
            continue
        raw = rec.get("answer") or ""
        if not raw:
            continue

        safe = sanitize(protein)
        odir = out_root / safe
        odir.mkdir(parents=True, exist_ok=True)
        md_path = odir / f"{safe}_formatted.md"
        docx_path = odir / f"{safe}_formatted.docx"

        if md_path.exists() and not overwrite:
            combined[protein] = md_path.read_text(encoding="utf-8")
            continue

        user_msg = build_prompt(raw, example_template, user_prompt_tmpl)
        formatted = summarize_one(client, model, temperature, system_msg, user_msg)

        md_path.write_text(formatted, encoding="utf-8")
        try:
            pypandoc.convert_text(formatted, to="docx", format="md", outputfile=str(docx_path))
        except Exception as e:
            print(f"Warning: {protein}: .docx conversion failed ({e})")

        combined[protein] = formatted

    (out_root / "formatted.json").write_text(json.dumps(combined, indent=2, ensure_ascii=False), encoding="utf-8")
    return combined

def main():
    ap = argparse.ArgumentParser(description="Format GT nomenclature outputs with OpenAI")
    ap.add_argument("--model", default="gpt-4o-mini")
    ap.add_argument("--temperature", type=float, default=0.0)
    ap.add_argument("--only", nargs="*", default=None, help="Limit to these protein symbols")
    ap.add_argument("--overwrite", action="store_true", help="Overwrite existing outputs")
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT), help="Root output dir")
    ap.add_argument("--system", default="You are a precise scientific editor. Reformat to match the template. Do not invent content.")
    ap.add_argument("--example", default=str(PROMPTS_DIR / "example_output.txt"))
    ap.add_argument("--step2", default=str(PROMPTS_DIR / "summarization_prompt(step2).txt"))
    ap.add_argument("--responses", default=str(RESP_PATH))
    args = ap.parse_args()

    run_formatter(
        model=args.model,
        temperature=args.temperature,
        only=args.only,
        overwrite=args.overwrite,
        out_dir=args.out_dir,
        system_msg=args.system,
        example_path=args.example,
        step2_path=args.step2,
        responses_path=args.responses,
    )
    print(f"Done. Outputs in: {args.out_dir}")

if __name__ == "__main__":
    main()
