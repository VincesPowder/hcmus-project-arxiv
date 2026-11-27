"""Milestone 3 - join `bibtex` and `references`, build the candidate pairs, then the features.

    bibtex (M2) + references (M1)  --pairs.py-->  m x n pairs per publication  --features.py-->  features

Only the publications of `selection.csv` that take part in the task are processed: partition train / valid /
test, kind manual / auto (reserve and dropped publications are skipped). The work is done by chunks of whole
publications, so memory stays bounded and every `*_rank` / `*_gap_best` relative feature is computed on a
complete publication matrix.

Output (`--out-dir`)
    features/features-00000.parquet ...   keys (arxiv_id, citation_key, ref_arxiv_id) + numeric features
    pairs/pairs-00000.parquet ...         only with --save-pairs: raw entry / reference fields of each pair
    features_summary.json                 what was processed, what was skipped and why

The features table carries NO label: `train.py` joins `labels.csv` and `selection.csv` itself.
The output folder is read directly by train.py / predict.py / evaluate.py (`--features <out-dir>/features`).

Example
    python src/build_features.py \\
        --selection src/tools/m3_selection/selection.csv \\
        --bibtex DATA/m2/tables/bibtex \\
        --references DATA/m1/range_2602/references.parquet DATA/m1/range_2606/references.parquet ... \\
        --out-dir work_m3
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Iterable, Iterator

import pandas as pd

from features import build_features
from pairs import build_pairs

ACTIVE_PARTITIONS = {"train", "valid", "test"}
BIBTEX_COLUMNS = ["arxiv_id", "citation_key", "title", "authors", "year", "doi", "journal", "url"]
REFERENCE_COLUMNS = ["arxiv_id", "ref_arxiv_id", "ref_title", "ref_authors", "ref_submission_date"]
LIST_COLUMNS = {"authors", "ref_authors"}


# ----------------------------------------------------------------------------- selection
def read_selection(path: str | Path, owner: str | None = None,
                   kinds: Iterable[str] = ("manual", "auto")) -> list[dict]:
    """Rows of selection.csv that take part in train / valid / test (reserve and dropped are excluded)."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    seen: set[str] = set()
    for row in rows:
        if row["arxiv_id"] in seen:
            raise ValueError(f"Duplicate publication in selection: {row['arxiv_id']}")
        seen.add(row["arxiv_id"])
    kinds = set(kinds)
    return [r for r in rows
            if r["partition"] in ACTIVE_PARTITIONS and r["kind"] in kinds and (not owner or r["owner"] == owner)]


# ----------------------------------------------------------------------------- streaming readers
def _list_value(value):
    """CSV stores authors as a JSON list; Parquet already gives a list."""
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("["):
            try:
                return json.loads(text)
            except ValueError:
                return [text]
        return [text] if text else []
    return value


def _clean_row(row: dict, columns: list[str]) -> dict:
    out = {}
    for c in columns:
        v = row.get(c)
        if c in LIST_COLUMNS:
            v = _list_value(v)
        elif isinstance(v, str) and v == "":
            v = None
        out[c] = v
    return out


def iter_rows(path: str | Path, columns: list[str], wanted: set[str]) -> Iterator[dict]:
    """Rows of one table (CSV, a Parquet file or a Parquet folder) whose arxiv_id is in `wanted`.

    Parquet is filtered by pyarrow while reading, in batches, so the full table is never loaded.
    """
    path = Path(path)
    if path.suffix.lower() == ".csv":
        with path.open(newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                if str(row.get("arxiv_id")) in wanted:
                    yield _clean_row(row, columns)
        return
    import pyarrow.dataset as ds
    dataset = ds.dataset(str(path), format="parquet")
    present = [c for c in columns if c in dataset.schema.names]
    if "arxiv_id" not in present:
        raise ValueError(f"{path} has no arxiv_id column")
    flt = ds.field("arxiv_id").isin(sorted(wanted))
    for batch in dataset.to_batches(columns=present, filter=flt, batch_size=8192):
        for row in batch.to_pylist():
            yield _clean_row(row, columns)


def load_by_paper(paths: list[str], columns: list[str], wanted: set[str]) -> dict[str, list[dict]]:
    by_paper: dict[str, list[dict]] = defaultdict(list)
    for path in paths:
        for row in iter_rows(path, columns, wanted):
            by_paper[str(row["arxiv_id"])].append(row)
    return by_paper


# ----------------------------------------------------------------------------- writing
def write_frame(frame: pd.DataFrame, out_dir: Path, stem: str, index: int, fmt: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{stem}-{index:05d}.{fmt}"
    if fmt == "parquet":
        frame.to_parquet(target, index=False)
    else:
        frame.to_csv(target, index=False)
    return target


def sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


# ----------------------------------------------------------------------------- main job
def run(selection: str, bibtex: list[str], references: list[str], out_dir: str, owner: str | None = None,
        chunk_papers: int = 100, drop_future: bool = False, relative: bool = True,
        save_pairs: bool = False, fmt: str = "parquet", kinds: Iterable[str] = ("manual", "auto")) -> dict:
    started = time.time()
    sel = read_selection(selection, owner=owner, kinds=kinds)
    if not sel:
        raise SystemExit("No publication selected: check --selection / --owner.")
    wanted = {r["arxiv_id"] for r in sel}
    print(f"Selected {len(wanted):,} publications (owner={owner or 'all'}). Reading source tables ...", flush=True)

    entries_by_paper = load_by_paper(bibtex, BIBTEX_COLUMNS, wanted)
    refs_by_paper = load_by_paper(references, REFERENCE_COLUMNS, wanted)
    print(f"  bibtex rows for {len(entries_by_paper):,} papers, references rows for {len(refs_by_paper):,} papers",
          flush=True)

    no_entries = sorted(wanted - set(entries_by_paper))
    no_refs = sorted(wanted - set(refs_by_paper))
    todo = sorted(set(entries_by_paper) & set(refs_by_paper))

    out = Path(out_dir)
    feature_dir, pair_dir = out / "features", out / "pairs"
    n_pairs = n_entries = shard = 0
    produced: set[str] = set()
    for start in range(0, len(todo), chunk_papers):
        chunk = todo[start:start + chunk_papers]
        entries = pd.DataFrame([r for p in chunk for r in entries_by_paper[p]], columns=BIBTEX_COLUMNS)
        refs = pd.DataFrame([r for p in chunk for r in refs_by_paper[p]], columns=REFERENCE_COLUMNS)
        pairs = build_pairs(entries, refs, papers=chunk, drop_future=drop_future)
        if pairs.empty:
            continue
        feats = build_features(pairs, relative=relative)
        write_frame(feats, feature_dir, "features", shard, fmt)
        if save_pairs:
            write_frame(pairs, pair_dir, "pairs", shard, fmt)
        shard += 1
        n_pairs += len(feats)
        n_entries += feats[["arxiv_id", "citation_key"]].drop_duplicates().shape[0]
        produced |= set(feats["arxiv_id"].unique())
        print(f"  shard {shard:>4}: papers {start + 1}-{start + len(chunk)} of {len(todo)}, "
              f"{len(feats):,} pairs", flush=True)

    # a paper can have entries and references but still produce no pair (every reference dropped, ...)
    empty = sorted(set(todo) - produced)
    by_partition = defaultdict(int)
    for r in sel:
        if r["arxiv_id"] in produced:
            by_partition[f"{r['kind']}/{r['partition']}"] += 1
    summary = {
        "selection": str(selection), "selection_sha256": sha256(selection), "owner": owner,
        "kinds": sorted(kinds), "drop_future": drop_future, "relative_features": relative,
        "n_selected": len(wanted), "n_papers_with_pairs": len(produced), "n_pairs": n_pairs,
        "n_entries": n_entries, "n_shards": shard,
        "papers_with_pairs_by_kind_partition": dict(sorted(by_partition.items())),
        "skipped_no_bibtex": no_entries, "skipped_no_references": no_refs, "skipped_no_pairs": empty,
        "seconds": round(time.time() - started, 1),
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / "features_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"Done: {n_pairs:,} pairs, {n_entries:,} entries, {len(produced):,}/{len(wanted):,} papers "
          f"in {summary['seconds']}s -> {feature_dir}")
    skipped = len(no_entries) + len(no_refs) + len(empty)
    if skipped:
        print(f"WARNING: {skipped} selected papers produced no pairs "
              f"(no bibtex: {len(no_entries)}, no references: {len(no_refs)}, no pairs: {len(empty)}). "
              f"See features_summary.json.", file=sys.stderr)
    return summary


def main(argv: Iterable[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Join bibtex + references, build candidate pairs and features.")
    p.add_argument("--selection", default="src/tools/m3_selection/selection.csv")
    p.add_argument("--bibtex", nargs="+", required=True, help="M2 bibtex table: Parquet folder/file or CSV.")
    p.add_argument("--references", nargs="+", required=True,
                   help="M1 references table(s): Parquet folder/file or CSV. Pass every range you need.")
    p.add_argument("--owner", default=None, help="Only the publications of this MSSV (default: whole group).")
    p.add_argument("--out-dir", default="work_m3")
    p.add_argument("--chunk-papers", type=int, default=100, help="Publications per shard / per batch in RAM.")
    p.add_argument("--drop-future", action="store_true",
                   help="Drop pairs whose reference is later than the publication (default: keep, flagged).")
    p.add_argument("--no-relative", action="store_true", help="Skip the within-group relative features.")
    p.add_argument("--save-pairs", action="store_true", help="Also write the raw pairs (large) to <out-dir>/pairs.")
    p.add_argument("--format", choices=["parquet", "csv"], default="parquet")
    p.add_argument("--kinds", nargs="+", default=["manual", "auto"])
    p.add_argument("--strict", action="store_true", help="Exit 1 if any selected publication produced no pair.")
    args = p.parse_args(list(argv) if argv is not None else None)

    summary = run(args.selection, args.bibtex, args.references, args.out_dir, owner=args.owner,
                  chunk_papers=args.chunk_papers, drop_future=args.drop_future, relative=not args.no_relative,
                  save_pairs=args.save_pairs, fmt=args.format, kinds=args.kinds)
    missing = summary["n_selected"] - summary["n_papers_with_pairs"]
    return 1 if (args.strict and missing) else 0


if __name__ == "__main__":
    raise SystemExit(main())
