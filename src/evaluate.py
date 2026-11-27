"""Milestone 3 - Evaluation (MRR@5, Hits@K, bootstrap CI and Error Analysis).

Evaluates reference matching predictions strictly on labeled queries
(entries with a non-null `groundtruth_arxiv_id`), reporting overall,
per-partition, per-source (manual vs auto) and partition x source breakdowns.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

CAND_COLS = ["cand_1", "cand_2", "cand_3", "cand_4", "cand_5"]
_ABSENT = {"", "none", "nan"}


def _is_present(val) -> bool:
    if val is None or pd.isna(val):
        return False
    return str(val).strip().lower() not in _ABSENT


def _present_mask(series: pd.Series) -> pd.Series:
    s = series.astype(str).str.strip().str.lower()
    return series.notna() & ~s.isin(_ABSENT)


def compute_query_ranks(predictions_df: pd.DataFrame) -> pd.DataFrame:
    """Attach `rank` (1..5 or NaN) and `rr` (reciprocal rank) to labeled rows (vectorised)."""
    df = predictions_df.copy()
    labeled = df.loc[_present_mask(df["groundtruth_arxiv_id"])].copy()
    gt = labeled["groundtruth_arxiv_id"].astype(str).str.strip().to_numpy()

    hit = np.zeros((len(labeled), len(CAND_COLS)), dtype=bool)
    for j, col in enumerate(CAND_COLS):
        if col in labeled.columns:
            s = labeled[col]
            hit[:, j] = _present_mask(s).to_numpy() & (s.astype(str).str.strip().to_numpy() == gt)

    found = hit.any(axis=1)
    rank = np.where(found, hit.argmax(axis=1) + 1, np.nan)
    labeled["rank"] = rank
    labeled["rr"] = np.where(found, 1.0 / np.where(found, rank, 1.0), 0.0)
    return labeled


def bootstrap_ci(values, n_boot: int = 1000, seed: int = 42, alpha: float = 0.05) -> tuple[float, float]:
    """Percentile bootstrap CI of the mean, resampling queries. Returns (nan, nan) if empty.

    Queries of one publication are correlated, so the interval is optimistic for few papers.
    """
    v = np.asarray(values, dtype=float)
    if len(v) == 0:
        return float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    means = v[rng.integers(0, len(v), size=(n_boot, len(v)))].mean(axis=1)
    return float(np.quantile(means, alpha / 2)), float(np.quantile(means, 1 - alpha / 2))


def summarize_metrics(ranked_df: pd.DataFrame, with_ci: bool = False) -> dict:
    """MRR@5, Hits@1, Hits@5 and query counts (optionally a bootstrap CI of MRR@5)."""
    n_queries = int(len(ranked_df))
    if n_queries == 0:
        out = {"n_queries": 0, "mrr_at_5": 0.0, "hits_at_1": 0.0, "hits_at_5": 0.0,
               "n_hits_at_1": 0, "n_hits_at_5": 0}
        if with_ci:
            out.update(ci_low=float("nan"), ci_high=float("nan"))
        return out

    n_hits_1 = int((ranked_df["rank"] == 1).sum())
    n_hits_5 = int(ranked_df["rank"].notna().sum())
    out = {
        "n_queries": n_queries,
        "mrr_at_5": float(ranked_df["rr"].mean()),
        "hits_at_1": float(n_hits_1 / n_queries),
        "hits_at_5": float(n_hits_5 / n_queries),
        "n_hits_at_1": n_hits_1,
        "n_hits_at_5": n_hits_5,
    }
    if with_ci:
        out["ci_low"], out["ci_high"] = bootstrap_ci(ranked_df["rr"].to_numpy())
    return out


def mrr_at_5(predictions_df: pd.DataFrame) -> float:
    """Scalar MRR@5 over all labeled rows in `predictions_df`."""
    return summarize_metrics(compute_query_ranks(predictions_df))["mrr_at_5"]


def evaluate_predictions(
    predictions_df: pd.DataFrame,
    labels_df: pd.DataFrame | None = None,
    selection_df: pd.DataFrame | None = None,
) -> dict:
    """Overall, per-partition, per-source and partition x source MRR@5 breakdowns."""
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
                _present_mask(df["groundtruth_arxiv_id"]), df["groundtruth_arxiv_id_lbl"]
            )
            df.drop(columns=["groundtruth_arxiv_id_lbl"], inplace=True)
        if "label_source_lbl" in df.columns:
            if "label_source" in df.columns:
                df["label_source"] = df["label_source"].fillna(df["label_source_lbl"])
            else:
                df["label_source"] = df["label_source_lbl"]
            df.drop(columns=["label_source_lbl"], inplace=True)

    # Fallback label_source from selection.csv `kind` if not already present
    if "label_source" not in df.columns and selection_df is not None and not selection_df.empty:
        sel = selection_df[["arxiv_id", "kind"]].drop_duplicates(subset=["arxiv_id"]).copy()
        sel["arxiv_id"] = sel["arxiv_id"].astype(str).str.strip()
        sel["label_source"] = sel["kind"].map(lambda k: "manual" if str(k).startswith("manual") else "auto")
        df["arxiv_id"] = df["arxiv_id"].astype(str).str.strip()
        df = df.merge(sel[["arxiv_id", "label_source"]], on="arxiv_id", how="left")

    ranked = compute_query_ranks(df)
    report = {
        "overall": summarize_metrics(ranked),
        "by_partition": {},
        "by_source": {},
        "by_partition_and_source": {},
    }

    if "label_source" in ranked.columns:
        for src, grp in ranked.groupby("label_source", dropna=True):
            report["by_source"][str(src)] = summarize_metrics(grp, with_ci=True)

    if "partition" in ranked.columns:
        for part, grp in ranked.groupby("partition", dropna=True):
            report["by_partition"][str(part)] = summarize_metrics(grp)
        if "label_source" in ranked.columns:
            for (part, src), grp in ranked.groupby(["partition", "label_source"], dropna=True):
                report["by_partition_and_source"][f"{part}_{src}"] = summarize_metrics(grp, with_ci=True)

    return report


def extract_errors(
    predictions_df: pd.DataFrame,
    pairs_df: pd.DataFrame | None = None,
    partition: str | None = "test",
    limit: int = 100,
) -> pd.DataFrame:
    """Misranked queries (rank > 1 or not in top-5) for error analysis."""
    ranked = compute_query_ranks(predictions_df)
    if partition and "partition" in ranked.columns:
        ranked = ranked[ranked["partition"] == partition]

    errors = ranked[(ranked["rank"].isna()) | (ranked["rank"] > 1)].copy()
    errors["error_type"] = errors["rank"].apply(
        lambda r: "miss_outside_top5" if pd.isna(r) else f"ranked_at_{int(r)}"
    )

    if pairs_df is not None and not pairs_df.empty and not errors.empty:
        feat_cols = [
            c for c in pairs_df.columns
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
    print(f"\nSaved evaluation report to {out_dir / 'eval_report.json'} and {len(errors_df)} test errors "
          f"to {out_dir / 'errors_test.csv'}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())