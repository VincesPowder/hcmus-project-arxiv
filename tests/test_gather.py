"""Unit tests of gather.py (root detection, include resolution, bib files)."""
import tempfile
import unittest
from pathlib import Path

from helpers import DOC, make_version, write
from src.gather import find_cited_keys, gather_version, strip_comments, load_bib_texts


class GatherCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def gather(self, files):
        make_version(self.dir, files)
        return gather_version(self.dir)


class TestComments(GatherCase):
    def test_commented_input_is_not_followed(self):
        result = self.gather({"main.tex": DOC % "Hello.\n%\\input{ghost}\nBye. % \\input{ghost2}",
                              "ghost.tex": "GHOST", "ghost2.tex": "GHOST2"})
        self.assertNotIn("GHOST", result.text)
        self.assertEqual(result.included, ["main.tex"])
        self.assertEqual(sorted(result.unreachable), ["ghost.tex", "ghost2.tex"])

    def test_escaped_percent_is_text_not_comment(self):
        self.assertEqual(strip_comments("50\\% of it"), "50\\% of it")
        result = self.gather({"main.tex": DOC % "50\\% \\input{real}", "real.tex": "REAL"})
        self.assertIn("REAL", result.text)

    def test_double_backslash_then_percent_is_comment(self):
        self.assertEqual(strip_comments("a \\\\% hidden").strip(), "a \\\\")

    def test_comment_environment_removed(self):
        result = self.gather({"main.tex": DOC % "A.\n\\begin{comment}\n\\input{ghost}\n\\end{comment}\nB.",
                              "ghost.tex": "GHOST"})
        self.assertNotIn("GHOST", result.text)
        self.assertEqual(result.included, ["main.tex"])


class TestIncludes(GatherCase):
    def test_extension_optional_and_explicit(self):
        result = self.gather({"main.tex": DOC % "\\input{a}\n\\include{b.tex}", "a.tex": "AAA", "b.tex": "BBB"})
        self.assertIn("AAA", result.text)
        self.assertIn("BBB", result.text)
        self.assertEqual(result.included, ["main.tex", "a.tex", "b.tex"])

    def test_reading_order_is_inlined_in_place(self):
        result = self.gather({"main.tex": DOC % "first\n\\input{a}\nlast", "a.tex": "middle"})
        self.assertLess(result.text.index("first"), result.text.index("middle"))
        self.assertLess(result.text.index("middle"), result.text.index("last"))

    def test_nested_includes_are_transitive(self):
        result = self.gather({"main.tex": DOC % "\\input{a}", "a.tex": "A \\input{b}", "b.tex": "B \\input{c}",
                              "c.tex": "CCC"})
        self.assertIn("CCC", result.text)
        self.assertEqual(result.included, ["main.tex", "a.tex", "b.tex", "c.tex"])

    def test_dot_slash_and_subfolder(self):
        result = self.gather({"main.tex": DOC % "\\input{./sec/a}", "sec/a.tex": "AAA"})
        self.assertIn("AAA", result.text)

    def test_root_in_subfolder_resolves_relative_to_root(self):
        result = self.gather({"paper/main.tex": DOC % "\\input{sec/a}", "paper/sec/a.tex": "AAA"})
        self.assertEqual(result.root, "paper/main.tex")
        self.assertIn("AAA", result.text)

    def test_fallback_to_including_files_folder(self):
        result = self.gather({"main.tex": DOC % "\\input{sec/a}", "sec/a.tex": "A \\input{b}", "sec/b.tex": "BBB"})
        self.assertIn("BBB", result.text)

    def test_case_insensitive_names(self):
        result = self.gather({"Main.tex": DOC % "\\input{Sec/INTRO}", "sec/intro.tex": "INTRO"})
        self.assertIn("INTRO", result.text)
        self.assertEqual(result.missing, [])

    def test_unique_basename_fallback(self):
        result = self.gather({"main.tex": DOC % "\\input{intro}", "deep/er/intro.tex": "INTRO"})
        self.assertIn("INTRO", result.text)

    def test_bare_input_without_braces(self):
        result = self.gather({"main.tex": DOC % "\\input a.tex", "a.tex": "AAA"})
        self.assertIn("AAA", result.text)

    def test_missing_file_is_recorded_not_fatal(self):
        result = self.gather({"main.tex": DOC % "Before. \\input{nothere} After."})
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.missing, ["nothere"])
        self.assertIn("After.", result.text)

    def test_includegraphics_and_inputencoding_are_not_includes(self):
        # files named like the tail of the command: "\\includegraphics" must not become "\\include" + "graphics"
        result = self.gather({"main.tex": DOC % "\\inputencoding{utf8}\n\\includegraphics{fig}\n\\includeonly{a}",
                              "encoding.tex": "ENC", "graphics.tex": "GFX", "only.tex": "ONLY", "a.tex": "A"})
        self.assertEqual(result.included, ["main.tex"])
        self.assertNotIn("GFX", result.text)
        self.assertNotIn("ENC", result.text)
        self.assertEqual(result.missing, [])

    def test_cycle_is_cut(self):
        result = self.gather({"main.tex": DOC % "\\input{a}", "a.tex": "A \\input{b}", "b.tex": "B \\input{a}"})
        self.assertEqual(result.status, "ok")
        self.assertTrue(any("cycle" in warning for warning in result.warnings))

    def test_cannot_escape_version_folder(self):
        write(self.dir.parent, "outside_secret.tex", "SECRET")
        result = self.gather({"main.tex": DOC % "\\input{../outside_secret}"})
        self.assertNotIn("SECRET", result.text)
        self.assertEqual(result.missing, ["../outside_secret"])

    def test_unused_files_are_unreachable(self):
        result = self.gather({"main.tex": DOC % "\\input{a}", "a.tex": "A", "junk.tex": "J", "old/b.tex": "B"})
        self.assertEqual(result.unreachable, ["junk.tex", "old/b.tex"])
        self.assertNotIn("J", result.text.replace("\\", ""))

    def test_subfile_keeps_only_body(self):
        result = self.gather({"main.tex": DOC % "\\subfile{part}",
                              "part.tex": "\\documentclass[main]{subfiles}\n\\begin{document}\nBODY\n\\end{document}"})
        self.assertIn("BODY", result.text)
        self.assertEqual(result.text.count("\\documentclass"), 1)


class TestRoot(GatherCase):
    def test_standalone_figure_is_not_chosen(self):
        result = self.gather({"main.tex": DOC % "\\input{a}\nLong paper text " * 20, "a.tex": "A",
                              "fig.tex": "\\documentclass{standalone}\\begin{document}x\\end{document}"})
        self.assertEqual(result.root, "main.tex")

    def test_file_included_by_another_candidate_is_not_root(self):
        result = self.gather({"paper.tex": DOC % "\\input{copy}", "copy.tex": DOC % "tiny"})
        self.assertEqual(result.root, "paper.tex")

    def test_root_name_is_not_assumed(self):
        result = self.gather({"zzz_weird_name.tex": DOC % "Hi.", "readme.tex": "no class here"})
        self.assertEqual(result.root, "zzz_weird_name.tex")

    def test_no_documentclass_is_no_root(self):
        result = self.gather({"a.tex": "just text", "b.tex": "more text"})
        self.assertEqual(result.status, "no_root")
        self.assertEqual(result.text, "")

    def test_commented_documentclass_does_not_count(self):
        result = self.gather({"a.tex": "%\\documentclass{article}\ntext"})
        self.assertEqual(result.status, "no_root")

    def test_no_tex_files(self):
        result = self.gather({"refs.bib": "@article{a,title={t}}"})
        self.assertEqual(result.status, "no_tex")

    def test_latin1_source_is_read(self):
        write(self.dir, "main.tex", DOC % "caf\u00e9 r\u00e9sum\u00e9", encoding="latin-1")
        result = gather_version(self.dir)
        self.assertEqual(result.status, "ok")
        self.assertIn("caf\u00e9", result.text)


class TestBibFiles(GatherCase):
    def test_bibliography_names_are_resolved(self):
        result = self.gather({"main.tex": DOC % "\\bibliography{refs,sub/more}",
                              "refs.bib": "@misc{a,title={A}}", "sub/more.bib": "@misc{b,title={B}}",
                              "unused.bib": "@misc{c,title={C}}"})
        self.assertEqual(result.bib_files, ["refs.bib", "sub/more.bib"])
        self.assertEqual(result.bib_missing, [])
        texts = load_bib_texts(self.dir, result.bib_files)
        self.assertIn("title={A}", texts["refs.bib"])

    def test_addbibresource_and_missing_bib(self):
        result = self.gather({"main.tex": DOC % "\\addbibresource{lib.bib}\n\\bibliography{ghost}",
                              "lib.bib": "@misc{a,title={A}}"})
        self.assertEqual(result.bib_files, ["lib.bib"])
        self.assertEqual(result.bib_missing, ["ghost"])

    def test_commented_bibliography_is_ignored(self):
        result = self.gather({"main.tex": DOC % "%\\bibliography{refs}", "refs.bib": "@misc{a,title={A}}"})
        self.assertEqual(result.bib_files, [])


class TestCitedKeys(unittest.TestCase):
    def test_variants_multiple_keys_and_optional_arguments(self):
        text = ("A~\\cite{a, b}. B \\citep*[see][p.~2]{c}. C \\citet{d}, \\parencite[x]{e}, "
                "\\autocite{f}, \\Citeauthor{g}, \\textcite {h}.")
        keys, star = find_cited_keys(text)
        self.assertEqual(keys, set("abcdefgh"))
        self.assertFalse(star)

    def test_nocite_star_means_whole_bib(self):
        keys, star = find_cited_keys("\\nocite{*} \\nocite{x}")
        self.assertTrue(star)
        self.assertEqual(keys, {"x"})

    def test_commented_cite_is_not_a_citation(self):
        make_version_dir = tempfile.TemporaryDirectory()
        try:
            make_version(make_version_dir.name, {"main.tex": DOC % "Real \\cite{yes}.\n% \\cite{no}\nEnd. %\\cite{no2}"})
            keys, _ = find_cited_keys(gather_version(make_version_dir.name).text)
        finally:
            make_version_dir.cleanup()
        self.assertEqual(keys, {"yes"})

    def test_non_citation_commands_are_ignored(self):
        keys, _ = find_cited_keys("\\bibliography{refs} \\bibitem{k} \\label{c1}")
        self.assertEqual(keys, set())


if __name__ == "__main__":
    unittest.main()
