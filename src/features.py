"""Milestone 3 - features of the candidate pairs built by pairs.py.

`build_features(pairs)` returns one row per pair: the keys (arxiv_id, citation_key, ref_arxiv_id) followed by
numeric columns (see `feature_columns()`). Features are EVIDENCE for a ranker, never a decision: no column is a
label, a match flag or a prediction, and nothing here reads `best_suggestion` or any ground truth.

Text is cleaned with text_clean.py, the same cleaning as the labellers (accents, LaTeX residue, stop words).

Groups of features
    title      exact key equality, token / character-3-gram Jaccard, containment of each title in the other,
               length ratio, missing flags
    authors    surname Jaccard / overlap coefficient, first author equal, missing flags
    year       |entry year - reference year|, within one year, missing flags, reference later than the publication
    arXiv id   the entry cites this very id (url / doi / journal / title), cites another id (conflict), cites any id.
               New ids and old ids (cond-mat/0609392, hep-th/9901001) are supported.
    relative   (relative=True) position of the pair inside its group: rank and gap to the best pair of the same
               entry (and of the same reference), and the size of the publication matrix.

Reference year > publication year is impossible for a right reference: it is exposed as `ref_after_pub`.
Missing values: missing text / authors give 0 similarity plus a *_missing flag; only `year_abs_diff` is NaN
(unknown year), which tree models such as LightGBM handle natively.
"""
from __future__ import annotations

from typing import NamedTuple

import numpy as np
import pandas as pd

from pairs import PAIR_KEYS, _require, publication_year, reference_year
from text_clean import (author_surnames, canonical_arxiv_id, char_ngrams, extract_arxiv_ids, parse_year,
                        title_key, title_tokens)

BASE_FEATURES = [
    "title_exact", "title_jaccard", "title_overlap_entry", "title_overlap_ref", "title_char3_jaccard",
    "title_len_ratio", "entry_title_missing", "ref_title_missing",
    "author_jaccard", "author_overlap", "first_author_match", "entry_authors_missing", "ref_authors_missing",
    "year_abs_diff", "year_within_1", "entry_year_missing", "ref_year_missing", "ref_after_pub",
    "entry_has_arxiv_id", "id_match", "id_conflict",
]
RELATIVE_FEATURES = [
    "n_entries_in_paper", "n_refs_in_paper",
    "title_jaccard_rank", "title_jaccard_gap_best",
    "title_char3_jaccard_rank", "title_char3_jaccard_gap_best",
    "author_overlap_rank", "author_overlap_gap_best",
    "title_jaccard_rank_by_ref", "title_jaccard_gap_best_by_ref",
]
_RANKED = ("title_jaccard", "title_char3_jaccard", "author_overlap")
_NEEDED = ["arxiv_id", "citation_key", "ref_arxiv_id", "entry_title", "entry_authors", "entry_year", "entry_doi",
           "entry_journal", "entry_url", "ref_title", "ref_authors", "ref_submission_date"]


def feature_columns(relative: bool = True) -> list[str]:
    return BASE_FEATURES + (RELATIVE_FEATURES if relative else [])


# ----------------------------------------------------------------------------- per entry / per reference
class _Side(NamedTuple):
    tokens: frozenset
    n_tokens: int
    key: str
    grams: frozenset
    surnames: frozenset
    first_author: str
    year: "int | None"
    ids: frozenset


def _missing(value) -> bool:
    return value is None or value is pd.NA or (isinstance(value, float) and value != value)


def _text(value) -> str:
    return "" if _missing(value) else str(value)


def _authors(value):
    return [] if _missing(value) else value


def _entry_side(title, authors, year, doi, journal, url) -> _Side:
    names = author_surnames(_authors(authors))
    tokens = title_tokens(_text(title))
    ids = extract_arxiv_ids(_text(journal) or None, _text(url) or None, _text(doi) or None, _text(title) or None)
    return _Side(frozenset(tokens), len(tokens), title_key(_text(title)), frozenset(char_ngrams(_text(title))),
                 frozenset(names), names[0] if names else "", parse_year(None if _missing(year) else year),
                 frozenset(ids))


def _ref_side(ref_id, title, authors, date) -> _Side:
    names = author_surnames(_authors(authors))
    tokens = title_tokens(_text(title))
    return _Side(frozenset(tokens), len(tokens), title_key(_text(title)), frozenset(char_ngrams(_text(title))),
                 frozenset(names), names[0] if names else "",
                 reference_year(ref_id, None if _missing(date) else date), frozenset())


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _base_features(e: _Side, r: _Side, ref_id: str, pub_year) -> list[float]:
    common = len(e.tokens & r.tokens)
    grams = len(e.grams & r.grams)
    shared_authors = len(e.surnames & r.surnames)
    both_years = e.year is not None and r.year is not None
    year_diff = float(abs(e.year - r.year)) if both_years else float("nan")
    return [
        float(bool(e.key) and e.key == r.key),                                   # title_exact
        _ratio(common, len(e.tokens | r.tokens)),                                # title_jaccard
        _ratio(common, len(e.tokens)),                                           # title_overlap_entry
        _ratio(common, len(r.tokens)),                                           # title_overlap_ref
        _ratio(grams, len(e.grams | r.grams)),                                   # title_char3_jaccard
        _ratio(min(e.n_tokens, r.n_tokens), max(e.n_tokens, r.n_tokens)),        # title_len_ratio
        float(not e.key), float(not r.key),                                      # *_title_missing
        _ratio(shared_authors, len(e.surnames | r.surnames)),                    # author_jaccard
        _ratio(shared_authors, min(len(e.surnames), len(r.surnames))),           # author_overlap
        float(bool(e.first_author) and e.first_author == r.first_author),        # first_author_match
        float(not e.surnames), float(not r.surnames),                            # *_authors_missing
        year_diff,                                                               # year_abs_diff
        float(both_years and abs(e.year - r.year) <= 1),                         # year_within_1
        float(e.year is None), float(r.year is None),                            # *_year_missing
        float(r.year is not None and pub_year is not None and r.year > pub_year),  # ref_after_pub
        float(bool(e.ids)),                                                      # entry_has_arxiv_id
        float(ref_id in e.ids),                                                  # id_match
        float(bool(e.ids) and ref_id not in e.ids),                              # id_conflict
    ]


# ----------------------------------------------------------------------------- relative features
def _add_relative(frame: pd.DataFrame) -> pd.DataFrame:
    by_entry = frame.groupby(["arxiv_id", "citation_key"], sort=False)
    for column in _RANKED:
        frame[f"{column}_rank"] = by_entry[column].rank(method="min", ascending=False)
        frame[f"{column}_gap_best"] = frame[column] - by_entry[column].transform("max")
    by_ref = frame.groupby(["arxiv_id", "ref_arxiv_id"], sort=False)["title_jaccard"]
    frame["title_jaccard_rank_by_ref"] = by_ref.rank(method="min", ascending=False)
    frame["title_jaccard_gap_best_by_ref"] = frame["title_jaccard"] - by_ref.transform("max")
    by_paper = frame.groupby("arxiv_id", sort=False)
    frame["n_entries_in_paper"] = by_paper["citation_key"].transform("nunique").astype("float64")
    frame["n_refs_in_paper"] = by_paper["ref_arxiv_id"].transform("nunique").astype("float64")
    return frame


# ----------------------------------------------------------------------------- public API
def build_features(pairs: pd.DataFrame, relative: bool = True) -> pd.DataFrame:
    """Features of every pair of `pairs` (output of pairs.build_pairs), in the order of the input rows."""
    _require(pairs, PAIR_KEYS, "pairs")
    columns = {name: (pairs[name].tolist() if name in pairs.columns else [None] * len(pairs)) for name in _NEEDED}
    entry_cache: dict = {}
    ref_cache: dict = {}
    rows = []
    for i in range(len(pairs)):
        paper, key = str(columns["arxiv_id"][i]), str(columns["citation_key"][i])
        ref_id = canonical_arxiv_id(columns["ref_arxiv_id"][i]) or str(columns["ref_arxiv_id"][i])
        e = entry_cache.get((paper, key))
        if e is None:
            e = entry_cache[(paper, key)] = _entry_side(
                columns["entry_title"][i], columns["entry_authors"][i], columns["entry_year"][i],
                columns["entry_doi"][i], columns["entry_journal"][i], columns["entry_url"][i])
        r = ref_cache.get((paper, ref_id))
        if r is None:
            r = ref_cache[(paper, ref_id)] = _ref_side(
                ref_id, columns["ref_title"][i], columns["ref_authors"][i], columns["ref_submission_date"][i])
        rows.append(_base_features(e, r, ref_id, publication_year(paper)))

    base = pd.DataFrame(rows, columns=BASE_FEATURES, dtype="float64")
    out = pd.concat([pairs[PAIR_KEYS].reset_index(drop=True), base], axis=1)
    if relative and len(out):
        out = _add_relative(out)
    elif relative:
        for name in RELATIVE_FEATURES:
            out[name] = np.array([], dtype="float64")
    return out[PAIR_KEYS + feature_columns(relative)]
