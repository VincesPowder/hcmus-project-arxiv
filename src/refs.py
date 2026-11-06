"""Milestone 2 / C - references (the real reference processor).

This module replaces ``dev_stubs.extract_references`` / ``unify_bibtex`` /
``rename_cites``.  It turns the paper's references into BibTeX-shaped entries
and deduplicates them across versions.

Two sources feed the ``bibtex`` table:

* ``\\bibitem`` entries inside ``\\begin{thebibliography}`` blocks of the
  flattened compilation path (``gathered.text``).  **Only** the compilation
  path is scanned - unused ``.tex`` files (abandoned drafts, duplicated
  sections) are never read for references.
* The ``.bib`` files the paper actually declares (``gathered.bib_files``),
  resolved by :mod:`gather`.  Undeclared ``.bib`` files are ignored.

An entry has the fields::

    citation_key, entry_type, title, authors (list[str]), year (int|None),
    doi, journal, url

Deduplication keeps one key per distinct reference and unionizes the fields of
duplicates.  Entries that carry no comparable information (no title, no key)
are never merged together.
"""

from __future__ import annotations

import re

from gather import find_cited_keys, load_bib_texts

# ---------------------------------------------------------------------------
# text / LaTeX helpers
# ---------------------------------------------------------------------------

def _balanced(text: str, start: int, opener: str = "{", closer: str = "}") -> tuple[str, int] | None:
    if start >= len(text) or text[start] != opener:
        return None
    depth = 0
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return text[start + 1 : index], index + 1
    return None


def _clean_value(text: str) -> str:
    """Strip LaTeX markup from a BibTeX field value (keeps the inner text)."""
    if not text:
        return ""
    t = text.replace("~", " ")
    t = re.sub(r"\\[A-Za-z@]+\*?", " ", t)      # \emph, \url, \bf ... -> drop command
    t = t.replace("{", "").replace("}", "")     # {B}ayesian -> Bayesian
    t = t.replace("\\&", "&").replace("\\%", "%").replace("\\_", "_")
    return re.sub(r"\s+", " ", t).strip()


def parse_authors(raw: str) -> list[str]:
    """Split a BibTeX author list into ``["First Last", ...]`` names."""
    if not raw:
        return []
    text = _clean_value(raw)
    parts = re.split(r"\s+and\s+", text, flags=re.IGNORECASE)
    out: list[str] = []
    for part in parts:
        part = part.strip().rstrip(",").strip()
        if not part or part.lower() in ("others", "and others", "etal", "et al"):
            continue
        if "," in part:                              # "Last, First" -> "First Last"
            last, _, first = part.partition(",")
            last = last.strip()
            first = re.sub(r"\s+", " ", first).strip()
            out.append(f"{first} {last}".strip() if first else last)
        else:
            out.append(part)
    return [name for name in out if name]


# ---------------------------------------------------------------------------
# .bib parsing
# ---------------------------------------------------------------------------

_AT_RE = re.compile(r"@([A-Za-z]+)\s*\{")
_STRING_DEF_RE = re.compile(r"\s*([A-Za-z][A-Za-z0-9_:\-]*)\s*=\s*")
_KEY_RE = re.compile(r"\s*([^,\s]+)\s*,")
_FIELD_RE = re.compile(r"([A-Za-z][A-Za-z0-9_\-]*)\s*=\s*")


def _read_value(body: str, pos: int) -> tuple[str, int]:
    """Read one BibTeX field value (brace / quote / bare / ``#``-concatenation)."""
    parts: list[str] = []
    while True:
        while pos < len(body) and body[pos].isspace():
            pos += 1
        if pos >= len(body):
            break
        char = body[pos]
        if char == "{":
            group = _balanced(body, pos)
            if group is None:
                parts.append(body[pos:])
                return "".join(parts), len(body)
            parts.append(group[0])
            pos = group[1]
        elif char == '"':
            end = body.find('"', pos + 1)
            if end >= 0:
                parts.append(body[pos + 1 : end])
                pos = end + 1
            else:
                parts.append(body[pos + 1 :])
                return "".join(parts), len(body)
        elif char in ",}":
            break
        else:                                        # bare token / number
            end = pos
            while end < len(body) and body[end] not in ",}# \t\n":
                end += 1
            parts.append(body[pos:end])
            pos = end
        while pos < len(body) and body[pos].isspace():
            pos += 1
        if pos < len(body) and body[pos] == "#":
            pos += 1
            continue
        break
    return "".join(parts), pos


def _scan_entries(text: str) -> tuple[list[tuple[str, str, str]], dict[str, str]]:
    """Return ([(entry_type, key, body), ...], {string_name: value})."""
    entries: list[tuple[str, str, str]] = []
    strings: dict[str, str] = {}
    pos = 0
    while True:
        match = _AT_RE.search(text, pos)
        if match is None:
            break
        entry_type = match.group(1).lower()
        open_brace = match.end() - 1
        group = _balanced(text, open_brace)
        if group is None:
            pos = match.end()                       # malformed: skip this block
            continue
        inner, pos = group
        if entry_type in ("comment", "preamble"):
            continue
        if entry_type == "string":
            definition = _STRING_DEF_RE.match(inner)
            if definition:
                value, _ = _read_value(inner, definition.end())
                strings[definition.group(1).lower()] = _clean_value(value)
            continue
        key_match = _KEY_RE.match(inner)
        if not key_match:
            continue
        entries.append((entry_type, key_match.group(1).strip(), inner[key_match.end() :]))
    return entries, strings


def _expand_strings(value: str, strings: dict[str, str]) -> str:
    for name, replacement in strings.items():
        value = re.sub(rf"\b{re.escape(name)}\b", replacement, value)
    return value


def _entry_from_fields(key: str, entry_type: str, fields: dict[str, str]) -> dict:
    year_raw = fields.get("year", "").strip()
    year_match = re.search(r"(19\d{2}|20\d{2})", year_raw)
    year = int(year_match.group(1)) if year_match else None

    url = fields.get("url", "")
    if not url:
        howpublished = fields.get("howpublished", "")
        url_match = re.search(r"https?://\S+", howpublished)
        if url_match:
            url = url_match.group(0)

    return {
        "citation_key": key,
        "entry_type": entry_type,
        "title": fields.get("title", "") or "",
        "authors": parse_authors(fields.get("author", "") or fields.get("editor", "")),
        "year": year,
        "doi": fields.get("doi", "") or "",
        "journal": fields.get("journal", "") or fields.get("booktitle", "") or fields.get("publisher", "") or "",
        "url": url,
    }


def parse_bib(text: str) -> list[dict]:
    """Parse a ``.bib`` file into a list of entry dicts (skips @comment/@preamble/@string)."""
    entries, strings = _scan_entries(text)
    out: list[dict] = []
    for entry_type, key, body in entries:
        fields: dict[str, str] = {}
        pos = 0
        while True:
            match = _FIELD_RE.search(body, pos)
            if not match:
                break
            name = match.group(1).lower()
            value, pos = _read_value(body, match.end())
            value = _expand_strings(value, strings)
            if name not in fields:
                fields[name] = _clean_value(value)
        out.append(_entry_from_fields(key, entry_type, fields))
    return out


# ---------------------------------------------------------------------------
# \bibitem parsing
# ---------------------------------------------------------------------------

_BIBITEM_RE = re.compile(
    r"\\bibitem(?:\s*\[([^\]]*)\])?\s*\{([^}]+)\}(.*?)(?=\\bibitem|\\end\{thebibliography\})",
    re.DOTALL | re.IGNORECASE,
)
_TITLE_PATTERNS = [
    re.compile(r"``(?P<t>[^']*)''", re.DOTALL),
    re.compile(r"[\u2018\u2019']\s*(?P<t>.{4,}?)\s*[\u2018\u2019']", re.DOTALL),
    re.compile(r"\\(?:emph|textit|textsl)\s*\{(?P<t>(?:[^{}]|\{[^{}]*\})*)\}", re.IGNORECASE),
    re.compile(r"\{\\(?:it|em|sl|itshape|emph)\s*(?P<t>(?:[^{}]|\{[^{}]*\})*)\}", re.IGNORECASE),
]


def _bibitem_authors(raw: str) -> list[str]:
    if not raw:
        return []
    text = re.sub(r"\\href\s*\{[^{}]*\}\{([^{}]*)\}", r"\1", raw)   # \href{url}{Name} -> Name
    text = re.sub(r"\\url\s*\{[^{}]*\}", " ", text)
    text = _clean_value(text)
    text = re.sub(r",?\s*$", "", text)
    parts = re.split(r",\s*|\s+and\s+", text, flags=re.IGNORECASE)
    return [re.sub(r"\s+", " ", p).strip(" ,.") for p in parts if re.sub(r"\s+", " ", p).strip(" ,.")]


def _split_bibitem(content: str, year: int | None) -> tuple[str, str]:
    """Split a ``\\bibitem`` body into ``(authors_text, title)``.

    Tries, in order:
      1. ``\\newblock`` separators (very common on arXiv: authors / title / venue);
      2. typographic title markup (quotes, ``\\emph``, ``{\\it ...}``);
      3. the first sentence break after a *lowercase* letter - ``"A. Vaswani"``
         initials are skipped, so ``"...Shazeer. Attention is..."`` splits at the
         real author/title boundary.
    """
    content = re.sub(r"\\href\s*\{[^{}]*\}\{([^{}]*)\}", r"\1", content)
    content = re.sub(r"\\url\s*\{[^{}]*\}", " ", content)

    segments = [segment for segment in re.split(r"\\newblock", content) if segment.strip()]
    if len(segments) >= 2:
        return segments[0], _clean_value(segments[1]).rstrip("., ")

    for pattern in _TITLE_PATTERNS:
        found = pattern.search(content)
        if found:
            return content[: found.start()], _clean_value(found.group("t")).rstrip("., ")

    head = content[: content.find(str(year))] if year is not None else content
    boundary = re.search(r"[a-z]\.\s+(?=[A-Z])", head)
    if not boundary:
        return content, ""
    authors = head[: boundary.start() + 1]
    rest = head[boundary.end() :].strip()
    sentence = re.search(r"[a-z]\.\s+(?=[A-Z])", rest)
    title = rest[: sentence.end()] if sentence else rest
    return authors, _clean_value(title).rstrip("., ")


def extract_bibitems(text: str) -> list[dict]:
    """Extract ``\\bibitem`` entries from the flattened compilation path.

    Heuristics turn the free-form text into BibTeX fields: the first 4-digit
    year, the authors before the title, a quoted/emphasized title (or a plain
    sentence after the author list), and any ``10.*`` DOI or ``http(s)`` URL.
    ``entry_type`` stays ``"misc"``.
    """
    entries: list[dict] = []
    for block in re.finditer(
        r"\\begin\s*\{thebibliography\}.*?\\end\s*\{thebibliography\}",
        text,
        re.DOTALL | re.IGNORECASE,
    ):
        for match in _BIBITEM_RE.finditer(block.group(0)):
            key = match.group(2).strip()
            content = match.group(3)
            year_match = re.search(r"\b(19\d{2}|20\d{2})\b", content)
            year = int(year_match.group(1)) if year_match else None

            authors_text, title = _split_bibitem(content, year)

            doi_match = re.search(r"10\.\d{4,9}/[^\s,}\]]+", content)
            url_match = re.search(r"https?://[^\s,}\]]+", content)

            entries.append({
                "citation_key": key,
                "entry_type": "misc",
                "title": title,
                "authors": _bibitem_authors(authors_text),
                "year": year,
                "doi": doi_match.group(0).rstrip(".,") if doi_match else "",
                "journal": "",
                "url": url_match.group(0).rstrip(".,") if url_match else "",
            })
    return entries


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------

def extract_references(gathered, version_dir, bib_scope: str = "cited") -> list[dict]:
    """All references of one version: ``\\bibitem`` + the declared ``.bib`` files.

    ``\\bibitem`` entries are always kept (the ``thebibliography`` block *is*
    the paper's reference list).  From the ``.bib`` files, ``bib_scope="cited"``
    keeps only the entries whose key is ``\\cite``'d (or everything when the
    paper uses ``\\nocite{*}``); ``bib_scope="all"`` keeps every entry.
    """
    entries = extract_bibitems(gathered.text)
    cited, nocite_star = find_cited_keys(gathered.text)
    for bib_text in load_bib_texts(version_dir, gathered.bib_files).values():
        for entry in parse_bib(bib_text):
            if bib_scope == "all" or nocite_star or entry["citation_key"] in cited:
                entries.append(entry)
    return entries


def _norm_title(title: str) -> str:
    return re.sub(r"\s+", " ", (title or "").lower()).strip(" ,.;:!?")


def unify_bibtex(per_version: dict) -> tuple[list[dict], dict[str, str]]:
    """Deduplicate reference entries across versions.

    Returns ``(unified_entries, {dropped_key: chosen_key})``.  Entries are
    grouped by normalized title, or by citation key when the title is missing.
    Entries with neither a title nor a key are never merged.  Fields are
    unionized: longest title and longest author list, earliest non-null year
    and first non-null doi/journal/url.
    """
    chosen: dict[str, dict] = {}
    order: list[str] = []
    mapping: dict[str, str] = {}

    for version in sorted(per_version):
        for entry in per_version[version]:
            title = _norm_title(entry.get("title") or "")
            key = (entry.get("citation_key") or "").strip().lower()
            if title:
                fingerprint = "title:" + title
            elif key:
                fingerprint = "key:" + key
            else:
                # Nothing to compare -> treat as its own reference.
                fingerprint = f"unique:{len(order)}:v{version}"

            if fingerprint in chosen:
                kept = chosen[fingerprint]
                if len(entry.get("title") or "") > len(kept.get("title") or ""):
                    kept["title"] = entry["title"]
                if len(entry.get("authors") or []) > len(kept.get("authors") or []):
                    kept["authors"] = entry["authors"]
            else:
                kept = dict(entry)
                chosen[fingerprint] = kept
                order.append(fingerprint)

            for field, value in entry.items():
                if field in ("title", "authors"):
                    continue
                if kept.get(field) in (None, "", []) and value not in (None, "", []):
                    kept[field] = value
            if entry.get("citation_key") and entry["citation_key"] != kept["citation_key"]:
                mapping[entry["citation_key"]] = kept["citation_key"]

    return [chosen[fingerprint] for fingerprint in order], mapping


_CITE_RE = re.compile(r"(\\cite[a-z]*\*?(?:\[[^\]]*\])*\{)([^{}]*)(\})", re.IGNORECASE)


def rename_cites(text: str, mapping: dict[str, str]) -> str:
    """Rewrite ``\\cite{old}`` to ``\\cite{chosen}`` using the dedup mapping."""
    if not mapping:
        return text

    def fix(match):
        keys = [mapping.get(key.strip(), key.strip()) for key in match.group(2).split(",")]
        return match.group(1) + ",".join(dict.fromkeys(keys)) + match.group(3)

    return _CITE_RE.sub(fix, text)
