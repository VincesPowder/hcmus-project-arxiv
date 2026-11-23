import argparse
import os
import re
import sys
import pandas as pd


# ============================================================
# Utilities
# ============================================================

def norm(value):
    """Normalize text for comparison."""
    if value is None:
        return ""

    s = str(value).strip().lower()

    # arXiv IDs: giữ số/chữ/chấm/gạch chéo
    s = re.sub(r"[^a-z0-9]+", " ", s)

    return " ".join(s.split())


def find_column(df, candidates):
    """Find a column ignoring case."""
    mapping = {
        str(c).strip().lower(): c
        for c in df.columns
    }

    for name in candidates:
        if name.lower() in mapping:
            return mapping[name.lower()]

    return None


def load_csv(path):
    if not os.path.exists(path):
        raise FileNotFoundError(f"Không tìm thấy file: {path}")

    return pd.read_csv(
        path,
        dtype=str,
        keep_default_na=False,
        encoding="utf-8-sig",
    )


# ============================================================
# Reference handling
# ============================================================

def prepare_reference_columns(refs):
    id_col = find_column(
        refs,
        [
            "arxiv_id",
            "ref_arxiv_id",
            "reference_arxiv_id",
            "id",
        ],
    )

    title_col = find_column(
        refs,
        [
            "title",
            "entry_title",
            "reference_title",
        ],
    )

    author_col = find_column(
        refs,
        [
            "authors",
            "author",
            "entry_authors",
        ],
    )

    year_col = find_column(
        refs,
        [
            "year",
            "entry_year",
        ],
    )

    if id_col is None:
        raise ValueError(
            "Không tìm thấy cột arXiv ID trong references CSV.\n"
            f"Các cột hiện có: {list(refs.columns)}"
        )

    return {
        "id": id_col,
        "title": title_col,
        "authors": author_col,
        "year": year_col,
    }


def get_reference_by_id(refs, columns, arxiv_id):
    if not arxiv_id:
        return pd.DataFrame()

    id_col = columns["id"]

    mask = (
        refs[id_col]
        .astype(str)
        .str.strip()
        .str.lower()
        == str(arxiv_id).strip().lower()
    )

    return refs[mask]


# ============================================================
# Candidate matching
# ============================================================

def candidate_score(row, ref, columns, suggestion):
    """
    Score a reference against one BibTeX entry.

    This is only an assistance score.
    It does NOT create ground truth automatically.
    """

    score = 0

    title = norm(row.get("entry_title", ""))
    authors = norm(row.get("entry_authors", ""))
    year = str(row.get("entry_year", "")).strip()

    ref_id = str(
        ref.get(columns["id"], "")
    ).strip()

    # --------------------------------------------------------
    # Best suggestion
    # --------------------------------------------------------

    if suggestion and ref_id.lower() == suggestion.lower():
        score += 100

    # --------------------------------------------------------
    # Title
    # --------------------------------------------------------

    if columns["title"]:
        ref_title = norm(ref.get(columns["title"], ""))

        if title and ref_title:

            if title == ref_title:
                score += 60

            else:
                title_words = set(title.split())
                ref_words = set(ref_title.split())

                if title_words:
                    overlap = (
                        len(title_words & ref_words)
                        / len(title_words)
                    )

                    score += int(overlap * 50)

    # --------------------------------------------------------
    # Authors
    # --------------------------------------------------------

    if columns["authors"]:
        ref_authors = norm(
            ref.get(columns["authors"], "")
        )

        if authors and ref_authors:

            author_words = set(authors.split())
            ref_author_words = set(ref_authors.split())

            if author_words:
                overlap = (
                    len(author_words & ref_author_words)
                    / len(author_words)
                )

                score += int(overlap * 30)

    # --------------------------------------------------------
    # Year
    # --------------------------------------------------------

    if columns["year"]:
        ref_year = str(
            ref.get(columns["year"], "")
        ).strip()

        if year and ref_year and year == ref_year:
            score += 10

    return score


def find_candidates(row, refs, columns, top_n=5):

    suggestion = str(
        row.get("best_suggestion", "")
    ).strip()

    results = []

    for _, ref in refs.iterrows():

        score = candidate_score(
            row,
            ref,
            columns,
            suggestion,
        )

        if score <= 0:
            continue

        result = {
            "score": score,
            "arxiv_id": str(
                ref.get(columns["id"], "")
            ).strip(),
        }

        if columns["title"]:
            result["title"] = str(
                ref.get(columns["title"], "")
            )

        else:
            result["title"] = ""

        if columns["authors"]:
            result["authors"] = str(
                ref.get(columns["authors"], "")
            )

        else:
            result["authors"] = ""

        if columns["year"]:
            result["year"] = str(
                ref.get(columns["year"], "")
            )

        else:
            result["year"] = ""

        results.append(result)

    if not results:
        return []

    results.sort(
        key=lambda x: x["score"],
        reverse=True,
    )

    # Không trùng arXiv ID
    seen = set()
    unique = []

    for item in results:

        rid = item["arxiv_id"].lower()

        if rid in seen:
            continue

        seen.add(rid)
        unique.append(item)

        if len(unique) >= top_n:
            break

    return unique


# ============================================================
# Display
# ============================================================

def print_reference(refs, columns, arxiv_id):

    if not arxiv_id:
        return

    found = get_reference_by_id(
        refs,
        columns,
        arxiv_id,
    )

    if found.empty:
        print(
            f"  Không tìm thấy reference ID: {arxiv_id}"
        )
        return

    ref = found.iloc[0]

    print()
    print("REFERENCE ĐƯỢC GỢI Ý")
    print("-" * 70)

    print(
        "arXiv ID :",
        ref.get(columns["id"], ""),
    )

    if columns["title"]:
        print(
            "Title    :",
            ref.get(columns["title"], ""),
        )

    if columns["authors"]:
        print(
            "Authors  :",
            ref.get(columns["authors"], ""),
        )

    if columns["year"]:
        print(
            "Year     :",
            ref.get(columns["year"], ""),
        )


def print_candidates(candidates):

    print()
    print("TOP CANDIDATES")
    print("-" * 100)

    if not candidates:
        print("Không tìm thấy candidate.")
        return

    for i, c in enumerate(candidates, start=1):

        print(f"\n[{i}] SCORE = {c['score']}")

        print(
            "    arXiv ID:",
            c["arxiv_id"],
        )

        print(
            "    Title   :",
            c["title"],
        )

        print(
            "    Authors :",
            c["authors"],
        )

        print(
            "    Year    :",
            c["year"],
        )


# ============================================================
# Save result
# ============================================================

def save_csv(df, path):

    df.to_csv(
        path,
        index=False,
        encoding="utf-8-sig",
    )

    print()
    print("Đã lưu:", path)


# ============================================================
# Interactive labeling
# ============================================================

def run_interactive(df, refs, columns):

    total = len(df)

    for index in range(total):

        row = df.iloc[index]

        print()
        print("=" * 110)
        print(f"CITATION {index + 1}/{total}")
        print("=" * 110)

        print(
            "Citation key     :",
            row.get("citation_key", ""),
        )

        print(
            "Title            :",
            row.get("entry_title", ""),
        )

        print(
            "Authors          :",
            row.get("entry_authors", ""),
        )

        print(
            "Year             :",
            row.get("entry_year", ""),
        )

        print(
            "Venue            :",
            row.get("entry_venue", ""),
        )

        print(
            "Best suggestion  :",
            row.get("best_suggestion", ""),
        )

        print(
            "Current ref      :",
            row.get("ref_arxiv_id", ""),
        )

        # ----------------------------------------------------
        # Show suggestion reference
        # ----------------------------------------------------

        suggestion = str(
            row.get("best_suggestion", "")
        ).strip()

        if suggestion:
            print_reference(
                refs,
                columns,
                suggestion,
            )

        # ----------------------------------------------------
        # Candidates
        # ----------------------------------------------------

        candidates = find_candidates(
            row,
            refs,
            columns,
            top_n=5,
        )

        print_candidates(candidates)

        # ----------------------------------------------------
        # User decision
        # ----------------------------------------------------

        print()
        print("LỰA CHỌN:")
        print("  1-5 = chọn candidate tương ứng")
        print("  m   = nhập arXiv ID thủ công")
        print("  n   = NONE, không có reference phù hợp")
        print("  s   = SKIP, chưa chắc / làm sau")
        print("  q   = QUIT")

        while True:

            choice = input("\nChọn: ").strip()

            if choice.lower() == "q":
                print("Thoát.")
                return df

            if choice.lower() == "s":

                df.at[index, "note"] = (
                    "Skipped for manual verification"
                )

                break

            if choice.lower() == "n":

                df.at[index, "ref_arxiv_id"] = "none"

                df.at[index, "match_detail"] = (
                    "No matching reference found"
                )

                df.at[index, "note"] = ""

                break

            if choice.lower() == "m":

                manual_id = input(
                    "Nhập arXiv ID đúng: "
                ).strip()

                if not manual_id:
                    print("ID không được rỗng.")
                    continue

                # Kiểm tra ID có tồn tại trong references
                found = get_reference_by_id(
                    refs,
                    columns,
                    manual_id,
                )

                if found.empty:
                    print(
                        "CẢNH BÁO: ID này không có trong "
                        "references của publication."
                    )

                    confirm = input(
                        "Vẫn sử dụng ID này? [y/N]: "
                    ).strip().lower()

                    if confirm != "y":
                        continue

                df.at[index, "ref_arxiv_id"] = manual_id

                df.at[index, "match_detail"] = (
                    "Manually matched reference"
                )

                df.at[index, "note"] = ""

                break

            # Chọn candidate 1-5
            if choice.isdigit():

                number = int(choice)

                if 1 <= number <= len(candidates):

                    selected = candidates[number - 1]

                    selected_id = selected["arxiv_id"]

                    print()
                    print(
                        "Bạn chọn:",
                        selected_id,
                    )

                    confirm = input(
                        "Xác nhận matched? [y/N]: "
                    ).strip().lower()

                    if confirm != "y":
                        continue

                    df.at[index, "ref_arxiv_id"] = (
                        selected_id
                    )

                    df.at[index, "match_detail"] = (
                        "Manual verification from candidate"
                    )

                    df.at[index, "note"] = ""

                    break

                print("Số candidate không hợp lệ.")
                continue

            print("Lựa chọn không hợp lệ.")

    return df


# ============================================================
# Main
# ============================================================

def main():

    parser = argparse.ArgumentParser(
        description=(
            "Interactive manual matching tool "
            "for M3 arXiv labeling."
        )
    )

    parser.add_argument(
        "--csv",
        required=True,
        help="Path to paper CSV",
    )

    parser.add_argument(
        "--references",
        required=True,
        help="Path to paper_references.csv",
    )

    args = parser.parse_args()

    print("=" * 80)
    print("M3 MANUAL MATCHING")
    print("=" * 80)

    print("CSV        :", args.csv)
    print("References :", args.references)

    # --------------------------------------------------------
    # Load
    # --------------------------------------------------------

    df = load_csv(args.csv)
    refs = load_csv(args.references)

    print()
    print("Loaded citations :", len(df))
    print("Loaded references:", len(refs))

    # --------------------------------------------------------
    # Check required columns
    # --------------------------------------------------------

    required = [
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

    missing = [
        c
        for c in required
        if c not in df.columns
    ]

    if missing:
        print()
        print("THIẾU CỘT:")
        for c in missing:
            print(" -", c)

        print()
        print(
            "Các cột hiện có:",
            list(df.columns),
        )

        sys.exit(1)

    columns = prepare_reference_columns(refs)

    print()
    print("Reference columns:")
    print("  ID     :", columns["id"])
    print("  Title  :", columns["title"])
    print("  Authors:", columns["authors"])
    print("  Year   :", columns["year"])

    # --------------------------------------------------------
    # Run
    # --------------------------------------------------------

    df = run_interactive(
        df,
        refs,
        columns,
    )

    # --------------------------------------------------------
    # Save
    # --------------------------------------------------------

    save_csv(
        df,
        args.csv,
    )

    print()
    print("=" * 80)
    print("HOÀN TẤT")
    print("=" * 80)


if __name__ == "__main__":
    main()