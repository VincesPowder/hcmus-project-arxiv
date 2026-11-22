"""End-to-end pipeline tests that use the REAL parser and reference modules.

Unlike ``test_pipeline.py`` (which swaps in ``dev_stubs`` to test the plumbing
independently of B/C), these tests run ``pipeline.process_paper`` with the real
``hier_parser`` and ``refs`` so a regression in either module fails here.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from helpers import DOC, make_paper  # noqa: F401  (sets sys.path)
from src import pipeline


class RealPipelineCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.corpus = Path(self._tmp.name) / "corpus"
        self.out = Path(self._tmp.name) / "parsed"

    def tearDown(self):
        self._tmp.cleanup()

    def run_paper(self, paper_dir, timeout=30):
        return pipeline.process_paper((str(paper_dir), str(self.out), timeout, False))

    def load(self, key):
        return json.loads((self.out / f"{key}.json").read_text(encoding="utf-8"))

    def test_real_parser_produces_full_tree_and_real_refs_fill_bibtex(self):
        bib = ("@article{vaswani2017, author={Vaswani, A and Shazeer, N}, "
               "title={Attention is all you need}, year={2017}, doi={10.1/x}}")
        body = (
            "\\section{Intro}\nWe study it.\\cite{vaswani2017}.\n"
            "\\begin{figure}\\caption{A result}\\end{figure}\n"
            "\\begin{equation}x=1\\end{equation}\n"
            "\\bibliography{refs}"
        )
        paper = make_paper(self.corpus, "2605-00001", "2605.00001",
                           {1: {"main.tex": DOC % body, "refs.bib": bib}})
        self.assertEqual(self.run_paper(paper)["status"], "done")
        data = self.load("2605-00001")

        kinds = {row[1] for row in data["elements"]}
        self.assertIn("formula", kinds)
        self.assertIn("figure", kinds)
        self.assertIn("heading", kinds)
        self.assertIn("sentence", kinds)

        self.assertEqual(len(data["bibtex"]), 1)
        entry = data["bibtex"][0]
        self.assertEqual(entry["citation_key"], "vaswani2017")
        self.assertEqual(entry["year"], 2017)
        self.assertEqual(entry["authors"], ["A Vaswani", "N Shazeer"])
        self.assertEqual(data["stats"]["hierarchy_problems"], 0)

    def test_references_deduplicated_across_versions_with_real_modules(self):
        bib1 = "@article{k, title={Attention is all you need}, author={Vaswani, A and Shazeer, N}, year={2017}}"
        bib3 = "@article{k2, title={Attention is all you need}, author={Vaswani, A}, doi={10.1/x}}"
        body = "\\section{Intro}\nIt is hard~\\cite{%s}.\n\\bibliography{refs}"
        paper = make_paper(self.corpus, "2605-00002", "2605.00002", {
            1: {"main.tex": DOC % (body % "k"), "refs.bib": bib1},
            3: {"main.tex": DOC % (body % "k2"), "refs.bib": bib3},
        })
        self.assertEqual(self.run_paper(paper)["status"], "done")
        data = self.load("2605-00002")
        self.assertEqual(len(data["bibtex"]), 1)
        self.assertEqual(data["bibtex"][0]["year"], 2017)
        self.assertEqual(data["bibtex"][0]["doi"], "10.1/x")
        self.assertEqual(data["cite_renames"], 1)
        hard = [row for row in data["elements"] if "hard" in row[2]]
        self.assertEqual(len(hard), 1)                       # cite-renamed text deduplicated
        self.assertIn("k", hard[0][2])
        self.assertNotIn("k2", hard[0][2])


if __name__ == "__main__":
    unittest.main()

