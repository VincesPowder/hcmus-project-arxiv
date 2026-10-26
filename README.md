# arXiv crawler — Milestone 1

Crawls an assigned range of arXiv papers: metadata of every version, `.tex`/`.bib` files of
every version (figures are never stored) and reference metadata from Semantic Scholar.
Works on Windows, macOS, Linux and Google Colab. Python 3.10+ required.

## 1. Setup

**Windows (PowerShell)**
```powershell
cd arxiv-crawler
python -m venv .venv
.\.venv\Scripts\Activate.ps1        # if blocked: Set-ExecutionPolicy -Scope Process Bypass
pip install -r src\requirements.txt
```
**macOS / Linux**
```bash
cd arxiv-crawler
python3 -m venv .venv && source .venv/bin/activate
pip install -r src/requirements.txt
```
**Google Colab:** `!pip install -q -r src/requirements.txt` (or use `src/run_on_colab.ipynb`).

## 2. Identify yourself (required, once per terminal)

arXiv asks for a descriptive User-Agent with a contact email. Replace the example below.

| Shell | Command |
|---|---|
| PowerShell | `$env:CRAWLER_USER_AGENT = "FIT-HCMUS-IntroDS-crawler/you@example.com"` |
| cmd.exe | `set CRAWLER_USER_AGENT=FIT-HCMUS-IntroDS-crawler/you@example.com` |
| bash / zsh | `export CRAWLER_USER_AGENT="FIT-HCMUS-IntroDS-crawler/you@example.com"` |

Strongly recommended Semantic Scholar key (steady 1 request/s instead of a shared, throttled pool), same way:

| Shell | Command |
|---|---|
| PowerShell | `$env:S2_API_KEY = "your-key"` |
| bash / zsh | `export S2_API_KEY="your-key"` |

On Colab add a Secret named `S2_API_KEY` and export it with `os.environ["S2_API_KEY"] = userdata.get("S2_API_KEY")` in a notebook cell *before* running `!python src/main.py` (the child process cannot read Secrets itself); the run log must say `Semantic Scholar key: True`. In PowerShell the `$` before `env:` is mandatory and
`export` does not exist. Never commit or share the key.
Stop manually at any time with `Ctrl+C` (progress is saved; rerun to resume).

## 3. Run

```bash
# smoke test: first 3 ids only (~1-2 min), separate folders
python src/main.py --range 2605.02338 2605.07337 --limit 3 --corpus-dir test_corpus --work-dir test_work

# full run
python src/main.py --range 2605.02338 2605.07337
# whole group in one run (recommended by the instructors): merge every member's range
python src/main.py --ids-file group_ids.txt
```
`group_ids.txt` takes one entry per line: a single id or an inclusive range, `#` starts a comment:
```
2605.02338..2605.07337   # member A
2605.07338..2605.12337   # member B
hep-th/9901001
```
Options: `--skip-references` (crawl arXiv only; rerun later without it to fill references), `--corpus-dir` (default `corpus`), `--work-dir` (default `work`), `--limit N`, `--workers N` (parallel arXiv download threads, default 1), `--passes N` (retry rounds for failed items inside one run, default 3).
The full run takes several hours (3 s pacing per download); the group's combined ids take proportionally longer. **Resume:** just run the same
command again; finished versions/references are skipped, failed ones are retried.
Keep the machine awake (on Colab keep the tab open and back up `corpus/` to Drive).

## 4. Outputs

```
corpus/2605-02338/paper.json        paper metadata + version list
corpus/2605-02338/references.json   ALL references from Semantic Scholar
corpus/2605-02338/v1/ ... vN/       .tex/.bib only + metadata.json (empty record if PDF-only)
work/tables/papers.parquet          arxiv_id, title, authors, submission_date, revised_dates, venue
work/tables/references.parquet      arxiv_id, ref_arxiv_id, ref_title, ref_authors, ref_submission_date, ref_s2_id
work/papers_log.csv, versions_log.csv, metrics.json, crawl.log   (inputs of the report)
```
Do not commit `corpus/`, `work/`, `.venv/` to git (see `.gitignore`).

## 5. Performance measurement
`work/metrics.json` holds wall time, discovery time, peak/avg RAM (psutil, 1 s sampling), disk
growth and corpus size. Independent peak-RAM check on Linux/Colab:
`/usr/bin/time -v python src/main.py ...` ("Maximum resident set size"). On Windows rely on `metrics.json`.

## 6. How HTTP 429 is handled

| Mechanism | Behaviour |
|---|---|
| One adaptive limiter per host | arXiv and Semantic Scholar each have a limiter shared by all threads. A 429/403 pauses that host for everybody (`Retry-After`, else 10, 20, 40 ... up to 300 s) and widens the gap between requests (x1.3, max 12 s / 15 s); every success shrinks it back to the base gap (3 s / 1.1 s). |
| Two retry budgets | Throttling: up to 8 cooldowns per request. Transient errors (network, 5xx, truncated body): 4 short backoffs; partial downloads resume with HTTP `Range`. |
| Failed items are queued | After the first pass, failed versions / references / lookups are retried automatically (`--passes`, default 3) after a growing cooldown, so one run normally ends complete. |
| Hard-block stop | After 10 throttles in a row without a single success, or 15 failed versions in a row, the run stops cleanly (exit code 1; references only: exit code 2). Wait 10-15 min and rerun the SAME command. |
| Fewer requests | Semantic Scholar is queried with `POST /paper/batch` (50 papers per request, automatic fallback to one GET per paper); discovery checkpoints every 250 ids and never splits a throttled batch into single calls. |
| Bad API key | If S2 rejects `S2_API_KEY` (401/403) the run continues without it instead of failing every request. |

`work/metrics.json` reports `arxiv_*` / `s2_*` requests, throttled requests, seconds lost to pauses and the final gap, so the report can show how much time the throttling cost (retry and delay time is part of the benchmark runtime). Check the whole mechanism offline with `python tests/test_resilience.py`.

## 7. Troubleshooting

| Symptom | Fix |
|---|---|
| `export ... not recognized` | You are in PowerShell: use `$env:CRAWLER_USER_AGENT = "..."` |
| `python` not found | Use `py -3` instead of `python` on Windows |
| `ModuleNotFoundError` | Activate the venv, then `pip install -r src/requirements.txt` |
| Many `HTTP 429/403` | Normal at first: the crawler pauses the host and retries by itself (section 6). Only after 10 throttles in a row it stops (exit code 1, progress saved): wait 10-15 min, rerun the SAME command. Never lower the 3 s delay; check User-Agent and `S2_API_KEY` |
| `refs_status = failed` (HTTP 429) | Add `S2_API_KEY`, rerun |
| `refs_status = not_found` | Paper not indexed by Semantic Scholar (expected for some) |
| Path too long (Windows) | Move the project near the drive root, e.g. `C:\arxiv\` |
| Run interrupted | Rerun the same command |
