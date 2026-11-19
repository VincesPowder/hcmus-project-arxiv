"""Regressions for complementary metadata, key reuse and safe dedup."""
import unittest
from helpers import DOC, make_paper, make_version
from src.gather import gather_version
from src import refs, pipeline
import json
import tempfile
from pathlib import Path


def entry(key, **fields):
    return dict(citation_key=key, entry_type="article", **fields)


class ReferenceUnion(unittest.TestCase):
    def test_complementary_author_lists_union(self):
        result, aliases = refs.unify_bibtex({
            1: [entry("a", title="The title", authors=["A. Smith", "Bob Jones"])],
            2: [entry("b", title="The title", authors=["Alice Smith", "Carol Lee"])]})
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["authors"], ["Alice Smith", "Bob Jones", "Carol Lee"])
        self.assertEqual(aliases, {"b": "a"})

    def test_doi_links_changed_title_and_preserves_conflicts(self):
        result, _ = refs.unify_bibtex({
            1: [entry("a", title="Early title", authors=["Jane Doe"], doi="https://doi.org/10.1000/X", year=2020, journal="Preprint")],
            2: [entry("b", title="Final title", authors=["Jane Doe", "John Roe"], doi="10.1000/x", year=2021, journal="Journal")]})
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["authors"], ["Jane Doe", "John Roe"])
        self.assertEqual(result[0]["field_conflicts"]["year"], [2020, 2021])
        self.assertEqual(result[0]["field_conflicts"]["title"], ["Early title", "Final title"])
        self.assertEqual(len(result[0]["source_keys"]), 2)

    def test_conflicting_dois_are_not_merged(self):
        result, _ = refs.unify_bibtex({
            1: [entry("a", title="Shared title", doi="10.1000/a")],
            2: [entry("b", title="Shared title", doi="10.1000/b")]})
        self.assertEqual(len(result), 2)

    def test_equal_title_disjoint_authors_are_not_merged(self):
        result, _ = refs.unify_bibtex({
            1: [entry("a", title="Introduction", authors=["Jane Doe"])],
            2: [entry("b", title="Introduction", authors=["John Roe"])]})
        self.assertEqual(len(result), 2)

    def test_reused_key_is_version_specific(self):
        result, maps = refs.unify_bibtex({
            1: [entry("k", title="First work")],
            2: [entry("k", title="Unrelated work")]}, versioned=True)
        self.assertEqual([e["citation_key"] for e in result], ["k", "k__2"])
        self.assertEqual(maps, {1: {}, 2: {"k": "k__2"}})

    def test_ambiguous_legacy_mapping_refuses_silent_corruption(self):
        with self.assertRaises(ValueError):
            refs.unify_bibtex({1: [entry("k", title="A")], 2: [entry("k", title="B")]})

    def test_same_version_conflicting_key_is_reported(self):
        with self.assertRaisesRegex(ValueError, "conflicting references"):
            refs.unify_bibtex({1: [entry("k", title="A"), entry("k", title="B")]}, versioned=True)

    def test_more_citation_commands(self):
        self.assertEqual(refs.rename_cites(r"\parencite [p. 2] {old} \autocite{old} \textcite{old}",
                                          {"old": "new"}),
                         r"\parencite [p. 2] {new} \autocite{new} \textcite{new}")

    def test_pipeline_keeps_key_reuse_separate(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            paper = make_paper(root / "corpus", "2606-00734", "2606.00734", {
                v: {"main.tex": DOC % (r"\section{Body}See \cite{k}. \bibliography{refs}"),
                    "refs.bib": f"@article{{k, title={{{title}}}, author={{Doe, Jane}}}}"}
                for v, title in [(1, "First work"), (2, "Unrelated work")]})
            result = pipeline.process_paper((str(paper), str(root / "parsed"), 10, False))
            self.assertEqual(result["status"], "done", result)
            data = json.loads((root / "parsed/2606-00734.json").read_text())
            self.assertEqual(len(data["bibtex"]), 2)
            contents = [e[2] for e in data["elements"]]
            self.assertTrue(any(r"\cite{k}" in t for t in contents))
            self.assertTrue(any(r"\cite{k__2}" in t for t in contents))


class BibitemBoundaries(unittest.TestCase):
    def parse(self, body):
        return refs.extract_bibitems(r"\begin{thebibliography}{9}\bibitem{k} " + body + r"\end{thebibliography}")[0]

    def test_newblock_before_venue(self):
        e = self.parse(r"Jane Doe. A useful title.\newblock Journal, 2020.")
        self.assertEqual(e["title"], "A useful title")
        self.assertEqual(e["authors"], ["Jane Doe"])

    def test_year_in_title_is_not_cut(self):
        e = self.parse("Jane Doe. ImageNet 2012 classification. Journal, 2020.")
        self.assertEqual(e["title"], "ImageNet 2012 classification")
        self.assertEqual(e["year"], 2020)

    def test_quoted_newblock_title(self):
        e = self.parse("Jane Doe.\\newblock " + chr(96)*2 + "A useful title''.\\newblock Journal, 2020.")
        self.assertEqual(e["title"], "A useful title")


class RealSourceRegressions(unittest.TestCase):
    def test_real_author_year_et_al_spacing(self):
        e = BibitemBoundaries().parse(
            r"Sofroniew, N., Kauvar, B., Saunders, W., et al.\ (2026). "
            r"\emph{Emotion concepts and their function in a large language model.} Anthropic.")
        self.assertEqual(e["authors"], ["N. Sofroniew", "B. Kauvar", "W. Saunders"])
        self.assertEqual(e["title"], "Emotion concepts and their function in a large language model")

    def test_real_tex_accented_surname(self):
        e = BibitemBoundaries().parse(
            r'Albrecht, R., Sch{\"{u}}tz, M., and Zeth, K. (2014).'
            r'\newblock Structure of BamA.\newblock Journal, 2014.')
        self.assertEqual(e["authors"], ["R. Albrecht", "M. Schütz", "K. Zeth"])

    def test_real_mixed_title_quotes(self):
        e = BibitemBoundaries().parse(
            "M. D. Eisaman, J. Fan, A. Migdall, and S. V. Polyakov, " + chr(96)*2 +
            'Invited Review Article: Single-photon sources and detectors," Rev. Sci. Instrum. 82, 071101 (2011).')
        self.assertEqual(e["title"], "Invited Review Article: Single-photon sources and detectors")
        self.assertEqual(e["authors"], ["M. D. Eisaman", "J. Fan", "A. Migdall", "S. V. Polyakov"])

    def test_selected_bib_matches_full_parse_on_macros(self):
        text = '@string{j="A journal"} @article{a,title={j is literal},journal=j} @article{b,title={Other}}'
        self.assertEqual(refs.parse_bib(text, {"a"}), [refs.parse_bib(text)[0]])

    def test_unclosed_inline_math_is_not_catastrophic(self):
        from src.hier_parser import normalize_text
        with pipeline.time_limit(2):
            result = normalize_text("$" + r"\alpha " * 200)
        self.assertTrue(result)

    def test_malformed_bib_suffix_is_scanned_once(self):
        text = "\n".join("@article{broken" + str(i) for i in range(100))
        parsed = refs.parse_bib(text + "\n@article{valid,title={A title},year={2020}}")
        self.assertEqual(parsed[-1]["citation_key"], "valid")

    def test_bib_strings_only_expand_bare_tokens(self):
        parsed = refs.parse_bib(r'@string{j={Journal \ }} @article{k,title={Literal j},journal=J}')
        self.assertEqual(parsed[0]["title"], "Literal j")
        self.assertIn("Journal", parsed[0]["journal"])

    def test_escaped_quotes_are_not_field_terminators(self):
        parsed = refs.parse_bib(r'@article{k,title="A {\"o} title",year={2020}}')
        self.assertEqual(parsed[0]["title"], "A ö title")
        self.assertEqual(parsed[0]["year"], 2020)

    def test_structured_entry_corrects_rendered_same_key(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            make_version(directory, {
                "main.tex": DOC % (r"\cite{k}\bibliography{refs}"
                    r"\begin{thebibliography}{9}\bibitem{k} K. Abe K. A real title. Journal, 2021.\end{thebibliography}"),
                "refs.bib": "@article{k,title={A real title},author={Abe, Ken},year={2021}}"})
            entries = refs.extract_references(gather_version(directory), directory)
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["authors"], ["Ken Abe"])
            self.assertEqual(entries[0]["title"], "A real title")
            self.assertTrue(entries[0]["extraction_conflicts"])

    def test_legacy_source_layout_and_missing_version(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            paper = root / "raw/2606.00734"
            version = paper / "sources/v1"
            version.mkdir(parents=True)
            (paper / "paper_metadata.json").write_text(json.dumps({
                "arxiv_id": "2606.00734", "versions": [{"version": 1}, {"version": 2}]}))
            (version / "main.tex").write_text(DOC % r"\section{Body}One sentence.")
            (version / "manifest.json").write_text('{"status":"extracted"}')
            result = pipeline.process_paper((str(paper), str(root / "parsed"), 10, False))
            self.assertEqual(result["status"], "done")
            output = json.loads((root / "parsed/2606.00734.json").read_text())
            self.assertEqual([v["status"] for v in output["versions"]], ["parsed", "not_downloaded"])


if __name__ == "__main__":
    unittest.main()
