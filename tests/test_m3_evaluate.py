"""Unit tests for Milestone 3 evaluation module (evaluate.py)."""
import sys
import unittest
from pathlib import Path

import pandas as pd

# Allow importing evaluate.py whether placed in src/ or output/
ROOT = Path(__file__).resolve().parents[1]
for candidate in (ROOT / "src", Path(__file__).resolve().parent):
    if (candidate / "evaluate.py").exists():
        sys.path.insert(0, str(candidate))
        break

from evaluate import compute_query_ranks, evaluate_predictions, extract_errors, mrr_at_5


class TestEvaluateMRR(unittest.TestCase):
    def setUp(self) -> None:
        self.predictions = pd.DataFrame(
            [
                # Rank 1 -> 1.0 (manual, test)
                {
                    "arxiv_id": "2512.19910",
                    "partition": "test",
                    "citation_key": "k1",
                    "groundtruth_arxiv_id": "2001.00001",
                    "cand_1": "2001.00001",
                    "cand_2": "2001.00002",
                    "cand_3": "2001.00003",
                    "cand_4": "2001.00004",
                    "cand_5": "2001.00005",
                },
                # Rank 2 -> 0.5 (manual, test)
                {
                    "arxiv_id": "2512.19910",
                    "partition": "test",
                    "citation_key": "k2",
                    "groundtruth_arxiv_id": "2001.00002",
                    "cand_1": "2001.00001",
                    "cand_2": "2001.00002",
                    "cand_3": "2001.00003",
                    "cand_4": "2001.00004",
                    "cand_5": "2001.00005",
                },
                # Rank 5 -> 0.2 (auto, test)
                {
                    "arxiv_id": "2512.18231",
                    "partition": "test",
                    "citation_key": "k3",
                    "groundtruth_arxiv_id": "2001.00005",
                    "cand_1": "2001.00001",
                    "cand_2": "2001.00002",
                    "cand_3": "2001.00003",
                    "cand_4": "2001.00004",
                    "cand_5": "2001.00005",
                },
                # Outside top 5 -> 0.0 (auto, test)
                {
                    "arxiv_id": "2512.18231",
                    "partition": "test",
                    "citation_key": "k4",
                    "groundtruth_arxiv_id": "2001.99999",
                    "cand_1": "2001.00001",
                    "cand_2": "2001.00002",
                    "cand_3": "2001.00003",
                    "cand_4": "2001.00004",
                    "cand_5": "2001.00005",
                },
                # Unlabeled entry -> MUST be excluded from MRR@5 denominator
                {
                    "arxiv_id": "2512.19910",
                    "partition": "test",
                    "citation_key": "k_unlabeled",
                    "groundtruth_arxiv_id": None,
                    "cand_1": "2001.00001",
                    "cand_2": "2001.00002",
                    "cand_3": None,
                    "cand_4": None,
                    "cand_5": None,
                },
            ]
        )
        self.selection = pd.DataFrame(
            [
                {"arxiv_id": "2512.19910", "kind": "manual"},
                {"arxiv_id": "2512.18231", "kind": "auto"},
            ]
        )

    def test_mrr_at_5_excludes_unlabeled(self) -> None:
        # Expected: (1.0 + 0.5 + 0.2 + 0.0) / 4 = 1.7 / 4 = 0.425
        score = mrr_at_5(self.predictions)
        self.assertAlmostEqual(score, 0.425, places=6)

    def test_breakdown_manual_vs_auto(self) -> None:
        report = evaluate_predictions(self.predictions, selection_df=self.selection)
        self.assertEqual(report["overall"]["n_queries"], 4)
        self.assertAlmostEqual(report["by_partition"]["test"]["mrr_at_5"], 0.425, places=6)
        self.assertAlmostEqual(report["by_partition_and_source"]["test_manual"]["mrr_at_5"], 0.75, places=6)
        self.assertAlmostEqual(report["by_partition_and_source"]["test_auto"]["mrr_at_5"], 0.10, places=6)
        self.assertAlmostEqual(report["by_partition"]["test"]["hits_at_1"], 0.25, places=6)
        self.assertAlmostEqual(report["by_partition"]["test"]["hits_at_5"], 0.75, places=6)

    def test_extract_errors(self) -> None:
        errs = extract_errors(self.predictions, partition="test")
        # k2 (rank 2), k3 (rank 5), k4 (miss) -> 3 error rows
        self.assertEqual(len(errs), 3)
        self.assertEqual(set(errs["citation_key"]), {"k2", "k3", "k4"})
        miss_row = errs[errs["citation_key"] == "k4"].iloc[0]
        self.assertEqual(miss_row["error_type"], "miss_outside_top5")


if __name__ == "__main__":
    unittest.main()