"""Milestone 3 - manual labelling with a spreadsheet.

STEP 1  make
    Tạo một CSV cho mỗi publication thủ công.
    Mỗi BibTeX entry có một best_suggestion do thuật toán đề xuất.
    best_suggestion CHỈ LÀ GỢI Ý, KHÔNG PHẢI GROUND TRUTH.

STEP 2  fill
    Mở CSV bằng Google Sheets và điền thủ công:
        ref_arxiv_id
        match_detail
        note

    ref_arxiv_id:
        - arXiv ID của reference đúng
        - none nếu đã kiểm tra và không có reference phù hợp
        - để trống nếu chưa làm / chưa chắc

STEP 3  collect
    Kiểm tra các CSV đã điền và tạo labels_manual_*.csv.

QUAN TRỌNG:
    best_suggestion không bao giờ được dùng để tự động điền ref_arxiv_id.
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


# ---------------------------------------------------------------------------
# IO
# ---------------------------------------------------------------------------

def read_rows(path, ids) -> pd.DataFrame:
    """Đọc parquet hoặc CSV và chỉ giữ các publication cần thiết."""
    path = Path(path)
    ids = sorted(ids)

    if path.suffix.lower() == ".csv":
        frame = pd.read_csv(
            path,
            dtype=str,
            keep_default_na=False,
        )
        return frame[
            frame["arxiv_id"].isin(ids)
        ].reset_index(drop=True)

    return pd.read_parquet(
        path,
        filters=[("arxiv_id", "in", ids)],
    ).reset_index(drop=True)


def manual_papers(selection, mssv, explicit=None) -> list[str]:
    """Lấy danh sách publication manual của annotator."""
    frame = pd.read_csv(
        selection,
        dtype=str,
        keep_default_na=False,
    )

    mine = frame[
        (frame["owner"] == mssv)
        & (frame["kind"] == "manual")
    ]["arxiv_id"].tolist()

    if explicit:
        extra = [p for p in explicit if p not in mine]

        if extra:
            print(
                f"WARNING: {extra} are not 'manual' publications "
                f"of {mssv} in {selection}",
                file=sys.stderr,
            )

        return list(explicit)

    return mine


def as_list(value):
    """Chuyển author field về list."""
    if value is None:
        return []

    if isinstance(value, float) and value != value:
        return []

    if isinstance(value, str):
        return [value]

    return [str(v) for v in list(value)]


# ---------------------------------------------------------------------------
# Suggestion matcher
# ---------------------------------------------------------------------------

class _Ref:
    def __init__(self, row):
        raw_id = str(row["ref_arxiv_id"])

        self.id = (
            canonical_arxiv_id(raw_id)
            or raw_id
        )

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
            parse_year(
                row.get("ref_submission_date")
            )
            or arxiv_id_year(self.id)
        )


def _score(entry, ref) -> float:
    """
    Tính điểm gợi ý giữa BibTeX entry và reference.

    Đây chỉ là suggestion score.
    Không phải ground truth.
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

    # ---------------------------------------------------------------
    # Title similarity
    # ---------------------------------------------------------------

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

    # ---------------------------------------------------------------
    # Author similarity
    # ---------------------------------------------------------------

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

    # ---------------------------------------------------------------
    # Year similarity
    # ---------------------------------------------------------------

    year = parse_year(
        entry.get("year")
    )

    if (
        year is not None
        and ref.year is not None
        and abs(year - ref.year) <= 1
    ):
        near = 1.0
    else:
        near = 0.0

    # ---------------------------------------------------------------
    # Explicit arXiv ID match gets strong bonus
    # ---------------------------------------------------------------

    id_bonus = (
        2.0
        if ref.id in ids
        else 0.0
    )

    return (
        0.6 * title
        + 0.3 * author
        + 0.1 * near
        + id_bonus
    )


def _short(text, n=90):
    """Rút gọn text để hiển thị."""
    text = " ".join(
        str(text).split()
    )

    if len(text) <= n:
        return text

    return text[: n - 1] + "…"


# ---------------------------------------------------------------------------
# Make spreadsheet
# ---------------------------------------------------------------------------

def make_sheet(arxiv_id, entries, refs):
    """
    Tạo dataframe cho một publication.

    best_suggestion:
        Gợi ý tốt nhất của thuật toán.

    ref_arxiv_id:
        LUÔN để trống để người dùng tự gán nhãn.

    match_detail:
        LUÔN để trống để người dùng ghi bằng chứng đối chiếu.
    """

    refs_list = [
        _Ref(row)
        for row in (
            refs
            .drop_duplicates("ref_arxiv_id")
            .to_dict("records")
        )
    ]

    rows = []

    for entry in (
        entries
        .drop_duplicates("citation_key")
        .to_dict("records")
    ):

        # -----------------------------------------------------------
        # Rank all references
        # -----------------------------------------------------------

        ranked = sorted(
            (
                (
                    _score(entry, ref),
                    ref,
                )
                for ref in refs_list
            ),
            key=lambda x: -x[0],
        )

        # -----------------------------------------------------------
        # Best suggestion
        # -----------------------------------------------------------

        if ranked:
            best_score, best_ref = ranked[0]

            if best_score > 0:
                best_suggestion = best_ref.id
            else:
                best_suggestion = ""
        else:
            best_score = 0.0
            best_suggestion = ""

        # -----------------------------------------------------------
        # Entry metadata
        # -----------------------------------------------------------

        authors = as_list(
            entry.get("authors")
        )

        entry_year = entry.get("year")

        if entry_year != entry_year:
            entry_year = ""

        entry_venue = _short(
            entry.get("journal")
            or entry.get("url")
            or "",
            80,
        )

        # -----------------------------------------------------------
        # IMPORTANT:
        #
        # best_suggestion = algorithm suggestion
        # ref_arxiv_id    = human ground truth
        #
        # NEVER copy one into the other.
        # -----------------------------------------------------------

        rows.append(
            {
                "arxiv_id": arxiv_id,

                "citation_key": entry[
                    "citation_key"
                ],

                "entry_title": str(
                    entry.get("title")
                    or ""
                ),

                "entry_authors": (
                    ", ".join(authors[:3])
                    + (
                        " et al."
                        if len(authors) > 3
                        else ""
                    )
                ),

                "entry_year": entry_year,

                "entry_venue": entry_venue,

                # THIS IS THE FIX
                "best_suggestion": (
                    best_suggestion
                ),

                # Human fills these manually
                "ref_arxiv_id": "",

                "match_detail": "",

                "note": "",
            }
        )

    # Explicit column order.
    # This prevents pandas from silently dropping best_suggestion.
    return pd.DataFrame(
        rows,
        columns=SHEET_COLUMNS,
    )


# ---------------------------------------------------------------------------
# MAKE command
# ---------------------------------------------------------------------------

def cmd_make(a):

    papers = manual_papers(
        a.selection,
        a.mssv,
        a.papers,
    )

    if not papers:
        raise SystemExit(
            f"no manual publication found "
            f"for {a.mssv}"
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

    print(
        "NOTE: 'Best suggestion' lines below "
        "are hints only. They are also written "
        "to the `best_suggestion` CSV column, "
        "but they are NOT ground truth: check "
        "every entry by hand against "
        "<paper>_references.csv."
    )

    print()

    for paper in papers:

        entries = bib[
            bib["arxiv_id"] == paper
        ]

        paper_refs = refs[
            refs["arxiv_id"] == paper
        ]

        if (
            entries.empty
            or paper_refs.empty
        ):
            print(
                f"{paper}: "
                f"{len(entries)} entries, "
                f"{len(paper_refs)} references "
                "-> cannot be labelled, "
                "use a reserve "
                "(split.py --replace)"
            )
            continue

        sheet = make_sheet(
            paper,
            entries,
            paper_refs,
        )

        # -----------------------------------------------------------
        # Count strong suggestions
        # -----------------------------------------------------------

        strong = 0

        for value in sheet[
            "best_suggestion"
        ]:

            if str(value).strip():
                strong += 1

        # -----------------------------------------------------------
        # WRITE MAIN CSV
        # -----------------------------------------------------------

        output_csv = (
            out / f"{paper}.csv"
        )

        sheet.to_csv(
            output_csv,
            index=False,
            encoding="utf-8-sig",
        )

        # -----------------------------------------------------------
        # WRITE REFERENCES CSV
        # -----------------------------------------------------------

        references_csv = (
            out / f"{paper}_references.csv"
        )

        refs_to_write = paper_refs[
            [
                "ref_arxiv_id",
                "ref_title",
                "ref_authors",
                "ref_submission_date",
            ]
        ].copy()

        refs_to_write[
            "ref_arxiv_id"
        ] = refs_to_write[
            "ref_arxiv_id"
        ].map(
            lambda value:
                canonical_arxiv_id(
                    value
                )
                or str(value)
        )

        refs_to_write[
            "ref_authors"
        ] = refs_to_write[
            "ref_authors"
        ].map(
            lambda value:
                ", ".join(
                    as_list(value)
                )
        )

        refs_to_write.to_csv(
            references_csv,
            index=False,
            encoding="utf-8-sig",
        )

        # -----------------------------------------------------------
        # Terminal output
        # -----------------------------------------------------------

        print(
            f"{paper}: "
            f"{len(sheet)} entries, "
            f"{paper_refs['ref_arxiv_id'].nunique()} references, "
            f"~{strong} entries with a strong suggestion "
            f"(a rough guide to how many matches to expect)"
        )

        # -----------------------------------------------------------
        # Print actual suggestions
        # -----------------------------------------------------------

        for row in sheet.itertuples(
            index=False
        ):

            suggestion = str(
                row.best_suggestion
            ).strip()

            if suggestion:
                print(
                    f"  {row.citation_key}: "
                    f"Best suggestion: "
                    f"{suggestion}"
                )

    print()

    print(
        f"Sheets written to {out}. "
        "Next: fill ref_arxiv_id / "
        "match_detail by hand, then run `collect`."
    )


# ---------------------------------------------------------------------------
# Collect helpers
# ---------------------------------------------------------------------------

def fix_id(value, valid):
    """
    Normalize arXiv IDs and repair
    common spreadsheet formatting errors.

    Examples:

        2511.19116
            -> 2511.19116

        251119116
            -> 2511.19116

        251.119.116
            -> 2511.19116

        1810.0481
            -> 1810.04810
            if that canonical ID exists in valid
    """

    value = str(value).strip()

    # Empty
    if not value:
        return None

    # Remove spreadsheet formula decoration
    if value.startswith("="):
        value = value[1:]

    value = value.strip(
        "\"'"
    ).strip()

    # Direct match
    if value in valid:
        return value

    # -----------------------------------------------------------
    # canonical_arxiv_id
    # -----------------------------------------------------------

    canon = canonical_arxiv_id(
        value
    )

    if canon in valid:
        return canon

    # -----------------------------------------------------------
    # Standard modern arXiv ID
    # Example:
    #     2511.19116
    # -----------------------------------------------------------

    m = re.fullmatch(
        r"(\d{4})\.(\d{1,5})",
        value,
    )

    if m:

        candidate = (
            f"{m.group(1)}."
            f"{m.group(2).ljust(5, '0')}"
        )

        if candidate in valid:
            return candidate

    # -----------------------------------------------------------
    # Spreadsheet removed the dot:
    #
    # 251119116
    # -> 2511.19116
    # -----------------------------------------------------------

    m = re.fullmatch(
        r"(\d{4})(\d{5})",
        value,
    )

    if m:

        candidate = (
            f"{m.group(1)}."
            f"{m.group(2)}"
        )

        if candidate in valid:
            return candidate

    # -----------------------------------------------------------
    # Spreadsheet inserted thousands separators:
    #
    # 251.119.116
    # -> 2511.19116
    # -----------------------------------------------------------

    compact = value.replace(
        ".",
        "",
    )

    if re.fullmatch(
        r"\d{9}",
        compact,
    ):

        candidate = (
            f"{compact[:4]}."
            f"{compact[4:]}"
        )

        if candidate in valid:
            return candidate

    return None


# ---------------------------------------------------------------------------
# COLLECT command
# ---------------------------------------------------------------------------

def cmd_collect(a):

    papers = manual_papers(
        a.selection,
        a.mssv,
        a.papers,
    )

    files = sorted(
        p
        for p in Path(
            a.sheets_dir
        ).glob("*.csv")
        if not p.name.endswith(
            "_references.csv"
        )
    )

    if not files:
        raise SystemExit(
            f"no sheet in {a.sheets_dir}"
        )

    # -----------------------------------------------------------
    # Read all filled sheets
    # -----------------------------------------------------------

    frames = []

    for file in files:

        frame = pd.read_csv(
            file,
            dtype=str,
            keep_default_na=False,
        )

        frames.append(frame)

    sheets = pd.concat(
        frames,
        ignore_index=True,
    )

    # -----------------------------------------------------------
    # Validate required columns
    # -----------------------------------------------------------

    required_columns = {
        "arxiv_id",
        "citation_key",
        "ref_arxiv_id",
    }

    missing = (
        required_columns
        - set(sheets.columns)
    )

    if missing:
        raise SystemExit(
            "Missing required columns: "
            + ", ".join(
                sorted(missing)
            )
        )

    # -----------------------------------------------------------
    # Normalize publication IDs
    # -----------------------------------------------------------

    paper_set = set(papers)

    sheets["arxiv_id"] = [
        fix_id(
            value,
            paper_set,
        )
        or value
        for value in sheets[
            "arxiv_id"
        ]
    ]

    # -----------------------------------------------------------
    # Read valid references
    # -----------------------------------------------------------

    refs = read_rows(
        a.references,
        set(
            sheets["arxiv_id"]
        ),
    )

    valid = {
        paper: set(
            group[
                "ref_arxiv_id"
            ].map(
                lambda value:
                    canonical_arxiv_id(
                        value
                    )
                    or str(value)
            )
        )
        for paper, group
        in refs.groupby(
            "arxiv_id"
        )
    }

    errors = []
    warnings = []
    rows = []

    # -----------------------------------------------------------
    # Duplicate BibTeX entries
    # -----------------------------------------------------------

    if sheets.duplicated(
        [
            "arxiv_id",
            "citation_key",
        ]
    ).any():

        errors.append(
            "duplicated "
            "(arxiv_id, citation_key) "
            "rows across the sheets"
        )

    # -----------------------------------------------------------
    # Validate each manually labelled row
    # -----------------------------------------------------------

    for row in sheets.itertuples(
        index=False
    ):

        value = str(
            row.ref_arxiv_id
        ).strip()

        # Blank = not labelled yet
        if not value:
            continue

        # -------------------------------------------------------
        # none = all-negative example
        # -------------------------------------------------------

        if value.lower() in NONE_WORDS:

            rows.append(
                (
                    row.arxiv_id,
                    row.citation_key,
                    "",
                )
            )

            continue

        # -------------------------------------------------------
        # Validate actual arXiv reference
        # -------------------------------------------------------

        ref = fix_id(
            value,
            valid.get(
                row.arxiv_id,
                set(),
            ),
        )

        if ref is None:

            errors.append(
                f"{row.arxiv_id} / "
                f"{row.citation_key}: "
                f"'{value}' is not a "
                f"reference of this publication"
            )

        else:

            rows.append(
                (
                    row.arxiv_id,
                    row.citation_key,
                    ref,
                )
            )

    # -----------------------------------------------------------
    # Build labels
    # -----------------------------------------------------------

    labels = pd.DataFrame(
        rows,
        columns=[
            "arxiv_id",
            "citation_key",
            "ref_arxiv_id",
        ],
    )

    # -----------------------------------------------------------
    # Per-paper statistics
    # -----------------------------------------------------------

    print(
        f"{'paper':12s} "
        f"{'entries':>7s} "
        f"{'done':>5s} "
        f"{'matched':>8s} "
        f"{'none':>5s} "
        f"{'todo':>5s}"
    )

    total_matched = 0

    for paper in papers:

        total = int(
            (
                sheets["arxiv_id"]
                == paper
            ).sum()
        )

        mine = labels[
            labels["arxiv_id"]
            == paper
        ]

        matched = int(
            (
                mine[
                    "ref_arxiv_id"
                ] != ""
            ).sum()
        )

        total_matched += matched

        flag = ""

        if matched < MIN_MATCHED_PER_PAPER:

            flag = (
                f"   <-- needs >= "
                f"{MIN_MATCHED_PER_PAPER} matched"
            )

        print(
            f"{paper:12s} "
            f"{total:7d} "
            f"{len(mine):5d} "
            f"{matched:8d} "
            f"{len(mine) - matched:5d} "
            f"{total - len(mine):5d}"
            f"{flag}"
        )

        if (
            total
            and matched < MIN_MATCHED_PER_PAPER
            and total - len(mine) == 0
        ):

            errors.append(
                f"{paper}: only "
                f"{matched} matched pairs "
                f"(< {MIN_MATCHED_PER_PAPER}); "
                "replace it by a reserve publication"
            )

        if total == 0:

            errors.append(
                f"{paper}: no sheet found"
            )

    # -----------------------------------------------------------
    # Overall statistics
    # -----------------------------------------------------------

    print(
        f"matched pairs for {a.mssv}: "
        f"{total_matched} "
        f"(needs >= {MIN_MATCHED_PER_PERSON})"
    )

    if total_matched < MIN_MATCHED_PER_PERSON:

        warnings.append(
            f"{total_matched} matched pairs "
            f"in total, < "
            f"{MIN_MATCHED_PER_PERSON}"
        )

    # -----------------------------------------------------------
    # Multiple BibTeX entries pointing to same reference
    # -----------------------------------------------------------

    shared = (
        labels[
            labels["ref_arxiv_id"] != ""
        ]
        .groupby(
            [
                "arxiv_id",
                "ref_arxiv_id",
            ]
        )
        .size()
    )

    for (
        paper,
        ref,
    ), n in shared[
        shared > 1
    ].items():

        warnings.append(
            f"{paper}: reference {ref} "
            f"is the answer of {n} entries "
            "(check they are not two different works)"
        )

    # -----------------------------------------------------------
    # Print warnings
    # -----------------------------------------------------------

    for warning in warnings:

        print(
            "WARNING:",
            warning,
        )

    # -----------------------------------------------------------
    # Stop on errors
    # -----------------------------------------------------------

    if errors:

        for error in errors:

            print(
                "ERROR:",
                error,
            )

        raise SystemExit(1)

    # -----------------------------------------------------------
    # Final labels
    # -----------------------------------------------------------

    labels["source"] = "manual"

    labels["annotator"] = (
        a.annotator
        or a.mssv
    )

    out = Path(a.out)

    out.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    labels.to_csv(
        out,
        index=False,
        encoding="utf-8",
    )

    print(
        f"wrote {len(labels)} labels -> {out}"
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():

    ap = argparse.ArgumentParser(
        description=(
            "Manual labelling "
            "with spreadsheets"
        )
    )

    sub = ap.add_subparsers(
        dest="command",
        required=True,
    )

    make = sub.add_parser(
        "make"
    )

    collect = sub.add_parser(
        "collect"
    )

    # -----------------------------------------------------------
    # Common arguments
    # -----------------------------------------------------------

    for parser in (
        make,
        collect,
    ):

        parser.add_argument(
            "--mssv",
            required=True,
        )

        parser.add_argument(
            "--selection",
            required=True,
        )

        parser.add_argument(
            "--references",
            required=True,
        )

        parser.add_argument(
            "--papers",
            nargs="*",
            default=None,
            help=(
                "manual publications "
                "(default: publications "
                "of selection.csv)"
            ),
        )

    # -----------------------------------------------------------
    # make arguments
    # -----------------------------------------------------------

    make.add_argument(
        "--bibtex",
        required=True,
        help=(
            "M2 bibtex table "
            "(folder of shards)"
        ),
    )

    make.add_argument(
        "--out-dir",
        required=True,
    )

    # -----------------------------------------------------------
    # collect arguments
    # -----------------------------------------------------------

    collect.add_argument(
        "--sheets-dir",
        required=True,
    )

    collect.add_argument(
        "--out",
        required=True,
    )

    collect.add_argument(
        "--annotator",
        default=None,
    )

    # -----------------------------------------------------------
    # Execute
    # -----------------------------------------------------------

    args = ap.parse_args()

    if args.command == "make":
        cmd_make(args)

    elif args.command == "collect":
        cmd_collect(args)


if __name__ == "__main__":
    main()