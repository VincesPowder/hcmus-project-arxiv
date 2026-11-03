# arXiv project — Milestone 1 (crawling) and Milestone 2 (hierarchical parsing)

This repository holds the source code of the group project. It is cumulative: each milestone adds
modules to `src/` without removing the previous ones.

| Milestone | Entry point | What it does |
|---|---|---|
| 1 — Crawling | `src/main.py` | Discovers the assigned arXiv papers, downloads the `.tex`/`.bib` files of **every version**, crawls reference metadata from Semantic Scholar, builds the `papers` and `references` tables. |
| 2 — Parsing | `src/pipeline.py` | Turns the crawled LaTeX into a hierarchical tree per version, cleans and normalizes it, converts references to BibTeX, deduplicates across versions, and produces the `elements`, `hierarchy` and `bibtex` tables. |

Works on Windows, macOS, Linux and Google Colab. **Python 3.10+** required.
No data is stored in this repository (`corpus/`, `work/`, `parsed/` are git-ignored).

---

## Table of contents

1. [Repository layout](#1-repository-layout)
2. [Setup](#2-setup)
3. [Milestone 1 — crawler](#3-milestone-1--crawler)
4. [Milestone 2 — parser](#4-milestone-2--parser)
5. [Running on Google Colab](#5-running-on-google-colab)
6. [Tests](#6-tests)
7. [Performance measurement](#7-performance-measurement)
8. [Troubleshooting](#8-troubleshooting)
9. [Known limitations](#9-known-limitations)

---

## 1. Repository layout

```
<repo>/
|-- README.md
|-- .gitignore
|-- src/
|   |-- requirements.txt
|   |-- main.py                  # M1 entry point
|   |-- crawler_*.py             # M1 modules (config, discovery, download, crawl, references, tables, monitor, utils)
|   |-- run_on_colab.ipynb       # M1 notebook for Colab
|   |-- pipeline.py              # M2 entry point (CLI, multiprocessing, resume, logs, tables)
|   |-- gather.py                # M2: root detection, \input/\include resolution, .bib resolution
|   |-- assemble.py              # M2: element ids, cross-version deduplication, elements/hierarchy rows
|   |-- hier_parser.py           # M2: LaTeX -> tree, cleanup, math normalization       (parser module)
|   |-- refs.py                  # M2: \bibitem/.bib -> BibTeX, reference dedup, \cite renaming  (reference module)
|   |-- dev_stubs.py             # M2: placeholders used ONLY while hier_parser.py / refs.py are missing
|-- tests/
|   |-- helpers.py, test_gather.py, test_assemble.py, test_pipeline.py
```

`pipeline.py` imports `hier_parser` and `refs`; if one of them is not found it prints a warning and
falls back to `dev_stubs.py`. **Numbers produced with the stubs are for plumbing checks only and must
not be used in the report.**

## 2. Setup

**Windows (PowerShell)**
```powershell
cd <repo>
python -m venv .venv
.\.venv\Scripts\Activate.ps1        # if blocked: Set-ExecutionPolicy -Scope Process Bypass
pip install -r src\requirements.txt
```
**macOS / Linux**
```bash
cd <repo>
python3 -m venv .venv && source .venv/bin/activate
pip install -r src/requirements.txt
```
**Google Colab:** `!pip install -q -r src/requirements.txt` (see [section 5](#5-running-on-google-colab)).

`src/requirements.txt` must contain at least:
```
arxiv>=4.0.0
requests>=2.31
pandas>=2.0
pyarrow>=14.0
psutil>=5.9
```
(`pyarrow` is required by Milestone 2 to write the Parquet tables.)

All commands below are run **from the repository root** with `python src/<script>.py`.

---

## 3. Milestone 1 — crawler

Crawls an assigned range of arXiv papers: metadata of every version, `.tex`/`.bib` files of every
version (figures are never stored) and reference metadata from Semantic Scholar.

### 3.1 Identify yourself (required, once per terminal)

arXiv asks for a descriptive User-Agent with a contact email. Replace the example below.

| Shell | Command |
|---|---|
| PowerShell | `$env:CRAWLER_USER_AGENT = "FIT-HCMUS-IntroDS-crawler/you@example.com"` |
| cmd.exe | `set CRAWLER_USER_AGENT=FIT-HCMUS-IntroDS-crawler/you@example.com` |
| bash / zsh | `export CRAWLER_USER_AGENT="FIT-HCMUS-IntroDS-crawler/you@example.com"` |

Optional Semantic Scholar key (faster reference crawl: 1.1 s vs 3.1 s per request):

| Shell | Command |
|---|---|
| PowerShell | `$env:S2_API_KEY = "your-key"` |
| bash / zsh | `export S2_API_KEY="your-key"` |

On Colab add a Secret named `S2_API_KEY` and export it with `os.environ["S2_API_KEY"] = userdata.get("S2_API_KEY")`
in a notebook cell *before* running `!python src/main.py` (the child process cannot read Secrets itself);
the run log must say `Semantic Scholar key: True`. In PowerShell the `$` before `env:` is mandatory and
`export` does not exist. Never commit or share the key. Stop manually at any time with `Ctrl+C`
(progress is saved; rerun to resume).

### 3.2 Run

```bash
# smoke test: first 3 ids only (~1-2 min), separate folders
python src/main.py --range 2605.02338 2605.07337 --limit 3 --corpus-dir test_corpus --work-dir test_work

# full run
python src/main.py --range 2605.02338 2605.07337
# or: python src/main.py --ids-file ids.txt     (one arXiv id per line)
```
Options: `--skip-references` (crawl arXiv only; rerun later without it to fill references), `--corpus-dir`
(default `corpus`), `--work-dir` (default `work`), `--limit N`.
The full run takes several hours (3 s pacing per download). **Resume:** run the same command again;
finished versions/references are skipped, failed ones are retried.

### 3.3 Outputs

```
corpus/2605-02338/paper.json        paper metadata + version list
corpus/2605-02338/references.json   ALL references from Semantic Scholar
corpus/2605-02338/v1/ ... vN/       .tex/.bib only + metadata.json (empty record if PDF-only)
work/tables/papers.parquet          arxiv_id, title, authors, submission_date, revised_dates, venue
work/tables/references.parquet      arxiv_id, ref_arxiv_id, ref_title, ref_authors, ref_submission_date, ref_s2_id
work/papers_log.csv, versions_log.csv, metrics.json, crawl.log
```
`corpus/<paper>/v<N>/metadata.json` is the "done" marker of a version; its `stats.status` is `ok`
(TeX kept), `no_tex` (empty record: PDF-only etc.) — a version without that file was never finished.
**Milestone 2 reads exactly this layout.**

---

## 4. Milestone 2 — parser

### 4.1 Pipeline

For each publication (one folder of the corpus), for each version listed in `paper.json`:

1. **Gather** (`gather.py`) — find the publication root and the compilation path (4.2).
2. **Parse** (`hier_parser.parse_document`) — build the tree of the flattened text; leaves are sentences,
   block formulas and figures/tables; `itemize`-like blocks branch into items; the references section is
   excluded, acknowledgements and appendices are included; cleanup and math normalization are applied.
3. **References** (`refs.extract_references`) — convert `\bibitem` entries and the declared `.bib` files
   to BibTeX fields.

Then, once per publication and in this order (the order matters, see 4.4):

4. **Unify references** across all versions (`refs.unify_bibtex`): one key per reference, fields unionized.
5. **Rename `\cite` keys** in the text of every node (`refs.rename_cites`) to the chosen key.
6. **Assemble** (`assemble.PaperAssembler`) — assign element ids, deduplicate full texts across versions,
   emit `elements` and `hierarchy` rows, and validate the trees.

The result of a publication is written to `parsed/<key>.json`; the report logs and the sharded Parquet
tables are rebuilt from those files, so nothing has to be re-parsed to regenerate them.

### 4.2 Multi-file gathering rules (`gather.py`)

- **Root** = the `.tex` file with `\documentclass` (or `\documentstyle`). Several candidates are common
  (standalone figures, a copy of the paper); the choice is: has `\begin{document}` > not included by any
  other file > flattens to the largest text > shallowest path. File names (`main.tex`...) are never assumed.
- **Includes** `\input`, `\include`, `\subfile`, `\import` and the brace-less `\input file.tex` are followed
  transitively and inlined **in reading order**. Commands inside `%` comments or
  `\begin{comment}` blocks are treated as plain text, never as includes. `\%` is a real percent sign.
  `\includegraphics`, `\inputencoding`, `\includeonly` are **not** includes.
- **Paths:** relative to the root's folder first (as LaTeX does), then to the including file's folder;
  `.tex` suffix optional; `./`, sub-folders, wrong letter case and a unique basename are tolerated; paths
  escaping the version folder are refused.
- **Robustness:** missing targets are recorded (`missing_includes`) and skipped; include cycles are cut
  with a warning; depth and size are capped; sources are read as UTF-8 with a Latin-1 fallback.
- **Redundant files** (`.tex` not reachable from the root) are ignored and counted (`n_unreachable`).
- **Bibliographies:** `\bibliography{a,b}` / `\addbibresource{x.bib}` are resolved to real files
  (`bib_files`, `bib_missing`); `.bib` files that the paper does not declare are ignored.

### 4.3 Reference scope (`--bib-scope`)

Papers often declare large shared libraries (e.g. a collaboration's 800 KB `.bib`). Taking every entry
would put thousands of non-references in the `bibtex` table, so by default
(`--bib-scope cited`) only the `.bib` entries whose key is cited with a `\cite`-like command in the
flattened text are kept (all of them if the paper uses `\nocite{*}`). `\bibitem` entries are always
kept: the `thebibliography` block is the paper's own reference list.
`--bib-scope all` keeps every entry of the declared `.bib` files (useful to report "before/after").

### 4.4 Element identity and deduplication (`assemble.py`)

All versions of a publication share **one element store**.

- Identity of an element = `(kind, cleaned content, occurrence index within the version)`. The content is
  whitespace-collapsed and Unicode-NFC normalized; the comparison happens after cleanup.
- The same identity in two versions gets **one** `element_id`; `hierarchy` holds the per-version
  node-parent edges, so a shared sentence can hang under different parents in different versions.
- The *occurrence index* (n-th time the same kind+content appears in one version) keeps ids unique per
  version, so each version is a real tree even when "See Fig. 1." appears twice or two sections share a
  title.
- Each parsed version has its own root `…#root<v>` (kind `document`, content `Version <v> root`).
  Roots have no hierarchy row.
- Ids look like `2605-02338#s17`: publication key, `#`, kind prefix (`h` heading, `l` list, `i` item,
  `s` sentence, `f` formula, `g` figure/table) and a counter; they are deterministic.
- **Order matters:** reference keys are unified and `\cite{old}` is renamed to `\cite{chosen}` **before**
  texts are compared; otherwise the same sentence citing `vaswani2017` in v1 and `vit_transformer` in v3
  would never match.
- **Empty versions** (no TeX source) are recorded with status `empty` — never skipped — and produce no
  elements. Other statuses: `parsed`, `no_root`, `not_downloaded` (Milestone 1 never finished the version),
  `parse_error`, `timeout`.

### 4.5 Run

```bash
# smoke test (needs a corpus; see the Milestone 1 smoke test above)
python src/pipeline.py --corpus-dir test_corpus --out-dir test_parsed --work-dir test_work_m2 --limit 5 --force --workers 1

# full run (resumable)
python src/pipeline.py --corpus-dir corpus --out-dir parsed --work-dir work_m2 --workers 2
```

| Option | Default | Meaning |
|---|---|---|
| `--corpus-dir` | `corpus` | Milestone 1 corpus (input) |
| `--out-dir` | `parsed` | one `<key>.json` per publication; its existence is the "done" marker |
| `--work-dir` | `work` | logs and metrics |
| `--tables-dir` | `<work-dir>/tables_m2` | sharded Parquet tables |
| `--workers` | CPU count | worker processes (`1` = sequential, easiest to debug) |
| `--timeout` | `120` | seconds allowed per version (needs `SIGALRM`: Linux/macOS/Colab, **no effect on Windows**) |
| `--limit N` | – | first N publications only |
| `--only KEY ...` | – | only these folders, e.g. `--only 2605-02342 2605-02347` |
| `--bib-scope` | `cited` | `cited` or `all` (see 4.3) |
| `--force` | off | reparse even if `parsed/<key>.json` exists |
| `--merge-only` | off | skip parsing; rebuild logs and tables from `parsed/` |
| `--no-merge` | off | parse only; skip logs and tables |
| `--shard-size` | `500` | publications per Parquet shard |

**Resume:** rerun the same command; publications with an existing `parsed/<key>.json` are skipped, failed
ones leave no file and are retried (reason in `parse_errors.jsonl`). Exit code is 1 if any publication failed.
The result of one publication is written atomically (temp file, then rename).
Use `--force` after changing parser code; delete `parsed/` and the work folder before a benchmark run.

### 4.6 Outputs

```
parsed/2605-02338.json                     per publication: versions, elements, hierarchy, bibtex, stats
work/parse.log                             run log
work/parse_errors.jsonl                    publications that failed (one json per line)
work/parse_papers.csv                      one row per publication (report input)
work/parse_versions.csv                    one row per version (report input)
work/parse_metrics.json                    wall time, avg time/publication, peak RAM, output sizes
work/tables_m2/elements/elements-00000.parquet     (+ further shards)
work/tables_m2/hierarchy/hierarchy-00000.parquet
work/tables_m2/bibtex/bibtex-00000.parquet
```

**Tables** (the three tables specified by Milestone 2):

| Table | Columns |
|---|---|
| `elements` | `element_id` (string, non-null), `arxiv_id` (string, non-null), `kind` (dictionary), `content` (string) — one row per *distinct* element across all versions |
| `hierarchy` | `arxiv_id`, `version` (int), `element_id`, `parent_id` — one row per node-parent edge of each version |
| `bibtex` | `arxiv_id`, `citation_key`, `entry_type` (dictionary), `title`, `authors` (list of strings), `year` (int, nullable), `doi`, `journal`, `url` — unified entries after deduplication |

**`parse_papers.csv`:** `arxiv_id, n_versions, versions_parsed, versions_empty, versions_failed, seconds,
nodes_before_dedup, elements_after_dedup, bib_before_dedup, bib_after_dedup, hierarchy_problems, cite_renames`.

**`parse_versions.csv`:** `arxiv_id, version, status, root, n_tex, n_included, n_unreachable,
missing_includes, warnings, n_bib_files, bib_missing, nodes, n_references, error`.

> When reading these CSV files in the report, always use `pd.read_csv(path, dtype={"arxiv_id": str})`;
> otherwise ids such as `2605.02340` are read as numbers and lose the trailing zero.

### 4.7 Contracts between modules

The assembler and the pipeline depend only on these functions, so each module can be developed and
tested separately.

```python
# assemble.py
@dataclass
class Node:                       # the tree contract
    kind: str                     # document | heading | list | item | sentence | formula | figure
    content: str = ""             # cleaned/normalized text (heading title, sentence, LaTeX of a formula...)
    children: list = ...

# gather.py
gather_version(version_dir) -> GatherResult      # .status ok|no_root|no_tex, .root, .text, .included,
                                                 # .unreachable, .missing, .warnings, .bib_files, .bib_missing
find_cited_keys(text) -> (set_of_keys, nocite_star)
load_bib_texts(version_dir, bib_files) -> {relpath: text}

# hier_parser.py
parse_document(text: str) -> Node                # root kind "document"; text = flattened, comment-free

# refs.py
extract_references(gathered, version_dir, bib_scope="cited") -> list[dict]
    # dict keys: citation_key, entry_type, title, authors(list[str]), year(int|None), doi, journal, url
unify_bibtex({version: [entry, ...]}) -> (unified_entries, {dropped_key: chosen_key})
rename_cites(text, {dropped_key: chosen_key}) -> str
```
`unify_bibtex` must keep one key per distinct reference, unionize the fields of duplicates, and never
merge entries that lack the information to be compared (e.g. entries with no title).

---

## 5. Running on Google Colab

Copy the corpus from Drive to the local Colab disk first (reading thousands of small files from Drive is
slow and distorts benchmarks), write results locally, and back them up to Drive. Runtime → Change runtime
type → **CPU** (the benchmark testbed).

```python
# 1. code + requirements
!git clone https://github.com/YOUR-ORG/YOUR-CODE-REPO.git repo 2>/dev/null || (cd repo && git pull)
%cd /content/repo
!pip install -q -r src/requirements.txt
```
```python
# 2. data from Drive (and restore previous progress; no-op the first time)
from google.colab import drive
drive.mount('/content/drive')
DRIVE = '/content/drive/MyDrive/arxiv_backup'
!mkdir -p /content/corpus /content/parsed /content/work_m2
!rsync -a --info=progress2 $DRIVE/corpus/ /content/corpus/
!rsync -a $DRIVE/parsed/ /content/parsed/ 2>/dev/null; rsync -a $DRIVE/work_m2/ /content/work_m2/ 2>/dev/null; echo ok
```
```python
# 3. smoke test, then the full run
!python src/pipeline.py --corpus-dir /content/corpus --out-dir /content/parsed --work-dir /content/work_m2 --limit 5 --force --workers 2
!python src/pipeline.py --corpus-dir /content/corpus --out-dir /content/parsed --work-dir /content/work_m2 --workers 2
```
```python
# 4. back up (repeat periodically for long runs)
!rsync -a /content/parsed/ $DRIVE/parsed/
!rsync -a /content/work_m2/ $DRIVE/work_m2/
```
If the session is lost, rerun cells 1, 2 and 3: finished publications are skipped. A Colab CPU instance has
about 2 cores, hence `--workers 2`. For the benchmark, start from empty `parsed/` and `work_m2/` and do not
use `--limit`.

---

## 6. Tests

```bash
python -m unittest discover -s tests -v      # standard library only; pytest also works
```

- `test_gather.py` — comments (`%`, `\%`, `comment` environment), include forms and resolution, missing files,
  cycles, escaping the folder, unreachable files, `\subfile`, root selection, encodings, `.bib` resolution,
  citation-key detection.
- `test_assemble.py` — shared elements across versions (the example of the statement), whitespace/Unicode
  normalization, repeated sentences and headings, id uniqueness/determinism, statistics, tree validation.
- `test_pipeline.py` — empty / not-downloaded / no-root versions are recorded, one bad version does not kill
  a publication, timeout, resume, `\cite` renaming before text deduplication, field unionization,
  `--bib-scope`.

The pipeline tests replace the parser and reference modules by `dev_stubs.py`, so they keep passing when
the real modules change. The timeout test is skipped on Windows (no `SIGALRM`).

## 7. Performance measurement

`work/parse_metrics.json` (Milestone 2) and `work/metrics.json` (Milestone 1) hold:

- wall time of the run and average seconds per publication (`parse_seconds`, `avg_seconds_per_paper`),
- peak and average RAM of the whole process tree sampled every second with `psutil` (`max_ram_mb`,
  `avg_ram_mb`; very short runs yield only a few samples),
- output size (`output_json_bytes`, `tables_bytes`) and disk growth.

Independent peak-RAM check on Linux/Colab: `/usr/bin/time -v python src/pipeline.py ...`
("Maximum resident set size"). On Windows rely on `parse_metrics.json`.
For a clean benchmark: empty `parsed/` and the work folder, Colab CPU runtime, no `--limit`. A resumed
run only measures the part that was re-executed.

## 8. Troubleshooting

| Symptom | Fix |
|---|---|
| `FileNotFoundError ... 'corpus'` (WinError 3) | The corpus folder does not exist relative to where you run: pass `--corpus-dir <path>` or run the Milestone 1 smoke test first |
| `WARNING: hier_parser not found` / `refs not found` | The parser/reference modules are not in `src/`; the placeholder stubs are used (also printed once per worker on Windows) |
| `ModuleNotFoundError: pyarrow` / `psutil` | Activate the venv, then `pip install -r src/requirements.txt` |
| Ids like `2605.0234` in a table | CSV read without `dtype={"arxiv_id": str}` |
| `n_references` huge (thousands) | A shared `.bib` library; use `--bib-scope cited` (default) |
| `status = no_root` | No file with `\documentclass`/`\documentstyle`; inspect the version folder |
| `status = timeout` on Linux/Colab | Raise `--timeout`; look at the paper in `parse_errors.jsonl`/`parse_versions.csv` |
| A hung paper on Windows | `--timeout` is a no-op there; run the full corpus on Colab/Linux |
| `export ... not recognized` | You are in PowerShell: use `$env:NAME = "..."` |
| `python` not found | Use `py -3` instead of `python` on Windows |
| Many `HTTP 429/403` (Milestone 1) | The crawler stops itself after 5 consecutive arXiv failures; wait 10-15 min and rerun the SAME command. Never lower the 3 s delay; check the User-Agent |
| `refs_status = failed` (HTTP 429) | Add `S2_API_KEY`, rerun |
| `refs_status = not_found` | Paper not indexed by Semantic Scholar (expected for some) |
| Path too long (Windows) | Move the project near the drive root, e.g. `C:\arxiv\` |
| Run interrupted | Rerun the same command (both milestones resume) |

## 9. Known limitations

- Per-version `--timeout` relies on `SIGALRM` and therefore only works on Linux/macOS/Colab.
- Worker processes use `multiprocessing.Pool`; if the operating system kills a worker (out of memory) the run
  can stall: interrupt it and rerun, finished publications are kept.
- Comment handling is textual: `%` inside `verbatim`/`lstlisting` blocks and `\iffalse ... \fi` regions are
  not special-cased; includes produced by user macros or conditional on `\if...` are not followed.
- Unreachable-file detection is only as good as the include resolution; a file included through a macro
  would be reported as unreachable.
- `.bib` files are only read when declared with `\bibliography{}` / `\addbibresource{}`; `.bbl` files are not
  part of the corpus (Milestone 1 keeps `.tex` and `.bib` only).
- The measured numbers of a run (timing, RAM, sizes) belong in the report, not in this file.
  numbers in the report, not in this file.
