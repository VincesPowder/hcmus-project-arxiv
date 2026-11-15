"""Focused regression cases. Run explicitly; book compilation never runs tests."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from refs import extract_bibitems


class BibitemFields(unittest.TestCase):
    def entry(self, body):
        return extract_bibitems(r"\begin{thebibliography}{9}\bibitem{k} " + body + r"\end{thebibliography}")[0]

    def test_initials(self):
        e = self.entry("A. Vaswani and N. Shazeer. Attention is all you need. NeurIPS, 2017.")
        self.assertEqual(e["authors"], ["A. Vaswani", "N. Shazeer"])
        self.assertEqual(e["title"], "Attention is all you need")

    def test_author_year(self):
        e = self.entry("Jane Doe (2020). A useful title. Journal of Examples.")
        self.assertEqual(e["authors"], ["Jane Doe"])
        self.assertEqual(e["title"], "A useful title")

    def test_apostrophe_and_quotes(self):
        e = self.entry('J. O\'Connor. "Learning from data". Journal, 2021.')
        self.assertEqual(e["authors"], ["J. O'Connor"])
        self.assertEqual(e["title"], "Learning from data")

    def test_formatted_venue_not_title(self):
        e = self.entry(r"Jane Doe. A useful title. \emph{Journal of Examples}, 2020.")
        self.assertEqual(e["title"], "A useful title")

    def test_explicit_fields(self):
        e = self.entry(r"\bibinfo{author}{Doe, Jane}. \bibinfo{title}{A {B}ayesian title}. 2020.")
        self.assertEqual(e["authors"], ["Jane Doe"])
        self.assertEqual(e["title"], "A Bayesian title")

    def test_ambiguous(self):
        e = self.entry("Unstructured reference without delimiters 2020")
        self.assertEqual(e["authors"], [])
        self.assertEqual(e["title"], "")


if __name__ == "__main__":
    unittest.main()
