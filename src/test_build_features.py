"""Tests of src/build_features.py on tiny hand-made CSV tables. No real data, no network, no Drive."""
import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from build_features import read_selection, run  # noqa: E402
from features import feature_columns  # noqa: E402

SEL_FIELDS = ["arxiv_id", "owner", "kind", "partition", "slot", "tier", "n_bib", "n_refs", "n_any_hit", "ratio"]


def write_csv(path, fields, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


class BuildFeatures(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        sel = [
            dict(arxiv_id="2602.00001", owner="1", kind="manual", partition="test"),
            dict(arxiv_id="2602.00002", owner="1", kind="auto", partition="train"),
            dict(arxiv_id="2602.00003", owner="2", kind="auto", partition="valid"),     # no references
            dict(arxiv_id="2602.00004", owner="1", kind="manual_reserve", partition="reserve"),
            dict(arxiv_id="2602.00005", owner="1", kind="manual_dropped", partition="dropped"),
        ]
        write_csv(self.tmp / "selection.csv", SEL_FIELDS, sel)
        bib_fields = ["arxiv_id", "citation_key", "title", "authors", "year", "doi", "journal", "url"]
        bib = []
        for paper in ("2602.00001", "2602.00002", "2602.00003", "2602.00004", "2602.00005", "2602.99999"):
            bib.append(dict(arxiv_id=paper, citation_key="attn", title="Attention is all you need",
                            authors=json.dumps(["Ashish Vaswani"]), year="2017",
                            journal="arXiv preprint arXiv:1706.03762"))
            bib.append(dict(arxiv_id=paper, citation_key="bert", title="BERT pre-training of deep transformers",
                            authors=json.dumps(["Jacob Devlin"]), year="2019"))
        write_csv(self.tmp / "bibtex.csv", bib_fields, bib)
        ref_fields = ["arxiv_id", "ref_arxiv_id", "ref_title", "ref_authors", "ref_submission_date", "ref_s2_id"]
        refs = []
        for paper in ("2602.00001", "2602.00002", "2602.00004", "2602.99999"):
            refs.append(dict(arxiv_id=paper, ref_arxiv_id="1706.03762", ref_title="Attention Is All You Need",
                             ref_authors=json.dumps(["Ashish Vaswani", "Noam Shazeer"]),
                             ref_submission_date="2017-06-12"))
            refs.append(dict(arxiv_id=paper, ref_arxiv_id="1810.04805v2", ref_title="BERT: Pre-training of Deep Transformers",
                             ref_authors=json.dumps(["Jacob Devlin"]), ref_submission_date="2018-10-11"))
        write_csv(self.tmp / "references.csv", ref_fields, refs)

    def go(self, **kwargs):
        out = self.tmp / "out"
        summary = run(str(self.tmp / "selection.csv"), [str(self.tmp / "bibtex.csv")],
                      [str(self.tmp / "references.csv")], str(out), fmt="csv", **kwargs)
        frames = [pd.read_csv(p, dtype={"arxiv_id": str, "citation_key": str, "ref_arxiv_id": str}) for p in sorted((out / "features").glob("features-*.csv"))]
        return summary, (pd.concat(frames, ignore_index=True) if frames else pd.DataFrame())

    def test_selection_keeps_only_active_papers(self):
        rows = read_selection(self.tmp / "selection.csv")
        self.assertEqual({r["arxiv_id"] for r in rows}, {"2602.00001", "2602.00002", "2602.00003"})
        self.assertEqual({r["arxiv_id"] for r in read_selection(self.tmp / "selection.csv", owner="2")},
                         {"2602.00003"})

    def test_pairs_and_features_only_for_selected_papers(self):
        summary, feats = self.go()
        self.assertEqual(set(feats["arxiv_id"]), {"2602.00001", "2602.00002"})      # reserve/dropped/outside skipped
        self.assertEqual(len(feats), 2 * 2 * 2)                                      # 2 papers x (2 entries x 2 refs)
        self.assertEqual(list(feats.columns), ["arxiv_id", "citation_key", "ref_arxiv_id"] + feature_columns())
        self.assertEqual(summary["skipped_no_references"], ["2602.00003"])
        self.assertEqual(summary["n_papers_with_pairs"], 2)
        self.assertEqual(summary["papers_with_pairs_by_kind_partition"], {"auto/train": 1, "manual/test": 1})

    def test_reference_version_suffix_is_canonical_and_id_match_works(self):
        _, feats = self.go()
        self.assertIn("1810.04805", set(feats["ref_arxiv_id"]))
        row = feats[(feats.arxiv_id == "2602.00001") & (feats.citation_key == "attn")
                    & (feats.ref_arxiv_id == "1706.03762")].iloc[0]
        self.assertEqual(row["id_match"], 1.0)
        self.assertEqual(row["title_exact"], 1.0)

    def test_no_label_leaks_into_features(self):
        _, feats = self.go()
        for name in ("label", "groundtruth_arxiv_id", "partition", "best_suggestion"):
            self.assertNotIn(name, feats.columns)

    def test_chunking_does_not_change_the_result(self):
        _, one = self.go(chunk_papers=100)
        _, many = self.go(chunk_papers=1)
        key = ["arxiv_id", "citation_key", "ref_arxiv_id"]
        pd.testing.assert_frame_equal(one.sort_values(key).reset_index(drop=True),
                                      many.sort_values(key).reset_index(drop=True))

    def test_summary_file_is_written(self):
        self.go()
        data = json.loads((self.tmp / "out" / "features_summary.json").read_text(encoding="utf-8"))
        self.assertEqual(data["n_selected"], 3)
        self.assertEqual(len(data["selection_sha256"]), 64)


if __name__ == "__main__":
    unittest.main()
