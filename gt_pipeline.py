#!/usr/bin/env python3
# gt_pipeline.py
import argparse
import csv
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

from dotenv import load_dotenv

try:
    from futurehouse_client import FutureHouseClient, JobNames
    from futurehouse_client.models.app import TaskRequest
except Exception:
    FutureHouseClient = None
    JobNames = None
    TaskRequest = None

DATA_DIR        = Path("./data")
PROMPTS_DIR     = Path("./prompts")
FH_DIR          = Path("./futurehouse")
PROMPT_OUT_DIR  = FH_DIR / "prompts"
RESPONSES_FILE  = FH_DIR / "responses.json"
RAW_OUT_DIR = FH_DIR / "raw_answers"
TSV_FILE        = DATA_DIR / "gta.tsv"
TMPL_STEP1      = PROMPTS_DIR / "unified_prompt(step1).txt"
JOB_NAME        = getattr(JobNames, "FALCON", None)
REQUIRED_COLS   = ["protein_name","protein","gene_synonyms","uniprot","protein_alternative_names","function"]

RETRYABLE_ERRORS = {"timeout","rate_limit","internal","unknown"}
MAX_RETRIES      = 2

def now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"

def load_json(path: Path, default):
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default

def save_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)

def load_tsv(path: Path) -> List[Dict[str,str]]:
    if not path.exists():
        raise FileNotFoundError(f"Input TSV not found: {path}")
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f, delimiter="\t"))

def require_cols(rows: List[Dict[str,str]], required: List[str]) -> None:
    missing = []
    for i, r in enumerate(rows, start=1):
        for col in required:
            val = r.get(col)
            if val in (None, "", "NA", "N/A"):
                missing.append((r.get("uniprot", f"row{i}"), col))
    if missing:
        print("Some rows are missing required fields needed for the prompt:")
        for uid, col in missing[:20]:
            print(f"  - {uid}: missing {col}")
        raise SystemExit(1)

def prompt_hash(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

def latest_by_protein(responses: Dict[str, dict]) -> Dict[str, dict]:
    latest: Dict[str, dict] = {}
    for tid, rec in responses.items():
        p = rec.get("protein")
        if not p:
            continue
        ts = rec.get("timestamp") or ""
        if p not in latest or ts > latest[p].get("timestamp",""):
            latest[p] = rec
    return latest

def should_retry(rec: dict) -> bool:
    return (
        rec.get("status") == "fail"
        and rec.get("error_code") in RETRYABLE_ERRORS
        and int(rec.get("retry_count", 0)) < MAX_RETRIES
    )

def ensure_fh_client() -> "FutureHouseClient":
    if FutureHouseClient is None or TaskRequest is None:
        raise RuntimeError("futurehouse_client is not installed")
    load_dotenv()
    api_key = os.getenv("FUTUREHOUSE_API_KEY")
    if not api_key:
        raise RuntimeError("FUTUREHOUSE_API_KEY missing")
    return FutureHouseClient(api_key=api_key)

def build_prompt_step1(row: Dict[str,str], template_text: str) -> Tuple[str,str]:
    prompt = template_text.format(
        protein_name              = row.get("protein_name",""),
        gene_name                 = row.get("protein",""),
        gene_synonyms             = row.get("gene_synonyms","N/A"),
        uniprotid                 = row.get("uniprot",""),
        protein_alternative_names = row.get("protein_alternative_names","N/A"),
        function                  = (row.get("function") or "N/A")[:1000],
    )
    phash = prompt_hash(prompt)
    return prompt, phash

def cmd_submit(args):
    rows = load_tsv(TSV_FILE)
    require_cols(rows, REQUIRED_COLS)
    tmpl = TMPL_STEP1.read_text(encoding="utf-8")
    responses: Dict[str,dict] = load_json(RESPONSES_FILE, {})
    latest = latest_by_protein(responses)

    to_submit = []
    for row in rows:
        protein = row.get("protein","").strip()
        if not protein:
            continue
        prompt, phash = build_prompt_step1(row, tmpl)
        rec = latest.get(protein)
        if rec and rec.get("status") == "success" and rec.get("prompt_sha256") == phash and not args.force:
            continue
        if rec and should_retry(rec) and not args.no_retry:
            to_submit.append((row, prompt, phash, "retry", rec))
        elif not rec or rec.get("status") in {"fail","unknown",""} or args.force:
            to_submit.append((row, prompt, phash, "new", rec))

    if args.dry_run:
        print(f"[dry-run] Would submit {len(to_submit)} tasks.")
        for row, _, _, tag, _ in to_submit[:20]:
            print(f"  - {row.get('protein')} ({tag})")
        return

    client = ensure_fh_client()
    PROMPT_OUT_DIR.mkdir(parents=True, exist_ok=True)

    submitted = 0
    try:
        for row, prompt, phash, tag, prior in to_submit:
            uniprot = row.get("uniprot","UNKNOWN")
            protein = row.get("protein","")
            prompt_file = PROMPT_OUT_DIR / f"{uniprot}_prompt.txt"
            with open(prompt_file, "w", encoding="utf-8") as pf:
                pf.write(prompt)

            task = TaskRequest(name=JOB_NAME, query=prompt)
            task_id = client.create_task(task)

            rec = {
                "task_id": task_id,
                "timestamp": now_iso(),
                "status": "submitted",
                "error_code": None,
                "retry_count": 0,
                "replaced_by": None,
                "prompt_sha256": phash,
                "uniprot": row.get("uniprot",""),
                "protein": row.get("protein",""),
                "protein_name": row.get("protein_name",""),
                "gene_synonyms": row.get("gene_synonyms",""),
                "protein_alternative_names": row.get("protein_alternative_names",""),
                "function": row.get("function",""),
                "group": row.get("group",""),
                "family": row.get("family",""),
                "subfamily": row.get("subfamily",""),
                "index": row.get("index",""),
            }
            responses[task_id] = rec

            if tag == "retry" and prior and prior.get("task_id"):
                old_tid = prior["task_id"]
                old = responses.get(old_tid, {})
                old["retry_count"] = int(old.get("retry_count", 0)) + 1
                old["replaced_by"] = task_id
                old.setdefault("protein", protein)
                old.setdefault("uniprot", uniprot)
                responses[old_tid] = old

            submitted += 1
            print(f"Submitted {protein} -> {task_id}")
    finally:
        save_json(RESPONSES_FILE, responses)
        try:
            client.close()
        except Exception:
            pass

    print(f"Done. Submitted: {submitted}")

def _normalize_error(status_obj):
    s = str(getattr(status_obj, "status", "") or "").lower()
    if s in {"success","succeeded","completed"}:
        return "success", None
    if s in {"pending","running","in progress"}:
        return "pending", None
    code = None
    for attr in ("error_code","message","detail","error"):
        v = getattr(status_obj, attr, None)
        if isinstance(v, str) and v:
            code = v.lower().replace(" ", "_")[:64]
            break
    if not code:
        code = "unknown"
    if "timeout" in code:
        code = "timeout"
    elif "rate" in code and "limit" in code:
        code = "rate_limit"
    elif "auth" in code:
        code = "auth"
    elif "validat" in code or "prompt_too_long" in code:
        code = "validation"
    elif "internal" in code or "server" in code:
        code = "internal"
    return "fail", code

def cmd_poll(args):
    responses: Dict[str,dict] = load_json(RESPONSES_FILE, {})
    if not responses:
        print("No responses.json found or it is empty.")
        return
    task_ids = []
    for tid, rec in responses.items():
        st = rec.get("status","")
        if args.all or st not in {"success","fail"}:
            task_ids.append(tid)
    if not task_ids:
        print("No tasks to poll.")
        return

    client = ensure_fh_client()
    updated = False
    try:
        for tid in task_ids:
            status = client.get_task(tid)
            mapped, err_code = _normalize_error(status)
            if mapped == "success":
                rec = responses.get(tid, {})
                rec.update({
                    "timestamp": now_iso(),
                    "status": "success",
                    "error_code": None,
                    "successful": getattr(status, "has_successful_answer", True),
                    "answer": getattr(status, "formatted_answer", None),
                    "reasoning": getattr(status, "answer_reasoning", None),
                })
                responses[tid] = rec
                updated = True
                # write raw FutureHouse answer as .md for quick inspection
                RAW_OUT_DIR.mkdir(parents=True, exist_ok=True)
                protein = rec.get("protein") or f"{tid}"
                safe = "".join(c for c in protein if c.isalnum() or c in " -_()").strip()
                raw_md = getattr(status, "formatted_answer", None) or ""
                (raw_path := RAW_OUT_DIR / f"{safe}.md").write_text(raw_md, encoding="utf-8")
                print(f"{tid} -> success")
                print(f"Wrote raw FutureHouse answer to {raw_path}")


            elif mapped == "pending":
                print(f"{tid} -> pending")
            else:
                rec = responses.get(tid, {})
                rec.update({
                    "timestamp": now_iso(),
                    "status": "fail",
                    "error_code": err_code or rec.get("error_code") or "unknown",
                })
                responses[tid] = rec
                updated = True
                print(f"{tid} -> fail ({rec['error_code']})")
    finally:
        if updated:
            save_json(RESPONSES_FILE, responses)
        try:
            client.close()
        except Exception:
            pass
    print("Polling complete.")

def cmd_summarize(args):
    # Use shared formatter module.
    from format_outputs_gt import run_formatter
    out_dir = str((Path("./futurehouse") / "formatted"))
    run_formatter(
        model=args.model,
        temperature=args.temperature,
        only=None,
        overwrite=False,
        out_dir=out_dir,
        system_msg="You are a precise scientific editor. Reformat to match the template. Do not invent content.",
        example_path=str(Path("./prompts") / "example_output.txt"),
        step2_path=str(Path("./prompts") / "summarization_prompt(step2).txt"),
        responses_path=str(Path("./futurehouse") / "responses.json"),
    )
    print(f"Wrote formatted outputs to {out_dir}")

def main():
    parser = argparse.ArgumentParser(description="GT nomenclature pipeline (submit | poll | summarize)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_submit = sub.add_parser("submit", help="Submit tasks to FutureHouse")
    p_submit.add_argument("--dry-run", action="store_true", help="Show what would be submitted without doing it")
    p_submit.add_argument("--force", action="store_true", help="Submit even if there is a prior success for the same prompt")
    p_submit.add_argument("--no-retry", action="store_true", help="Do not retry failed tasks even if retryable")
    p_submit.set_defaults(func=cmd_submit)

    p_poll = sub.add_parser("poll", help="Poll FutureHouse for task status and fetch answers")
    p_poll.add_argument("--all", action="store_true", help="Poll all known task_ids, not just non-terminal")
    p_poll.set_defaults(func=cmd_poll)

    p_sum = sub.add_parser("summarize", help="Summarize successful answers with OpenAI using step2 template")
    p_sum.add_argument("--model", default="gpt-4o-mini", help="OpenAI model name")
    p_sum.add_argument("--temperature", type=float, default=0.2)
    p_sum.set_defaults(func=cmd_summarize)

    args = parser.parse_args()
    args.func(args)

if __name__ == "__main__":
    main()
