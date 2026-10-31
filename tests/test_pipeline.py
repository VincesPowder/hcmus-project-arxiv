"""Integration tests of pipeline.process_paper on tiny fake M1 corpora (placeholder parser/refs)."""
import json
import signal
import tempfile
import time
import unittest
from pathlib import Path

from helpers import DOC, make_paper
from src import dev_stubs, pipeline


def load(out_dir, key):
    return json.loads((Path(out_dir) / f"{key}.json").read_text(encoding="utf-8"))


class PipelineCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.corpus, self.out = Path(self._tmp.name) / "corpus", Path(self._tmp.name) / "parsed"
        self._saved = (pipeline.parse_document, pipeline.extract_references,
                       pipeline.unify_bibtex, pipeline.rename_cites)
        pipeline.parse_document = dev_stubs.parse_document            # tests must not depend on B/C's code
        pipeline.extract_references = dev_stubs.extract_references
        pipeline.unify_bibtex, pipeline.rename_cites = dev_stubs.unify_bibtex, dev_stubs.rename_cites

    def tearDown(self):
        (pipeline.parse_document, pipeline.extract_references,
         pipeline.unify_bibtex, pipeline.rename_cites) = self._saved
        self._tmp.cleanup()

    def run_paper(self, paper_dir, timeout=30, force=False):
        return pipeline.process_paper((str(paper_dir), str(self.out), timeout, force))


class TestVersionStatuses(PipelineCase):
    def test_empty_and_not_downloaded_versions_are_recorded_not_skipped(self):
        body = "\\section{Intro}\nWe study it. It works."
        paper = make_paper(self.corpus, "2605-00001", "2605.00001",
                           {1: {"main.tex": DOC % body}, 2: "no_tex", 3: None})
        self.assertEqual(self.run_paper(paper)["status"], "done")
        data = load(self.out, "2605-00001")
        statuses = {v["version"]: v["status"] for v in data["versions"]}
        self.assertEqual(statuses, {1: "parsed", 2: "empty", 3: "not_downloaded"})
        element_ids = {row[0] for row in data["elements"]}
        self.assertIn("2605-00001#root1", element_ids)
        self.assertNotIn("2605-00001#root2", element_ids)          # empty version yields no elements
        self.assertEqual({row[0] for row in data["hierarchy"]}, {1})

    def test_paper_with_only_empty_versions_still_writes_output(self):
        paper = make_paper(self.corpus, "2605-00002", "2605.00002", {1: "no_tex"})
        self.assertEqual(self.run_paper(paper)["status"], "done")
        data = load(self.out, "2605-00002")
        self.assertEqual(data["elements"], [])
        self.assertEqual(data["versions"][0]["status"], "empty")

    def test_no_root_is_recorded(self):
        paper = make_paper(self.corpus, "2605-00003", "2605.00003", {1: {"a.tex": "no documentclass"}})
        self.run_paper(paper)
        self.assertEqual(load(self.out, "2605-00003")["versions"][0]["status"], "no_root")

    def test_one_bad_version_does_not_kill_the_paper(self):
        def explode(text):
            if "BOOM" in text:
                raise ValueError("bad tex")
            return dev_stubs.parse_document(text)
        pipeline.parse_document = explode
        paper = make_paper(self.corpus, "2605-00004", "2605.00004",
                           {1: {"main.tex": DOC % "fine text."}, 2: {"main.tex": DOC % "BOOM"}})
        self.assertEqual(self.run_paper(paper)["status"], "done")
        versions = {v["version"]: v for v in load(self.out, "2605-00004")["versions"]}
        self.assertEqual(versions[1]["status"], "parsed")
        self.assertEqual(versions[2]["status"], "parse_error")
        self.assertIn("ValueError", versions[2]["error"])

    @unittest.skipUnless(hasattr(signal, "SIGALRM"), "per-version timeout needs SIGALRM (Linux/macOS/Colab)")
    def test_hung_version_times_out(self):
        pipeline.parse_document = lambda text: time.sleep(10)
        paper = make_paper(self.corpus, "2605-00005", "2605.00005", {1: {"main.tex": DOC % "x."}})
        started = time.time()
        self.run_paper(paper, timeout=1)
        self.assertLess(time.time() - started, 5)
        self.assertEqual(load(self.out, "2605-00005")["versions"][0]["status"], "timeout")

    def test_unreadable_paper_json_fails_without_output(self):
        paper = make_paper(self.corpus, "2605-00006", "2605.00006", {1: "no_tex"})
        (paper / "paper.json").write_text("{not json", encoding="utf-8")
        result = self.run_paper(paper)
        self.assertEqual(result["status"], "failed")
        self.assertFalse((self.out / "2605-00006.json").exists())   # no marker -> retried next run


class TestResume(PipelineCase):
    def test_second_run_skips_and_force_reprocesses(self):
        paper = make_paper(self.corpus, "2605-00007", "2605.00007", {1: {"main.tex": DOC % "Hi there."}})
        self.assertEqual(self.run_paper(paper)["status"], "done")
        self.assertEqual(self.run_paper(paper)["status"], "skipped")
        self.assertEqual(self.run_paper(paper, force=True)["status"], "done")

    def test_no_temp_files_left_behind(self):
        paper = make_paper(self.corpus, "2605-00008", "2605.00008", {1: {"main.tex": DOC % "Hi there."}})
        self.run_paper(paper)
        self.assertEqual([p.name for p in self.out.iterdir()], ["2605-00008.json"])


class TestAcrossVersions(PipelineCase):
    def test_multi_file_gathering_and_unused_file_ignored(self):
        paper = make_paper(self.corpus, "2605-00009", "2605.00009", {1: {
            "main.tex": DOC % "\\section{A}\nMain text.\n%\\input{ghost}\n\\input{part}",
            "part.tex": "Included text.", "ghost.tex": "Ghost text.", "unused.tex": "Unused text."}})
        self.run_paper(paper)
        data = load(self.out, "2605-00009")
        contents = " ".join(row[2] for row in data["elements"])
        self.assertIn("Included text", contents)
        self.assertNotIn("Ghost", contents)
        self.assertNotIn("Unused", contents)
        version = data["versions"][0]
        self.assertEqual((version["n_tex"], version["n_included"], version["n_unreachable"]), (4, 2, 2))

    def test_unchanged_sentence_shared_between_versions(self):
        paper = make_paper(self.corpus, "2605-00010", "2605.00010", {
            1: {"main.tex": DOC % "\\section{Intro}\nWe study the problem. Old claim."},
            2: {"main.tex": DOC % "\\section{Background}\nWe study the problem. New claim."}})
        self.run_paper(paper)
        data = load(self.out, "2605-00010")
        shared = [row for row in data["elements"] if row[2] == "We study the problem."]
        self.assertEqual(len(shared), 1)
        parents = {v: p for v, e, p in data["hierarchy"] if e == shared[0][0]}
        self.assertEqual(set(parents), {1, 2})
        self.assertNotEqual(parents[1], parents[2])
        self.assertEqual(data["stats"]["hierarchy_problems"], 0)

    def test_cite_key_renamed_before_text_dedup_and_bib_unioned(self):
        bib1 = "@article{vaswani2017,\n title={Attention is all you need},\n author={Vaswani, A and Shazeer, N},\n year={2017}}"
        bib3 = "@article{vit_transformer,\n title={Attention is all you need},\n author={Vaswani, A},\n doi={10.1/x}}"
        paper = make_paper(self.corpus, "2605-00011", "2605.00011", {
            1: {"main.tex": DOC % "\\section{Intro}\nIt is hard~\\cite{vaswani2017}.\n\\bibliography{refs}", "refs.bib": bib1},
            3: {"main.tex": DOC % "\\section{Intro}\nIt is hard~\\cite{vit_transformer}.\n\\bibliography{refs}", "refs.bib": bib3}})
        self.run_paper(paper)
        data = load(self.out, "2605-00011")
        self.assertEqual(len(data["bibtex"]), 1)                              # deduplicated across versions
        entry = data["bibtex"][0]
        self.assertEqual(entry["citation_key"], "vaswani2017")                # one key survives
        self.assertEqual(entry["year"], 2017)                                 # union: from v1
        self.assertEqual(entry["doi"], "10.1/x")                              # union: from v3
        self.assertEqual(data["cite_renames"], 1)
        hard = [row for row in data["elements"] if "hard" in row[2]]
        self.assertEqual(len(hard), 1)                                        # same sentence -> single element
        self.assertIn("vaswani2017", hard[0][2])
        self.assertNotIn("vit_transformer", hard[0][2])
        self.assertEqual(data["stats"]["bib_before_dedup"], 2)
        self.assertEqual(data["stats"]["bib_after_dedup"], 1)

    def test_bib_files_are_counted_in_version_record(self):
        paper = make_paper(self.corpus, "2605-00012", "2605.00012", {1: {
            "main.tex": DOC % "Text here \\cite{a}.\n\\bibliography{refs,ghost}", "refs.bib": "@misc{a, title={A}}"}})
        self.run_paper(paper)
        version = load(self.out, "2605-00012")["versions"][0]
        self.assertEqual((version["n_bib_files"], version["bib_missing"], version["n_references"]), (1, 1, 1))


class TestBibScope(PipelineCase):
    LIBRARY = "\n".join("@article{k%d, title={Title %d}, year={200%d}}" % (i, i, i) for i in range(5))

    def make(self, key, body, extra=None):
        files = {"main.tex": DOC % body, "lib.bib": self.LIBRARY}
        files.update(extra or {})
        return make_paper(self.corpus, key, "2605." + key[-5:], {1: files})

    def references(self, key):
        data = load(self.out, key)
        return data["versions"][0]["n_references"], {e["citation_key"] for e in data["bibtex"]}

    def test_cited_scope_keeps_only_cited_library_entries(self):
        paper = self.make("2605-00020", "Text \\cite{k1,k3}.\n\\bibliography{lib}")
        self.run_paper(paper)
        self.assertEqual(self.references("2605-00020"), (2, {"k1", "k3"}))

    def test_all_scope_keeps_the_whole_library(self):
        paper = self.make("2605-00021", "Text \\cite{k1}.\n\\bibliography{lib}")
        pipeline.process_paper((str(paper), str(self.out), 30, False, "all"))
        self.assertEqual(self.references("2605-00021")[0], 5)

    def test_nocite_star_keeps_everything_even_in_cited_scope(self):
        paper = self.make("2605-00022", "Text.\n\\nocite{*}\n\\bibliography{lib}")
        self.run_paper(paper)
        self.assertEqual(self.references("2605-00022")[0], 5)

    def test_commented_cite_does_not_pull_entries_in(self):
        paper = self.make("2605-00023", "Text \\cite{k0}.\n% \\cite{k4}\n\\bibliography{lib}")
        self.run_paper(paper)
        self.assertEqual(self.references("2605-00023")[1], {"k0"})

    def test_bibitems_are_always_kept(self):
        body = ("Text \\cite{k1}.\n\\begin{thebibliography}{9}\n\\bibitem{own} An own item.\n"
                "\\end{thebibliography}\n\\bibliography{lib}")
        paper = self.make("2605-00024", body)
        self.run_paper(paper)
        self.assertEqual(self.references("2605-00024")[1], {"own", "k1"})


class TestListing(PipelineCase):
    def test_list_papers_ignores_folders_without_paper_json_and_honours_limit(self):
        make_paper(self.corpus, "2605-00001", "2605.00001", {1: "no_tex"})
        make_paper(self.corpus, "2605-00002", "2605.00002", {1: "no_tex"})
        (self.corpus / "stray_folder").mkdir()
        self.assertEqual([p.name for p in pipeline.list_papers(self.corpus)], ["2605-00001", "2605-00002"])
        self.assertEqual(len(pipeline.list_papers(self.corpus, limit=1)), 1)
        self.assertEqual([p.name for p in pipeline.list_papers(self.corpus, only=["2605-00002"])], ["2605-00002"])


if __name__ == "__main__":
    unittest.main()
