"""Milestone 3 - manual labelling with a spreadsheet: `make` the sheets, fill them, `collect` labels.csv.

STEP 1  make     one CSV per manual publication: every bibtex entry of the paper, its 5 best candidate
                 references (suggestions only, NOT labels) and an empty column `ref_arxiv_id` to fill.
    python src/label_sheet.py make --mssv 24127465 \\
        --bibtex <m2 export>/tables/bibtex --references <m1>/work/tables/references.parquet \\
        --selection m3_selection/selection.csv --out-dir labeling/24127465 \\
        --papers 2602.11395 2602.12886 2602.11055 2602.10677 2602.11244

STEP 2  fill     open each CSV in Google Sheets (import with "Convert text to numbers" UNCHECKED), write in
                 column `ref_arxiv_id`:  the arXiv id of the true reference | none (no reference matches) | blank (not done yet)
                 then File > Download > CSV and keep the files in one folder.

STEP 3  collect  validate the filled sheets and write labels.csv (format of docs/m3_labels.md).
    python src/label_sheet.py collect --mssv 24127465 --sheets-dir labeling/24127465_filled \\
        --references <m1>/work/tables/references.parquet --selection m3_selection/selection.csv \\
        --out labels/labels_manual_24127465.csv

Only blank-vs-none matters for correctness: blank rows are NOT written (unlabeled), `none` rows are written
with an empty ref_arxiv_id (an all-negative row for the model).
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import pandas as pd

from text_clean import (arxiv_id_year, author_surnames, canonical_arxiv_id, extract_arxiv_ids, parse_year,
                        title_tokens)

SHEET_COLUMNS = ["arxiv_id", "citation_key", "entry_title", "entry_authors", "entry_year", "entry_venue",
                 "suggestions", "best_guess", "ref_arxiv_id", "note"]
NONE_WORDS = {"none", "no", "n", "-", "0", "null"}
MIN_MATCHED_PER_PAPER, MIN_MATCHED_PER_PERSON = 20, 100


# ----------------------------------------------------------------------------- io
def read_rows(path, ids) -> pd.DataFrame:
    """Rows of a parquet file/folder (filtered while reading) or of a CSV, restricted to the given arxiv_ids."""
    path, ids = Path(path), sorted(ids)
    if path.suffix == ".csv":
        frame = pd.read_csv(path, dtype=str, keep_default_na=False)
        return frame[frame["arxiv_id"].isin(ids)].reset_index(drop=True)
    return pd.read_parquet(path, filters=[("arxiv_id", "in", ids)]).reset_index(drop=True)


def manual_papers(selection, mssv, explicit=None) -> list[str]:
    frame = pd.read_csv(selection, dtype=str, keep_default_na=False)
    mine = frame[(frame["owner"] == mssv) & (frame["kind"] == "manual")]["arxiv_id"].tolist()
    if explicit:
        extra = [p for p in explicit if p not in mine]
        if extra:
            print(f"WARNING: {extra} are not 'manual' publications of {mssv} in {selection}", file=sys.stderr)
        return list(explicit)
    return mine


def as_list(value):
    if value is None or (isinstance(value, float) and value != value):
        return []
    if isinstance(value, str):
        return [value]
    return [str(v) for v in list(value)]


# ----------------------------------------------------------------------------- suggestions
class _Ref:
    def __init__(self, row):
        self.id = canonical_arxiv_id(str(row["ref_arxiv_id"])) or str(row["ref_arxiv_id"])
        self.title = str(row.get("ref_title") or "")
        self.authors = as_list(row.get("ref_authors"))
        self.tset = set(title_tokens(self.title))
        self.sset = set(author_surnames(self.authors))
        self.year = parse_year(row.get("ref_submission_date")) or arxiv_id_year(self.id)


def _score(entry, ref) -> float:
    title_tokens_e = set(title_tokens(str(entry.get("title") or "")))
    ids = set(extract_arxiv_ids(entry.get("journal"), entry.get("url"), entry.get("doi"), entry.get("title")))
    title = 0.0
    if title_tokens_e and ref.tset:
        common = title_tokens_e & ref.tset
        title = max(len(common) / len(title_tokens_e | ref.tset), 0.9 * len(common) / len(ref.tset))
    surnames = set(author_surnames(as_list(entry.get("authors"))))
    author = len(surnames & ref.sset) / min(len(surnames), len(ref.sset)) if surnames and ref.sset else 0.0
    year = parse_year(entry.get("year"))
    near = 1.0 if year is not None and ref.year is not None and abs(year - ref.year) <= 1 else 0.0
    return 0.6 * title + 0.3 * author + 0.1 * near + (2.0 if ref.id in ids else 0.0)


def _short(text, n=90):
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


def make_sheet(arxiv_id, entries, refs):
    refs_list = [_Ref(r) for r in refs.drop_duplicates("ref_arxiv_id").to_dict("records")]
    rows = []
    for entry in entries.drop_duplicates("citation_key").to_dict("records"):
        ranked = sorted(((_score(entry, r), r) for r in refs_list), key=lambda x: -x[0])[:5]
        lines = [f"{i}) {r.id} | {_short(r.title)} | {', '.join(r.authors[:2])} | {r.year} | {s:.2f}"
                 for i, (s, r) in enumerate(ranked, 1)]
        authors = as_list(entry.get("authors"))
        rows.append({
            "arxiv_id": arxiv_id, "citation_key": entry["citation_key"], "entry_title": str(entry.get("title") or ""),
            "entry_authors": ", ".join(authors[:3]) + (" et al." if len(authors) > 3 else ""),
            "entry_year": entry.get("year") if entry.get("year") == entry.get("year") else "",
            "entry_venue": _short(entry.get("journal") or entry.get("url") or "", 80),
            "suggestions": "\n".join(lines), "best_guess": ranked[0][1].id if ranked and ranked[0][0] > 0 else "",
            "ref_arxiv_id": "", "note": ""})
    return pd.DataFrame(rows, columns=SHEET_COLUMNS)


def cmd_make(a):
    papers = manual_papers(a.selection, a.mssv, a.papers)
    if not papers:
        raise SystemExit(f"no manual publication found for {a.mssv}")
    bib, refs = read_rows(a.bibtex, papers), read_rows(a.references, papers)
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for paper in papers:
        entries, paper_refs = bib[bib["arxiv_id"] == paper], refs[refs["arxiv_id"] == paper]
        if entries.empty or paper_refs.empty:
            print(f"{paper}: {len(entries)} entries, {len(paper_refs)} references -> cannot be labelled, use a reserve (split.py --replace)")
            continue
        sheet = make_sheet(paper, entries, paper_refs)
        strong = int((sheet["suggestions"].str.extract(r"1\) .*\| ([0-9.]+)$", flags=re.M)[0].astype(float) >= 0.6).sum())
        sheet.to_csv(out / f"{paper}.csv", index=False, encoding="utf-8-sig")
        paper_refs[["ref_arxiv_id", "ref_title", "ref_authors", "ref_submission_date"]].assign(
            ref_authors=lambda d: d["ref_authors"].map(lambda v: ", ".join(as_list(v)))).to_csv(
            out / f"{paper}_references.csv", index=False, encoding="utf-8-sig")
        print(f"{paper}: {len(sheet)} entries, {paper_refs['ref_arxiv_id'].nunique()} references, "
              f"~{strong} entries with a strong suggestion (a rough guide to how many matches to expect)")
    print(f"\nSheets written to {out}. Next: fill the column ref_arxiv_id, then run `collect`.")


# ----------------------------------------------------------------------------- collect
def fix_id(value, valid):
    """Repair what a spreadsheet does to ids ('1810.0481' lost its trailing zero); None if not a valid id."""
    value = str(value).strip()
    if value in valid:
        return value
    m = re.fullmatch(r"(\d{4})\.(\d{1,4})", value)
    if m:
        candidate = f"{m.group(1)}.{m.group(2).ljust(5, '0')}"
        if candidate in valid:
            return candidate
    canon = canonical_arxiv_id(value)
    return canon if canon in valid else None


def cmd_collect(a):
    papers = manual_papers(a.selection, a.mssv, a.papers)
    files = sorted(p for p in Path(a.sheets_dir).glob("*.csv") if not p.name.endswith("_references.csv"))
    if not files:
        raise SystemExit(f"no sheet in {a.sheets_dir}")
    sheets = pd.concat([pd.read_csv(f, dtype=str, keep_default_na=False) for f in files], ignore_index=True)
    sheets["arxiv_id"] = [fix_id(v, set(papers)) or v for v in sheets["arxiv_id"]]
    refs = read_rows(a.references, set(sheets["arxiv_id"]))
    valid = {p: set(g["ref_arxiv_id"].map(lambda v: canonical_arxiv_id(v) or v)) for p, g in refs.groupby("arxiv_id")}

    errors, warnings, rows = [], [], []
    if sheets.duplicated(["arxiv_id", "citation_key"]).any():
        errors.append("duplicated (arxiv_id, citation_key) rows across the sheets")
    for r in sheets.itertuples(index=False):
        value = str(r.ref_arxiv_id).strip()
        if not value:
            continue                                               # not labelled yet
        if value.lower() in NONE_WORDS:
            rows.append((r.arxiv_id, r.citation_key, ""))
            continue
        ref = fix_id(value, valid.get(r.arxiv_id, set()))
        if ref is None:
            errors.append(f"{r.arxiv_id} / {r.citation_key}: '{value}' is not a reference of this publication")
        else:
            rows.append((r.arxiv_id, r.citation_key, ref))
    labels = pd.DataFrame(rows, columns=["arxiv_id", "citation_key", "ref_arxiv_id"])

    print(f"{'paper':12s} {'entries':>7s} {'done':>5s} {'matched':>8s} {'none':>5s} {'todo':>5s}")
    total_matched = 0
    for paper in papers:
        total = int((sheets["arxiv_id"] == paper).sum())
        mine = labels[labels["arxiv_id"] == paper]
        matched = int((mine["ref_arxiv_id"] != "").sum())
        total_matched += matched
        flag = "" if matched >= MIN_MATCHED_PER_PAPER else f"   <-- needs >= {MIN_MATCHED_PER_PAPER} matched"
        print(f"{paper:12s} {total:7d} {len(mine):5d} {matched:8d} {len(mine) - matched:5d} {total - len(mine):5d}{flag}")
        if total and matched < MIN_MATCHED_PER_PAPER and total - len(mine) == 0:
            errors.append(f"{paper}: only {matched} matched pairs (< {MIN_MATCHED_PER_PAPER}); replace it by a reserve publication")
        if total == 0:
            errors.append(f"{paper}: no sheet found")
    print(f"matched pairs for {a.mssv}: {total_matched} (needs >= {MIN_MATCHED_PER_PERSON})")
    if total_matched < MIN_MATCHED_PER_PERSON:
        warnings.append(f"{total_matched} matched pairs in total, < {MIN_MATCHED_PER_PERSON}")
    shared = labels[labels["ref_arxiv_id"] != ""].groupby(["arxiv_id", "ref_arxiv_id"]).size()
    for (paper, ref), n in shared[shared > 1].items():
        warnings.append(f"{paper}: reference {ref} is the answer of {n} entries (check they are not two different works)")

    for w in warnings:
        print("WARNING:", w)
    if errors:
        print("\n".join("ERROR:  " + e for e in errors))
        raise SystemExit(1)
    labels["source"], labels["annotator"] = "manual", a.annotator or a.mssv
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    labels.to_csv(out, index=False, encoding="utf-8")
    print(f"wrote {len(labels)} labels -> {out}")


def main():
    ap = argparse.ArgumentParser(description="Manual labelling with spreadsheets")
    sub = ap.add_subparsers(dest="command", required=True)
    make = sub.add_parser("make")
    collect = sub.add_parser("collect")
    for p in (make, collect):
        p.add_argument("--mssv", required=True)
        p.add_argument("--selection", required=True)
        p.add_argument("--references", required=True)
        p.add_argument("--papers", nargs="*", default=None, help="manual publications (default: the ones of selection.csv)")
    make.add_argument("--bibtex", required=True, help="M2 bibtex table (folder of shards)")
    make.add_argument("--out-dir", required=True)
    collect.add_argument("--sheets-dir", required=True)
    collect.add_argument("--out", required=True)
    collect.add_argument("--annotator", default=None)
    a = ap.parse_args()
    {"make": cmd_make, "collect": cmd_collect}[a.command](a)


if __name__ == "__main__":
    main()