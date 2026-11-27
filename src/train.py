"""Milestone 3 - Model Training, Baselines, and Feature Ablation Study.

Trains and compares:
1. Heuristic Baseline (single strongest similarity feature)
2. Binary Classification Baseline (LogisticRegression ranked by probability)
3. Main Model: LightGBM LambdaRank (LGBMRanker) grouped by (arxiv_id, citation_key)
4. Feature-Group Ablation Study (measuring MRR@5 drop when removing each group)
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Iterable

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

from evaluate import CAND_COLS, evaluate_predictions, extract_errors

ID_AND_META_COLS = {
    "arxiv_id",
    "citation_key",
    "ref_arxiv_id",
    "groundtruth_arxiv_id",
    "partition",
    "kind",
    "owner",
    "slot",
    "tier",
    "label",
    "label_source",
}


def _read_table(path: str | Path) -> pd.DataFrame:
    p = Path(path)
    if p.is_dir() or p.suffix.lower() == ".parquet":
        return pd.read_parquet(p)
    return pd.read_csv(p, dtype=str)


def load_training_frame(
    features_path: str | Path,
    labels_path: str | Path,
    selection_path: str | Path,
) -> tuple[pd.DataFrame, list[str]]:
    """Join features with groundtruth labels and train/valid/test partitions."""
    feats = _read_table(features_path)
    labels = pd.read_csv(labels_path, dtype=str)
    selection = pd.read_csv(selection_path, dtype=str)

    for col in ("arxiv_id", "citation_key", "ref_arxiv_id"):
        feats[col] = feats[col].astype(str).str.strip()
    for col in ("arxiv_id", "citation_key", "groundtruth_arxiv_id"):
        labels[col] = labels[col].astype(str).str.strip()
    selection["arxiv_id"] = selection["arxiv_id"].astype(str).str.strip()

    # Drop pre-attached partition/kind/owner from features so selection.csv is authoritative
    drop_pre = [c for c in ("partition", "kind", "owner") if c in feats.columns]
    if drop_pre:
        feats = feats.drop(columns=drop_pre)

    # Keep only active train/valid/test papers (exclude reserve and dropped)
    sel_active = selection[selection["partition"].isin(["train", "valid", "test"])][
        ["arxiv_id", "partition", "kind"]
    ].drop_duplicates(subset=["arxiv_id"])

    # Keep only labeled (arxiv_id, citation_key) queries
    lbl_cols = ["arxiv_id", "citation_key", "groundtruth_arxiv_id"]
    if "label_source" in labels.columns:
        lbl_cols.append("label_source")
    labels_clean = labels[lbl_cols].drop_duplicates(subset=["arxiv_id", "citation_key"])

    df = feats.merge(sel_active, on="arxiv_id", how="inner")
    df = df.merge(labels_clean, on=["arxiv_id", "citation_key"], how="inner")
    df["label"] = (df["ref_arxiv_id"] == df["groundtruth_arxiv_id"]).astype(int)

    # Sort deterministically so query groups (arxiv_id, citation_key) are contiguous
    df.sort_values(["partition", "arxiv_id", "citation_key", "ref_arxiv_id"], inplace=True)
    df.reset_index(drop=True, inplace=True)

    feature_cols = [
        c
        for c in df.columns
        if c not in ID_AND_META_COLS and pd.api.types.is_numeric_dtype(df[c])
    ]
    if not feature_cols:
        raise ValueError("No numeric feature columns detected in features table.")

    return df, feature_cols


def scores_to_predictions(df_part: pd.DataFrame, scores: np.ndarray) -> pd.DataFrame:
    """Convert pair-level scores into top-5 ranked candidates per (arxiv_id, citation_key)."""
    scored = df_part[["arxiv_id", "partition", "citation_key", "groundtruth_arxiv_id", "ref_arxiv_id"]].copy()
    if "label_source" in df_part.columns:
        scored["label_source"] = df_part["label_source"]
    scored["_score"] = scores

    scored.sort_values(
        ["arxiv_id", "citation_key", "_score", "ref_arxiv_id"],
        ascending=[True, True, False, True],
        inplace=True,
    )

    rows = []
    for (aid, ckey), grp in scored.groupby(["arxiv_id", "citation_key"], sort=False):
        cands = grp["ref_arxiv_id"].head(5).tolist()
        cands += [None] * (5 - len(cands))
        first = grp.iloc[0]
        row = {
            "arxiv_id": aid,
            "partition": first["partition"],
            "citation_key": ckey,
            "groundtruth_arxiv_id": first["groundtruth_arxiv_id"],
            **{col: val for col, val in zip(CAND_COLS, cands)},
        }
        if "label_source" in first:
            row["label_source"] = first["label_source"]
        rows.append(row)

    return pd.DataFrame(rows)


def _group_sizes(df_part: pd.DataFrame) -> list[int]:
    return df_part.groupby(["arxiv_id", "citation_key"], sort=False).size().tolist()


def infer_feature_groups(feature_cols: list[str]) -> dict[str, list[str]]:
    """Group features by semantic category (matching pairs/features schema) for ablation study."""
    groups = {
        "group_relative": [],
        "title": [],
        "author": [],
        "year": [],
        "arxiv_id": [],
        "paper_context": [],
        "other": [],
    }
    for col in feature_cols:
        low = col.lower()
        if any(
            tok in low
            for tok in ("_rank", "_gap_best", "_by_ref", "rel_", "rank_", "diff_", "zscore", "margin", "norm_")
        ):
            groups["group_relative"].append(col)
        elif "arxiv" in low or low.startswith("id_") or low.endswith("_id"):
            groups["arxiv_id"].append(col)
        elif "title" in low:
            groups["title"].append(col)
        elif "author" in low or "surname" in low:
            groups["author"].append(col)
        elif "year" in low or "date" in low or low == "ref_after_pub":
            groups["year"].append(col)
        elif "in_paper" in low:
            groups["paper_context"].append(col)
        else:
            groups["other"].append(col)
    return {k: v for k, v in groups.items() if v}


def train_lgbm_ranker(
    df_train: pd.DataFrame,
    df_valid: pd.DataFrame,
    feature_cols: list[str],
    random_state: int = 42,
) -> lgb.LGBMRanker:
    """Train LightGBM LambdaRank with early stopping on validation MRR/NDCG."""
    X_train = df_train[feature_cols].fillna(0.0)
    y_train = df_train["label"].values
    g_train = _group_sizes(df_train)

    X_valid = df_valid[feature_cols].fillna(0.0)
    y_valid = df_valid["label"].values
    g_valid = _group_sizes(df_valid)

    model = lgb.LGBMRanker(
        objective="lambdarank",
        metric="ndcg",
        ndcg_eval_at=[1, 5],
        n_estimators=600,
        learning_rate=0.05,
        num_leaves=31,
        min_child_samples=20,
        subsample=0.8,
        colsample_bytree=0.8,
        random_state=random_state,
        n_jobs=-1,
        verbose=-1,
    )
    model.fit(
        X_train,
        y_train,
        group=g_train,
        eval_set=[(X_valid, y_valid)],
        eval_group=[g_valid],
        callbacks=[lgb.early_stopping(stopping_rounds=40, verbose=False)],
    )
    return model


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Train Milestone 3 baselines and LightGBM ranker.")
    parser.add_argument("--features", required=True, help="Path to pairs+features Parquet dir/file or CSV.")
    parser.add_argument("--labels", required=True, help="Path to merged labels.csv.")
    parser.add_argument("--selection", default="src/tools/m3_selection/selection.csv", help="Path to selection.csv.")
    parser.add_argument("--baseline-col", default=None, help="Feature column for heuristic baseline.")
    parser.add_argument("--out-dir", default="work_m3/models", help="Output directory for model & reports.")
    args = parser.parse_args(list(argv) if argv is not None else None)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    df, feature_cols = load_training_frame(args.features, args.labels, args.selection)
    selection_df = pd.read_csv(args.selection, dtype=str)

    df_train = df[df["partition"] == "train"].copy()
    df_valid = df[df["partition"] == "valid"].copy()
    df_test = df[df["partition"] == "test"].copy()
    print(f"Loaded rows -> train: {len(df_train):,}, valid: {len(df_valid):,}, test: {len(df_test):,}")
    print(f"Detected {len(feature_cols)} features: {feature_cols}")

    results = {}

    # 1. Heuristic Baseline (default: title_jaccard if available)
    base_col = args.baseline_col
    if base_col is None:
        if "title_jaccard" in feature_cols:
            base_col = "title_jaccard"
        else:
            title_cands = [c for c in feature_cols if "title" in c.lower()]
            base_col = title_cands[0] if title_cands else feature_cols[0]
    heur_valid_pred = scores_to_predictions(df_valid, df_valid[base_col].fillna(0.0).to_numpy())
    heur_test_pred = scores_to_predictions(df_test, df_test[base_col].fillna(0.0).to_numpy())
    results["baseline_heuristic"] = {
        "feature_used": base_col,
        "valid": evaluate_predictions(heur_valid_pred, selection_df=selection_df),
        "test": evaluate_predictions(heur_test_pred, selection_df=selection_df),
    }

    # 2. Logistic Regression Baseline
    scaler = StandardScaler()
    X_tr_sc = scaler.fit_transform(df_train[feature_cols].fillna(0.0))
    X_va_sc = scaler.transform(df_valid[feature_cols].fillna(0.0))
    X_te_sc = scaler.transform(df_test[feature_cols].fillna(0.0))
    clf = LogisticRegression(max_iter=1000, class_weight="balanced", random_state=42)
    clf.fit(X_tr_sc, df_train["label"].values)
    lr_valid_pred = scores_to_predictions(df_valid, clf.predict_proba(X_va_sc)[:, 1])
    lr_test_pred = scores_to_predictions(df_test, clf.predict_proba(X_te_sc)[:, 1])
    results["baseline_logreg"] = {
        "valid": evaluate_predictions(lr_valid_pred, selection_df=selection_df),
        "test": evaluate_predictions(lr_test_pred, selection_df=selection_df),
    }

    # 3. Main Model: LightGBM LambdaRank
    ranker = train_lgbm_ranker(df_train, df_valid, feature_cols)
    ranker.booster_.save_model(str(out_dir / "lgbm_ranker.txt"))
    (out_dir / "feature_cols.json").write_text(json.dumps(feature_cols, indent=2), encoding="utf-8")

    lgb_valid_pred = scores_to_predictions(df_valid, ranker.predict(df_valid[feature_cols].fillna(0.0)))
    lgb_test_pred = scores_to_predictions(df_test, ranker.predict(df_test[feature_cols].fillna(0.0)))
    results["main_lgbm_ranker"] = {
        "best_iteration": int(ranker.best_iteration_ or ranker.n_estimators),
        "valid": evaluate_predictions(lgb_valid_pred, selection_df=selection_df),
        "test": evaluate_predictions(lgb_test_pred, selection_df=selection_df),
    }

    # Feature Importances
    fi_df = pd.DataFrame(
        {"feature": feature_cols, "importance_gain": ranker.booster_.feature_importance(importance_type="gain")}
    ).sort_values("importance_gain", ascending=False)
    fi_df.to_csv(out_dir / "feature_importance.csv", index=False)

    # Error Analysis on Test Set
    test_errors = extract_errors(lgb_test_pred, pairs_df=df_test, partition="test")
    test_errors.to_csv(out_dir / "errors_test.csv", index=False)

    # 4. Feature-Group Ablation Study
    full_valid_mrr = results["main_lgbm_ranker"]["valid"]["overall"]["mrr_at_5"]
    full_test_mrr = results["main_lgbm_ranker"]["test"]["overall"]["mrr_at_5"]
    ablation_rows = [
        {
            "dropped_group": "None (Full Model)",
            "n_features": len(feature_cols),
            "valid_mrr_at_5": full_valid_mrr,
            "test_mrr_at_5": full_test_mrr,
            "delta_valid": 0.0,
            "delta_test": 0.0,
        }
    ]
    for grp_name, grp_cols in infer_feature_groups(feature_cols).items():
        sub_cols = [c for c in feature_cols if c not in grp_cols]
        if not sub_cols:
            continue
        sub_ranker = train_lgbm_ranker(df_train, df_valid, sub_cols)
        sub_va_mrr = evaluate_predictions(
            scores_to_predictions(df_valid, sub_ranker.predict(df_valid[sub_cols].fillna(0.0))),
            selection_df=selection_df,
        )["overall"]["mrr_at_5"]
        sub_te_mrr = evaluate_predictions(
            scores_to_predictions(df_test, sub_ranker.predict(df_test[sub_cols].fillna(0.0))),
            selection_df=selection_df,
        )["overall"]["mrr_at_5"]
        ablation_rows.append(
            {
                "dropped_group": f"- {grp_name} ({len(grp_cols)} cols)",
                "n_features": len(sub_cols),
                "valid_mrr_at_5": sub_va_mrr,
                "test_mrr_at_5": sub_te_mrr,
                "delta_valid": sub_va_mrr - full_valid_mrr,
                "delta_test": sub_te_mrr - full_test_mrr,
            }
        )

    ablation_df = pd.DataFrame(ablation_rows)
    ablation_df.to_csv(out_dir / "ablation_results.csv", index=False)
    (out_dir / "train_metrics.json").write_text(json.dumps(results, indent=2), encoding="utf-8")

    print("\n=== MODEL COMPARISON (TEST MRR@5) ===")
    for name, res in results.items():
        print(f"- {name:20s}: valid={res['valid']['overall']['mrr_at_5']:.4f} | test={res['test']['overall']['mrr_at_5']:.4f}")
    print("\n=== ABLATION STUDY ===")
    print(ablation_df.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())