"""Milestone 3 - Generate final `predictions` sharded Parquet table.

Loads a trained LightGBM LambdaRank model, scores all candidate pairs in
active `train`/`valid`/`test` partitions (excluding `reserve`/`dropped`),
and writes the 9-column `predictions` table conforming to the Hugging Face schema.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Iterable

import lightgbm as lgb
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from evaluate import CAND_COLS

PREDICTIONS_SCHEMA = pa.schema(
    [
        pa.field("arxiv_id", pa.string(), nullable=False),
        pa.field("partition", pa.dictionary(pa.int8(), pa.string()), nullable=False),
        pa.field("citation_key", pa.string(), nullable=False),
        pa.field("groundtruth_arxiv_id", pa.string(), nullable=True),
        pa.field("cand_1", pa.string(), nullable=True),
        pa.field("cand_2", pa.string(), nullable=True),
        pa.field("cand_3", pa.string(), nullable=True),
        pa.field("cand_4", pa.string(), nullable=True),
        pa.field("cand_5", pa.string(), nullable=True),
    ]
)


def _read_table(path: str | Path) -> pd.DataFrame:
    p = Path(path)
    if p.is_dir() or p.suffix.lower() == ".parquet":
        return pd.read_parquet(p)
    return pd.read_csv(p, dtype=str)


def generate_predictions(
    features_path: str | Path,
    selection_path: str | Path,
    model_path: str | Path,
    feature_cols_path: str | Path,
    labels_path: str | Path | None = None,
    owner: str | None = None,
) -> pd.DataFrame:
    """Score candidate pairs and return the 9-column predictions DataFrame."""
    booster = lgb.Booster(model_file=str(model_path))
    feature_cols = json.loads(Path(feature_cols_path).read_text(encoding="utf-8"))

    feats = _read_table(features_path)
    selection = pd.read_csv(selection_path, dtype=str)

    feats["arxiv_id"] = feats["arxiv_id"].astype(str).str.strip()
    feats["citation_key"] = feats["citation_key"].astype(str).str.strip()
    feats["ref_arxiv_id"] = feats["ref_arxiv_id"].astype(str).str.strip()
    selection["arxiv_id"] = selection["arxiv_id"].astype(str).str.strip()

    # Drop pre-attached partition/kind/owner from features so selection.csv is authoritative
    drop_pre = [c for c in ("partition", "kind", "owner") if c in feats.columns]
    if drop_pre:
        feats = feats.drop(columns=drop_pre)

    # Keep strictly active train/valid/test papers (exclude reserve and dropped)
    sel_active = selection[selection["partition"].isin(["train", "valid", "test"])].copy()
    if owner:
        sel_active = sel_active[sel_active["owner"].astype(str).str.strip() == str(owner).strip()]
    sel_active = sel_active[["arxiv_id", "partition"]].drop_duplicates(subset=["arxiv_id"])

    df = feats.merge(sel_active, on="arxiv_id", how="inner")

    if labels_path:
        labels = pd.read_csv(labels_path, dtype=str)
        labels["arxiv_id"] = labels["arxiv_id"].astype(str).str.strip()
        labels["citation_key"] = labels["citation_key"].astype(str).str.strip()
        labels["groundtruth_arxiv_id"] = labels["groundtruth_arxiv_id"].astype(str).str.strip()
        labels = labels[["arxiv_id", "citation_key", "groundtruth_arxiv_id"]].drop_duplicates(
            subset=["arxiv_id", "citation_key"]
        )
        df = df.merge(labels, on=["arxiv_id", "citation_key"], how="left")
    else:
        df["groundtruth_arxiv_id"] = None

    df["_score"] = booster.predict(df[feature_cols].fillna(0.0))
    df.sort_values(
        ["arxiv_id", "citation_key", "_score", "ref_arxiv_id"],
        ascending=[True, True, False, True],
        inplace=True,
    )

    rows = []
    for (aid, ckey), grp in df.groupby(["arxiv_id", "citation_key"], sort=False):
        cands = grp["ref_arxiv_id"].head(5).tolist()
        cands += [None] * (5 - len(cands))
        first = grp.iloc[0]
        gt = first["groundtruth_arxiv_id"]
        if pd.isna(gt) or str(gt).strip() == "":
            gt = None
        rows.append(
            {
                "arxiv_id": str(aid),
                "partition": str(first["partition"]),
                "citation_key": str(ckey),
                "groundtruth_arxiv_id": gt,
                **{col: val for col, val in zip(CAND_COLS, cands)},
            }
        )

    return pd.DataFrame(rows, columns=[f.name for f in PREDICTIONS_SCHEMA])


def write_sharded_parquet(df: pd.DataFrame, out_dir: Path, shard_size: int = 50000) -> list[Path]:
    """Write predictions DataFrame into sharded Parquet files matching Hugging Face schema."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    if df.empty:
        table = pa.Table.from_pandas(df, schema=PREDICTIONS_SCHEMA, preserve_index=False)
        target = out_dir / "predictions-00000.parquet"
        pq.write_table(table, target)
        return [target]

    for idx, start in enumerate(range(0, len(df), shard_size)):
        chunk = df.iloc[start : start + shard_size]
        table = pa.Table.from_pandas(chunk, schema=PREDICTIONS_SCHEMA, preserve_index=False)
        target = out_dir / f"predictions-{idx:05d}.parquet"
        pq.write_table(table, target)
        written.append(target)
    return written


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate Milestone 3 predictions Parquet shards.")
    parser.add_argument("--features", required=True, help="Path to pairs+features Parquet dir/file or CSV.")
    parser.add_argument("--selection", default="src/tools/m3_selection/selection.csv", help="Path to selection.csv.")
    parser.add_argument("--model", default="work_m3/models/lgbm_ranker.txt", help="Path to trained LightGBM model.")
    parser.add_argument("--feature-cols", default="work_m3/models/feature_cols.json", help="Path to feature_cols.json.")
    parser.add_argument("--labels", default=None, help="Optional merged labels.csv for groundtruth_arxiv_id.")
    parser.add_argument("--owner", default=None, help="Optional MSSV owner filter for per-member HF dataset.")
    parser.add_argument("--out-dir", default="work_m3/predictions", help="Directory to write sharded Parquet files.")
    parser.add_argument("--shard-size", type=int, default=50000, help="Rows per Parquet shard.")
    args = parser.parse_args(list(argv) if argv is not None else None)

    pred_df = generate_predictions(
        features_path=args.features,
        selection_path=args.selection,
        model_path=args.model,
        feature_cols_path=args.feature_cols,
        labels_path=args.labels,
        owner=args.owner,
    )
    shards = write_sharded_parquet(pred_df, Path(args.out_dir), shard_size=args.shard_size)
    print(f"Wrote {len(pred_df):,} prediction rows across {len(shards)} shard(s) in {args.out_dir}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())