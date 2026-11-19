"""Unit tests of the REAL reference processor (refs.py)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from helpers import DOC, make_version  # noqa: F401  (sets sys.path)
from src.gather import gather_version
from src import refs


class TestParseBib(unittest.TestCase):
    def test_basic_entry_fields(self):
        bib = (
            "@article{vaswani2017,\n"
            "  author = {Vaswani, Ashish and Shazeer, Noam},\n"
            "  title = {Attention is all you need},\n"
            "  year = {2017},\n"
            "  doi = {10.1/x},\n"
            "  journal = {NeurIPS},\n"
            "  url = {https://example.org/a}\n"
            "}"
        )
        [entry] = refs.parse_bib(bib)
        self.assertEqual(entry["citation_key"], "vaswani2017")
        self.assertEqual(entry["entry_type"], "article")
        self.assertEqual(entry["title"], "Attention is all you need")
        self.assertEqual(entry["authors"], ["Ashish Vaswani", "Noam Shazeer"])
        self.assertEqual(entry["year"], 2017)
        self.assertEqual(entry["doi"], "10.1/x")
        self.assertEqual(entry["journal"], "NeurIPS")
        self.assertEqual(entry["url"], "https://example.org/a")

    def test_quoted_and_nested_brace_values(self):
        bib = '@misc{k, title = "A {B}ayesian title", author = {Last, First}, year = 2020}'
        [entry] = refs.parse_bib(bib)
        self.assertEqual(entry["title"], "A Bayesian title")
        self.assertEqual(entry["authors"], ["First Last"])
        self.assertEqual(entry["year"], 2020)

    def test_string_macros_and_concatenation_are_expanded(self):
        bib = (
            '@string{jmlr = "Journal of Machine Learning Research"}\n'
            '@string{one = "1"}\n'
            "@article{k, journal = jmlr, pages = one # \"--\" # {10}, year = {2019}}"
        )
        [entry] = refs.parse_bib(bib)
        self.assertEqual(entry["journal"], "Journal of Machine Learning Research")

    def test_comment_preamble_and_string_are_skipped(self):
        bib = (
            "@comment{a comment with {braces}}\n"
            '@preamble{"\\newcommand{\\x}{y}"}\n'
            "@string{name = {value}}\n"
            "@article{k, title = {Real}, year = {2018}}"
        )
        entries = refs.parse_bib(bib)
        self.assertEqual([e["citation_key"] for e in entries], ["k"])

    def test_editor_used_as_author_fallback(self):
        bib = "@inproceedings{k, editor = {Doe, Jane}, title = {T}, booktitle = {Proc}, year = {2017}}"
        [entry] = refs.parse_bib(bib)
        self.assertEqual(entry["authors"], ["Jane Doe"])
        self.assertEqual(entry["journal"], "Proc")

    def test_year_extracted_from_non_numeric_field(self):
        bib = "@misc{k, title = {T}, year = {Published 2021}}"
        [entry] = refs.parse_bib(bib)
        self.assertEqual(entry["year"], 2021)


class TestExtractBibitems(unittest.TestCase):
    def test_plain_authors_title_year(self):
        latex = (
            "\\begin{thebibliography}{9}\n"
            "\\bibitem{vaswani2017} A. Vaswani and N. Shazeer. Attention is all you need. NeurIPS, 2017.\n"
            "\\end{thebibliography}"
        )
        [entry] = refs.extract_bibitems(latex)
        self.assertEqual(entry["citation_key"], "vaswani2017")
        self.assertEqual(entry["entry_type"], "misc")
        self.assertEqual(entry["title"], "Attention is all you need")
        self.assertEqual(entry["authors"], ["A. Vaswani", "N. Shazeer"])
        self.assertEqual(entry["year"], 2017)

    def test_newblock_segmentation_and_doi_url(self):
        latex = (
            "\\begin{thebibliography}{9}\n"
            "\\bibitem{b} J. Smith, \\newblock A very good title. \\newblock JMLR 12(1), 2020.\n"
            "\\bibitem{c} Doe. ``Deep learning''. \\url{https://x.example.org}, doi:10.1000/xyz, 2019.\n"
            "\\end{thebibliography}"
        )
        entries = {e["citation_key"]: e for e in refs.extract_bibitems(latex)}
        self.assertEqual(entries["b"]["title"], "A very good title")
        self.assertEqual(entries["b"]["authors"], ["J. Smith"])
        self.assertEqual(entries["b"]["year"], 2020)
        self.assertEqual(entries["c"]["title"], "Deep learning")
        self.assertEqual(entries["c"]["url"], "https://x.example.org")
        self.assertEqual(entries["c"]["doi"], "10.1000/xyz")

    def test_optional_label_and_href_author(self):
        latex = (
            "\\begin{thebibliography}{1}\n"
            "\\bibitem[1]{k} \\href{https://doi.org/10.1/x}{Jane Doe}. The title here. 2020.\n"
            "\\end{thebibliography}"
        )
        [entry] = refs.extract_bibitems(latex)
        self.assertEqual(entry["citation_key"], "k")
        self.assertEqual(entry["title"], "The title here")
        self.assertEqual(entry["authors"], ["Jane Doe"])


class TestExtractReferences(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def gather(self, files):
        make_version(self.dir, files)
        return gather_version(self.dir)

    def test_only_compilation_path_is_scanned_for_bibitems(self):
        # unused.tex carries a thebibliography block but is NOT on the path.
        gathered = self.gather({
            "main.tex": DOC % "Main text.\n\\input{body}",
            "body.tex": "Body text.",
            "unused.tex": "\\begin{thebibliography}{1}\\bibitem{ghost} Ghost ref. 2010.\\end{thebibliography}",
        })
        self.assertIn("unused.tex", gathered.unreachable)
        entries = refs.extract_references(gathered, self.dir)
        self.assertEqual(entries, [])               # ghost is never read

    def test_declared_bib_cited_scope_and_bibitem_always_kept(self):
        bib = "\n".join(
            "@article{k%d, title = {Title %d}, year = {200%d}}" % (i, i, i) for i in range(3)
        )
        body = "Text \\cite{k0}.\n\\begin{thebibliography}{1}\\bibitem{own} Own item. 2015.\\end{thebibliography}\n\\bibliography{lib}"
        gathered = self.gather({"main.tex": DOC % body, "lib.bib": bib, "extra.bib": "@misc{x, title={X}}"})
        entries = refs.extract_references(gathered, self.dir, bib_scope="cited")
        keys = {e["citation_key"] for e in entries}
        self.assertEqual(keys, {"own", "k0"})       # k1/k2 not cited; extra.bib not declared

    def test_all_scope_and_nocite_star(self):
        bib = "@article{a, title={A}, year={2001}}\n@article{b, title={B}, year={2002}}"
        gathered = self.gather({"main.tex": DOC % "\\nocite{*}\n\\bibliography{lib}", "lib.bib": bib})
        self.assertEqual(len(refs.extract_references(gathered, self.dir)), 2)
        gathered = self.gather({"main.tex": DOC % "Text.\n\\bibliography{lib}", "lib.bib": bib})
        self.assertEqual(len(refs.extract_references(gathered, self.dir, bib_scope="all")), 2)


class TestUnifyBibtex(unittest.TestCase):
    def test_same_title_unioned_and_key_renamed(self):
        per_version = {
            1: [{"citation_key": "a", "entry_type": "article", "title": "Attention is all you need",
                 "authors": ["A", "B"], "year": 2017, "doi": "", "journal": "", "url": ""}],
            3: [{"citation_key": "b", "entry_type": "article", "title": "Attention is all you need",
                 "authors": ["A"], "year": None, "doi": "10.1/x", "journal": "J", "url": ""}],
        }
        unified, mapping = refs.unify_bibtex(per_version)
        self.assertEqual(len(unified), 1)
        self.assertEqual(unified[0]["citation_key"], "a")
        self.assertEqual(unified[0]["year"], 2017)
        self.assertEqual(unified[0]["doi"], "10.1/x")
        self.assertEqual(unified[0]["authors"], ["A", "B"])      # longest list
        self.assertEqual(mapping, {"b": "a"})

    def test_titleless_entries_never_merge_across_different_keys(self):
        per_version = {
            1: [{"citation_key": "x", "entry_type": "misc", "title": "", "authors": [],
                 "year": None, "doi": "", "journal": "", "url": ""}],
            2: [{"citation_key": "y", "entry_type": "misc", "title": "", "authors": [],
                 "year": None, "doi": "", "journal": "", "url": ""}],
        }
        unified, _ = refs.unify_bibtex(per_version)
        self.assertEqual(len(unified), 2)          # never merged

    def test_same_key_without_title_is_merged(self):
        per_version = {
            1: [{"citation_key": "k", "entry_type": "misc", "title": "", "authors": [],
                 "year": 2001, "doi": "", "journal": "", "url": ""}],
            2: [{"citation_key": "k", "entry_type": "misc", "title": "", "authors": [],
                 "year": None, "doi": "10.1/x", "journal": "", "url": ""}],
        }
        unified, mapping = refs.unify_bibtex(per_version)
        self.assertEqual(len(unified), 1)
        self.assertEqual(unified[0]["year"], 2001)
        self.assertEqual(unified[0]["doi"], "10.1/x")
        self.assertEqual(mapping, {})

    def test_longest_title_wins_within_same_reference(self):
        # Same normalized title (case / punctuation / whitespace differ) -> one entry,
        # keeping the longest raw form.
        per_version = {
            1: [{"citation_key": "a", "entry_type": "article", "title": "A  Short   Title", "authors": [],
                 "year": 2001, "doi": "", "journal": "", "url": ""}],
            2: [{"citation_key": "b", "entry_type": "article", "title": "A Short Title.", "authors": [],
                 "year": None, "doi": "", "journal": "", "url": ""}],
        }
        unified, mapping = refs.unify_bibtex(per_version)
        self.assertEqual(len(unified), 1)
        self.assertEqual(unified[0]["citation_key"], "a")
        self.assertEqual(unified[0]["title"], "A  Short   Title")
        self.assertEqual(mapping, {"b": "a"})


class TestRenameCites(unittest.TestCase):
    def test_renames_and_dedupes_keys(self):
        text = "See \\cite{a, b} and \\citep[a]{b}."
        self.assertEqual(
            refs.rename_cites(text, {"a": "x", "b": "x"}),
            "See \\cite{x} and \\citep[a]{x}.",
        )

    def test_no_mapping_is_identity(self):
        text = "See \\cite{a}."
        self.assertEqual(refs.rename_cites(text, {}), text)


if __name__ == "__main__":
    unittest.main()

