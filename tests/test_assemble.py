"""Unit tests of assemble.py (ids, cross-version deduplication, hierarchy consistency)."""
import unittest

from helpers import DOC  # noqa: F401  (sets sys.path)
from src.assemble import Node, PaperAssembler, canonical

KEY, ARXIV = "2310-12345", "2310.12345"


def tree(*children):
    return Node("document", "", list(children))


def heading(title, *children):
    return Node("heading", title, list(children))


def sentence(text):
    return Node("sentence", text)


class TestAssembler(unittest.TestCase):
    def test_statement_example_shared_sentence(self):
        paper = PaperAssembler(KEY, ARXIV)
        paper.add_version(1, tree(heading("Introduction", sentence("We study the citation matching problem"))))
        paper.add_version(2, tree(heading("Background and model", sentence("We study the citation matching problem"))))
        elements = {row["element_id"]: row for row in paper.element_rows()}
        sentences = [row for row in elements.values() if row["kind"] == "sentence"]
        self.assertEqual(len(sentences), 1)                       # one row serves both versions
        self.assertEqual(elements[f"{KEY}#root1"]["content"], "Version 1 root")
        self.assertEqual(elements[f"{KEY}#root2"]["content"], "Version 2 root")
        edges = {(row["version"], row["element_id"], row["parent_id"]) for row in paper.hierarchy_rows()}
        shared = sentences[0]["element_id"]
        parents = {v: p for v, e, p in edges if e == shared}
        self.assertEqual(len(parents), 2)
        self.assertNotEqual(parents[1], parents[2])               # attached to h1 in v1, h2 in v2
        self.assertEqual(paper.validate(), [])

    def test_whitespace_differences_do_not_break_dedup(self):
        paper = PaperAssembler(KEY, ARXIV)
        paper.add_version(1, tree(sentence("a  b\n c")))
        paper.add_version(2, tree(sentence("a b c ")))
        self.assertEqual(sum(row["kind"] == "sentence" for row in paper.element_rows()), 1)
        self.assertEqual(canonical("e\u0301"), canonical("\u00e9"))           # NFC

    def test_different_content_is_not_merged(self):
        paper = PaperAssembler(KEY, ARXIV)
        paper.add_version(1, tree(sentence("One.")))
        paper.add_version(2, tree(sentence("Two.")))
        self.assertEqual(sum(row["kind"] == "sentence" for row in paper.element_rows()), 2)

    def test_same_kind_different_kind_same_text_not_merged(self):
        paper = PaperAssembler(KEY, ARXIV)
        paper.add_version(1, tree(Node("sentence", "x = 1"), Node("formula", "x = 1")))
        self.assertEqual({row["kind"] for row in paper.element_rows()} - {"document"}, {"sentence", "formula"})

    def test_repeated_sentence_in_one_version_keeps_a_valid_tree(self):
        paper = PaperAssembler(KEY, ARXIV)
        paper.add_version(1, tree(heading("A", sentence("See Fig. 1.")), heading("B", sentence("See Fig. 1."))))
        ids = [row["element_id"] for row in paper.element_rows() if row["kind"] == "sentence"]
        self.assertEqual(len(ids), 2)                              # one id per occurrence
        self.assertEqual(paper.validate(), [])
        paper.add_version(2, tree(heading("A", sentence("See Fig. 1.")), heading("B", sentence("See Fig. 1."))))
        self.assertEqual(sum(row["kind"] == "sentence" for row in paper.element_rows()), 2)  # shared again

    def test_same_heading_under_two_parents_gets_two_ids(self):
        paper = PaperAssembler(KEY, ARXIV)
        paper.add_version(1, tree(heading("Methods", heading("Results", sentence("x."))),
                                  heading("Experiments", heading("Results", sentence("y.")))))
        results = [row for row in paper.element_rows() if row["content"] == "Results"]
        self.assertEqual(len(results), 2)
        self.assertEqual(paper.validate(), [])

    def test_empty_leaves_are_dropped_but_empty_containers_kept(self):
        paper = PaperAssembler(KEY, ARXIV)
        count = paper.add_version(1, tree(sentence("  "), Node("list", "", [Node("item", "", [sentence("ok.")])])))
        kinds = sorted(row["kind"] for row in paper.element_rows())
        self.assertEqual(kinds, ["document", "item", "list", "sentence"])
        self.assertEqual(count, 3)

    def test_ids_start_with_paper_key_and_are_unique(self):
        paper = PaperAssembler(KEY, ARXIV)
        paper.add_version(1, tree(heading("A", sentence("s1."), sentence("s2."))))
        paper.add_version(2, tree(heading("A", sentence("s1."), sentence("s3."))))
        ids = [row["element_id"] for row in paper.element_rows()]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(i.startswith(f"{KEY}#") for i in ids))

    def test_ids_are_deterministic(self):
        def build():
            paper = PaperAssembler(KEY, ARXIV)
            paper.add_version(1, tree(heading("A", sentence("x."), sentence("y."))))
            paper.add_version(2, tree(heading("A", sentence("y."), sentence("z."))))
            return paper.element_rows(), paper.hierarchy_rows()
        self.assertEqual(build(), build())

    def test_stats_before_and_after_dedup(self):
        paper = PaperAssembler(KEY, ARXIV)
        paper.add_version(1, tree(heading("A", sentence("x."), sentence("y."))))   # 3 nodes
        paper.add_version(2, tree(heading("A", sentence("x."), sentence("z."))))   # 3 nodes, 2 shared
        self.assertEqual(paper.stats(), {"nodes_before_dedup": 6, "elements_after_dedup": 4})

    def test_validate_detects_broken_trees(self):
        paper = PaperAssembler(KEY, ARXIV)
        paper.add_version(1, tree(heading("A", sentence("x."))))
        sentence_id = next(r["element_id"] for r in paper.element_rows() if r["kind"] == "sentence")
        paper.hierarchy.append((1, sentence_id, f"{KEY}#root1"))               # second parent
        paper.hierarchy.append((1, sentence_id, "nonexistent"))
        problems = " ".join(paper.validate())
        self.assertIn("two parents", problems)
        self.assertIn("unknown parent", problems)

    def test_version_without_tree_creates_nothing(self):
        paper = PaperAssembler(KEY, ARXIV)
        paper.add_version(1, tree(sentence("x.")))
        self.assertNotIn(f"{KEY}#root2", {row["element_id"] for row in paper.element_rows()})


if __name__ == "__main__":
    unittest.main()
