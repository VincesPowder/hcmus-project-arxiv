"""Milestone 3 - Model Training, Baselines, ID-masking experiment and Feature Ablation.

Official result (as required by the assignment) = condition `mask0` (no masking).
Supplementary experiment = hide the arXiv-ID evidence of an entry (url / arXiv ID / DOI) for a
fraction of the entries, in train AND valid/test, so the model must match on title, authors, year.

Masking acts on the three ID-derived features (entry_has_arxiv_id, id_match, id_conflict): masked
entries get the value of "no ID" (0). The masked entries are chosen by a hash of
(seed, arxiv_id, citation_key), so the choice is reproducible and applies to a whole query.

Trains and compares, for every masking rate:
  1. heuristic baselines (title only / ID then title / title+author+year)
  2. LogisticRegression baseline
  3. LightGBM LambdaRank (LGBMRanker) grouped by (arxiv_id, citation_key)
and runs a feature-group ablation at the chosen masking rates.
All metrics are reported overall and split into manual / auto labels, with the query count n.
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

from evaluate import (CAND_COLS, compute_query_ranks, evaluate_predictions, extract_errors,
                      summarize_metrics)

ID_AND_META_COLS = {
    "arxiv_id", "citation_key", "ref_arxiv_id", "groundtruth_arxiv_id", "partition",
    "kind", "owner", "slot", "tier", "label", "label_source",
}
ID_FEATURE_CANDIDATES = ["entry_has_arxiv_id", "id_match", "id_conflict"]


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

    drop_pre = [c for c in ("partition", "kind", "owner") if c in feats.columns]
    if drop_pre:
        feats = feats.drop(columns=drop_pre)

    sel_active = selection[selection["partition"].isin(["train", "valid", "test"])][
        ["arxiv_id", "partition", "kind"]
    ].drop_duplicates(subset=["arxiv_id"])

    lbl_cols = ["arxiv_id", "citation_key", "groundtruth_arxiv_id"]
    if "label_source" in labels.columns:
        lbl_cols.append("label_source")
    labels_clean = labels[lbl_cols].drop_duplicates(subset=["arxiv_id", "citation_key"])

    df = feats.merge(sel_active, on="arxiv_id", how="inner")
    df = df.merge(labels_clean, on=["arxiv_id", "citation_key"], how="inner")
    df["label"] = (df["ref_arxiv_id"] == df["groundtruth_arxiv_id"]).astype(int)

    # manual / auto source: from labels.csv, else from selection `kind`
    if "label_source" not in df.columns:
        df["label_source"] = df["kind"].map(lambda k: "manual" if str(k).startswith("manual") else "auto")
    df["label_source"] = df["label_source"].fillna("auto")

    df.sort_values(["partition", "arxiv_id", "citation_key", "ref_arxiv_id"], inplace=True)
    df.reset_index(drop=True, inplace=True)

    feature_cols = [
        c for c in df.columns
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

    scored.sort_values(["arxiv_id", "citation_key", "_score", "ref_arxiv_id"],
                       ascending=[True, True, False, True], inplace=True)

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
    """Group features by semantic category for the ablation study."""
    groups = {"group_relative": [], "title": [], "author": [], "year": [],
              "arxiv_id": [], "paper_context": [], "other": []}
    for col in feature_cols:
        low = col.lower()
        if any(tok in low for tok in ("_rank", "_gap_best", "_by_ref", "rel_", "rank_", "diff_",
                                      "zscore", "margin", "norm_")):
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


def train_lgbm_ranker(df_train: pd.DataFrame, df_valid: pd.DataFrame,
                      feature_cols: list[str], random_state: int = 42) -> lgb.LGBMRanker:
    """LightGBM LambdaRank with early stopping on validation NDCG."""
    model = lgb.LGBMRanker(
        objective="lambdarank", metric="ndcg", ndcg_eval_at=[1, 5],
        n_estimators=600, learning_rate=0.05, num_leaves=31, min_child_samples=20,
        subsample=0.8, colsample_bytree=0.8, random_state=random_state, n_jobs=-1, verbose=-1,
    )
    model.fit(
        df_train[feature_cols].fillna(0.0), df_train["label"].values, group=_group_sizes(df_train),
        eval_set=[(df_valid[feature_cols].fillna(0.0), df_valid["label"].values)],
        eval_group=[_group_sizes(df_valid)],
        callbacks=[lgb.early_stopping(stopping_rounds=40, verbose=False)],
    )
    return model


# ----------------------------------------------------------------------------- masking
def hash_unit(df: pd.DataFrame, seed: int, salt: str = "") -> np.ndarray:
    """Deterministic pseudo-random number in [0, 1) per (arxiv_id, citation_key)."""
    key = f"{salt}{seed}|" + df["arxiv_id"] + "|" + df["citation_key"]
    return (pd.util.hash_pandas_object(key, index=False).to_numpy() % 1_000_000) / 1_000_000


def mask_id_features(df: pd.DataFrame, id_cols: list[str], rate: float, seed: int) -> pd.DataFrame:
    """Hide the ID evidence of a fraction `rate` of the entries (whole queries, not single pairs)."""
    if rate <= 0 or not id_cols:
        return df
    masked = hash_unit(df, seed) < rate
    out = df.copy()
    for c in id_cols:
        out[c] = df[c].where(~masked, 0.0)
    return out


def subsample_queries(df: pd.DataFrame, frac: float, seed: int) -> pd.DataFrame:
    if frac >= 1.0:
        return df
    return df[hash_unit(df, seed, salt="sub") < frac]    # order kept => groups stay contiguous


# ----------------------------------------------------------------------------- evaluation helpers
def evaluate_scores(d: pd.DataFrame, scores: np.ndarray) -> dict:
    """MRR@5 overall and per label source (manual / auto), with bootstrap CI per source."""
    ranked = compute_query_ranks(scores_to_predictions(d, scores))
    out = {"all": summarize_metrics(ranked)}
    for src, grp in ranked.groupby("label_source"):
        out[str(src)] = summarize_metrics(grp, with_ci=True)
    return out


def result_row(cond: str, model: str, split: str, res: dict, **extra) -> dict:
    row = {"cond": cond, "model": model, "split": split, **extra}
    for k in ("all", "manual", "auto"):
        r = res.get(k, {})
        row[f"mrr_{k}"] = r.get("mrr_at_5", np.nan)
        row[f"n_{k}"] = r.get("n_queries", 0)
    row["hits1_manual"] = res.get("manual", {}).get("hits_at_1", np.nan)
    row["ci_manual_low"] = res.get("manual", {}).get("ci_low", np.nan)
    row["ci_manual_high"] = res.get("manual", {}).get("ci_high", np.nan)
    return row


def col0(d: pd.DataFrame, c: str) -> np.ndarray:
    return d[c].fillna(0.0).to_numpy() if c in d.columns else np.zeros(len(d))


def heuristic_scorers(base_col: str) -> dict:
    return {
        "heur_title": lambda d: col0(d, base_col),
        "heur_id_then_title": lambda d: 10 * col0(d, "id_match") + col0(d, base_col),
        "heur_title_author_year": lambda d: (col0(d, base_col) + 0.5 * col0(d, "author_overlap")
                                             + 0.2 * col0(d, "year_within_1")),
    }


def pct(rate: float) -> str:
    return f"mask{int(round(rate * 100))}"


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Train Milestone 3 baselines, LightGBM ranker, masking experiment.")
    parser.add_argument("--features", required=True, help="Path to features Parquet dir/file or CSV.")
    parser.add_argument("--labels", required=True, help="Path to merged labels.csv.")
    parser.add_argument("--selection", default="src/tools/m3_selection/selection.csv")
    parser.add_argument("--baseline-col", default=None, help="Feature column for the title heuristic baseline.")
    parser.add_argument("--out-dir", default="work_m3/models")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--mask-rates", type=float, nargs="+", default=[0.0, 0.5, 1.0],
                        help="Fractions of entries whose ID evidence is hidden. 0.0 = official result.")
    parser.add_argument("--ablate-rates", type=float, nargs="+", default=[0.0, 1.0],
                        help="Masking rates at which the feature-group ablation runs.")
    parser.add_argument("--train-frac", type=float, default=1.0,
                        help="Fraction of TRAIN queries used by the masked experiments and ablation (speed).")
    parser.add_argument("--skip-masking", action="store_true", help="Only the official mask0 run.")
    parser.add_argument("--skip-ablation", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)

    out_dir = Path(args.out_dir)
    (out_dir / "masked").mkdir(parents=True, exist_ok=True)

    df, feature_cols = load_training_frame(args.features, args.labels, args.selection)
    selection_df = pd.read_csv(args.selection, dtype=str)
    id_cols = [c for c in ID_FEATURE_CANDIDATES if c in feature_cols]

    df_train = df[df["partition"] == "train"]
    df_valid = df[df["partition"] == "valid"]
    df_test = df[df["partition"] == "test"]
    print(f"Loaded rows -> train: {len(df_train):,}, valid: {len(df_valid):,}, test: {len(df_test):,}")
    print(f"Detected {len(feature_cols)} features: {feature_cols}")
    print(f"ID features to mask: {id_cols}")

    # --- data audit: how many labeled queries per partition and source
    audit = (df.drop_duplicates(["arxiv_id", "citation_key"])
               .groupby(["partition", "label_source"])
               .agg(queries=("citation_key", "size"), papers=("arxiv_id", "nunique")))
    print("\nLabeled queries by partition and source:\n", audit.to_string())
    for part in ("valid", "test"):
        if "manual" not in set(df.loc[df.partition == part, "label_source"]):
            print(f"!!! WARNING: partition '{part}' has NO manual labels; its MRR@5 is auto-only "
                  f"(auto labels come from title similarity, so they are the easy cases).")
    if "entry_has_arxiv_id" in id_cols:
        no_id = df_train[df_train["entry_has_arxiv_id"] == 0]
        enc_ok = bool((no_id[[c for c in ("id_match", "id_conflict") if c in id_cols]] == 0).all().all())
        print(f"Encoding check: entries without ID have id_match = id_conflict = 0 -> {enc_ok}")
        if not enc_ok:
            print("!!! WARNING: 0 is not the 'no ID' encoding; masking by 0 would not mean 'no ID'.")

    base_col = args.baseline_col or ("title_jaccard" if "title_jaccard" in feature_cols else
                                     next((c for c in feature_cols if "title" in c.lower()), feature_cols[0]))
    scorers = heuristic_scorers(base_col)

    rows: list[dict] = []
    official: dict = {}
    rates = [0.0] + [r for r in args.mask_rates if r > 0 and not args.skip_masking]
    rates = sorted(set(rates))

    # ====================================================== masking scale (rate 0 = official)
    for rate in rates:
        cond = pct(rate)
        tr = subsample_queries(mask_id_features(df_train, id_cols, rate, args.seed),
                               1.0 if rate == 0 else args.train_frac, args.seed)
        va = mask_id_features(df_valid, id_cols, rate, args.seed)
        te = mask_id_features(df_test, id_cols, rate, args.seed)
        print(f"\n=== condition {cond}: train {len(tr):,} | valid {len(va):,} | test {len(te):,} rows ===")

        # 1. heuristics
        for name, fn in scorers.items():
            for split, d in (("valid", va), ("test", te)):
                rows.append(result_row(cond, name, split, evaluate_scores(d, fn(d))))

        # 2. logistic regression
        scaler = StandardScaler().fit(tr[feature_cols].fillna(0.0))
        clf = LogisticRegression(max_iter=1000, class_weight="balanced", random_state=args.seed)
        clf.fit(scaler.transform(tr[feature_cols].fillna(0.0)), tr["label"].values)
        lr_scores = {s: clf.predict_proba(scaler.transform(d[feature_cols].fillna(0.0)))[:, 1]
                     for s, d in (("valid", va), ("test", te))}
        for split, d in (("valid", va), ("test", te)):
            rows.append(result_row(cond, "logreg", split, evaluate_scores(d, lr_scores[split])))

        # 3. LightGBM LambdaRank
        ranker = train_lgbm_ranker(tr, va, feature_cols, args.seed)
        lgb_scores = {s: ranker.predict(d[feature_cols].fillna(0.0)) for s, d in (("valid", va), ("test", te))}
        for split, d in (("valid", va), ("test", te)):
            rows.append(result_row(cond, "lgbm", split, evaluate_scores(d, lgb_scores[split]),
                                   best_iteration=int(ranker.best_iteration_ or ranker.n_estimators)))

        if rate == 0:
            # official artifacts, same names/locations as before (predict.py depends on them)
            ranker.booster_.save_model(str(out_dir / "lgbm_ranker.txt"))
            (out_dir / "feature_cols.json").write_text(json.dumps(feature_cols, indent=2), encoding="utf-8")
            fi = pd.DataFrame({"feature": feature_cols,
                               "importance_gain": ranker.booster_.feature_importance(importance_type="gain")})
            fi.sort_values("importance_gain", ascending=False).to_csv(out_dir / "feature_importance.csv", index=False)

            lgb_test_pred = scores_to_predictions(te, lgb_scores["test"])
            extract_errors(lgb_test_pred, pairs_df=te, partition="test").to_csv(out_dir / "errors_test.csv", index=False)

            def full_report(valid_scores, test_scores):
                return {"valid": evaluate_predictions(scores_to_predictions(va, valid_scores), selection_df=selection_df),
                        "test": evaluate_predictions(scores_to_predictions(te, test_scores), selection_df=selection_df)}
            official["baseline_heuristic"] = {"feature_used": base_col,
                                              **full_report(col0(va, base_col), col0(te, base_col))}
            official["baseline_logreg"] = full_report(lr_scores["valid"], lr_scores["test"])
            official["main_lgbm_ranker"] = {"best_iteration": int(ranker.best_iteration_ or ranker.n_estimators),
                                            **full_report(lgb_scores["valid"], lgb_scores["test"])}
        else:
            ranker.booster_.save_model(str(out_dir / "masked" / f"lgbm_ranker_{cond}.txt"))
        del tr, va, te

    # ====================================================== feature-group ablation
    if not args.skip_ablation:
        groups = infer_feature_groups(feature_cols)
        for rate in sorted(set(args.ablate_rates)):
            cond = pct(rate)
            tr = subsample_queries(mask_id_features(df_train, id_cols, rate, args.seed),
                                   1.0 if rate == 0 else args.train_frac, args.seed)
            va = mask_id_features(df_valid, id_cols, rate, args.seed)
            te = mask_id_features(df_test, id_cols, rate, args.seed)
            print(f"\n=== ablation at {cond} ===")
            for g, gcols in groups.items():
                sub = [c for c in feature_cols if c not in gcols]
                if not sub:
                    continue
                rk = train_lgbm_ranker(tr, va, sub, args.seed)
                for split, d in (("valid", va), ("test", te)):
                    rows.append(result_row(cond, "lgbm_ablate", split,
                                           evaluate_scores(d, rk.predict(d[sub].fillna(0.0))),
                                           dropped_group=g, n_dropped=len(gcols), n_features=len(sub)))
                print(f"  - {g} ({len(gcols)} cols) done", flush=True)
            del tr, va, te

    res = pd.DataFrame(rows)
    res.to_csv(out_dir / "masking_results.csv", index=False)

    # ablation table with deltas against the full model of the same condition
    abl = res[res.model == "lgbm_ablate"].copy()
    if not abl.empty:
        full = res[res.model == "lgbm"][["cond", "split", "mrr_all", "mrr_manual"]].rename(
            columns={"mrr_all": "full_all", "mrr_manual": "full_manual"})
        abl = abl.merge(full, on=["cond", "split"], how="left")
        abl["delta_all"] = abl["mrr_all"] - abl["full_all"]
        abl["delta_manual"] = abl["mrr_manual"] - abl["full_manual"]
        abl.to_csv(out_dir / "ablation_results.csv", index=False)

    (out_dir / "train_metrics.json").write_text(json.dumps(official, indent=2), encoding="utf-8")

    # ====================================================== printed summary
    show = ["cond", "model", "mrr_all", "n_all", "mrr_manual", "n_manual", "mrr_auto", "n_auto"]
    pd.set_option("display.width", 220)
    print("\n=== OFFICIAL (mask0) MODEL COMPARISON ===")
    for name, r in official.items():
        print(f"- {name:20s}: valid={r['valid']['overall']['mrr_at_5']:.4f} | test={r['test']['overall']['mrr_at_5']:.4f}")
    print("\n=== TEST MRR@5: masking scale x model (all / manual / auto) ===")
    print(res[(res.split == "test") & (res.model != "lgbm_ablate")][show].round(4).to_string(index=False))
    if not abl.empty:
        print("\n=== TEST ABLATION (delta vs full model of the same condition) ===")
        print(abl[abl.split == "test"][["cond", "dropped_group", "n_dropped", "mrr_all", "delta_all",
                                        "mrr_manual", "delta_manual", "n_manual"]].round(4).to_string(index=False))
    test_rows = res[(res.split == "test") & (res.model == "lgbm")]
    if {"mrr_manual", "mrr_auto"} <= set(test_rows.columns):
        gap = (test_rows["mrr_auto"] - test_rows["mrr_manual"]).max()
        if pd.notna(gap) and gap > 0.02:
            print("\nNOTE: auto MRR@5 exceeds manual MRR@5; auto labels (title-similarity based) are the "
                  "easier, partly circular cases. Use the manual column as the main analysis figure.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())