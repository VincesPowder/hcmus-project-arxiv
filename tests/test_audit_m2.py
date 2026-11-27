import tempfile
import unittest
from pathlib import Path
from helpers import DOC, make_paper
from src import pipeline
from src.tools.audit_m2 import audit


class GroupAudit(unittest.TestCase):
    def test_aggregates_real_outputs_and_exposes_missing_ranges(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            dirs = []
            for index, key in enumerate(("2606.00734", "2605.02338")):
                paper = make_paper(root / "corpus", key, key,
                                   {1: {"main.tex": DOC % r"\section{Body}One sentence."}})
                out = root / str(index)
                self.assertEqual(pipeline.process_paper((str(paper), str(out), 10, False))["status"], "done")
                dirs.append(out)
            result = audit(dirs, root / "summary")
            self.assertEqual(result["publications"], 2)
            self.assertEqual(result["version_statuses"], {"parsed": 2})
            self.assertEqual([r["processed"] for r in result["range_coverage"]], [1, 1, 0, 0])
            self.assertEqual(result["totals"]["hierarchy_problems"], 0)
            self.assertTrue((root / "summary/coverage.csv").exists())
            self.assertTrue((root / "summary/publications.csv").exists())
            with self.assertRaisesRegex(ValueError, "Duplicate"):
                audit([dirs[0], dirs[0]], root / "duplicate")
