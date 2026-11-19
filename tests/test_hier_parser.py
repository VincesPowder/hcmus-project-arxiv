"""Unit tests of the REAL parser (hier_parser.parse_document)."""

from __future__ import annotations

import unittest

from helpers import DOC  # noqa: F401  (sets sys.path)
from src.assemble import iter_nodes
from src.hier_parser import normalize_math, parse_document, split_sentences, strip_comments


def kinds(root):
    return [node.kind for node in iter_nodes(root)]


def headings(root):
    return [node.content for node in iter_nodes(root) if node.kind == "heading"]


class TestHeadings(unittest.TestCase):
    def test_dynamic_levels_starred_appendix_and_acknowledgements(self):
        latex = DOC % r"""
        \part*{Main matter}
        \section{Introduction}
        Intro sentence.
        \subsection*{Motivation}
        Motivation sentence.
        \section*{Acknowledgements}
        Thanks to the reviewers.
        \appendix
        \section*{Proof details}
        Appendix sentence.
        """
        root = parse_document(latex)
        self.assertEqual(
            headings(root),
            ["Main matter", "Introduction", "Motivation", "Acknowledgements", "Appendix", "Proof details"],
        )
        appendix = [n for n in iter_nodes(root) if n.content == "Appendix"][0]
        self.assertEqual([c.content for c in appendix.children], ["Proof details"])

    def test_partial_command_is_not_a_part_heading(self):
        latex = DOC % r"\section{Math}The derivative is $\partial f/\partial x$."
        root = parse_document(latex)
        self.assertEqual(headings(root), ["Math"])

    def test_chapter_is_supported(self):
        latex = DOC % r"\chapter{One}\section{Sub}Text."
        root = parse_document(latex)
        self.assertEqual(headings(root), ["One", "Sub"])


class TestExclusions(unittest.TestCase):
    def test_bibliography_excluded_but_later_appendix_kept(self):
        latex = DOC % r"""
        \section{Body}Body sentence.
        \begin{thebibliography}{9}
        \bibitem{x} Reference that must disappear.
        \end{thebibliography}
        \bibliographystyle{plain}
        \bibliography{refs}
        \part*{Appendix}\appendix\section*{Extra}Kept appendix sentence.
        """
        root = parse_document(latex)
        content = "\n".join(node.content for node in iter_nodes(root))
        self.assertNotIn("Reference that must disappear", content)
        self.assertNotIn("refs", content)
        self.assertIn("Kept appendix sentence", content)


class TestLists(unittest.TestCase):
    def test_lists_are_parents_and_items_are_children(self):
        latex = DOC % r"""
        \section{Method}
        \begin{enumerate}
          \item First item has one sentence.
          \item[Case B] Second item.
            \begin{itemize}
              \item Nested point.
            \end{itemize}
        \end{enumerate}
        """
        root = parse_document(latex)
        lists = [n for n in iter_nodes(root) if n.kind == "list"]
        self.assertEqual(len(lists), 2)
        outer = lists[0]
        self.assertEqual([child.kind for child in outer.children], ["item", "item"])
        self.assertEqual(outer.children[1].content, "Case B")
        self.assertTrue(any(child.kind == "list" for child in outer.children[1].children))


class TestAtomicLeaves(unittest.TestCase):
    def test_figures_tables_and_block_math_are_atomic_leaves(self):
        latex = DOC % r"""
        \section{Results}
        \begin{figure}[htpb]\centering\includegraphics{a}\caption{A result}\label{fig:a}\end{figure}
        \begin{table}[t]\caption{Scores}\begin{tabular}{cc}A&B\\1&2\end{tabular}\end{table}
        \begin{equation*}x = 1.\end{equation*}
        $$ y = 2 $$
        \[z = 3\]
        """
        root = parse_document(latex)
        figures = [n for n in iter_nodes(root) if n.kind == "figure"]
        formulas = [n for n in iter_nodes(root) if n.kind == "formula"]
        self.assertEqual(len(figures), 2)
        self.assertTrue(figures[0].content.startswith("Figure: A result"))
        self.assertTrue(figures[1].content.startswith("Table: Scores"))
        self.assertEqual(len(formulas), 3)
        self.assertTrue(all(n.content.startswith(r"\begin{equation}") for n in formulas))
        self.assertTrue(all(len(n.children) == 0 for n in figures))
        self.assertTrue(all(len(n.children) == 0 for n in formulas))

    def test_linebreak_optional_space_is_not_display_math(self):
        latex = DOC % r"\section{Layout}First line.\\[2mm]Second line."
        root = parse_document(latex)
        self.assertFalse(any(n.kind == "formula" for n in iter_nodes(root)))


class TestHelpers(unittest.TestCase):
    def test_sentence_split_protects_special_dots(self):
        text = (
            r"Fig. 1 uses 3.14 units, i.e. a decimal, with $x=1.2$. "
            r"See https://example.org/a.b for details. Next sentence."
        )
        sentences = split_sentences(text)
        self.assertEqual(len(sentences), 3)
        self.assertIn("3.14", sentences[0])
        self.assertIn("example.org/a.b", sentences[1])

    def test_normalize_math_wraps_in_equation(self):
        self.assertEqual(normalize_math("x = 1"), r"\begin{equation} x = 1 \end{equation}")

    def test_strip_comments_keeps_escaped_percent(self):
        self.assertEqual(strip_comments("50\\% of it"), "50\\% of it")
        self.assertEqual(strip_comments("a % comment\nb"), "a \nb")


class TestContract(unittest.TestCase):
    def test_root_is_document_and_kinds_are_from_the_contract(self):
        latex = DOC % r"\section{S}Sentence.\begin{itemize}\item i\end{itemize}"
        root = parse_document(latex)
        self.assertEqual(root.kind, "document")
        self.assertTrue(set(kinds(root)) <= {"document", "heading", "sentence", "list", "item", "formula", "figure"})


if __name__ == "__main__":
    unittest.main()

