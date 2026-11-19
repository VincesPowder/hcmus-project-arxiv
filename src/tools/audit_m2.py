"""Parse existing local M1 sources and audit M2 outputs without network access."""
import argparse
import csv
import hashlib
import json
import multiprocessing
import platform
import resource
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline import list_papers, process_paper, write_json_atomic
from gather import find_cited_keys

RANGES = [
    ("24127192", "2606.00734", "2606.05733"),
    ("24127262", "2605.02338", "2605.07337"),
    ("24127465", "2602.09870", "2602.14869"),
    ("24127534", "2512.18231", "2512.23230"),
]


def audit(parsed_dir, output, run=None):
    statuses, totals, kinds, issues = Counter(), Counter(), Counter(), Counter()
    coverage, publication_rows, ref_review, unresolved = [], [], [], []
    seen, per_range = set(), Counter()
    directories = [parsed_dir] if isinstance(parsed_dir, (str, Path)) else parsed_dir
    paths = [path for directory in directories for path in sorted(Path(directory).glob("*.json"))]
    for path in paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        paper = data["arxiv_id"]
        if paper in seen:
            raise ValueError(f"Duplicate publication: {paper}")
        seen.add(paper)
        for student, first, last in RANGES:
            if first <= paper <= last:
                per_range[student] += 1
                break
        else:
            issues["out_of_range"] += 1
        stats = data["stats"]
        states = Counter(v["status"] for v in data["versions"])
        publication_rows.append({"arxiv_id": paper, "versions": len(data["versions"]),
                                 **{s: states[s] for s in ("parsed", "empty", "not_downloaded", "no_root", "timeout", "parse_error")},
                                 **{s: stats[s] for s in ("nodes_before_dedup", "elements_after_dedup",
                                                          "bib_before_dedup", "bib_after_dedup", "hierarchy_problems")}})
        totals.update({k: stats[k] for k in ("nodes_before_dedup", "elements_after_dedup",
                                            "bib_before_dedup", "bib_after_dedup", "hierarchy_problems")})
        elements = {e: (kind, text) for e, kind, text in data["elements"]}
        kinds.update(kind for kind, _ in elements.values())
        refs = {e["citation_key"]: e for e in data["bibtex"]}
        if len(refs) != len(data["bibtex"]):
            issues["duplicate_canonical_keys"] += 1
        for entry in data["bibtex"]:
            for field in ("title", "authors", "year", "doi"):
                if not entry.get(field):
                    issues["references_missing_" + field] += 1
            if entry.get("field_conflicts"):
                issues["references_with_conflicts"] += 1
            if entry.get("extraction_conflicts"):
                issues["references_with_extraction_conflicts"] += 1
            if not entry.get("title") or not entry.get("authors") or entry.get("field_conflicts") or entry.get("extraction_conflicts"):
                ref_review.append({"arxiv_id": paper, **entry})
        for v in data["versions"]:
            statuses[v["status"]] += 1
            coverage.append({"arxiv_id": paper, "version": v["version"], "status": v["status"],
                             "nodes": v["nodes"], "references": v["n_references"],
                             "missing_includes": len(v["missing_includes"]), "warnings": len(v["warnings"]),
                             "error": v["error"]})
            ids = {e for version, e, _ in data["hierarchy"] if version == v["version"]}
            keys, _ = find_cited_keys(" ".join(elements[e][1] for e in ids))
            for key in sorted(keys - refs.keys()):
                unresolved.append({"arxiv_id": paper, "version": v["version"], "citation_key": key})
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    for name, rows, fields in [
        ("coverage.csv", coverage, ["arxiv_id", "version", "status", "nodes", "references", "missing_includes", "warnings", "error"]),
        ("publications.csv", publication_rows, ["arxiv_id", "versions", "parsed", "empty", "not_downloaded", "no_root", "timeout", "parse_error",
                                              "nodes_before_dedup", "elements_after_dedup", "bib_before_dedup", "bib_after_dedup", "hierarchy_problems"]),
        ("unresolved_citations.csv", unresolved, ["arxiv_id", "version", "citation_key"]),
    ]:
        with (output / name).open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
    # JSONL retains arrays, field conflicts and provenance for manual review.
    with (output / "reference_review.jsonl").open("w", encoding="utf-8") as handle:
        for row in ref_review:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    summary = {"publications": len(seen), "version_statuses": dict(statuses),
               "totals": dict(totals), "element_kinds": dict(kinds), "quality_flags": dict(issues),
               "unresolved_citation_occurrences": len(unresolved),
               "range_coverage": [{"student_id": s, "first_id": first, "last_id": last,
                                   "assigned": 5000, "processed": per_range[s],
                                   "missing_output": 5000 - per_range[s]} for s, first, last in RANGES],
               "run": run, "scope_note": "Only supplied local sources; absent ranges are not extrapolated."}
    failures_path = output / "paper_failures.json"
    if failures_path.is_file():
        summary["quarantined_publications"] = len(json.loads(failures_path.read_text(encoding="utf-8")))
    else:
        quarantined = set()
        for directory in directories:
            failure_log = Path(directory).parent / "paper_failures.json"
            if failure_log.is_file():
                quarantined.update(row["key"] for row in json.loads(failure_log.read_text(encoding="utf-8")))
        summary["quarantined_publications"] = len(quarantined)
    write_json_atomic(output / "summary.json", summary)
    return summary


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--corpus-dir")
    parser.add_argument("--aggregate-parsed", nargs="+", help="Audit existing parsed directories; never reparse")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--sample", type=int, help="Deterministic evenly spaced sample; omit for full range")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--timeout", type=int, default=15)
    args = parser.parse_args()
    if args.aggregate_parsed:
        fingerprints = []
        for directory in args.aggregate_parsed:
            sidecar = Path(directory).parent / "source_fingerprint.json"
            if not sidecar.is_file():
                raise ValueError(f"Missing source fingerprint: {sidecar}")
            fingerprints.append(json.loads(sidecar.read_text())["sha256"])
        if len(set(fingerprints)) != 1:
            raise ValueError("Do not aggregate outputs produced by different parser revisions")
        print(json.dumps(audit(args.aggregate_parsed, args.output_dir), ensure_ascii=False, indent=2))
        return
    if not args.corpus_dir:
        parser.error("--corpus-dir is required when not aggregating")
    source = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256(b"".join((source / p).read_bytes() for p in (
        "gather.py", "hier_parser.py", "refs.py", "assemble.py", "pipeline.py"))).hexdigest()
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    fingerprint = output / "source_fingerprint.json"
    if fingerprint.exists() and json.loads(fingerprint.read_text())["sha256"] != digest:
        raise ValueError("Code changed; use a fresh output directory to avoid stale results")
    write_json_atomic(fingerprint, {"sha256": digest, "python": sys.version, "platform": platform.platform()})
    papers = list_papers(args.corpus_dir)
    if args.sample and len(papers) > args.sample:
        papers = [papers[i * (len(papers)-1) // (args.sample-1)] for i in range(args.sample)] if args.sample > 1 else papers[:1]
    jobs = [(str(p), str(output / "parsed"), args.timeout, False, "cited") for p in papers]
    started, counts, failed = time.perf_counter(), Counter(), []
    try:
        from crawler_monitor import ResourceMonitor
        monitor = ResourceMonitor(output)
        monitor.start()
    except ImportError:
        monitor = None
    with multiprocessing.Pool(args.workers, maxtasksperchild=100) as pool:
        for i, result in enumerate(pool.imap_unordered(process_paper, jobs, chunksize=1), 1):
            counts[result["status"]] += 1
            if result["status"] == "failed":
                failed.append(result)
                write_json_atomic(output / "paper_failures.json", failed)
            if i % 25 == 0 or i == len(jobs):
                print(f"{i}/{len(jobs)} {dict(counts)} elapsed={time.perf_counter()-started:.1f}s", flush=True)
    elapsed = time.perf_counter() - started
    resources = monitor.stop() if monitor else {}
    write_json_atomic(output / "paper_failures.json", failed)
    run = {"wall_seconds_parse": elapsed, "selected_papers": len(jobs), "result_counts": dict(counts),
           "workers": args.workers, "timeout_seconds": args.timeout, "sha256": digest,
           "python": sys.version, "platform": platform.platform(), "benchmark": "local macOS, not Colab",
           "main_process_peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss *
               (1 if sys.platform == "darwin" else 1024), **resources}
    summary = audit(output / "parsed", output, run)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
