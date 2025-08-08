# GT Nomenclature Generation Pipeline

This project generates **comprehensive nomenclature and functional profiles** for glycosyltransferase (GT) proteins using a two-step process:

1. **Step 1 (FutureHouse API)** — Submits structured prompts to the FutureHouse platform to generate the initial nomenclature and functional descriptions for each protein.
2. **Step 2 (OpenAI API)** — Summarizes, reformats, and harmonizes the generated content so that the tone, structure, and wording match the provided template and example.

The process uses two prompt templates:
- `./prompts/unified_prompt(step1).txt` — Input to the FutureHouse API for initial content generation.
- `./prompts/summarization_prompt(step2).txt` — Input to the OpenAI API for summarizing and formatting the initial output.
- `./prompts/example_output.txt` — Example formatted output used to guide summarization style.

---

## Setup

1. **Clone the repository** and install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. **Configure API keys**
 - Rename `.env.example` to `.env`
 - Fill in the values for OPENAI_API_KEY and FUTUREHOUSE_API_KEY in the `.env` file.

--

## Workflow

1. **Submit new tasks**

Submits protein-specific prompts to FutureHouse.
Skips proteins already marked as successful for the same prompt unless `--force` is given.

```bash
# normal submit
python gt_pipeline.py submit

# dry run without sending tasks
python gt_pipeline.py submit --dry-run

# force resubmit everything even if previously successful
python gt_pipeline.py submit --force

# disable automatic retries
python gt_pipeline.py submit --no-retry
```

2.	**Poll for results** 

Retrieves FutureHouse task results and updates `./futurehouse/responses.json`.

```bash
# poll only incomplete/failed tasks
python gt_pipeline.py poll

# poll all known task_ids
python gt_pipeline.py poll --all

# optionally save raw FutureHouse answers as .md files
python gt_pipeline.py poll --write-files
```

If `--write-files `is used, raw .md files will be saved in:
`./futurehouse/raw_answers/{PROTEIN}.md`

3.	 **Summarize and format with OpenAI**

Takes successful FutureHouse results from responses.json, applies the step 2 prompt, and saves per-protein formatted outputs.

```bash
python gt_pipeline.py summarize \
  --model gpt-4o-mini \
  --temperature 0.2 \
  --out-dir ./futurehouse/outputs_formatted
```
Formatted output folder will contain:

./futurehouse/outputs_formatted/{PROTEIN}/{PROTEIN}_formatted.md
./futurehouse/outputs_formatted/{PROTEIN}/{PROTEIN}_formatted.docx