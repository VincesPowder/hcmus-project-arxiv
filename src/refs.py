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
import unicodedata
from collections import defaultdict

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
    accents = {"'": "\u0301", '"': "\u0308", "^": "\u0302", "~": "\u0303",
               "=": "\u0304", ".": "\u0307", "u": "\u0306", "v": "\u030c",
               "H": "\u030b", "c": "\u0327", "k": "\u0328", "b": "\u0331", "d": "\u0323",
               chr(96): "\u0300"}
    def accent(match):
        return unicodedata.normalize("NFC", match.group(2) + accents[match.group(1)])
    symbolic = r"\\(['" + '"' + "^~=." + chr(96) + r"])\s*\{?([A-Za-zı])\}?"
    t = re.sub(symbolic, accent, text)
    t = re.sub(r"\\([uvHckbd])(?:\s+|\s*\{)([A-Za-z])\}?", accent, t)
    letters = {"aa": "å", "AA": "Å", "o": "ø", "O": "Ø", "l": "ł", "L": "Ł",
               "ss": "ß", "ae": "æ", "AE": "Æ", "oe": "œ", "OE": "Œ"}
    t = re.sub(r"\\(aa|AA|o|O|l|L|ss|ae|AE|oe|OE)\b", lambda m: letters[m.group(1)], t)
    t = t.replace("~", " ").replace("\\ ", " ").replace("\\\\", " ")
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


def _read_value(body: str, pos: int, strings=None) -> tuple[str, int]:
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
            end, depth, escaped = pos + 1, 0, False
            while end < len(body):
                char = body[end]
                if not escaped:
                    if char == '"' and depth == 0:
                        break
                    if char == "{":
                        depth += 1
                    elif char == "}":
                        depth = max(0, depth - 1)
                escaped = char == "\\" and not escaped
                end += 1
            if end < len(body):
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
            value = body[pos:end]
            parts.append((strings or {}).get(value.lower(), value))
            pos = end
        while pos < len(body) and body[pos].isspace():
            pos += 1
        if pos < len(body) and body[pos] == "#":
            pos += 1
            continue
        break
    return "".join(parts), pos


def _scan_entries(text: str, cited_keys=None) -> tuple[list[tuple[str, str, str]], dict[str, str]]:
    """Return ([(entry_type, key, body), ...], {string_name: value})."""
    entries: list[tuple[str, str, str]] = []
    strings: dict[str, str] = {}
    # Index balanced groups once. Re-scanning the entire suffix for each broken
    # entry is quadratic on large, partially malformed shared .bib libraries.
    stack, pairs, escaped = [], {}, False
    for index, char in enumerate(text if cited_keys is None else ""):
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == "{":
            stack.append(index)
        elif char == "}" and stack:
            pairs[stack.pop()] = index
    pos = 0
    while True:
        match = _AT_RE.search(text, pos)
        if match is None:
            break
        entry_type = match.group(1).lower()
        if cited_keys is not None and entry_type not in {"string", "comment", "preamble"}:
            key_match = _KEY_RE.match(text, match.end())
            if not key_match or key_match.group(1).strip() not in cited_keys:
                pos = match.end()
                continue
        open_brace = match.end() - 1
        if cited_keys is None:
            close_brace = pairs.get(open_brace)
        else:
            group = _balanced(text, open_brace)
            close_brace = group[1] - 1 if group else None
        if close_brace is None:
            pos = match.end()                       # malformed: skip this block
            continue
        inner, pos = text[open_brace + 1:close_brace], close_brace + 1
        if entry_type in ("comment", "preamble"):
            continue
        if entry_type == "string":
            definition = _STRING_DEF_RE.match(inner)
            if definition:
                value, _ = _read_value(inner, definition.end(), strings)
                strings[definition.group(1).lower()] = _clean_value(value)
            continue
        key_match = _KEY_RE.match(inner)
        if not key_match:
            continue
        entries.append((entry_type, key_match.group(1).strip(), inner[key_match.end() :]))
    return entries, strings


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


def parse_bib(text: str, cited_keys=None) -> list[dict]:
    """Parse a ``.bib`` file into a list of entry dicts (skips @comment/@preamble/@string)."""
    entries, strings = _scan_entries(text, cited_keys)
    out: list[dict] = []
    for entry_type, key, body in entries:
        fields: dict[str, str] = {}
        pos = 0
        while True:
            match = _FIELD_RE.search(body, pos)
            if not match:
                break
            name = match.group(1).lower()
            value, pos = _read_value(body, match.end(), strings)
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
    re.compile(chr(96) * 2 + r"(?P<t>.*?)(?:''|[”" + '"' + "])", re.DOTALL),
    re.compile(r'"(?P<t>[^"\n]+)"|“(?P<u>[^”]+)”', re.DOTALL),
    re.compile(r"\\(?:emph|textit|textsl)\s*\{(?P<t>(?:[^{}]|\{[^{}]*\})*)\}", re.IGNORECASE),
    re.compile(r"\{\\(?:it|em|sl|itshape|emph)\s*(?P<t>(?:[^{}]|\{[^{}]*\})*)\}", re.IGNORECASE),
]


def _bibitem_authors(raw: str) -> list[str]:
    if not raw:
        return []
    text = re.sub(r"\\href\s*\{[^{}]*\}\{([^{}]*)\}", r"\1", raw)   # \href{url}{Name} -> Name
    text = re.sub(r"\\url\s*\{[^{}]*\}", " ", text)
    text = _clean_value(text)
    text = re.sub(r"\s*\(?\b(?:19|20)\d{2}[a-z]?\)?[.,]?\s*$", "", text)
    text = re.sub(r"\bet\s+al\.?", "", text, flags=re.IGNORECASE)
    text = text.strip(" ,;")
    text = re.sub(r"([^\W\d_]{2,})\.$", r"\1", text)
    # Commas normally separate formatted names. Preserve 'Last, First' pairs
    # when the surname is a single word; BibTeX's author parser normalizes them.
    parts = []
    for group in re.split(r"\s+and\s+|\s*&\s*", text, flags=re.IGNORECASE):
        names = [p.strip(" ,;") for p in group.split(",") if p.strip(" ,;")]
        index = 0
        while index < len(names):
            if (index + 1 < len(names) and re.fullmatch(r"[^\W\d_]+(?:[-'][^\W\d_]+)*", names[index])
                    and names[index + 1]):
                parts.extend(parse_authors(names[index] + ", " + names[index + 1]))
                index += 2
            else:
                parts.append(names[index])
                index += 1
    return [re.sub(r"\s+", " ", p).strip(" ,.") for p in parts if any(c.isalpha() for c in p)]


def _clean_title(raw):
    title = _clean_value(raw).rstrip("., ")
    for left, right in ((chr(96) * 2, "''"), ('"', '"'), ("“", "”")):
        if title.startswith(left) and title.endswith(right):
            return title[len(left):-len(right)].strip().rstrip("., ")
    return title


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

    # biblatex/natbib generated .bbl text can carry explicit field markers.
    fields = {}
    for marker in re.finditer(r"\\bibinfo\s*\{(author|title)\}\s*", content):
        group = _balanced(content, marker.end())
        if group:
            fields.setdefault(marker.group(1), []).append(group[0])
    if fields.get("title"):
        return " and ".join(fields.get("author", [])), _clean_title(fields["title"][0])

    segments = [segment for segment in re.split(r"\\newblock", content) if segment.strip()]
    if len(segments) >= 2:
        first = _clean_value(segments[0])
        if re.search(r"[^\W\d_]{2,}\.\s+(?=[^\W\d_])", first):
            return _split_bibitem(segments[0], year)
        return segments[0], _clean_title(segments[1])

    # A leading year belongs to the author-year style, not the end of a title.
    head = re.sub(r"\s*\((?:19|20)\d{2}[a-z]?\)\.?\s*", ". ", content, count=1)
    boundary = re.search(r"[^\W\d_]{2,}\.\s+(?=[^\W\d_])", head)
    for pattern in _TITLE_PATTERNS:
        found = pattern.search(content)
        if found and (not boundary or found.start() <= boundary.end()):
            title = found.groupdict().get("t") or found.groupdict().get("u")
            return content[:found.start()], _clean_title(title)
    if not boundary:
        # Ambiguous text must not become a fake author containing title/venue.
        return "", ""
    authors = head[: boundary.end()].strip(" .,")
    rest = _clean_value(head[boundary.end() :]).strip()
    sentence = re.search(r"(?<=[^\W\d_])\.\s+(?=[^\W\d_\d]|(?:19|20)\d{2})", rest)
    title = rest[:sentence.start()] if sentence else rest
    title = re.sub(r"[,;.]?\s*\(?\b(?:19|20)\d{2}[a-z]?\)?[.,]?\s*$", "", title)
    return authors, _clean_title(title)


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
            explicit_year = re.search(r"\\bibinfo\s*\{year\}\s*\{((?:19|20)\d{2})\}", content)
            dated = re.sub(r"https?://[^\s{}]+|10\.\d{4,9}/[^\s{}]+", "", content)
            author_year = re.search(r"\(((?:19|20)\d{2})[a-z]?\)", dated)
            years = list(re.finditer(r"\b(19\d{2}|20\d{2})\b", dated))
            year_match = explicit_year or author_year or (years[-1] if years else None)
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
    inline = {entry["citation_key"]: index for index, entry in enumerate(entries)}
    cited, nocite_star = find_cited_keys(gathered.text)
    for bib_text in load_bib_texts(version_dir, gathered.bib_files).values():
        selected = None if bib_scope == "all" or nocite_star else cited
        for entry in parse_bib(bib_text, selected):
            if bib_scope == "all" or nocite_star or entry["citation_key"] in cited:
                index = inline.pop(entry["citation_key"], None)
                if index is None:
                    entries.append(entry)
                else:
                    # A structured .bib entry is better evidence than heuristically
                    # splitting the same key's inline rendering. Retain disagreements.
                    rendered = entries[index]
                    conflicts = {}
                    for field in ("title", "authors", "year", "doi", "journal", "url"):
                        old, new = rendered.get(field), entry.get(field)
                        if not new and old:
                            entry[field] = old
                        elif old and new and old != new:
                            conflicts[field] = [old, new]
                    if conflicts:
                        entry["extraction_conflicts"] = conflicts
                    entries[index] = entry
    return entries


def _norm_title(title: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", title or "").casefold()).strip(" ,.;:!?")


def _doi(value):
    return re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", value or "", flags=re.I).strip().rstrip(".,").lower()


def _name(value):
    return " ".join(re.sub(r"[^\w\s]", "", unicodedata.normalize("NFC", value).casefold()).split())


def _same_author(a, b):
    a, b = _name(a).split(), _name(b).split()
    if a == b:
        return True
    return (len(a) == len(b) and len(a) > 1 and a[-1] == b[-1]
            and all(x == y or (min(len(x), len(y)) == 1 and x[0] == y[0])
                    for x, y in zip(a[:-1], b[:-1])))


def _union_authors(left, right):
    result = list(left or [])
    for author in right or []:
        if not author or _name(author) in {"others", "et al", "etal"}:
            continue
        match = next((i for i, old in enumerate(result) if _same_author(old, author)), None)
        if match is None:
            result.append(author)
        elif len(_name(author)) > len(_name(result[match])):
            result[match] = author
    return result


def unify_bibtex(per_version: dict, *, versioned=False, conflict_policy="raise") -> tuple[list[dict], dict]:
    """Indexed dedup, field union and JSON conflict/source-key provenance.

    DOI is strongest evidence. Equal titles or matching keys with missing title
    are accepted only without contradictory DOI/year/author evidence.
    versioned=True returns {version: {old_key: canonical_key}}. With
    conflict_policy="preserve", ambiguous same-version keys remain unresolved;
    all candidates survive with ambiguous_source_keys provenance. The original
    ambiguous key is never a canonical key, so a join cannot silently choose one.
    """
    if conflict_policy not in {"raise", "preserve"}:
        raise ValueError("Unknown reference conflict policy")
    if conflict_policy == "preserve" and not versioned:
        raise ValueError("Preserving ambiguous references requires versioned=True")
    entries, indexes, used = [], defaultdict(set), set()
    targets = defaultdict(set)
    source_keys = {(e.get("citation_key") or "").strip()
                   for refs in per_version.values() for e in refs}

    def allocate(base, original=""):
        canonical, suffix = base, 2
        while canonical in used or (canonical in source_keys and canonical != original):
            canonical = f"{base}__{suffix}"
            suffix += 1
        used.add(canonical)
        return canonical

    def compatible(a, b):
        da, db = _doi(a.get("doi")), _doi(b.get("doi"))
        if da and db:
            return da == db
        ta, tb = _norm_title(a.get("title")), _norm_title(b.get("title"))
        if ta and tb and ta != tb:
            return False
        if a.get("year") and b.get("year") and a["year"] != b["year"]:
            return False
        aa, ab = a.get("authors", []), b.get("authors", [])
        return not (aa and ab) or any(_same_author(x, y) for x in aa for y in ab)

    for version in sorted(per_version):
        for source in per_version[version]:
            entry = dict(source)
            key = (entry.get("citation_key") or "").strip()
            fingerprints = [("doi", _doi(entry.get("doi"))),
                            ("title", _norm_title(entry.get("title"))), ("key", key)]
            candidate = None
            for field, value in fingerprints:
                if not value:
                    continue
                matches = [i for i in sorted(indexes[(field, value)]) if compatible(entries[i], entry)]
                if len(matches) == 1:
                    candidate = matches[0]
                    break
                if len(matches) > 1:
                    break
            if candidate is None:
                candidate = len(entries)
                canonical = allocate(key or f"reference_{candidate + 1}", key)
                kept = dict(entry, citation_key=canonical, authors=_union_authors([], entry.get("authors")))
                kept["source_keys"] = []
                entries.append(kept)
            else:
                kept = entries[candidate]
                kept["authors"] = _union_authors(kept.get("authors"), entry.get("authors"))
                for field in ("title", "year", "doi", "journal", "url", "entry_type"):
                    old, new = kept.get(field), entry.get(field)
                    if old in (None, "", "misc") and new not in (None, ""):
                        kept[field] = new
                    elif new not in (None, "") and old != new:
                        equivalent = (field == "title" and _norm_title(old) == _norm_title(new)
                                      or field == "doi" and _doi(old) == _doi(new))
                        if not equivalent:
                            values = kept.setdefault("field_conflicts", {}).setdefault(field, [old])
                            if new not in values:
                                values.append(new)
                        if field == "title" and equivalent and len(new) > len(old):
                            kept[field] = new
            provenance = {"version": version, "citation_key": key}
            if provenance not in kept["source_keys"]:
                kept["source_keys"].append(provenance)
            for field, value in fingerprints:
                if value:
                    indexes[(field, value)].add(candidate)
            if key:
                targets[(version, key)].add(candidate)
                if len(targets[(version, key)]) > 1 and conflict_policy == "raise":
                    raise ValueError(f"v{version}: citation key {key!r} identifies conflicting references")

    ambiguous = {(v, k): ids for (v, k), ids in targets.items() if len(ids) > 1}
    blocked_keys = {k for _, k in ambiguous}
    for entry in entries:
        if entry["citation_key"] in blocked_keys:
            entry["citation_key"] = allocate(entry["citation_key"] + "__candidate")
    for (version, key), candidates in sorted(ambiguous.items()):
        for index in sorted(candidates):
            entries[index].setdefault("ambiguous_source_keys", []).append(
                {"version": version, "citation_key": key})
    aliases = {v: {} for v in per_version}
    for (version, key), candidates in targets.items():
        if len(candidates) == 1:
            aliases[version][key] = entries[next(iter(candidates))]["citation_key"]

    if versioned:
        return entries, {v: {k: dest for k, dest in m.items() if k != dest} for v, m in aliases.items()}
    destinations = defaultdict(set)
    for mapping in aliases.values():
        for key, dest in mapping.items():
            destinations[key].add(dest)
    if any(len(values) > 1 for values in destinations.values()):
        raise ValueError("Citation keys reused across versions; use versioned=True")
    return entries, {k: next(iter(values)) for k, values in destinations.items() if k not in values}


def reference_conflicts(entries):
    """Group ambiguous source keys with all candidate canonical references."""
    conflicts = {}
    for entry in entries:
        for source in entry.get("ambiguous_source_keys", []):
            key = (source["version"], source["citation_key"])
            record = conflicts.setdefault(key, {
                **source, "candidate_keys": [], "reason": "conflicting_reference_identity"})
            record["candidate_keys"].append(entry["citation_key"])
    return [conflicts[key] for key in sorted(conflicts)]


_CITE_RE = re.compile(r"(\\[A-Za-z]*cite[A-Za-z]*\*?\s*(?:\[[^\]]*\]\s*)*\{)([^{}]*)(\})", re.IGNORECASE)


def rename_cites(text: str, mapping: dict[str, str]) -> str:
    """Rewrite ``\\cite{old}`` to ``\\cite{chosen}`` using the dedup mapping."""
    if not mapping:
        return text

    def fix(match):
        keys = [mapping.get(key.strip(), key.strip()) for key in match.group(2).split(",")]
        return match.group(1) + ",".join(dict.fromkeys(keys)) + match.group(3)

    return _CITE_RE.sub(fix, text)
