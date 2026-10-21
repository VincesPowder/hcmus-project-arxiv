"""Aggregate a data directory into CSV tables and print the headline numbers for the report."""
import argparse
from pathlib import Path

import pandas as pd

from ..common.paths import read_json


def collect(data_dir: Path):
    paper_rows, version_rows = [], []
    for paper_folder in sorted(p for p in data_dir.iterdir() if p.is_dir()):
        metadata_path = paper_folder / "metadata.json"
        if not metadata_path.exists():
            continue
        metadata = read_json(metadata_path)
        statuses = [read_json(p) for p in sorted(paper_folder.glob("v*/status.json"))]
        version_rows.extend(statuses)
        references_path = paper_folder / "references.json"
        references = read_json(references_path) if references_path.exists() else {}
        paper_rows.append({
            "arxiv_id": metadata["arxiv_id"], "num_versions": metadata["num_versions"],
            "versions_ok": sum(s["status"] == "ok" for s in statuses),
            "versions_empty": sum(s["status"] == "empty" for s in statuses),
            "versions_failed": sum(s["status"] == "failed" for s in statuses),
            "total_uncompressed_bytes": sum(s.get("uncompressed_bytes", 0) for s in statuses),
            "total_kept_bytes": sum(s.get("kept_bytes", 0) for s in statuses),
            "download_seconds": sum(s.get("seconds", 0) for s in statuses),
            "references_status": references.get("status"),
            "num_references": references.get("num_references"),
            "num_references_with_arxiv_id": references.get("num_with_arxiv_id"),
            "references_seconds": references.get("seconds"),
        })
    return pd.DataFrame(paper_rows), pd.DataFrame(version_rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--output-dir", default="stats")
    args = parser.parse_args()
    papers, versions = collect(Path(args.data_dir))
    Path(args.output_dir).mkdir(exist_ok=True)
    papers.to_csv(Path(args.output_dir) / "papers.csv", index=False)
    versions.to_csv(Path(args.output_dir) / "versions.csv", index=False)
    print("papers resolved:", len(papers))
    print("versions:", len(versions), "| ok:", int((versions.status == "ok").sum()),
          "| empty:", int((versions.status == "empty").sum()), "| failed:", int((versions.status == "failed").sum()))
    print("mean bytes per paper before/after figure removal:",
          int(papers.total_uncompressed_bytes.mean()), "/", int(papers.total_kept_bytes.mean()))
    print("mean references per paper:", round(papers.num_references.mean(), 1))
    print("reference crawl success rate:", round((papers.references_status == "ok").mean(), 3))


if __name__ == "__main__":
    main()
