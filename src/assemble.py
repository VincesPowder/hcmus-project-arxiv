"""Milestone 2 / A2 - element ids, cross-version deduplication, the `elements` and `hierarchy` rows.

One PaperAssembler per publication. Feed it the parsed tree of every version in ascending
version order (add_version); it keeps ONE shared element store:

    identity of an element = (kind, canonical cleaned content, occurrence index in the version)

* Same identity in two versions            -> one element_id (the deduplication required by M2).
* The occurrence index is the n-th time the same (kind, content) appears inside ONE version.
  It keeps every element_id unique per version, so the hierarchy of a version is a real tree
  (otherwise two "Results" headings or two "See Fig. 1." sentences would collapse into one
  node with two parents and the children could no longer be attributed).
* Documents are never shared: root{v} = "Version v root", exactly like the example of the statement.
"""
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field

KIND_PREFIX = {"heading": "h", "list": "l", "item": "i", "sentence": "s", "formula": "f", "figure": "g"}
LEAF_KINDS = {"sentence", "formula", "figure"}


@dataclass
class Node:
    """Contract between the parser (B/C) and the assembler (A)."""
    kind: str                                   # document|heading|list|item|sentence|formula|figure
    content: str = ""                           # already cleaned/normalised text (heading title, sentence, ...)
    children: list = field(default_factory=list)


def canonical(text):
    """Key used for exact full-text matching: NFC + collapsed whitespace."""
    return " ".join(unicodedata.normalize("NFC", text or "").split())


def iter_nodes(node):
    """Depth-first, document order (children left to right)."""
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        stack.extend(reversed(current.children))


class PaperAssembler:
    def __init__(self, paper_key, arxiv_id):
        self.paper_key, self.arxiv_id = paper_key, arxiv_id
        self.elements = {}                       # element_id -> (kind, content)
        self._ids = {}                           # (kind, content, occurrence) -> element_id
        self._counters = defaultdict(int)
        self.hierarchy = []                      # (version, element_id, parent_id)
        self.nodes_per_version = {}              # version -> nodes before dedup (roots excluded)

    def _new_id(self, kind):
        prefix = KIND_PREFIX.get(kind, "x")
        self._counters[prefix] += 1
        return f"{self.paper_key}#{prefix}{self._counters[prefix]}"

    def add_version(self, version, root):
        """Register one version tree. Returns the number of nodes it contributed (before dedup)."""
        root_id = f"{self.paper_key}#root{version}"
        self.elements[root_id] = ("document", f"Version {version} root")
        seen = Counter()
        count = 0
        stack = [(child, root_id) for child in reversed(root.children)]
        while stack:
            node, parent_id = stack.pop()
            content = canonical(node.content)
            if not content and not node.children and node.kind in LEAF_KINDS:
                continue                          # empty leaf = parser noise
            key = (node.kind, content)
            occurrence = seen[key]
            seen[key] += 1
            element_id = self._ids.get(key + (occurrence,))
            if element_id is None:
                element_id = self._new_id(node.kind)
                self._ids[key + (occurrence,)] = element_id
                self.elements[element_id] = (node.kind, content)
            self.hierarchy.append((version, element_id, parent_id))
            count += 1
            stack.extend((child, element_id) for child in reversed(node.children))
        self.nodes_per_version[version] = count
        return count

    # ------------------------------------------------------------------ output rows
    def element_rows(self):
        return [{"element_id": element_id, "arxiv_id": self.arxiv_id, "kind": kind, "content": content}
                for element_id, (kind, content) in self.elements.items()]

    def hierarchy_rows(self):
        return [{"arxiv_id": self.arxiv_id, "version": version, "element_id": element_id, "parent_id": parent_id}
                for version, element_id, parent_id in self.hierarchy]

    def stats(self):
        roots = len(self.nodes_per_version)
        return {"nodes_before_dedup": sum(self.nodes_per_version.values()),
                "elements_after_dedup": len(self.elements) - roots}

    def validate(self):
        """Return a list of problems (empty = consistent). Used by the tests and the report."""
        problems, parents = [], defaultdict(dict)
        for version, element_id, parent_id in self.hierarchy:
            if element_id not in self.elements:
                problems.append(f"v{version}: unknown element {element_id}")
            if parent_id not in self.elements:
                problems.append(f"v{version}: unknown parent {parent_id}")
            if element_id in parents[version]:
                problems.append(f"v{version}: {element_id} has two parents")
            parents[version][element_id] = parent_id
        for version, mapping in parents.items():
            root_id = f"{self.paper_key}#root{version}"
            for element_id in mapping:                 # every node must climb to its own root
                cursor, hops = element_id, 0
                while cursor != root_id and hops < 1000:
                    cursor, hops = mapping.get(cursor), hops + 1
                    if cursor is None:
                        problems.append(f"v{version}: {element_id} does not reach {root_id}")
                        break
        return problems
