"""Milestone 3 - 100% manual labelling with a spreadsheet.

STEP 1: make
    Create one CSV per manual publication with every BibTeX entry of the
    paper and these columns:

        arxiv_id
        citation_key
        entry_title
        entry_authors
        entry_year
        entry_venue
        best_suggestion
        ref_arxiv_id
        match_detail
        note

    `best_suggestion` is an algorithmic hint only. It is NEVER copied to
    `ref_arxiv_id` automatically.

STEP 2: fill
    Open each CSV in Google Sheets and manually compare the BibTeX entry
    against <paper>_references.csv.

    - ref_arxiv_id = the human-verified ground truth arXiv ID
    - ref_arxiv_id = "none" when no matching reference exists
    - leave ref_arxiv_id blank when not done / uncertain
    - match_detail explains the decision

STEP 3: collect
    Validate filled sheets and create labels.csv.

Example:
    python src/label_sheet.py make --mssv 24127465 \
        --bibtex <m2 export>/tables/bibtex \
        --references <m1>/work/tables/references.parquet \
        --selection m3_selection/selection.csv \
        --out-dir labeling/24127465 \
        --papers 2602.11395 2602.12886 2602.11055 2602.10677 2602.11244

    python src/label_sheet.py collect --mssv 24127465 \
        --sheets-dir labeling/24127465_filled \
        --references <m1>/work/tables/references.parquet \
        --selection m3_selection/selection.csv \
        --out labels/labels_manual_24127465.csv
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import pandas as pd

from text_clean import (
    arxiv_id_year,
    author_surnames,
    canonical_arxiv_id,
    extract_arxiv_ids,
    parse_year,
    title_tokens,
)


# ---------------------------------------------------------------------------
# CSV schema
# ---------------------------------------------------------------------------

SHEET_COLUMNS = [
    "arxiv_id",
    "citation_key",
    "entry_title",
    "entry_authors",
    "entry_year",
    "entry_venue",
    "best_suggestion",
    "ref_arxiv_id",
    "match_detail",
    "note",
]

NONE_WORDS = {"none", "no", "n", "-", "0", "null"}

MIN_MATCHED_PER_PAPER = 20
MIN_MATCHED_PER_PERSON = 100

# Only used for the rough count printed in the terminal.
STRONG_SCORE = 0.6


# ---------------------------------------------------------------------------
# IO helpers
# ---------------------------------------------------------------------------

def read_rows(path, ids) -> pd.DataFrame:
    """Read rows from a parquet file/folder or CSV.

    The returned dataframe is restricted to the requested arXiv IDs.
    """
    path = Path(path)
    ids = sorted(set(ids))

    if path.suffix.lower() == ".csv":
        frame = pd.read_csv(
            path,
            dtype=str,
            keep_default_na=False,
        )

        if "arxiv_id" not in frame.columns:
            raise ValueError(
                f"{path} does not contain required column 'arxiv_id'"
            )

        return frame[
            frame["arxiv_id"].isin(ids)
        ].reset_index(drop=True)

    return pd.read_parquet(
        path,
        filters=[("arxiv_id", "in", ids)],
    ).reset_index(drop=True)


def manual_papers(selection, mssv, explicit=None) -> list[str]:
    """Return manual publications belonging to this annotator."""
    frame = pd.read_csv(
        selection,
        dtype=str,
        keep_default_na=False,
    )

    required = {"owner", "kind", "arxiv_id"}
    missing = required - set(frame.columns)

    if missing:
        raise ValueError(
            f"{selection} is missing columns: {sorted(missing)}"
        )

    mine = frame[
        (frame["owner"] == mssv)
        & (frame["kind"] == "manual")
    ]["arxiv_id"].tolist()

    if explicit:
        extra = [
            p for p in explicit
            if p not in mine
        ]

        if extra:
            print(
                f"WARNING: {extra} are not 'manual' publications "
                f"of {mssv} in {selection}",
                file=sys.stderr,
            )

        return list(explicit)

    return mine


def as_list(value):
    """Normalize scalar/list-like author fields to a list of strings."""
    if value is None:
        return []

    if isinstance(value, float) and value != value:
        return []

    if isinstance(value, str):
        return [value]

    try:
        return [str(v) for v in list(value)]
    except TypeError:
        return [str(value)]


# ---------------------------------------------------------------------------
# arXiv ID formatting
# ---------------------------------------------------------------------------

def _clean_cell(value) -> str:
    """Undo spreadsheet decoration.

    Examples:
        ="2511.19116" -> 2511.19116
        '2511.19116  -> 2511.19116
        "2511.19116" -> 2511.19116
    """
    if value is None:
        return ""

    text = str(value).strip()

    if text.startswith("="):
        text = text[1:].strip()

    if len(text) >= 2:
        if (
            (text.startswith('"') and text.endswith('"'))
            or (text.startswith("'") and text.endswith("'"))
        ):
            text = text[1:-1].strip()

    return text


def fix_id(value) -> str:
    """Normalize an arXiv ID from a spreadsheet cell."""
    text = _clean_cell(value)

    if not text:
        return ""

    if text.lower() in NONE_WORDS:
        return "none"

    canonical = canonical_arxiv_id(text)

    if canonical:
        return canonical

    # A fallback for modern arXiv IDs that may have been damaged by
    # spreadsheet formatting.
    compact = re.sub(r"\s+", "", text)

    m = re.fullmatch(
        r"(\d{4})[.]?(\d{4,5})(?:v\d+)?",
        compact,
    )

    if m:
        return f"{m.group(1)}.{m.group(2)}"

    # Old-style IDs such as cond-mat/0510677.
    m = re.fullmatch(
        r"([A-Za-z][A-Za-z0-9-]*)/(?:\d{7})(?:v\d+)?",
        compact,
    )

    if m:
        old = compact.split("/", 1)
        return f"{old[0].lower()}/{old[1]}"

    return text


def sheet_text(value) -> str:
    """Return a value formatted as a Google Sheets text formula.

    This prevents Google Sheets from turning an arXiv ID such as
    2511.19116 into a locale-formatted number.

    Example:
        2511.19116 -> ="2511.19116"
    """
    value = "" if value is None else str(value)

    if not value:
        return ""

    escaped = value.replace('"', '""')
    return f'="{escaped}"'


# ---------------------------------------------------------------------------
# Reference matching
# ---------------------------------------------------------------------------

class _Ref:
    """Lightweight representation of one crawled reference."""

    def __init__(self, row):
        self.id = fix_id(row.get("ref_arxiv_id"))

        self.title = str(
            row.get("ref_title") or ""
        )

        self.authors = as_list(
            row.get("ref_authors")
        )

        self.tset = set(
            title_tokens(self.title)
        )

        self.sset = set(
            author_surnames(self.authors)
        )

        self.year = (
            parse_year(row.get("ref_submission_date"))
            or arxiv_id_year(self.id)
        )


def _score(entry, ref) -> float:
    """Score one BibTeX entry against one reference.

    The score is only used to provide a candidate suggestion.
    It is NOT ground truth.
    """
    title_tokens_e = set(
        title_tokens(
            str(entry.get("title") or "")
        )
    )

    ids = set(
        extract_arxiv_ids(
            entry.get("journal"),
            entry.get("url"),
            entry.get("doi"),
            entry.get("title"),
        )
    )

    title = 0.0

    if title_tokens_e and ref.tset:
        common = title_tokens_e & ref.tset

        title = max(
            len(common)
            / len(title_tokens_e | ref.tset),

            0.9
            * len(common)
            / len(ref.tset),
        )

    surnames = set(
        author_surnames(
            as_list(entry.get("authors"))
        )
    )

    if surnames and ref.sset:
        author = (
            len(surnames & ref.sset)
            / min(
                len(surnames),
                len(ref.sset),
            )
        )
    else:
        author = 0.0

    year = parse_year(
        entry.get("year")
    )

    near = (
        1.0
        if (
            year is not None
            and ref.year is not None
            and abs(year - ref.year) <= 1
        )
        else 0.0
    )

    exact_id_bonus = (
        2.0
        if ref.id in ids
        else 0.0
    )

    return (
        0.6 * title
        + 0.3 * author
        + 0.1 * near
        + exact_id_bonus
    )


def _short(text, n=90):
    """Normalize and shorten display text."""
    text = " ".join(
        str(text).split()
    )

    if len(text) <= n:
        return text

    return text[: n - 1] + "…"


# ---------------------------------------------------------------------------
# MAKE
# ---------------------------------------------------------------------------

def make_sheet(arxiv_id, entries, refs):
    """Create one manual-label sheet.

    Returns:
        sheet:
            DataFrame containing the CSV rows.

        hints:
            List of:
                (citation_key, best_reference_id, score)

    IMPORTANT:
        best_suggestion is written to the CSV only as an algorithmic hint.
        It is NEVER copied into ref_arxiv_id.
    """
    refs_list = [
        _Ref(row)
        for row in refs
        .drop_duplicates("ref_arxiv_id")
        .to_dict("records")
    ]

    rows = []
    hints = []

    for entry in (
        entries
        .drop_duplicates("citation_key")
        .to_dict("records")
    ):
        score, best = max(
            (
                (_score(entry, ref), ref)
                for ref in refs_list
            ),
            key=lambda x: x[0],
            default=(0.0, None),
        )

        best_id = (
            best.id
            if best is not None and score > 0
            else ""
        )

        hints.append(
            (
                entry["citation_key"],
                best_id,
                score,
            )
        )

        authors = as_list(
            entry.get("authors")
        )

        rows.append(
            {
                "arxiv_id": arxiv_id,

                "citation_key": entry[
                    "citation_key"
                ],

                "entry_title": str(
                    entry.get("title") or ""
                ),

                "entry_authors": (
                    ", ".join(authors[:3])
                    + (
                        " et al."
                        if len(authors) > 3
                        else ""
                    )
                ),

                "entry_year": (
                    entry.get("year")
                    if entry.get("year")
                    == entry.get("year")
                    else ""
                ),

                "entry_venue": _short(
                    entry.get("journal")
                    or entry.get("url")
                    or "",
                    80,
                ),

                # Algorithmic suggestion ONLY.
                "best_suggestion": best_id,

                # Human ground truth starts EMPTY.
                "ref_arxiv_id": "",

                "match_detail": "",

                "note": "",
            }
        )

    # IMPORTANT:
    # best_suggestion must be present in SHEET_COLUMNS.
    # Otherwise pandas silently drops it from the DataFrame.
    sheet = pd.DataFrame(
        rows,
        columns=SHEET_COLUMNS,
    )

    return sheet, hints


def cmd_make(a):
    """Generate manual-labeling CSV sheets."""
    papers = manual_papers(
        a.selection,
        a.mssv,
        a.papers,
    )

    if not papers:
        raise SystemExit(
            f"no manual publication found for {a.mssv}"
        )

    bib = read_rows(
        a.bibtex,
        papers,
    )

    refs = read_rows(
        a.references,
        papers,
    )

    out = Path(a.out_dir)
    out.mkdir(
        parents=True,
        exist_ok=True,
    )

    if not a.no_suggestions:
        print(
            "NOTE: 'Best suggestion' lines below are "
            "hints only. They are also written to the "
            "`best_suggestion` CSV column, but they are "
            "NOT ground truth: check every entry by hand "
            "against <paper>_references.csv.\n"
        )

    for paper in papers:
        entries = bib[
            bib["arxiv_id"] == paper
        ]

        paper_refs = refs[
            refs["arxiv_id"] == paper
        ]

        if entries.empty or paper_refs.empty:
            print(
                f"{paper}: {len(entries)} entries, "
                f"{len(paper_refs)} references -> "
                "cannot be labelled, use a reserve "
                "(split.py --replace)"
            )
            continue

        sheet, hints = make_sheet(
            paper,
            entries,
            paper_refs,
        )

        # Force publication ID to text in Google Sheets.
        sheet["arxiv_id"] = sheet[
            "arxiv_id"
        ].map(sheet_text)

        sheet.to_csv(
            out / f"{paper}.csv",
            index=False,
            encoding="utf-8-sig",
        )

        # Reference file used by the human annotator.
        ref_export = paper_refs[
            [
                "ref_arxiv_id",
                "ref_title",
                "ref_authors",
                "ref_submission_date",
            ]
        ].copy()

        ref_export["ref_arxiv_id"] = (
            ref_export["ref_arxiv_id"]
            .map(sheet_text)
        )

        ref_export["ref_authors"] = (
            ref_export["ref_authors"]
            .map(
                lambda value:
                ", ".join(as_list(value))
            )
        )

        ref_export.to_csv(
            out / f"{paper}_references.csv",
            index=False,
            encoding="utf-8-sig",
        )

        strong = sum(
            1
            for _, _, score in hints
            if score >= STRONG_SCORE
        )

        print(
            f"{paper}: {len(sheet)} entries, "
            f"{paper_refs['ref_arxiv_id'].nunique()} "
            f"references, ~{strong} entries with a strong "
            "suggestion (a rough guide to how many matches "
            "to expect)"
        )

        if not a.no_suggestions:
            for key, best, _ in hints:
                print(
                    f"  {key}: Best suggestion: "
                    f"{best or 'none'}"
                )

    print(
        f"\nSheets written to {out}. "
        "Next: fill ref_arxiv_id / match_detail by hand, "
        "then run `collect`."
    )


# ---------------------------------------------------------------------------
# COLLECT
# ---------------------------------------------------------------------------

def cmd_collect(a):
    """Validate filled manual sheets and write labels.csv."""
    sheets_dir = Path(a.sheets_dir)

    if not sheets_dir.is_dir():
        raise SystemExit(
            f"Missing sheets directory: {sheets_dir}"
        )

    papers = manual_papers(
        a.selection,
        a.mssv,
        None,
    )

    if not papers:
        raise SystemExit(
            f"no manual publication found for {a.mssv}"
        )

    refs = read_rows(
        a.references,
        papers,
    )

    labels = []
    errors = []
    warnings = []

    for paper in papers:
        sheet_path = (
            sheets_dir / f"{paper}.csv"
        )

        if not sheet_path.is_file():
            errors.append(
                f"{paper}: missing filled sheet "
                f"{sheet_path}"
            )
            continue

        sheet = pd.read_csv(
            sheet_path,
            dtype=str,
            keep_default_na=False,
            encoding="utf-8-sig",
        )

        required = {
            "arxiv_id",
            "citation_key",
            "entry_title",
            "entry_authors",
            "entry_year",
            "entry_venue",
            "ref_arxiv_id",
            "match_detail",
            "note",
        }

        missing = (
            required
            - set(sheet.columns)
        )

        if missing:
            errors.append(
                f"{paper}: missing columns "
                f"{sorted(missing)}"
            )
            continue

        paper_refs = refs[
            refs["arxiv_id"] == paper
        ]

        valid_refs = {
            fix_id(value)
            for value in paper_refs[
                "ref_arxiv_id"
            ].tolist()
        }

        # Remove empty/invalid IDs from valid reference set.
        valid_refs.discard("")

        matched_count = 0
        paper_labels = []

        for _, row in sheet.iterrows():
            ref = fix_id(
                row.get("ref_arxiv_id", "")
            )

            citation_key = str(
                row.get("citation_key", "")
            ).strip()

            # Blank = not labelled yet.
            if ref == "":
                continue

            # "none" = explicitly verified no match.
            if ref == "none":
                paper_labels.append(
                    {
                        "arxiv_id": paper,
                        "citation_key": citation_key,
                        "ref_arxiv_id": "",
                        "source": "manual",
                        "annotator": (
                            a.annotator
                            or a.mssv
                        ),
                    }
                )
                continue

            # Non-none ID must be one of this paper's references.
            if ref not in valid_refs:
                errors.append(
                    f"{paper}/{citation_key}: "
                    f"ref_arxiv_id={ref!r} is not "
                    "a reference of this publication"
                )
                continue

            matched_count += 1

            paper_labels.append(
                {
                    "arxiv_id": paper,
                    "citation_key": citation_key,
                    "ref_arxiv_id": ref,
                    "source": "manual",
                    "annotator": (
                        a.annotator
                        or a.mssv
                    ),
                }
            )

        if matched_count < MIN_MATCHED_PER_PAPER:
            errors.append(
                f"{paper}: only {matched_count} "
                f"matched pairs; minimum is "
                f"{MIN_MATCHED_PER_PAPER}"
            )

        # Warn if the same reference was assigned to many entries.
        counts = {}

        for label in paper_labels:
            ref = label["ref_arxiv_id"]

            if ref:
                counts[ref] = (
                    counts.get(ref, 0) + 1
                )

        for ref, n in counts.items():
            if n >= 3:
                warnings.append(
                    f"{paper}: reference {ref} is "
                    f"the answer of {n} entries "
                    "(check they are not two different works)"
                )

        labels.extend(paper_labels)

    for warning in warnings:
        print(
            "WARNING:",
            warning,
        )

    if errors:
        print(
            "\n".join(
                "ERROR:  " + error
                for error in errors
            )
        )
        raise SystemExit(1)

    # Only matched rows + explicit none rows are included.
    if len(labels) < MIN_MATCHED_PER_PERSON:
        raise SystemExit(
            f"Only {len(labels)} labelled rows found; "
            f"minimum is {MIN_MATCHED_PER_PERSON}"
        )

    labels_df = pd.DataFrame(
        labels,
        columns=[
            "arxiv_id",
            "citation_key",
            "ref_arxiv_id",
            "source",
            "annotator",
        ],
    )

    # Count actual positive matches.
    total_matched = int(
        labels_df["ref_arxiv_id"]
        .astype(str)
        .str.strip()
        .ne("")
        .sum()
    )

    if total_matched < MIN_MATCHED_PER_PERSON:
        raise SystemExit(
            f"Only {total_matched} matched pairs found; "
            f"minimum is {MIN_MATCHED_PER_PERSON}"
        )

    out = Path(a.out)

    out.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    labels_df.to_csv(
        out,
        index=False,
        encoding="utf-8",
    )

    print(
        f"wrote {len(labels_df)} labels -> {out}"
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description=(
            "100% manual labelling with spreadsheets"
        )
    )

    sub = ap.add_subparsers(
        dest="command",
        required=True,
    )

    # ------------------------------------------------------------------
    # make
    # ------------------------------------------------------------------

    make = sub.add_parser(
        "make",
        help="create manual labelling sheets",
    )

    make.add_argument(
        "--mssv",
        required=True,
    )

    make.add_argument(
        "--bibtex",
        required=True,
    )

    make.add_argument(
        "--references",
        required=True,
    )

    make.add_argument(
        "--selection",
        required=True,
    )

    make.add_argument(
        "--out-dir",
        required=True,
    )

    make.add_argument(
        "--papers",
        nargs="*",
    )

    make.add_argument(
        "--no-suggestions",
        action="store_true",
    )

    make.set_defaults(
        func=cmd_make
    )

    # ------------------------------------------------------------------
    # collect
    # ------------------------------------------------------------------

    collect = sub.add_parser(
        "collect",
        help="validate filled sheets and create labels.csv",
    )

    collect.add_argument(
        "--mssv",
        required=True,
    )

    collect.add_argument(
        "--sheets-dir",
        required=True,
    )

    collect.add_argument(
        "--references",
        required=True,
    )

    collect.add_argument(
        "--selection",
        required=True,
    )

    collect.add_argument(
        "--out",
        required=True,
    )

    collect.add_argument(
        "--annotator",
        default="",
    )

    collect.set_defaults(
        func=cmd_collect
    )

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()