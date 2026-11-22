"""Execute every notebook code cell on isolated local M1 fixtures.

No Drive, network, crawl or full-corpus parsing is used by these tests.
"""
import contextlib
import ast
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from helpers import DOC, make_paper
from src.tools.run_m2_full import inventory, run_full
from src.tools.export_verified_m2 import export

READY = all(importlib.util.find_spec(m) for m in ("pandas", "pyarrow", "psutil", "matplotlib"))
REPO = Path(__file__).resolve().parents[1]


class NotebookCommand(unittest.TestCase):
    def test_child_logs_and_nonzero_exit_are_visible_in_notebook(self):
        notebook = json.loads((REPO / "src/milestone2.ipynb").read_text())
        tree = ast.parse("".join(notebook["cells"][3]["source"]))
        definition = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "command")
        namespace = {"subprocess": subprocess}
        exec(compile(ast.Module(body=[definition], type_ignores=[]), "notebook-command", "exec"), namespace)
        command = namespace["command"]
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            with self.assertRaises(subprocess.CalledProcessError) as error:
                command([sys.executable, "-c", "import sys; print('progress'); print('failure',file=sys.stderr); sys.exit(7)"])
        self.assertEqual(error.exception.returncode, 7)
        self.assertIn("progress", output.getvalue())
        self.assertIn("failure", output.getvalue())
        captured = command([sys.executable, "-c", "print('json-result')"], capture_output=True, text=True)
        self.assertEqual(captured.stdout.strip(), "json-result")


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
        # Preserve the conflicted paper too, without choosing one reference.
        make_paper(canonical, "2606-01068", "2606.01068", {
            1: {"main.tex": DOC % (
                r"\section{Body}Text \cite{k}.\begin{thebibliography}{9}"
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
            self.assertEqual(result["summary"]["publications"], 3)
            self.assertEqual(result["summary"]["version_statuses"], {"parsed": 4, "empty": 1})
            self.assertEqual(result["summary"]["versions_without_paper_output"], 0)
            self.assertEqual(result["summary"]["quality_flags"]["ambiguous_citation_keys"], 1)
            self.assertEqual(result["summary"]["unresolved_citation_occurrences"], 1)
            self.assertEqual(result["export"]["table_rows"]["bibtex"], 3)
            conflicted = json.loads((root / "run/parsed/2606-01068.json").read_text())
            self.assertEqual(len(conflicted["reference_conflicts"]), 1)
            self.assertNotIn("k", {e["citation_key"] for e in conflicted["bibtex"]})
            self.assertEqual(len((root / "run/audit/reference_conflicts.jsonl").read_text().splitlines()), 1)
            self.assertEqual(json.loads((root / "run/paper_failures.json").read_text()), [])
            verification = json.loads((root / "run/pipeline_verification.json").read_text())
            self.assertEqual(verification["checks"], "passed")
            self.assertEqual(verification["run_status"], "partial")
            self.assertEqual(verification["version_records"], 5)
            self.assertEqual(verification["previously_failing_papers_checked"], 1)
            self.assertEqual(verification["recovered_papers"][0]["reference_conflicts"], 1)
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
            # An unchanged source conflict needs review, not repeated identical parsing.
            rerun = run_full([str(p) for p in inputs], root / "run", workers=1)
            self.assertEqual(rerun["metrics"]["status_counts"], {"skipped": 3})
            self.assertEqual(rerun["status"], "partial")
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

    def test_explicit_io_workers_are_not_capped_by_cpu_count(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            make_paper(root / "corpus", "2606-00734", "2606.00734", {1: "no_tex"})
            with patch("src.tools.run_m2_full.os.cpu_count", return_value=1):
                result = run_full([str(root / "corpus")], root / "run", workers=2)
            self.assertEqual(result["metrics"]["workers"], 2)
            self.assertEqual(result["metrics"]["cpu_count"], 1)
            self.assertEqual(result["summary"]["publications"], 1)

    def test_duplicate_inputs_and_nested_output_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            make_paper(root / "corpus", "2606-00734", "2606.00734", {1: "no_tex"})
            with self.assertRaisesRegex(ValueError, "Duplicate"):
                inventory([root / "corpus", root / "corpus"])
            with self.assertRaisesRegex(ValueError, "separate"):
                run_full([str(root / "corpus")], root / "corpus/run", workers=1)

    def test_inventory_ignores_auxiliary_children_and_validates_all_versions(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            inputs = self.fixture(root)
            (inputs[0] / ".DS_Store").write_text("auxiliary file")
            (inputs[0] / "unrelated").mkdir()
            result = inventory(inputs)
            self.assertEqual(result["publications"], 3)
            self.assertEqual(result["declared_versions"], 5)
            self.assertEqual([p["arxiv_id"] for p in result["papers"]], ["2606.00734", "2606.01068", "2605.02338"])
            marker = inputs[0] / "2606-00734/paper.json"
            data = json.loads(marker.read_text())
            data["versions"].append(data["versions"][0])
            marker.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, "Invalid/duplicate version"):
                inventory(inputs)

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
