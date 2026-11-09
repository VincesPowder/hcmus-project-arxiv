"""PLACEHOLDERS so that A can run the pipeline end-to-end before B/C finish.

Replace by the real modules (same function names/signatures):
    hier_parser.parse_document(text) -> assemble.Node           (B: hierarchy, leaves, cleanup, math)
    refs.extract_references(gathered, version_dir, bib_scope="cited") -> list[dict]
                                                                (C: \\bibitem in gathered.text AND the .bib files
                                                                 listed in gathered.bib_files -> BibTeX fields)
    refs.unify_bibtex(per_version)   -> (list[dict], dict)      (C: dedup + union fields + rename map)
    refs.rename_cites(text, mapping) -> str                     (C: \\cite{old} -> \\cite{chosen})
"""
import re

from assemble import Node

SECTION = re.compile(r"\\(section|subsection|subsubsection|paragraph)\*?\s*\{([^{}]*)\}")
LEVEL = {"section": 1, "subsection": 2, "subsubsection": 3, "paragraph": 4}
SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")
BIBITEM = re.compile(r"\\bibitem(?:\[[^\]]*\])?\{([^{}]+)\}(.*?)(?=\\bibitem|\\end\{thebibliography\}|\Z)", re.S)
CITE = re.compile(r"(\\cite[a-z]*\*?(?:\[[^\]]*\])*\{)([^{}]*)(\})")


def parse_document(text):
    start = re.search(r"\\begin\{document\}", text)
    body = text[start.end():] if start else text
    body = re.split(r"\\end\{document\}|\\begin\{thebibliography\}", body)[0]
    root = Node("document")
    stack = [(0, root)]
    position = 0
    for match in list(SECTION.finditer(body)) + [None]:
        chunk = body[position:match.start() if match else len(body)]
        for sentence in SENTENCE_SPLIT.split(" ".join(chunk.split())):
            if sentence.strip():
                stack[-1][1].children.append(Node("sentence", sentence.strip()))
        if match is None:
            break
        level = LEVEL[match.group(1)]
        while stack[-1][0] >= level:
            stack.pop()
        heading = Node("heading", match.group(2))
        stack[-1][1].children.append(heading)
        stack.append((level, heading))
        position = match.end()
    return root


def extract_bibitems(text):
    return [{"citation_key": key, "entry_type": "misc", "title": " ".join(body.split())[:120],
             "authors": [], "year": None, "doi": None, "journal": None, "url": None}
            for key, body in BIBITEM.findall(text)]


def parse_bib(text):
    """Tiny brace-balanced .bib reader (placeholder; C should write the real one)."""
    entries = []
    for head in re.finditer(r"@(\w+)\s*\{\s*([^,\s{}]+)\s*,", text):
        entry_type = head.group(1).lower()
        if entry_type in ("comment", "string", "preamble"):
            continue
        depth, position = 1, head.end()
        while position < len(text) and depth:
            depth += {"{": 1, "}": -1}.get(text[position], 0)
            position += 1
        body, fields = text[head.end():position - 1], {}
        for field_match in re.finditer(r"(\w+)\s*=\s*", body):
            start, value = field_match.end(), ""
            if start < len(body) and body[start] == "{":
                depth, end = 1, start + 1
                while end < len(body) and depth:
                    depth += {"{": 1, "}": -1}.get(body[end], 0)
                    end += 1
                value = body[start + 1:end - 1]
            elif start < len(body) and body[start] == '"':
                end = body.find('"', start + 1)
                value = body[start + 1:end if end > 0 else len(body)]
            else:
                value = re.match(r"[^,\s}]*", body[start:]).group(0)
            fields.setdefault(field_match.group(1).lower(), " ".join(value.replace("{", "").replace("}", "").split()))
        year = fields.get("year", "")
        entries.append({"citation_key": head.group(2), "entry_type": entry_type,
                        "title": fields.get("title"), "authors": [a.strip() for a in fields.get("author", "").split(" and ") if a.strip()],
                        "year": int(year) if year.isdigit() else None, "doi": fields.get("doi"),
                        "journal": fields.get("journal") or fields.get("booktitle"), "url": fields.get("url")})
    return entries


def extract_references(gathered, version_dir, bib_scope="cited"):
    """Entries from \\bibitem in the flattened text plus the .bib files the paper declares.

    bib_scope="cited": from .bib files keep only entries whose key is \\cite'd (or all when \\nocite{*});
                       shared collaboration libraries of thousands of entries would otherwise flood the table.
    bib_scope="all"  : keep every entry of the declared .bib files.
    \\bibitem entries are always kept: the thebibliography block IS the paper's reference list.
    """
    from gather import find_cited_keys, load_bib_texts
    entries = extract_bibitems(gathered.text)
    cited, nocite_star = find_cited_keys(gathered.text)
    for text in load_bib_texts(version_dir, gathered.bib_files).values():
        for entry in parse_bib(text):
            if bib_scope == "all" or nocite_star or entry["citation_key"] in cited:
                entries.append(entry)
    return entries


def unify_bibtex(per_version):
    """per_version: {version: [entry dicts]} -> (unified entries, {dropped_key: chosen_key})."""
    chosen, mapping = {}, {}
    for version in sorted(per_version):
        for entry in per_version[version]:
            fingerprint = re.sub(r"\W+", "", (entry["title"] or "").lower())
            kept = chosen.setdefault(fingerprint, dict(entry))
            for field_name, value in entry.items():            # union of the fields
                if kept.get(field_name) in (None, [], "") and value not in (None, [], ""):
                    kept[field_name] = value
            if entry["citation_key"] != kept["citation_key"]:
                mapping[entry["citation_key"]] = kept["citation_key"]
    return list(chosen.values()), mapping


def rename_cites(text, mapping):
    if not mapping:
        return text
    def fix(match):
        keys = [mapping.get(key.strip(), key.strip()) for key in match.group(2).split(",")]
        return match.group(1) + ",".join(dict.fromkeys(keys)) + match.group(3)
    return CITE.sub(fix, text)
