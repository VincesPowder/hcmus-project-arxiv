"""Milestone 3 - 100% manual labelling with a spreadsheet: `make` the sheets, fill them BY HAND, `collect` labels.csv.

STEP 1  make     one CSV per manual publication with every bibtex entry of the paper and three EMPTY columns to
                 fill by hand: `ref_arxiv_id`, `match_detail`, `note`. A second file <paper>_references.csv lists the
                 references of the paper (the ones you search in). Nothing computed is ever written to the CSV.
    python src/label_sheet.py make --mssv 24127465 \\
        --bibtex <m2 export>/tables/bibtex --references <m1>/work/tables/references.parquet \\
        --selection m3_selection/selection.csv --out-dir labeling/24127465 \\
        --papers 2602.11395 2602.12886 2602.11055 2602.10677 2602.11244

    The terminal prints ONE line per entry, `Best suggestion: <arXiv id>`. It is only a hint to save time, it is
    NOT written to the CSV, it never fills `ref_arxiv_id` and it is NOT ground truth. `--no-suggestions` hides it.

STEP 2  fill     open each CSV in Google Sheets and compare entry_title / entry_authors / entry_year / entry_venue
                 with <paper>_references.csv. In column `ref_arxiv_id` write the arXiv id of the ONE matching
                 reference, or `none` when no reference matches; leave it blank while not done. Explain the decision in
                 `match_detail`. Then File > Download > CSV and keep the files in one folder (see MANUAL_LABELING.md).

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
                 "ref_arxiv_id", "match_detail", "note"]
NONE_WORDS = {"none", "no", "n", "-", "0", "null"}
MIN_MATCHED_PER_PAPER, MIN_MATCHED_PER_PERSON = 20, 100
STRONG_SCORE = 0.6          # only used for the rough "strong suggestion" count printed in the terminal


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


# ----------------------------------------------------------------------------- arXiv id formatting
def _clean_cell(value) -> str:
    """Undo spreadsheet decoration: ="2511.19116", a leading apostrophe, surrounding quotes and spaces."""
    text = str(value).strip()
    if text.startswith("="):
        text = text[1:]
    return text.strip().strip("\"'").strip()


def format_arxiv_id(value):
    """Return arXiv IDs in canonical form, repairing what a spreadsheet may have done to them.

    2511.19116 stays 2511.19116; 251119116 and 251.119.116 (thousands separators added by a spreadsheet
    locale) become 2511.19116. canonical_arxiv_id (text_clean) is used as is and always tried first.
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    value = _clean_cell(value)
    if not value:
        return ""

    # Keep already valid modern and legacy IDs in their canonical form.
    canonical = canonical_arxiv_id(value)
    if canonical:
        return canonical

    # Modern IDs without the dot (YYMMNNNN / YYMMNNNNN), possibly with thousands separators: 251.119.116
    if re.fullmatch(r"\d{8,9}", value) or re.fullmatch(r"\d{1,3}(?:[.,\s]\d{3})+", value):
        digits = re.sub(r"\D", "", value)
        if len(digits) in (8, 9):
            return f"{digits[:4]}.{digits[4:]}"

    return value


def sheet_text(value) -> str:
    """Cell that Google Sheets keeps as TEXT (otherwise 2511.19116 may be read as the number 251.119.116)."""
    value = format_arxiv_id(value)
    return f'="{value}"' if value else ""


# ----------------------------------------------------------------------------- suggestions (terminal only)
class _Ref:
    def __init__(self, row):
        self.id = format_arxiv_id(row["ref_arxiv_id"])
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
    """Return (sheet, hints). `sheet` has only descriptive columns plus empty columns to fill by hand.

    `hints` = [(citation_key, best_reference_id or "", score)] is used for the terminal and for the
    `best_suggestion` CSV column; it is never used to fill `ref_arxiv_id`.
    """
    refs_list = [_Ref(r) for r in refs.drop_duplicates("ref_arxiv_id").to_dict("records")]
    rows, hints = [], []
    for entry in entries.drop_duplicates("citation_key").to_dict("records"):
        score, best = max(((_score(entry, r), r) for r in refs_list), key=lambda x: x[0], default=(0.0, None))
        hints.append((entry["citation_key"], best.id if best is not None and score > 0 else "", score))
        authors = as_list(entry.get("authors"))
        rows.append({
            "arxiv_id": arxiv_id, "citation_key": entry["citation_key"], "entry_title": str(entry.get("title") or ""),
            "entry_authors": ", ".join(authors[:3]) + (" et al." if len(authors) > 3 else ""),
            "entry_year": entry.get("year") if entry.get("year") == entry.get("year") else "",
            "entry_venue": _short(entry.get("journal") or entry.get("url") or "", 80),
            "best_suggestion": best.id if best is not None and score > 0 else "",
            "ref_arxiv_id": "", "match_detail": "", "note": ""})
    return pd.DataFrame(rows, columns=SHEET_COLUMNS), hints


def cmd_make(a):
    papers = manual_papers(a.selection, a.mssv, a.papers)
    if not papers:
        raise SystemExit(f"no manual publication found for {a.mssv}")
    bib, refs = read_rows(a.bibtex, papers), read_rows(a.references, papers)
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    if not a.no_suggestions:
        print("NOTE: 'Best suggestion' lines below are hints only. They are also written to the `best_suggestion` CSV column, "
              "but they are NOT ground truth: check every entry by hand against <paper>_references.csv.\n")
    for paper in papers:
        entries, paper_refs = bib[bib["arxiv_id"] == paper], refs[refs["arxiv_id"] == paper]
        if entries.empty or paper_refs.empty:
            print(f"{paper}: {len(entries)} entries, {len(paper_refs)} references -> cannot be labelled, use a reserve (split.py --replace)")
            continue
        sheet, hints = make_sheet(paper, entries, paper_refs)

        # Force Google Sheets to display IDs as text instead of locale-formatted numbers.
        # Sheets evaluates ="2602.12886" and displays the exact arXiv ID.
        sheet["arxiv_id"] = sheet["arxiv_id"].map(sheet_text)
        sheet.to_csv(out / f"{paper}.csv", index=False, encoding="utf-8-sig")
        paper_refs[["ref_arxiv_id", "ref_title", "ref_authors", "ref_submission_date"]].assign(
            ref_arxiv_id=lambda d: d["ref_arxiv_id"].map(sheet_text),
            ref_authors=lambda d: d["ref_authors"].map(lambda v: ", ".join(as_list(v)))).to_csv(
            out / f"{paper}_references.csv", index=False, encoding="utf-8-sig")
        strong = sum(1 for _, _, s in hints if s >= STRONG_SCORE)
        print(f"{paper}: {len(sheet)} entries, {paper_refs['ref_arxiv_id'].nunique()} references, "
              f"~{strong} entries with a strong suggestion (a rough guide to how many matches to expect)")
        if not a.no_suggestions:
            for key, best, _ in hints:
                print(f"  {key}: Best suggestion: {best or 'none'}")
    print(f"\nSheets written to {out}. Next: fill ref_arxiv_id / match_detail by hand, then run `collect`.")


# ----------------------------------------------------------------------------- collect
def fix_id(value, valid):
    """Normalize arXiv IDs and repair spreadsheet formatting; None if not a valid id."""
    value = _clean_cell(value)
    normalized = format_arxiv_id(value)
    if normalized in valid:
        return normalized
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
    if "match_detail" not in sheets.columns:                      # sheets made by an older version
        sheets["match_detail"] = ""
    sheets["arxiv_id"] = [fix_id(v, set(papers)) or v for v in sheets["arxiv_id"]]
    refs = read_rows(a.references, set(sheets["arxiv_id"]))
    valid = {p: set(g["ref_arxiv_id"].map(format_arxiv_id)) for p, g in refs.groupby("arxiv_id")}

    errors, warnings, rows = [], [], []
    if sheets.duplicated(["arxiv_id", "citation_key"]).any():
        errors.append("duplicated (arxiv_id, citation_key) rows across the sheets")
    for r in sheets.itertuples(index=False):
        value = _clean_cell(r.ref_arxiv_id)
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

    filled = sheets[sheets["ref_arxiv_id"].map(_clean_cell) != ""]
    no_detail = int((filled["match_detail"].str.strip() == "").sum())
    if no_detail:
        warnings.append(f"{no_detail} labelled rows have an empty match_detail "
                        f"(write why it matches, or 'No matching reference found' for none)")

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
    ap = argparse.ArgumentParser(description="100% manual labelling with spreadsheets")
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
    make.add_argument("--no-suggestions", action="store_true",
                      help="do not print the 'Best suggestion' hint of each entry (label completely blind)")
    collect.add_argument("--sheets-dir", required=True)
    collect.add_argument("--out", required=True)
    collect.add_argument("--annotator", default=None)
    a = ap.parse_args()
    {"make": cmd_make, "collect": cmd_collect}[a.command](a)


if __name__ == "__main__":
    main()