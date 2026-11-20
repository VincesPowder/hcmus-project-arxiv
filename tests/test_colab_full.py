"""Execute every notebook code cell on isolated local M1 fixtures.

No Drive, network, crawl or full-corpus parsing is used by these tests.
"""
import contextlib
import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from helpers import DOC, make_paper
from src.tools.run_m2_full import inventory, run_full
from src.tools.export_verified_m2 import export

READY = all(importlib.util.find_spec(m) for m in ("pandas", "pyarrow", "psutil", "matplotlib"))
REPO = Path(__file__).resolve().parents[1]


@unittest.skipUnless(READY, "Full M2 notebook dependencies required")
class FullColabNotebook(unittest.TestCase):
    def fixture(self, root):
        canonical, legacy = root / "canonical", root / "legacy"
        body = (r"\part*{Main}\section{Introduction}We study \cite{%s}."
                r"\begin{itemize}\item A condition.\end{itemize}"
                r"\begin{equation}x=1\end{equation}"
                r"\begin{table}\caption{Results}\end{table}"
                r"\section*{Acknowledgements}Thanks.\appendix\section*{Proof}Proof text."
                r"\bibliography{refs}")
        make_paper(canonical, "2606-00734", "2606.00734", {
            1: {"main.tex": DOC % (body % "k1"),
                "refs.bib": "@article{k1,title={A useful paper},author={Doe, Jane},year={2020}}"},
            2: {"main.tex": DOC % (body % "k2"),
                "refs.bib": "@article{k2,title={A useful paper},author={Doe, Jane and Roe, John},doi={10.1000/x}}"},
            3: "no_tex",
        })
        # A genuine same-version key conflict must not stop export of good papers.
        make_paper(canonical, "2606-00735", "2606.00735", {
            1: {"main.tex": DOC % (
                r"\section{Body}Text.\begin{thebibliography}{9}"
                r"\bibitem{k}Jane Doe. First work. 2020."
                r"\bibitem{k}John Roe. Different work. 2021.\end{thebibliography}")}})
        paper = legacy / "2605.02338"
        version = paper / "sources/v1"
        version.mkdir(parents=True)
        (paper / "paper_metadata.json").write_text(json.dumps({
            "arxiv_id": "2605.02338", "versions": [{"version": 1}]}))
        (version / "manifest.json").write_text('{"status":"extracted"}')
        (version / "main.tex").write_text(DOC % r"\section{Body}No citations here.")
        return [canonical, legacy]

    def test_run_all_cells_outputs_parsed_parquet_audit_plots_and_zip(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            inputs = self.fixture(root)
            notebook = json.loads((REPO / "src/milestone2.ipynb").read_text(encoding="utf-8"))
            namespace = {"CONFIG_OVERRIDES": {
                "REPO_DIR": str(REPO), "CORPUS_DIRS": [str(p) for p in inputs],
                "RUN_DIR": str(root / "run"), "USE_DRIVE": False, "WORKERS": 1,
                "SYNC_REPOSITORY": False, "INSTALL_DEPENDENCIES": False,
                "MAKE_PLOTS": True, "DOWNLOAD_REPORTS": False,
            }}
            old_cwd = Path.cwd()
            old_mpl = os.environ.get("MPLCONFIGDIR")
            old_backend = os.environ.get("MPLBACKEND")
            os.environ["MPLCONFIGDIR"] = str(root / "mpl")
            os.environ["MPLBACKEND"] = "Agg"
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    for i, cell in enumerate(notebook["cells"]):
                        if cell["cell_type"] == "code":
                            exec(compile("".join(cell["source"]), f"milestone2.ipynb:cell-{i}", "exec"), namespace)
            finally:
                os.chdir(old_cwd)
                for key, previous in (("MPLCONFIGDIR", old_mpl), ("MPLBACKEND", old_backend)):
                    if previous is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = previous
            result = namespace["RESULT"]
            self.assertEqual(result["status"], "partial")
            self.assertEqual(result["summary"]["selected_publications"], 3)
            self.assertEqual(result["summary"]["publications"], 2)
            self.assertEqual(result["summary"]["version_statuses"], {"parsed": 3, "empty": 1})
            self.assertEqual(result["summary"]["versions_without_paper_output"], 1)
            self.assertEqual(result["export"]["table_rows"]["bibtex"], 1)
            self.assertTrue((root / "run/reports.zip").is_file())
            self.assertTrue((root / "run/figures/version_status.png").is_file())
            self.assertTrue((root / "run/figures/dedup_counts.png").is_file())
            self.assertTrue((root / "run/audit/reference_review.jsonl").is_file())
            self.assertTrue((root / "run/work/parse_metrics.json").is_file())
            import pyarrow.dataset as ds
            for name in ("elements", "hierarchy", "bibtex"):
                folder = Path(result["export"]["directory"]) / "tables" / name
                self.assertEqual(ds.dataset(folder, format="parquet").count_rows(), result["export"]["table_rows"][name])
            data = json.loads((root / "run/parsed/2606-00734.json").read_text())
            self.assertEqual(data["bibtex"][0]["authors"], ["Jane Doe", "John Roe"])
            self.assertEqual(data["stats"]["hierarchy_problems"], 0)
            # Resume reruns the quarantined paper, skips valid JSON, exports fresh shards.
            rerun = run_full([str(p) for p in inputs], root / "run", workers=1)
            self.assertEqual(rerun["metrics"]["status_counts"], {"skipped": 2, "failed": 1})
            self.assertNotEqual(rerun["export"]["directory"], result["export"]["directory"])
            with self.assertRaisesRegex(ValueError, "changed"):
                run_full([str(p) for p in inputs], root / "run", workers=1, timeout=30)

    def test_empty_only_corpus_still_has_three_typed_parquet_datasets(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            make_paper(root / "corpus", "2606-00734", "2606.00734", {1: "no_tex"})
            result = run_full([str(root / "corpus")], root / "run", workers=1)
            self.assertEqual(result["status"], "complete_for_selected_input")
            self.assertEqual(result["export"]["table_rows"], {"elements": 0, "hierarchy": 0, "bibtex": 0})
            import pyarrow.dataset as ds
            for name in ("elements", "hierarchy", "bibtex"):
                dataset = ds.dataset(Path(result["export"]["directory"]) / "tables" / name, format="parquet")
                self.assertEqual(dataset.count_rows(), 0)

    def test_duplicate_inputs_and_nested_output_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            make_paper(root / "corpus", "2606-00734", "2606.00734", {1: "no_tex"})
            with self.assertRaisesRegex(ValueError, "Duplicate"):
                inventory([root / "corpus", root / "corpus"])
            with self.assertRaisesRegex(ValueError, "separate"):
                run_full([str(root / "corpus")], root / "corpus/run", workers=1)

    def test_nonempty_output_without_identity_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            make_paper(root / "corpus", "2606-00734", "2606.00734", {1: "no_tex"})
            out = root / "run"
            out.mkdir()
            (out / "keep.txt").write_text("existing user data")
            with self.assertRaisesRegex(ValueError, "no identity"):
                run_full([str(root / "corpus")], out, workers=1)
            self.assertEqual((out / "keep.txt").read_text(), "existing user data")


if __name__ == "__main__":
    unittest.main()
