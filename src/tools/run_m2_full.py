"""Resumable M2 orchestration for Colab: existing corpus -> JSON -> audit -> Parquet.

No crawler or external API is called. Input may contain multiple disjoint ranges.
"""
import argparse
import hashlib
import json
import multiprocessing
import os
import platform
import subprocess
import sys
import time
import uuid
import zipfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline import FAIL_STATUSES, list_papers, process_paper, read_json, write_json_atomic, directory_size_bytes
from crawler_monitor import ResourceMonitor
from tools.audit_m2 import audit
from tools.export_verified_m2 import export


def inventory(corpus_dirs):
    """Validate metadata without reading large TeX/BibTeX libraries."""
    papers, ids, keys = [], set(), set()
    versions = 0
    for value in corpus_dirs:
        root = Path(value).expanduser().resolve()
        if not root.is_dir():
            raise FileNotFoundError(f"Corpus directory does not exist: {root}")
        found = list_papers(root)
        if not found:
            raise ValueError(f"No paper.json/paper_metadata.json in child folders of {root}. "
                             "Select corpus/ or work/raw/, not a single version or a parent archive directory.")
        for folder in found:
            marker = folder / "paper.json"
            marker = marker if marker.is_file() else folder / "paper_metadata.json"
            raw = marker.read_bytes()
            data = json.loads(raw)
            arxiv_id = data["arxiv_id"]
            numbers = [v["version"] for v in data["versions"]]
            if not numbers or any(type(n) is not int or n < 1 for n in numbers) or len(set(numbers)) != len(numbers):
                raise ValueError(f"Invalid/duplicate version numbers: {marker}")
            if arxiv_id in ids or folder.name in keys:
                raise ValueError(f"Duplicate publication or output filename: {arxiv_id} ({folder})")
            ids.add(arxiv_id)
            keys.add(folder.name)
            versions += len(numbers)
            papers.append({"arxiv_id": arxiv_id, "paper_key": folder.name, "path": str(folder),
                           "versions": sorted(numbers), "metadata_sha256": hashlib.sha256(raw).hexdigest()})
    if not papers:
        raise ValueError("No publications selected")
    return {"publications": len(papers), "declared_versions": versions, "papers": papers}


def parser_fingerprint():
    source = Path(__file__).resolve().parents[1]
    return hashlib.sha256(b"".join((source / name).read_bytes() for name in (
        "gather.py", "hier_parser.py", "refs.py", "assemble.py", "pipeline.py"))).hexdigest()


def run_full(corpus_dirs, run_dir, *, workers=2, timeout=120, bib_scope="cited",
             limit=0, shard_size=250, retry_incomplete=True, expected_publications=20000):
    if workers < 1 or timeout < 1 or shard_size < 1 or limit < 0:
        raise ValueError("workers, timeout and shard_size must be positive; limit must be >= 0")
    if bib_scope not in {"cited", "all"}:
        raise ValueError("bib_scope must be cited or all")
    inputs = inventory(corpus_dirs)
    selected = inputs["papers"][:limit] if limit else inputs["papers"]
    run_dir = Path(run_dir).expanduser().resolve()
    source_roots = [Path(p).expanduser().resolve() for p in corpus_dirs]
    if any(run_dir == root or root in run_dir.parents or run_dir in root.parents for root in source_roots):
        raise ValueError("RUN_DIR must be separate from input corpus directories")
    run_dir.mkdir(parents=True, exist_ok=True)
    signature = {"parser_sha256": parser_fingerprint(), "bib_scope": bib_scope, "timeout": timeout,
                 "selected_papers": selected}
    identity = run_dir / "run_identity.json"
    if identity.exists():
        if read_json(identity) != signature:
            raise ValueError("Parser, input selection or configuration changed. Use a new RUN_DIR.")
    elif any(run_dir.iterdir()):
        raise ValueError("Non-empty RUN_DIR has no identity; choose a new directory.")
    write_json_atomic(identity, signature)
    write_json_atomic(run_dir / "source_fingerprint.json",
                      {"sha256": signature["parser_sha256"], "python": sys.version, "platform": platform.platform()})
    write_json_atomic(run_dir / "input_inventory.json", inputs)
    work = run_dir / "work"
    work.mkdir(exist_ok=True)
    def announce(message):
        print(message, flush=True)
        with (work / "parse.log").open("a", encoding="utf-8") as handle:
            handle.write(time.strftime("%Y-%m-%d %H:%M:%S") + " " + message + "\n")
    parsed = run_dir / "parsed"
    parsed.mkdir(exist_ok=True)
    expected_keys = {p["paper_key"] for p in selected}
    extras = {p.stem for p in parsed.glob("*.json")} - expected_keys
    if extras:
        raise ValueError(f"Unexpected JSON outputs in RUN_DIR: {sorted(extras)[:5]}")
    jobs = []
    for paper in selected:
        saved = parsed / f"{paper['paper_key']}.json"
        force = False
        if saved.exists():
            try:
                data = read_json(saved)
                if data["arxiv_id"] != paper["arxiv_id"]:
                    raise ValueError(f"Output identity mismatch: {saved}")
                required = {"versions", "elements", "hierarchy", "bibtex", "stats"}
                if not required.issubset(data) or sorted(v["version"] for v in data["versions"]) != paper["versions"]:
                    force = True
                elif retry_incomplete:
                    force = any(v["status"] in FAIL_STATUSES for v in data["versions"])
            except (json.JSONDecodeError, KeyError, TypeError):
                force = True
        jobs.append((paper["path"], str(parsed), timeout, force, bib_scope))
    workers = min(workers, os.cpu_count() or 1)
    if expected_publications and inputs["publications"] != expected_publications:
        announce(f"NOTICE: input has {inputs['publications']} publications; group target is "
                 f"{expected_publications}. Missing ranges are not fabricated.")
    announce(f"Selected {len(jobs)} publications / {inputs['declared_versions']} declared input versions; "
             f"workers={workers}; scope={bib_scope}")
    started = time.perf_counter()
    monitor = ResourceMonitor(run_dir)
    monitor.start()
    failures, statuses = [], Counter()
    exported = None
    try:
        with multiprocessing.Pool(workers, maxtasksperchild=100) as pool:
            for index, result in enumerate(pool.imap_unordered(process_paper, jobs, chunksize=1), 1):
                statuses[result["status"]] += 1
                if result["status"] == "failed":
                    failures.append(result)
                if result["status"] == "failed" or index % 25 == 0 or index == len(jobs):
                    write_json_atomic(run_dir / "paper_failures.json", failures)
                    announce(f"{index}/{len(jobs)} {dict(statuses)} {time.perf_counter()-started:.1f}s")
        parse_seconds = time.perf_counter() - started
        write_json_atomic(run_dir / "paper_failures.json", failures)
        with (work / "parse_errors.jsonl").open("w", encoding="utf-8") as handle:
            for failure in failures:
                handle.write(json.dumps(failure, ensure_ascii=False) + "\n")
        audit_dir = run_dir / "audit"
        summary = audit(parsed, audit_dir)
        if list(parsed.glob("*.json")):
            export_dir = run_dir / "exports" / (time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6])
            exported = export(parsed, export_dir, shard_size)
            exported["directory"] = str(export_dir)
        else:
            export_dir = None
            announce("No successful paper JSON: export skipped; inspect paper_failures.json.")
        wall_seconds = time.perf_counter() - started
    finally:
        resources = monitor.stop()
    try:
        commit = subprocess.check_output(
            ["git", "-C", str(Path(__file__).resolve().parents[2]), "rev-parse", "HEAD"], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        commit = "unknown"
    processed = statuses["done"] + statuses["failed"]
    metrics = {"n_papers_this_run": len(jobs), "status_counts": dict(statuses),
               "n_papers_in_output": summary["publications"], "parse_seconds": parse_seconds,
               "wall_seconds": wall_seconds,
               "avg_seconds_per_paper": parse_seconds / processed if processed else None,
               "avg_end_to_end_seconds_per_processed_paper": wall_seconds / processed if processed else None,
               "workers": workers, "timeout": timeout, "bib_scope": bib_scope, "commit": commit,
               "python": sys.version, "platform": platform.platform(),
               "cpu_count": os.cpu_count(), "parser_sha256": signature["parser_sha256"],
               "output_json_bytes": directory_size_bytes(parsed),
               "tables_bytes": exported["parquet_bytes"] if exported else 0, **resources}
    write_json_atomic(run_dir / "work" / "parse_metrics.json", metrics)
    summary["run"] = metrics
    summary["input_publications"] = inputs["publications"]
    summary["input_declared_versions"] = inputs["declared_versions"]
    summary["selected_publications"] = len(selected)
    summary["selected_declared_versions"] = sum(len(p["versions"]) for p in selected)
    summary["versions_without_paper_output"] = sum(
        len(p["versions"]) for p in selected if not (parsed / f"{p['paper_key']}.json").exists())
    write_json_atomic(audit_dir / "summary.json", summary)
    incomplete = (any(summary["version_statuses"].get(s, 0) for s in FAIL_STATUSES)
                  or summary["quality_flags"].get("ambiguous_citation_keys", 0) > 0)
    result = {"status": "partial" if failures or incomplete else "complete_for_selected_input",
              "run_dir": str(run_dir), "parsed_dir": str(parsed), "audit_dir": str(audit_dir),
              "export": exported, "metrics": metrics, "summary": summary,
              "note": "Input files are read-only. Check coverage and errors before claiming group completion."}
    write_json_atomic(run_dir / "latest_result.json", result)
    announce(f"Finished: {result['status']}; JSON papers={summary['publications']}; "
             f"failed papers={len(failures)}; wall_seconds={wall_seconds:.3f}")
    return result


def make_reports_zip(run_dir):
    """Small reports only. Parsed JSON and Parquet stay in RUN_DIR/Drive."""
    run_dir = Path(run_dir)
    archive = run_dir / "reports.zip"
    candidates = [run_dir / p for p in ("input_inventory.json", "source_fingerprint.json",
                  "run_identity.json", "paper_failures.json", "latest_result.json", "work/parse_metrics.json",
                  "audit/summary.json", "audit/coverage.csv", "audit/publications.csv",
                  "audit/unresolved_citations.csv", "audit/reference_conflicts.jsonl",
                  "work/parse_errors.jsonl")]
    result = read_json(run_dir / "latest_result.json")
    if result.get("export"):
        candidates.append(Path(result["export"]["directory"]) / "export_validation.json")
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as handle:
        for path in candidates:
            if path.is_file():
                handle.write(path, arcname=path.relative_to(run_dir))
    return archive


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--corpus-dirs", nargs="+", required=True)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--bib-scope", choices=["cited", "all"], default="cited")
    parser.add_argument("--limit", type=int, default=0, help="0 = full input")
    parser.add_argument("--shard-size", type=int, default=250)
    parser.add_argument("--expected-publications", type=int, default=20000)
    parser.add_argument("--no-retry-incomplete", action="store_true")
    args = parser.parse_args()
    result = run_full(args.corpus_dirs, args.run_dir, workers=args.workers, timeout=args.timeout,
                      bib_scope=args.bib_scope, limit=args.limit, shard_size=args.shard_size,
                      retry_incomplete=not args.no_retry_incomplete,
                      expected_publications=args.expected_publications)
    archive = make_reports_zip(args.run_dir)
    print(json.dumps({"status": result["status"], "run_dir": result["run_dir"],
                      "export": result["export"], "reports_zip": str(archive)}, ensure_ascii=False, indent=2))
    # Partial source data is a reported result, not an infrastructure exception:
    # subsequent notebook cells still display and verify all available outputs.


if __name__ == "__main__":
    main()
