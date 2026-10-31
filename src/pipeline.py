"""Milestone 2 pipeline: corpus/ (M1) -> parsed/<paper>.json -> tables/ (elements, hierarchy, bibtex).

    python src/pipeline.py --corpus-dir corpus --out-dir parsed --work-dir work --workers 2
    python src/pipeline.py --limit 5 --force            # smoke test
    python src/pipeline.py --merge-only                 # rebuild logs + parquet tables, no parsing

Design
  * Unit of work = one publication. Its result is ONE json file parsed/<key>.json written
    atomically; that file is the "done" marker, so a rerun skips finished papers (resume).
  * Failed papers leave no file and are retried on the next run; the reason goes to
    work/parse_errors.jsonl. A hung regex is cut by a per-version alarm (POSIX only).
  * A version without TeX is recorded with status "empty" (never skipped silently).
  * Tables (sharded parquet) and the report logs are rebuilt from the json files, so the
    report never needs re-parsing.
"""
import argparse
import contextlib
import json
import logging
import multiprocessing
import os
import signal
import sys
import time
from pathlib import Path

from assemble import PaperAssembler, iter_nodes
from gather import gather_version

try:
    from hier_parser import parse_document                       # B
except ImportError:
    from dev_stubs import parse_document
    print("WARNING: hier_parser not found, using dev_stubs (placeholder parser)", file=sys.stderr)
try:
    from refs import extract_references, unify_bibtex, rename_cites  # C
except ImportError:
    from dev_stubs import extract_references, unify_bibtex, rename_cites
    print("WARNING: refs not found, using dev_stubs (placeholder BibTeX)", file=sys.stderr)

LOGGER = logging.getLogger("m2.pipeline")
FAIL_STATUSES = {"parse_error", "timeout", "no_root", "not_downloaded"}


# ----------------------------------------------------------------------------- small utilities
class VersionTimeout(Exception):
    pass


@contextlib.contextmanager
def time_limit(seconds):
    """Raise VersionTimeout after `seconds` (SIGALRM, i.e. Linux/macOS/Colab; no-op on Windows)."""
    if not hasattr(signal, "SIGALRM") or not seconds:
        yield
        return
    def handler(signum, frame):
        raise VersionTimeout(f"more than {seconds} s")
    previous = signal.signal(signal.SIGALRM, handler)
    signal.alarm(int(seconds))
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def write_json_atomic(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    temporary.replace(path)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def directory_size_bytes(path):
    return sum(f.stat().st_size for f in Path(path).rglob("*") if f.is_file()) if Path(path).exists() else 0


# ----------------------------------------------------------------------------- one publication
def parse_one_version(version_dir, timeout, bib_scope="cited"):
    """gather -> parse -> references (.bib + \\bibitem). Returns (record, tree or None, bib entries)."""
    record = {"status": None, "root": None, "n_tex": 0, "n_included": 0, "n_unreachable": 0,
              "missing_includes": [], "warnings": [], "n_bib_files": 0, "bib_missing": 0,
              "nodes": 0, "n_references": 0, "error": None}
    marker = Path(version_dir) / "metadata.json"
    if not marker.exists():                                  # M1 never finished this version
        record["status"] = "not_downloaded"
        return record, None, []
    if read_json(marker)["stats"]["status"] != "ok":         # PDF-only etc.: empty record, kept
        record["status"] = "empty"
        return record, None, []
    try:
        with time_limit(timeout):
            gathered = gather_version(version_dir)
            record.update(n_tex=gathered.n_tex, n_included=len(gathered.included),
                          n_unreachable=len(gathered.unreachable), root=gathered.root,
                          missing_includes=gathered.missing, warnings=gathered.warnings,
                          n_bib_files=len(gathered.bib_files), bib_missing=len(gathered.bib_missing))
            if gathered.status == "no_tex":
                record["status"] = "empty"
                return record, None, []
            if gathered.status == "no_root":
                record["status"] = "no_root"
                return record, None, []
            tree = parse_document(gathered.text)
            entries = extract_references(gathered, version_dir, bib_scope)
    except VersionTimeout as error:
        record.update(status="timeout", error=str(error))
        return record, None, []
    except Exception as error:  # noqa: BLE001 - one bad version must not kill the paper
        record.update(status="parse_error", error=f"{type(error).__name__}: {error}")
        return record, None, []
    record.update(status="parsed", n_references=len(entries))
    return record, tree, entries


def process_paper(job):
    """Worker entry point: parse every version of one publication and write parsed/<key>.json."""
    paper_dir, out_dir, timeout, force, *extra = job
    bib_scope = extra[0] if extra else "cited"
    paper_dir, out_dir = Path(paper_dir), Path(out_dir)
    key = paper_dir.name
    out_file = out_dir / f"{key}.json"
    if out_file.exists() and not force:
        return {"key": key, "status": "skipped"}
    started = time.perf_counter()
    try:
        paper = read_json(paper_dir / "paper.json")
        arxiv_id = paper["arxiv_id"]
        versions, trees, bib_by_version = [], {}, {}
        for version_info in sorted(paper["versions"], key=lambda v: v["version"]):
            number = version_info["version"]
            record, tree, entries = parse_one_version(paper_dir / f"v{number}", timeout, bib_scope)
            record["version"] = number
            versions.append(record)
            if tree is not None:
                trees[number], bib_by_version[number] = tree, entries

        # Order matters: unify references FIRST and rename the \cite keys, THEN compare texts,
        # otherwise the same sentence citing vaswani2017 (v1) / vit_transformer (v3) never matches.
        bibtex, rename_map = unify_bibtex(bib_by_version) if bib_by_version else ([], {})
        assembler = PaperAssembler(key, arxiv_id)
        for number in sorted(trees):
            if rename_map:
                for node in iter_nodes(trees[number]):
                    node.content = rename_cites(node.content, rename_map)
            for record in versions:
                if record["version"] == number:
                    record["nodes"] = assembler.add_version(number, trees[number])
        problems = assembler.validate()
        if problems:
            LOGGER.warning("%s: %d hierarchy problems, e.g. %s", key, len(problems), problems[0])

        n_entries_before = sum(len(entries) for entries in bib_by_version.values())
        seconds = time.perf_counter() - started
        write_json_atomic(out_file, {
            "arxiv_id": arxiv_id, "paper_key": key, "seconds": seconds,
            "versions": versions,
            "elements": [[r["element_id"], r["kind"], r["content"]] for r in assembler.element_rows()],
            "hierarchy": [[v, e, p] for v, e, p in assembler.hierarchy],
            "bibtex": bibtex, "cite_renames": len(rename_map),
            "stats": {**assembler.stats(), "bib_before_dedup": n_entries_before,
                      "bib_after_dedup": len(bibtex), "hierarchy_problems": len(problems)}})
        return {"key": key, "status": "done", "seconds": seconds,
                "n_failed_versions": sum(r["status"] in FAIL_STATUSES for r in versions)}
    except Exception as error:  # noqa: BLE001
        return {"key": key, "status": "failed", "error": f"{type(error).__name__}: {error}",
                "seconds": time.perf_counter() - started}


# ----------------------------------------------------------------------------- run + tables
def list_papers(corpus_dir, only=None, limit=None):
    papers = [p for p in sorted(Path(corpus_dir).iterdir()) if (p / "paper.json").exists()]
    if only:
        papers = [p for p in papers if p.name in set(only)]
    return papers[:limit] if limit else papers


def run_parsing(arguments):
    papers = list_papers(arguments.corpus_dir, arguments.only, arguments.limit)
    jobs = [(str(p), arguments.out_dir, arguments.timeout, arguments.force, arguments.bib_scope)
            for p in papers]
    LOGGER.info("%d papers, %d workers", len(jobs), arguments.workers)
    counts, errors_path = {}, Path(arguments.work_dir) / "parse_errors.jsonl"
    started = time.perf_counter()
    if arguments.workers > 1:
        pool = multiprocessing.Pool(arguments.workers, maxtasksperchild=200)  # recycle workers: bounded RAM
        results = pool.imap_unordered(process_paper, jobs, chunksize=1)
    else:
        pool, results = None, map(process_paper, jobs)
    try:
        for index, result in enumerate(results, 1):
            counts[result["status"]] = counts.get(result["status"], 0) + 1
            if result["status"] == "failed":
                LOGGER.error("%s failed: %s", result["key"], result["error"])
                with errors_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(result, ensure_ascii=False) + "\n")
            if index % 50 == 0 or index == len(jobs):
                LOGGER.info("%d/%d  %s  %.0f s", index, len(jobs), counts, time.perf_counter() - started)
    finally:
        if pool:
            pool.close()
            pool.join()
    return len(jobs), counts


def build_logs_and_tables(out_dir, work_dir, tables_dir, shard_size):
    """Rebuild parse_papers.csv, parse_versions.csv and the sharded parquet tables from parsed/*.json."""
    import pandas as pd
    paper_rows, version_rows = [], []
    writers = TableWriter(tables_dir, shard_size)
    for path in sorted(Path(out_dir).glob("*.json")):
        data = read_json(path)
        statuses = [v["status"] for v in data["versions"]]
        paper_rows.append({
            "arxiv_id": data["arxiv_id"], "n_versions": len(statuses),
            "versions_parsed": statuses.count("parsed"), "versions_empty": statuses.count("empty"),
            "versions_failed": sum(s in FAIL_STATUSES for s in statuses),
            "seconds": data["seconds"], **data["stats"], "cite_renames": data["cite_renames"]})
        for v in data["versions"]:
            version_rows.append({"arxiv_id": data["arxiv_id"], **v,
                                 "missing_includes": len(v["missing_includes"]),
                                 "warnings": len(v["warnings"])})
        writers.add(data)
    writers.close()
    Path(work_dir).mkdir(parents=True, exist_ok=True)
    pd.DataFrame(paper_rows).to_csv(Path(work_dir) / "parse_papers.csv", index=False)
    pd.DataFrame(version_rows).to_csv(Path(work_dir) / "parse_versions.csv", index=False)
    return len(paper_rows)


class TableWriter:
    """Buffer rows and flush every `shard_size` papers into tables/<name>/<name>-00000.parquet."""

    def __init__(self, tables_dir, shard_size):
        self.dir, self.shard_size = Path(tables_dir), shard_size
        self.buffers = {"elements": [], "hierarchy": [], "bibtex": []}
        self.papers_in_buffer, self.shard_index = 0, 0

    def add(self, data):
        arxiv_id = data["arxiv_id"]
        self.buffers["elements"] += [{"element_id": e, "arxiv_id": arxiv_id, "kind": k, "content": c}
                                     for e, k, c in data["elements"]]
        self.buffers["hierarchy"] += [{"arxiv_id": arxiv_id, "version": v, "element_id": e, "parent_id": p}
                                      for v, e, p in data["hierarchy"]]
        for entry in data["bibtex"]:
            year = entry.get("year")
            self.buffers["bibtex"].append({
                "arxiv_id": arxiv_id, "citation_key": entry["citation_key"],
                "entry_type": entry.get("entry_type"), "title": entry.get("title"),
                "authors": list(entry.get("authors") or []),
                "year": int(year) if str(year or "").isdigit() else None,
                "doi": entry.get("doi"), "journal": entry.get("journal"), "url": entry.get("url")})
        self.papers_in_buffer += 1
        if self.papers_in_buffer >= self.shard_size:
            self.flush()

    def flush(self):
        if not self.papers_in_buffer:
            return
        import pyarrow as pa
        import pyarrow.parquet as pq
        string, kind = pa.string(), "dictionary"
        schemas = {
            "elements": [("element_id", string, False), ("arxiv_id", string, False), ("kind", kind, True),
                         ("content", string, True)],
            "hierarchy": [("arxiv_id", string, False), ("version", pa.int32(), True),
                          ("element_id", string, False), ("parent_id", string, False)],
            "bibtex": [("arxiv_id", string, False), ("citation_key", string, False), ("entry_type", kind, True),
                       ("title", string, True), ("authors", pa.list_(string), True), ("year", pa.int32(), True),
                       ("doi", string, True), ("journal", string, True), ("url", string, True)]}
        for name, fields in schemas.items():
            rows = self.buffers[name]
            if not rows:
                continue
            arrays, schema_fields = [], []
            for column, dtype, nullable in fields:
                values = [row.get(column) for row in rows]
                if dtype == "dictionary":
                    array = pa.array(values, type=string).dictionary_encode()
                    dtype = array.type
                else:
                    array = pa.array(values, type=dtype)
                arrays.append(array)
                schema_fields.append(pa.field(column, dtype, nullable))
            target = self.dir / name
            target.mkdir(parents=True, exist_ok=True)
            pq.write_table(pa.Table.from_arrays(arrays, schema=pa.schema(schema_fields)),
                           target / f"{name}-{self.shard_index:05d}.parquet", compression="zstd")
            self.buffers[name] = []
        self.papers_in_buffer, self.shard_index = 0, self.shard_index + 1

    def close(self):
        self.flush()


# ----------------------------------------------------------------------------- CLI
def parse_arguments():
    parser = argparse.ArgumentParser(description="arXiv hierarchical parsing (Milestone 2)")
    parser.add_argument("--corpus-dir", default="corpus", help="M1 corpus")
    parser.add_argument("--out-dir", default="parsed", help="one json per paper (resume markers)")
    parser.add_argument("--work-dir", default="work", help="logs and metrics")
    parser.add_argument("--tables-dir", default=None, help="parquet shards (default: <work-dir>/tables_m2)")
    parser.add_argument("--workers", type=int, default=os.cpu_count() or 1)
    parser.add_argument("--timeout", type=int, default=120, help="seconds per version (POSIX only)")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--only", nargs="*", help="folder names, e.g. 2605-02338")
    parser.add_argument("--bib-scope", choices=["cited", "all"], default="cited",
                        help="cited: keep only \\cite'd entries of the .bib files (default); all: keep every entry")
    parser.add_argument("--force", action="store_true", help="reparse even if parsed/<key>.json exists")
    parser.add_argument("--merge-only", action="store_true", help="skip parsing, rebuild logs and tables")
    parser.add_argument("--no-merge", action="store_true", help="parse only, skip logs and tables")
    parser.add_argument("--shard-size", type=int, default=500, help="papers per parquet shard")
    return parser.parse_args()


def main():
    arguments = parse_arguments()
    work_dir = Path(arguments.work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    tables_dir = Path(arguments.tables_dir) if arguments.tables_dir else work_dir / "tables_m2"
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=[logging.FileHandler(work_dir / "parse.log", encoding="utf-8"),
                                  logging.StreamHandler()])
    from crawler_monitor import ResourceMonitor              # reused from Milestone 1
    monitor = ResourceMonitor(arguments.out_dir if Path(arguments.out_dir).exists() else ".")
    monitor.start()
    wall_started = time.perf_counter()
    n_jobs, counts = 0, {}
    if not arguments.merge_only:
        Path(arguments.out_dir).mkdir(parents=True, exist_ok=True)
        n_jobs, counts = run_parsing(arguments)
    parse_seconds = time.perf_counter() - wall_started
    n_papers = 0
    if not arguments.no_merge:
        n_papers = build_logs_and_tables(arguments.out_dir, work_dir, tables_dir, arguments.shard_size)
    wall_seconds = time.perf_counter() - wall_started
    resources = monitor.stop()
    metrics = {"n_papers_this_run": n_jobs, "status_counts": counts, "n_papers_in_output": n_papers,
               "parse_seconds": parse_seconds, "wall_seconds": wall_seconds,
               "avg_seconds_per_paper": parse_seconds / max(1, n_jobs - counts.get("skipped", 0)),
               "workers": arguments.workers,
               "output_json_bytes": directory_size_bytes(arguments.out_dir),
               "tables_bytes": directory_size_bytes(tables_dir), **resources}
    write_json_atomic(work_dir / "parse_metrics.json", metrics)
    LOGGER.info("Finished in %.1f s. Metrics: %s", wall_seconds, metrics)
    if counts.get("failed"):
        sys.exit(1)


if __name__ == "__main__":
    main()
