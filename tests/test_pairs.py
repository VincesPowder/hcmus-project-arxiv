"""Tests of src/pairs.py on tiny hand-made tables. No real data, no network, no Drive."""
import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from pairs import PAIR_COLUMNS, PAIR_KEYS, build_pairs, iter_pairs  # noqa: E402

P1, P2 = "2602.12886", "2602.11395"


def entries(*rows):
    """rows: (arxiv_id, citation_key, title, authors, year)"""
    return pd.DataFrame([{"arxiv_id": a, "citation_key": k, "title": t, "authors": au, "year": y,
                          "doi": None, "journal": None, "url": None} for a, k, t, au, y in rows])


def refs(*rows):
    """rows: (arxiv_id, ref_arxiv_id, ref_title, ref_authors, ref_submission_date)"""
    return pd.DataFrame([{"arxiv_id": a, "ref_arxiv_id": i, "ref_title": t, "ref_authors": au,
                          "ref_submission_date": d} for a, i, t, au, d in rows])


def two_papers():
    e = entries((P1, "a", "Title A", ["Ann Lee"], 2020), (P1, "b", "Title B", ["Bob Ray"], 2019),
                (P2, "c", "Title C", ["Cy Poe"], 2018),
                ("2602.00001", "z", "Entry without references", [], 2020))       # no reference at all
    r = refs((P1, "1706.03762", "Ref 1", ["A"], "2017-06-12"), (P1, "1810.04805", "Ref 2", ["B"], "2018-10-11"),
             (P1, "cond-mat/0609392", "Ref 3", ["C"], "2006-09-01"),
             (P2, "2001.00001", "Ref 4", ["D"], "2020-01-02"), (P2, "hep-th/9901001", "Ref 5", ["E"], "1999-01-04"),
             ("2602.00002", "1706.03762", "Ref without entries", ["F"], "2017-06-12"))  # no entry at all
    return e, r


class Pairs(unittest.TestCase):
    def test_full_matrix_inside_each_publication(self):
        e, r = two_papers()
        out = build_pairs(e, r)
        self.assertEqual(len(out), 2 * 3 + 1 * 2)                       # m x n per publication, nothing else
        self.assertEqual(list(out.columns), PAIR_COLUMNS)
        self.assertEqual(set(out.loc[out.arxiv_id == P1, "ref_arxiv_id"]),
                         {"1706.03762", "1810.04805", "cond-mat/0609392"})
        self.assertEqual(set(out.loc[out.arxiv_id == P2, "ref_arxiv_id"]), {"2001.00001", "hep-th/9901001"})
        self.assertEqual(set(out.arxiv_id), {P1, P2})                   # papers with only one side give no pair

    def test_no_pair_crosses_publications(self):
        e, r = two_papers()
        out = build_pairs(e, r)
        allowed = {(a, i) for a, i in zip(r.arxiv_id, r.ref_arxiv_id)}
        self.assertTrue(all((a, i) in allowed for a, i in zip(out.arxiv_id, out.ref_arxiv_id)))
        # the same reference id in another publication must not leak into this one
        self.assertNotIn("1706.03762", set(out.loc[out.arxiv_id == P2, "ref_arxiv_id"]))
        self.assertNotIn("hep-th/9901001", set(out.loc[out.arxiv_id == P1, "ref_arxiv_id"]))

    def test_ids_are_canonical_new_and_old(self):
        e = entries((P1, "a", "T", ["X Y"], 2020))
        r = refs((P1, "arXiv:2511.19116v3", "t1", ["A"], "2025-11-19"), (P1, "2511.19116", "dup", ["A"], "2025-11-19"),
                 (P1, "hep-th/9901001v2", "t2", ["B"], "1999-01-04"), (P1, "math.AG/0601001", "t3", ["C"], "2006-01-02"),
                 (P1, "cond-mat/0609392", "t4", ["D"], "2006-09-01"),
                 (P1, "not an id", "bad", ["E"], None), (P1, None, "none", ["F"], None))
        out = build_pairs(e, r)
        self.assertEqual(sorted(out.ref_arxiv_id),
                         ["2511.19116", "cond-mat/0609392", "hep-th/9901001", "math/0601001"])

    def test_duplicate_entries_and_references_collapse(self):
        e = entries((P1, "a", "first", ["X Y"], 2020), (P1, "a", "second copy", ["X Y"], 2020))
        r = refs((P1, "1706.03762", "r", ["A"], "2017-06-12"), (P1, "1706.03762v2", "r again", ["A"], "2017-06-12"))
        out = build_pairs(e, r)
        self.assertEqual(len(out), 1)
        self.assertEqual(out.loc[0, "entry_title"], "first")            # first occurrence is kept

    def test_reference_after_publication_is_flagged_or_dropped(self):
        e = entries((P1, "a", "T", ["X Y"], 2020))                      # publication year 2026
        r = refs((P1, "2701.00001", "future by date", ["A"], "2027-01-10"),
                 (P1, "2501.00002", "past", ["A"], "2025-01-02"),
                 (P1, "2602.00003", "same year, date missing: id year", ["A"], None),
                 (P1, "hep-th/9901001", "old id, date missing: id year", ["A"], None))
        out = build_pairs(e, r).set_index("ref_arxiv_id")
        self.assertTrue(bool(out.loc["2701.00001", "ref_after_pub"]))
        self.assertFalse(bool(out.loc["2501.00002", "ref_after_pub"]))
        self.assertFalse(bool(out.loc["2602.00003", "ref_after_pub"]))
        self.assertEqual(int(out.loc["hep-th/9901001", "ref_year"]), 1999)
        self.assertEqual(int(out.loc["2701.00001", "pub_year"]), 2026)
        kept = build_pairs(e, r, drop_future=True)
        self.assertNotIn("2701.00001", set(kept.ref_arxiv_id))
        self.assertEqual(len(kept), 3)

    def test_suggestions_and_labels_are_never_carried(self):
        e, r = two_papers()
        plain = build_pairs(e, r)
        noisy = e.assign(best_suggestion="1810.04805", groundtruth_arxiv_id="1706.03762")
        out = build_pairs(noisy, r)
        self.assertNotIn("best_suggestion", out.columns)
        self.assertNotIn("groundtruth_arxiv_id", out.columns)
        pd.testing.assert_frame_equal(plain, out)

    def test_deterministic_whatever_the_input_order(self):
        e, r = two_papers()
        a = build_pairs(e, r)
        b = build_pairs(e.sample(frac=1, random_state=0), r.sample(frac=1, random_state=1))
        pd.testing.assert_frame_equal(a, b)
        keys = a[PAIR_KEYS].values.tolist()
        self.assertEqual(keys, sorted(keys))

    def test_papers_filter_and_streaming(self):
        e, r = two_papers()
        only = build_pairs(e, r, papers=[P2])
        self.assertEqual(set(only.arxiv_id), {P2})
        parts = list(iter_pairs(e, r))
        self.assertEqual([set(p.arxiv_id) for p in parts], [{P2}, {P1}])   # one publication per chunk, sorted

    def test_missing_columns_and_empty_result(self):
        e, r = two_papers()
        with self.assertRaises(ValueError):
            build_pairs(e.drop(columns="citation_key"), r)
        with self.assertRaises(ValueError):
            build_pairs(e, r.drop(columns="ref_arxiv_id"))
        out = build_pairs(e, r.iloc[0:0])
        self.assertEqual(len(out), 0)
        self.assertEqual(list(out.columns), PAIR_COLUMNS)
        minimal = build_pairs(e[["arxiv_id", "citation_key"]], r[["arxiv_id", "ref_arxiv_id"]])
        self.assertEqual(len(minimal), 8)                               # optional columns may be absent


if __name__ == "__main__":
    unittest.main()
