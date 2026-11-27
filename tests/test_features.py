"""Tests of src/features.py on tiny hand-made tables. No real data, no network, no Drive."""
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from features import BASE_FEATURES, RELATIVE_FEATURES, build_features, feature_columns  # noqa: E402
from pairs import PAIR_KEYS, build_pairs  # noqa: E402

PAPER = "2602.12886"                                                    # publication year 2026


def entry(key, title, authors, year, journal=None, url=None, doi=None):
    return {"arxiv_id": PAPER, "citation_key": key, "title": title, "authors": authors, "year": year,
            "doi": doi, "journal": journal, "url": url}


def ref(ref_id, title, authors, date):
    return {"arxiv_id": PAPER, "ref_arxiv_id": ref_id, "ref_title": title, "ref_authors": authors,
            "ref_submission_date": date}


def features_of(entry_rows, ref_rows, **kwargs):
    pairs = build_pairs(pd.DataFrame(entry_rows), pd.DataFrame(ref_rows))
    return build_features(pairs, **kwargs).set_index(["citation_key", "ref_arxiv_id"])


class Features(unittest.TestCase):
    def setUp(self):
        self.f = features_of(
            [entry("attn", "Attention is all you need", ["Ashish Vaswani", "Noam Shazeer"], 2017,
                   journal="arXiv preprint arXiv:1706.03762"),
             entry("bert", "BERT: Pre-training of Deep Bidirectional Transformers", ["Jacob Devlin"], 2019,
                   journal="NAACL", url="https://arxiv.org/abs/1810.04805")],
            [ref("1706.03762", "Attention Is All You Need", ["Ashish Vaswani", "Noam Shazeer", "Niki Parmar"],
                 "2017-06-12"),
             ref("1810.04805", "BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding",
                 ["Jacob Devlin", "Ming-Wei Chang"], "2018-10-11"),
             ref("hep-th/9901001", "Some old paper", ["X Y"], "1999-01-04")])

    def test_right_pair_has_all_the_evidence(self):
        row = self.f.loc[("attn", "1706.03762")]
        for name in ("title_exact", "title_jaccard", "title_char3_jaccard", "first_author_match", "author_overlap",
                     "year_within_1", "id_match", "entry_has_arxiv_id"):
            self.assertEqual(row[name], 1.0, name)
        self.assertEqual(row["year_abs_diff"], 0.0)
        self.assertEqual(row["id_conflict"], 0.0)
        self.assertEqual(row["ref_after_pub"], 0.0)

    def test_wrong_reference_of_an_entry_citing_an_id(self):
        for wrong in ("1810.04805", "hep-th/9901001"):
            row = self.f.loc[("attn", wrong)]
            self.assertEqual(row["id_match"], 0.0)
            self.assertEqual(row["id_conflict"], 1.0)                   # the entry cites another arXiv id
            self.assertEqual(row["title_exact"], 0.0)

    def test_partial_title_is_between_zero_and_one(self):
        row = self.f.loc[("bert", "1810.04805")]
        self.assertEqual(row["title_exact"], 0.0)
        self.assertTrue(0.5 < row["title_jaccard"] < 1.0)
        self.assertEqual(row["title_overlap_entry"], 1.0)               # every entry word is in the reference title
        self.assertEqual(row["id_match"], 1.0)                          # arXiv id found in the url
        self.assertEqual(row["year_abs_diff"], 1.0)
        self.assertEqual(row["year_within_1"], 1.0)

    def test_old_style_arxiv_ids(self):
        f = features_of(
            [entry("cm", "Some condensed matter paper", ["Ann Lee"], 2006, journal="arXiv:cond-mat/0609392"),
             entry("ht", "Some hep paper", ["Bob Ray"], 1999, url="https://arxiv.org/abs/hep-th/9901001v2")],
            [ref("cond-mat/0609392", "Some condensed matter paper", ["Ann Lee"], "2006-09-01"),
             ref("hep-th/9901001", "Some hep paper", ["Bob Ray"], "1999-01-04")])
        self.assertEqual(f.loc[("cm", "cond-mat/0609392"), "id_match"], 1.0)
        self.assertEqual(f.loc[("ht", "hep-th/9901001"), "id_match"], 1.0)
        self.assertEqual(f.loc[("cm", "hep-th/9901001"), "id_match"], 0.0)
        self.assertEqual(f.loc[("cm", "hep-th/9901001"), "id_conflict"], 1.0)

    def test_same_topic_or_one_author_is_not_a_match(self):
        f = features_of(
            [entry("k", "Deep learning for image recognition", ["Alice Smith"], 2020)],
            [ref("2005.00001", "Deep learning for speech synthesis", ["Alice Smith", "Bob Lee"], "2020-05-01")])
        row = f.loc[("k", "2005.00001")]
        self.assertEqual(row["first_author_match"], 1.0)                # evidence exists...
        self.assertEqual(row["year_within_1"], 1.0)
        self.assertEqual(row["title_exact"], 0.0)                       # ...but it is not enough: titles differ
        self.assertLess(row["title_jaccard"], 0.5)
        self.assertEqual(row["id_match"], 0.0)
        for forbidden in ("label", "match", "is_match", "prediction", "score", "best_suggestion"):
            self.assertNotIn(forbidden, f.columns)                      # features are evidence, never a decision

    def test_missing_fields_do_not_crash(self):
        f = features_of(
            [entry("empty", None, None, None),
             entry("arr", "Some Title", np.array(["John Smith", "Mary Jones"]), "2020.0"),
             entry("txt", "Some Title", "John Smith and Mary Jones", float("nan"))],
            [ref("2501.00001", "Some Title", ["J. Smith"], None)])
        empty = f.loc[("empty", "2501.00001")]
        self.assertEqual(empty["entry_title_missing"], 1.0)
        self.assertEqual(empty["entry_authors_missing"], 1.0)
        self.assertEqual(empty["entry_year_missing"], 1.0)
        self.assertEqual(empty["title_jaccard"], 0.0)
        self.assertTrue(np.isnan(empty["year_abs_diff"]))
        self.assertEqual(empty["ref_year_missing"], 0.0)                # the id 2501.00001 gives the year 2025
        for key in ("arr", "txt"):                                      # ndarray / "A and B" string authors
            self.assertEqual(f.loc[(key, "2501.00001"), "first_author_match"], 1.0, key)
        self.assertEqual(f.loc[("arr", "2501.00001"), "author_overlap"], 1.0)

    def test_reference_later_than_publication_is_flagged(self):
        f = features_of([entry("k", "T", ["A B"], 2020)],
                        [ref("2701.00001", "Future", ["A B"], "2027-01-10"), ref("2601.00001", "Past", ["A B"], "2026-01-10")])
        self.assertEqual(f.loc[("k", "2701.00001"), "ref_after_pub"], 1.0)
        self.assertEqual(f.loc[("k", "2601.00001"), "ref_after_pub"], 0.0)

    def test_accents_and_latex_are_cleaned(self):
        f = features_of([entry("k", r'Schr{\"o}dinger operators with singular potentials', ["A B"], 2020)],
                        [ref("2005.00001", "Schrödinger Operators with Singular Potentials", ["A B"], "2020-05-01")])
        self.assertEqual(f.loc[("k", "2005.00001"), "title_exact"], 1.0)

    def test_relative_features_inside_the_group(self):
        f = features_of(
            [entry("e1", "Attention is all you need", ["A One"], 2017),
             entry("e2", "Convolutional networks for image recognition", ["B Two"], 2016)],
            [ref("1706.03762", "Attention Is All You Need", ["A One"], "2017-06-12"),
             ref("1604.00001", "Convolutional Networks for Image Recognition", ["B Two"], "2016-04-01")])
        self.assertEqual(f.loc[("e1", "1706.03762"), "title_jaccard_rank"], 1.0)
        self.assertEqual(f.loc[("e1", "1604.00001"), "title_jaccard_rank"], 2.0)
        self.assertEqual(f.loc[("e1", "1706.03762"), "title_jaccard_gap_best"], 0.0)
        self.assertEqual(f.loc[("e1", "1604.00001"), "title_jaccard_gap_best"], -1.0)
        self.assertEqual(f.loc[("e1", "1706.03762"), "title_jaccard_rank_by_ref"], 1.0)   # e1 beats e2 for this ref
        self.assertEqual(f.loc[("e2", "1706.03762"), "title_jaccard_rank_by_ref"], 2.0)
        self.assertEqual(f.loc[("e2", "1706.03762"), "title_jaccard_gap_best_by_ref"], -1.0)
        self.assertTrue((f["n_entries_in_paper"] == 2).all() and (f["n_refs_in_paper"] == 2).all())

    def test_columns_order_and_nan_policy(self):
        pairs = build_pairs(pd.DataFrame([entry("k", "T", ["A B"], None)]),
                            pd.DataFrame([ref("2501.00001", "T", ["A B"], None)]))
        out = build_features(pairs)
        self.assertEqual(list(out.columns), PAIR_KEYS + BASE_FEATURES + RELATIVE_FEATURES)
        self.assertEqual(list(build_features(pairs, relative=False).columns), PAIR_KEYS + feature_columns(False))
        self.assertEqual(int(out.drop(columns="year_abs_diff").isna().sum().sum()), 0)   # only year_abs_diff may be NaN
        empty = build_features(pairs.iloc[0:0])
        self.assertEqual(list(empty.columns), PAIR_KEYS + feature_columns())
        self.assertEqual(len(empty), 0)

    def test_suggestion_and_ground_truth_columns_are_ignored(self):
        e = [entry("attn", "Attention is all you need", ["Ashish Vaswani"], 2017)]
        r = [ref("1706.03762", "Attention Is All You Need", ["Ashish Vaswani"], "2017-06-12"),
             ref("1810.04805", "BERT", ["Jacob Devlin"], "2018-10-11")]
        pairs = build_pairs(pd.DataFrame(e), pd.DataFrame(r))
        poisoned = pairs.assign(best_suggestion="1810.04805", groundtruth_arxiv_id="1810.04805", label=1)
        a, b = build_features(pairs), build_features(poisoned)
        pd.testing.assert_frame_equal(a, b)
        self.assertNotIn("best_suggestion", b.columns)

    def test_deterministic(self):
        e = [entry("a", "Title one", ["A One"], 2020), entry("b", "Title two", ["B Two"], 2019)]
        r = [ref("2001.00001", "Title one", ["A One"], "2020-01-02"), ref("1901.00002", "Other", ["C Three"], "2019-01-03")]
        pairs = build_pairs(pd.DataFrame(e), pd.DataFrame(r))
        pd.testing.assert_frame_equal(build_features(pairs), build_features(pairs))


if __name__ == "__main__":
    unittest.main()
