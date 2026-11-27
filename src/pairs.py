"""Milestone 3 - candidate (citation, reference) pairs.

The candidate space of a publication is the m x n matrix of the task: the m BibTeX entries (M2 table `bibtex`)
against the n references with an arXiv ID (M1 table `references`) of the SAME publication. This module builds
exactly that matrix, one row per (entry, reference), and nothing else:

  * a pair never crosses two publications (entries and references are joined inside each `arxiv_id`);
  * rows carry raw fields only: no label, no score, no suggestion. A `best_suggestion` column present in the
    input is ignored (it is a hint for the human labeller, never ground truth);
  * every reference id is canonical (text_clean.canonical_arxiv_id): new ids and old ids such as
    `cond-mat/0609392` or `hep-th/9901001` are kept, versions and prefixes are removed;
  * `ref_after_pub` flags the pairs whose reference year is later than the publication year. Such a reference
    cannot be the right one. The flag is kept by default so the matrix stays complete; `drop_future=True` removes them.

Row keys follow the labels / predictions tables of the task: arxiv_id, citation_key, ref_arxiv_id.
To label: join the labels on (arxiv_id, citation_key) and compare ref_arxiv_id with groundtruth_arxiv_id.

Input columns (names of the M2 / M1 tables)
    entries    : arxiv_id, citation_key, title, authors, year, doi, journal, url   (missing optional columns -> None)
    references : arxiv_id, ref_arxiv_id, ref_title, ref_authors, ref_submission_date
"""
from __future__ import annotations

from typing import Iterable, Iterator

import pandas as pd

from text_clean import arxiv_id_year, canonical_arxiv_id, parse_year

PAIR_KEYS = ["arxiv_id", "citation_key", "ref_arxiv_id"]
ENTRY_COLUMNS = {"title": "entry_title", "authors": "entry_authors", "year": "entry_year",
                 "doi": "entry_doi", "journal": "entry_journal", "url": "entry_url"}
REF_COLUMNS = ["ref_title", "ref_authors", "ref_submission_date"]
PAIR_COLUMNS = (PAIR_KEYS + list(ENTRY_COLUMNS.values()) + REF_COLUMNS
                + ["pub_year", "ref_year", "ref_after_pub"])


# ----------------------------------------------------------------------------- years
def publication_year(arxiv_id) -> int | None:
    """Year of the publication, read from its own arXiv id (first submission)."""
    return arxiv_id_year(str(arxiv_id)) if arxiv_id is not None else None


def reference_year(ref_arxiv_id, ref_submission_date=None) -> int | None:
    """Year of a reference: its submission date when usable, else the year encoded in its arXiv id."""
    return parse_year(ref_submission_date) or arxiv_id_year(ref_arxiv_id)


# ----------------------------------------------------------------------------- input handling
def _require(frame: pd.DataFrame, columns: Iterable[str], name: str) -> None:
    missing = [c for c in columns if c not in frame.columns]
    if missing:
        raise ValueError(f"{name} is missing required columns: {missing}")


def _column(frame: pd.DataFrame, name: str) -> list:
    return frame[name].tolist() if name in frame.columns else [None] * len(frame)


def _entry_groups(entries: pd.DataFrame) -> dict[str, pd.DataFrame]:
    _require(entries, ("arxiv_id", "citation_key"), "entries")
    data = {"arxiv_id": [str(v) for v in entries["arxiv_id"]],
            "citation_key": [str(v) for v in entries["citation_key"]]}
    for source, target in ENTRY_COLUMNS.items():
        data[target] = _column(entries, source)
    frame = pd.DataFrame(data)
    return {paper: g.drop(columns="arxiv_id") for paper, g in frame.groupby("arxiv_id", sort=False)}


def _reference_groups(references: pd.DataFrame) -> dict[str, pd.DataFrame]:
    _require(references, ("arxiv_id", "ref_arxiv_id"), "references")
    canonical = [canonical_arxiv_id(v) if isinstance(v, str) else None for v in references["ref_arxiv_id"]]
    data = {"arxiv_id": [str(v) for v in references["arxiv_id"]], "ref_arxiv_id": canonical}
    for name in REF_COLUMNS:
        data[name] = _column(references, name)
    frame = pd.DataFrame(data)
    frame = frame[frame["ref_arxiv_id"].notna()]            # a reference without a valid arXiv id is no candidate
    return {paper: g.drop(columns="arxiv_id") for paper, g in frame.groupby("arxiv_id", sort=False)}


# ----------------------------------------------------------------------------- pairs
def empty_pairs() -> pd.DataFrame:
    return pd.DataFrame(columns=PAIR_COLUMNS)


def iter_pairs(entries: pd.DataFrame, references: pd.DataFrame, papers: Iterable[str] | None = None,
               drop_future: bool = False) -> Iterator[pd.DataFrame]:
    """Yield the pair matrix of one publication at a time (bounded memory), publications in sorted order.

    Duplicates inside a publication (same citation_key, or same canonical ref_arxiv_id) keep their first row.
    A publication without entries or without references yields nothing.
    """
    entry_groups, ref_groups = _entry_groups(entries), _reference_groups(references)
    wanted = None if papers is None else {str(p) for p in papers}
    for paper in sorted(set(entry_groups) & set(ref_groups)):
        if wanted is not None and paper not in wanted:
            continue
        e = entry_groups[paper].drop_duplicates("citation_key").sort_values("citation_key")
        r = ref_groups[paper].drop_duplicates("ref_arxiv_id").sort_values("ref_arxiv_id")
        cross = e.merge(r, how="cross")
        cross.insert(0, "arxiv_id", paper)

        pub = publication_year(paper)
        year_of = {rid: reference_year(rid, date) for rid, date in zip(r["ref_arxiv_id"], r["ref_submission_date"])}
        ref_years = [year_of[rid] for rid in cross["ref_arxiv_id"]]
        cross["pub_year"] = pd.array([pub] * len(cross), dtype="Int64")
        cross["ref_year"] = pd.array(ref_years, dtype="Int64")
        cross["ref_after_pub"] = [ry is not None and pub is not None and ry > pub for ry in ref_years]
        if drop_future:
            cross = cross.loc[~cross["ref_after_pub"]]
        yield cross[PAIR_COLUMNS].reset_index(drop=True)


def build_pairs(entries: pd.DataFrame, references: pd.DataFrame, papers: Iterable[str] | None = None,
                drop_future: bool = False) -> pd.DataFrame:
    """All candidate pairs (see module docstring), sorted by (arxiv_id, citation_key, ref_arxiv_id)."""
    parts = [p for p in iter_pairs(entries, references, papers, drop_future) if len(p)]
    if not parts:
        return empty_pairs()
    return pd.concat(parts, ignore_index=True)
