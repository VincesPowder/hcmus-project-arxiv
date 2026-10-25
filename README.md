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

Optional Semantic Scholar key (faster reference crawl: 1.1 s vs 3.1 s per request), same way:

| Shell | Command |
|---|---|
| PowerShell | `$env:S2_API_KEY = "your-key"` |
| bash / zsh | `export S2_API_KEY="your-key"` |

On Colab add a Secret named `S2_API_KEY`. In PowerShell the `$` before `env:` is mandatory and
`export` does not exist. Never commit or share the key.
Stop manually at any time with `Ctrl+C` (progress is saved; rerun to resume).

## 3. Run

```bash
# smoke test: first 3 ids only (~1-2 min), separate folders
python src/main.py --range 2605.02338 2605.07337 --limit 3 --corpus-dir test_corpus --work-dir test_work

# full run
python src/main.py --range 2605.02338 2605.07337
# or: python src/main.py --ids-file ids.txt     (one arXiv id per line)
```
Options: `--corpus-dir` (default `corpus`), `--work-dir` (default `work`), `--limit N`.
The full run takes several hours (3 s pacing per download). **Resume:** just run the same
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

## 6. Troubleshooting

| Symptom | Fix |
|---|---|
| `export ... not recognized` | You are in PowerShell: use `$env:CRAWLER_USER_AGENT = "..."` |
| `python` not found | Use `py -3` instead of `python` on Windows |
| `ModuleNotFoundError` | Activate the venv, then `pip install -r src/requirements.txt` |
| Many `HTTP 429/403` | The crawler **stops itself** after 5 consecutive arXiv failures (10 for Semantic Scholar) and exits with code 1; progress is saved. Wait 10-15 min, rerun the SAME command (resume). Never lower the 3 s delay; check User-Agent |
| `refs_status = failed` (HTTP 429) | Add `S2_API_KEY`, rerun |
| `refs_status = not_found` | Paper not indexed by Semantic Scholar (expected for some) |
| Path too long (Windows) | Move the project near the drive root, e.g. `C:\arxiv\` |
| Run interrupted | Rerun the same command |
