"""Build the two Milestone 1 tables (papers, references) from the corpus folder."""
import json
import logging

import pandas as pd

from crawler_utils import read_json


def build_tables(corpus_dir, tables_dir):
    """Write papers.parquet and references.parquet (plus CSV copies) and return both frames."""
    paper_rows, reference_rows = [], []
    for paper_dir in sorted(corpus_dir.iterdir()):
        paper_file = paper_dir / "paper.json"
        if not paper_file.exists():
            continue
        paper = read_json(paper_file)
        paper_rows.append({
            "arxiv_id": paper["arxiv_id"],
            "title": paper["title"],
            "authors": paper["authors"],
            "submission_date": paper["submission_date"],
            "revised_dates": paper["revised_dates"],
            "venue": paper["venue"],
        })
        reference_file = paper_dir / "references.json"
        if not reference_file.exists():
            continue
        for reference in read_json(reference_file)["references"]:
            if not reference["arxiv_id"]:   # only references with an arXiv id are published
                continue
            reference_rows.append({
                "arxiv_id": paper["arxiv_id"],
                "ref_arxiv_id": reference["arxiv_id"],
                "ref_title": reference["title"],
                "ref_authors": reference["authors"],
                "ref_submission_date": reference["publication_date"]
                                       or (str(reference["year"]) if reference["year"] else None),
                "ref_s2_id": reference["s2_id"],
            })
    papers = pd.DataFrame(paper_rows, columns=["arxiv_id", "title", "authors", "submission_date",
                                               "revised_dates", "venue"])
    references = pd.DataFrame(reference_rows, columns=["arxiv_id", "ref_arxiv_id", "ref_title",
                                                       "ref_authors", "ref_submission_date", "ref_s2_id"])
    tables_dir.mkdir(parents=True, exist_ok=True)
    save_table(papers, tables_dir / "papers")
    save_table(references, tables_dir / "references")
    return papers, references


def save_table(table, path_without_suffix):
    """Save as parquet; if no parquet engine is installed, fall back to CSV (lists as JSON text)."""
    try:
        table.to_parquet(f"{path_without_suffix}.parquet", index=False)
    except ImportError:
        logging.getLogger("crawler.tables").warning(
            "pyarrow is not installed -> writing CSV instead. Run: pip install pyarrow")
        flat = table.copy()
        for column in flat.columns:
            flat[column] = flat[column].map(lambda v: json.dumps(v, ensure_ascii=False)
                                            if isinstance(v, list) else v)
        flat.to_csv(f"{path_without_suffix}.csv", index=False, encoding="utf-8")
