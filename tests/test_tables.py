import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from helpers import DOC, make_paper
from src import pipeline
from src.tools.export_verified_m2 import export


@unittest.skipUnless(importlib.util.find_spec("pyarrow") and importlib.util.find_spec("pandas"),
                     "pyarrow/pandas required for Parquet validation")
class VerifiedTables(unittest.TestCase):
    def test_real_json_exports_matching_rows_and_typed_authors(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            paper = make_paper(root / "corpus", "2606-00734", "2606.00734", {
                1: {"main.tex": DOC % r"\section{Body}Text \cite{k}.\bibliography{refs}",
                    "refs.bib": "@article{k,title={A title},author={Doe, Jane},year={2020}}"}})
            result = pipeline.process_paper((str(paper), str(root / "parsed"), 10, False))
            self.assertEqual(result["status"], "done")
            data = json.loads((root / "parsed/2606-00734.json").read_text())
            check = export(root / "parsed", root / "export")
            self.assertEqual(check["table_rows"], {k: len(data[k]) for k in ("elements", "hierarchy", "bibtex")})
            with self.assertRaisesRegex(ValueError, "fresh"):
                export(root / "parsed", root / "export")
