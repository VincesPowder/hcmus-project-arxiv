"""Entry point.  python -m src.run_crawl --ids-file ids/shard_1.txt --stage all"""
import argparse
import json
import time
from pathlib import Path

from .common.config import DEFAULT_DATA_DIR
from .common.logging_utils import setup_logger
from .common.monitor import ResourceMonitor
from .common.paths import read_ids


def run_stage(stage: str, arxiv_ids, data_dir):
    if stage == "discovery":
        from .discovery.resolve_ids import discover
        discover(arxiv_ids, data_dir)
    elif stage == "download":
        from .download.fetch_source import download_all
        download_all(arxiv_ids, data_dir)
    elif stage == "references":
        from .references.s2_crawler import crawl_all_references
        crawl_all_references(arxiv_ids, data_dir)


def main():
    parser = argparse.ArgumentParser(description="arXiv crawler, Milestone 1")
    parser.add_argument("--ids-file", required=True)
    parser.add_argument("--stage", choices=["discovery", "download", "references", "all"], default="all")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    parser.add_argument("--limit", type=int, default=None, help="only the first N ids (for tests and benchmarks)")
    args = parser.parse_args()

    run_name = f"{Path(args.ids_file).stem}_{args.stage}"
    logger = setup_logger(run_name)
    arxiv_ids = read_ids(args.ids_file)[: args.limit]
    stages = ["discovery", "download", "references"] if args.stage == "all" else [args.stage]
    logger.info("run %s: %d ids, stages %s", run_name, len(arxiv_ids), stages)

    with ResourceMonitor(run_name, args.data_dir):
        for stage in stages:
            started = time.time()
            run_stage(stage, arxiv_ids, args.data_dir)
            record = {"stage": stage, "ids_file": args.ids_file, "num_ids": len(arxiv_ids),
                      "start_unix": started, "end_unix": time.time(), "seconds": round(time.time() - started, 1)}
            Path("metrics").mkdir(exist_ok=True)
            with open("metrics/stage_times.jsonl", "a", encoding="utf-8") as handle:
                handle.write(json.dumps(record) + "\n")
            logger.info("stage %s finished in %.0fs", stage, record["seconds"])


if __name__ == "__main__":
    main()
