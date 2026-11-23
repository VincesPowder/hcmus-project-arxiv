"""Milestone 3 - Evaluation (MRR@5, Hits@K, and Error Analysis).

Evaluates reference matching predictions strictly on labeled queries
(entries with a non-null `groundtruth_arxiv_id`), reporting overall,
per-partition, and manual-vs-auto breakdowns.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Iterable

import pandas as pd

CAND_COLS = ["cand_1", "cand_2", "cand_3", "cand_4", "cand_5"]


def _is_present(val) -> bool:
    if val is None or pd.isna(val):
        return False
    s = str(val).strip()
    return s != "" and s.lower() != "none" and s.lower() != "nan"


def compute_query_ranks(predictions_df: pd.DataFrame) -> pd.DataFrame:
    """Attach `rank` (1..5 or None) and `rr` (reciprocal rank) to labeled rows."""
    df = predictions_df.copy()
    mask = df["groundtruth_arxiv_id"].apply(_is_present)
    labeled = df.loc[mask].copy()

    ranks = []
    rrs = []
    for _, row in labeled.iterrows():
        gt = str(row["groundtruth_arxiv_id"]).strip()
        found_rank = None
        for idx, col in enumerate(CAND_COLS, start=1):
            if col in row and _is_present(row[col]) and str(row[col]).strip() == gt:
                found_rank = idx
                break
        ranks.append(found_rank)
        rrs.append(1.0 / found_rank if found_rank is not None else 0.0)

    labeled["rank"] = ranks
    labeled["rr"] = rrs
    return labeled


def summarize_metrics(ranked_df: pd.DataFrame) -> dict:
    """Compute MRR@5, Hits@1, Hits@5, and query counts from a ranked DataFrame."""
    n_queries = int(len(ranked_df))
    if n_queries == 0:
        return {
            "n_queries": 0,
            "mrr_at_5": 0.0,
            "hits_at_1": 0.0,
            "hits_at_5": 0.0,
            "n_hits_at_1": 0,
            "n_hits_at_5": 0,
        }

    n_hits_1 = int((ranked_df["rank"] == 1).sum())
    n_hits_5 = int(ranked_df["rank"].notna().sum())
    return {
        "n_queries": n_queries,
        "mrr_at_5": float(ranked_df["rr"].mean()),
        "hits_at_1": float(n_hits_1 / n_queries),
        "hits_at_5": float(n_hits_5 / n_queries),
        "n_hits_at_1": n_hits_1,
        "n_hits_at_5": n_hits_5,
    }


def mrr_at_5(predictions_df: pd.DataFrame) -> float:
    """Return scalar MRR@5 over all labeled rows in `predictions_df`."""
    ranked = compute_query_ranks(predictions_df)
    return summarize_metrics(ranked)["mrr_at_5"]


def evaluate_predictions(
    predictions_df: pd.DataFrame,
    labels_df: pd.DataFrame | None = None,
    selection_df: pd.DataFrame | None = None,
) -> dict:
    """Compute overall, per-partition, and manual-vs-auto MRR@5 breakdowns."""
    df = predictions_df.copy()

    # Backfill groundtruth_arxiv_id and label_source from labels_df if provided
    if labels_df is not None and not labels_df.empty:
        lbl = labels_df.copy()
        for col in ("arxiv_id", "citation_key", "groundtruth_arxiv_id"):
            lbl[col] = lbl[col].astype(str).str.strip()
        keep_cols = ["arxiv_id", "citation_key", "groundtruth_arxiv_id"]
        if "label_source" in lbl.columns:
            keep_cols.append("label_source")
        lbl = lbl[keep_cols].drop_duplicates(subset=["arxiv_id", "citation_key"])

        df["arxiv_id"] = df["arxiv_id"].astype(str).str.strip()
        df["citation_key"] = df["citation_key"].astype(str).str.strip()
        df = df.merge(lbl, on=["arxiv_id", "citation_key"], how="left", suffixes=("", "_lbl"))
        if "groundtruth_arxiv_id_lbl" in df.columns:
            df["groundtruth_arxiv_id"] = df["groundtruth_arxiv_id"].where(
                df["groundtruth_arxiv_id"].apply(_is_present),
                df["groundtruth_arxiv_id_lbl"],
            )
            df.drop(columns=["groundtruth_arxiv_id_lbl"], inplace=True)

    # Fallback label_source from selection.csv `kind` if not already present
    if "label_source" not in df.columns and selection_df is not None and not selection_df.empty:
        sel = selection_df[["arxiv_id", "kind"]].drop_duplicates(subset=["arxiv_id"]).copy()
        sel["arxiv_id"] = sel["arxiv_id"].astype(str).str.strip()
        sel["label_source"] = sel["kind"].map(
            lambda k: "manual" if str(k).startswith("manual") else "auto"
        )
        df["arxiv_id"] = df["arxiv_id"].astype(str).str.strip()
        df = df.merge(sel[["arxiv_id", "label_source"]], on="arxiv_id", how="left")

    ranked = compute_query_ranks(df)

    report = {
        "overall": summarize_metrics(ranked),
        "by_partition": {},
        "by_partition_and_source": {},
    }

    if "partition" in ranked.columns:
        for part, grp in ranked.groupby("partition", dropna=True):
            report["by_partition"][str(part)] = summarize_metrics(grp)

        if "label_source" in ranked.columns:
            for (part, src), grp in ranked.groupby(["partition", "label_source"], dropna=True):
                key = f"{part}_{src}"
                report["by_partition_and_source"][key] = summarize_metrics(grp)

    return report


def extract_errors(
    predictions_df: pd.DataFrame,
    pairs_df: pd.DataFrame | None = None,
    partition: str | None = "test",
    limit: int = 100,
) -> pd.DataFrame:
    """Return misranked queries (rank > 1 or not in top-5) for error analysis."""
    ranked = compute_query_ranks(predictions_df)
    if partition and "partition" in ranked.columns:
        ranked = ranked[ranked["partition"] == partition]

    errors = ranked[(ranked["rank"].isna()) | (ranked["rank"] > 1)].copy()
    errors["error_type"] = errors["rank"].apply(
        lambda r: "miss_outside_top5" if pd.isna(r) else f"ranked_at_{int(r)}"
    )

    if pairs_df is not None and not pairs_df.empty and not errors.empty:
        # Attach feature values for both cand_1 (predicted top) and groundtruth_arxiv_id
        feat_cols = [
            c
            for c in pairs_df.columns
            if c not in {"arxiv_id", "citation_key", "ref_arxiv_id", "label"}
            and pd.api.types.is_numeric_dtype(pairs_df[c])
        ][:8]
        if feat_cols:
            gt_feats = pairs_df[["arxiv_id", "citation_key", "ref_arxiv_id"] + feat_cols].rename(
                columns={"ref_arxiv_id": "groundtruth_arxiv_id", **{c: f"gt_{c}" for c in feat_cols}}
            )
            c1_feats = pairs_df[["arxiv_id", "citation_key", "ref_arxiv_id"] + feat_cols].rename(
                columns={"ref_arxiv_id": "cand_1", **{c: f"c1_{c}" for c in feat_cols}}
            )
            errors = errors.merge(gt_feats, on=["arxiv_id", "citation_key", "groundtruth_arxiv_id"], how="left")
            errors = errors.merge(c1_feats, on=["arxiv_id", "citation_key", "cand_1"], how="left")

    return errors.head(limit)


def _read_table(path: str | Path) -> pd.DataFrame:
    p = Path(path)
    if p.is_dir() or p.suffix.lower() == ".parquet":
        return pd.read_parquet(p)
    return pd.read_csv(p, dtype=str)


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate Milestone 3 reference matching predictions (MRR@5).")
    parser.add_argument("--predictions", required=True, help="Path to predictions Parquet dir/file or CSV.")
    parser.add_argument("--labels", default=None, help="Optional labels.csv to backfill groundtruth/label_source.")
    parser.add_argument("--selection", default=None, help="Optional selection.csv for manual/auto breakdown.")
    parser.add_argument("--pairs", default=None, help="Optional pairs/features table for error analysis.")
    parser.add_argument("--out-dir", default="work_m3", help="Directory to write eval_report.json and errors.csv.")
    args = parser.parse_args(list(argv) if argv is not None else None)

    pred_df = _read_table(args.predictions)
    labels_df = _read_table(args.labels) if args.labels else None
    sel_df = _read_table(args.selection) if args.selection else None
    pairs_df = _read_table(args.pairs) if args.pairs else None

    report = evaluate_predictions(pred_df, labels_df=labels_df, selection_df=sel_df)
    errors_df = extract_errors(pred_df, pairs_df=pairs_df, partition="test")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "eval_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    errors_df.to_csv(out_dir / "errors_test.csv", index=False)

    print(json.dumps(report, indent=2))
    print(f"\nSaved evaluation report to {out_dir / 'eval_report.json'} and {len(errors_df)} test errors to {out_dir / 'errors_test.csv'}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())