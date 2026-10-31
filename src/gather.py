"""Milestone 2 / A1 - multi-file gathering.

Input : a version folder of the M1 corpus (only .tex/.bib files + metadata.json).
Output: GatherResult with the publication root, the files really reachable from it
        (the compilation path) and the flattened text, in reading order, with every
        \\input / \\include replaced by the content of the target file.

Rules (see the M2 statement):
  * root = the .tex file holding \\documentclass (and \\begin{document});
  * follow \\input / \\include transitively; a commented-out command is NOT an include;
  * everything not reachable from the root is an unused auxiliary file -> ignored.
"""
import posixpath
import re
from dataclasses import dataclass, field
from pathlib import Path

MAX_DEPTH = 25                       # guard against pathological include chains
MAX_FLAT_CHARS = 40_000_000          # guard against include bombs

DOCCLASS = re.compile(r"\\document(?:class|style)(?![A-Za-z@])")
BEGIN_DOC = re.compile(r"\\begin\s*\{document\}")
END_DOC = re.compile(r"\\end\s*\{document\}")
COMMENT_ENV = re.compile(r"\\begin\{comment\}.*?\\end\{comment\}", re.DOTALL)

# \input{a}  \include{a}  \subfile{a}   |  bare form  \input a.tex
# (?![A-Za-z@]) is essential: without it \includegraphics{fig} and \inputencoding match.
INCLUDE_RE = re.compile(
    r"\\(?P<cmd>input|include|subfile|subfileinclude)(?![A-Za-z@])\s*"
    r"(?:\{\s*(?P<arg>[^{}]*?)\s*\}|(?P<bare>[^\s{}\\%$]+))")
# \import{dir/}{file}   (package import) - resolved as dir/file
IMPORT_RE = re.compile(
    r"\\(?:sub)?import\*?\s*\{\s*(?P<dir>[^{}]*?)\s*\}\s*\{\s*(?P<file>[^{}]*?)\s*\}")
BIBLIOGRAPHY_RE = re.compile(
    r"\\(?:bibliography|addbibresource)(?![A-Za-z@])\s*(?:\[[^\]]*\])?\s*\{([^{}]*)\}")


@dataclass
class GatherResult:
    status: str                      # ok | no_root | no_tex
    root: str = None                 # relative path of the root file
    text: str = ""                   # flattened, comment-free source of the compilation path
    included: list = field(default_factory=list)     # files on the path, in reading order
    unreachable: list = field(default_factory=list)  # .tex files NOT on the path (ignored)
    missing: list = field(default_factory=list)      # \input targets that do not exist
    warnings: list = field(default_factory=list)
    bib_files: list = field(default_factory=list)    # .bib files (relative paths) the paper really uses
    bib_missing: list = field(default_factory=list)  # names in \bibliography{...} with no file in the corpus
    n_tex: int = 0


# ----------------------------------------------------------------------------- text helpers
def read_text(path):
    """utf-8 first (BOM tolerated), latin-1 as a lossless fallback for old sources."""
    data = Path(path).read_bytes()
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def _comment_start(line):
    """Index of the first UNESCAPED % (even number of backslashes before it), else None."""
    backslashes = 0
    for index, char in enumerate(line):
        if char == "\\":
            backslashes += 1
            continue
        if char == "%" and backslashes % 2 == 0:
            return index
        backslashes = 0
    return None


def strip_comments(text):
    """Remove % comments and \\begin{comment} blocks. Commented-out \\input never survives."""
    kept = []
    for line in text.splitlines():
        if "%" in line:
            start = _comment_start(line)
            if start is not None:
                line = line[:start]
                if not line.strip():       # pure comment line: drop it completely
                    continue
        kept.append(line)
    return COMMENT_ENV.sub("", "\n".join(kept))


# ----------------------------------------------------------------------------- path handling
class _Sources:
    """All .tex files of one version, already comment-stripped, with tolerant lookup."""

    def __init__(self, version_dir):
        self.dir = Path(version_dir)
        self.raw = {}                                     # relpath -> stripped text
        for path in sorted(self.dir.rglob("*")):
            if path.is_file() and path.suffix.lower() == ".tex":
                rel = path.relative_to(self.dir).as_posix()
                self.raw[rel] = strip_comments(read_text(path))
        self.bibs = {path.relative_to(self.dir).as_posix()
                     for path in self.dir.rglob("*") if path.is_file() and path.suffix.lower() == ".bib"}
        self.bib_lower = {rel.lower(): rel for rel in self.bibs}
        self.lower = {rel.lower(): rel for rel in self.raw}
        self.by_name = {}
        for rel in self.raw:
            self.by_name.setdefault(posixpath.basename(rel).lower(), []).append(rel)

    def resolve(self, argument, current_file, root_file):
        """Map an \\input argument to a known file or None.

        LaTeX resolves relative to the *compilation directory* (the root's folder), so that is
        tried first; the including file's folder is the fallback. The .tex suffix is optional,
        case differences are tolerated (authors on macOS/Windows), and a unique basename match
        is the last resort.
        """
        argument = argument.strip().strip('"')
        if not argument:
            return None
        bases = [posixpath.dirname(root_file), posixpath.dirname(current_file)]
        for base in dict.fromkeys(bases):
            joined = posixpath.normpath(posixpath.join(base, argument))
            if joined.startswith(".."):
                continue                                  # never leave the version folder
            for candidate in (joined + ".tex", joined):   # TeX tries name.tex before name
                if candidate in self.raw:
                    return candidate
                if candidate.lower() in self.lower:
                    return self.lower[candidate.lower()]
        names = self.by_name.get(posixpath.basename(argument).lower(), []) \
            or self.by_name.get(posixpath.basename(argument).lower() + ".tex", [])
        return names[0] if len(names) == 1 else None

# \cite, \citep*, \citet[see][p.2]{a,b}, \parencite, \autocite, \nocite ... (any command containing "cite"/"Cite")
CITE_RE = re.compile(r"\\(?P<cmd>[A-Za-z]*[Cc]ite[A-Za-z]*)\*?\s*(?:\[[^\]]*\]\s*){0,2}\{(?P<keys>[^{}]*)\}")


def find_cited_keys(text):
    """Citation keys used in (comment-free, flattened) text -> (set of keys, nocite_star).

    nocite_star is True when the paper has \\nocite{*}: the whole .bib is then really listed.
    """
    keys, nocite_star = set(), False
    for match in CITE_RE.finditer(text):
        for key in match.group("keys").split(","):
            key = key.strip()
            if key == "*" and match.group("cmd") == "nocite":
                nocite_star = True
            elif key and key != "*":
                keys.add(key)
    return keys, nocite_star


def _resolve_bib(sources, name, root_file):
    """Map a \\bibliography{name} entry to an existing .bib file (extension optional) or None."""
    name = name.strip().strip('"')
    if not name:
        return None
    for base in dict.fromkeys([posixpath.dirname(root_file), ""]):
        joined = posixpath.normpath(posixpath.join(base, name))
        if joined.startswith(".."):
            continue
        for candidate in (joined + ".bib", joined):
            if candidate in sources.bibs:
                return candidate
            if candidate.lower() in sources.bib_lower:
                return sources.bib_lower[candidate.lower()]
    wanted = posixpath.basename(name).lower()
    same_name = [rel for rel in sources.bibs
                 if posixpath.basename(rel).lower() in (wanted, wanted + ".bib")]
    return same_name[0] if len(same_name) == 1 else None


def load_bib_texts(version_dir, bib_files):
    """{relative path: text} of the .bib files returned by gather_version (for the reference module)."""
    return {rel: read_text(Path(version_dir) / rel) for rel in bib_files}


def _find_includes(text):
    """Yield (match, argument) for every \\input-like command in already-stripped text."""
    for match in INCLUDE_RE.finditer(text):
        yield match, (match.group("arg") if match.group("arg") is not None else match.group("bare"))


# ----------------------------------------------------------------------------- flattening
def _expand(rel, sources, root_file, stack, state, depth=0):
    """Return the text of `rel` with all includes inlined (depth-first, reading order)."""
    text = sources.raw[rel]
    state["included"].append(rel)
    if rel != root_file and BEGIN_DOC.search(text):       # subfiles: keep only the body
        body_start = BEGIN_DOC.search(text).end()
        end = END_DOC.search(text, body_start)
        text = text[body_start:end.start() if end else len(text)]
    if depth >= MAX_DEPTH:
        state["warnings"].append(f"max include depth reached in {rel}")
        return text

    def replace_import(match):
        target = sources.resolve(posixpath.join(match.group("dir"), match.group("file")), rel, root_file)
        return _inline(match.group(0), target, match.group("dir") + match.group("file"))

    def replace_include(match):
        argument = match.group("arg") if match.group("arg") is not None else match.group("bare")
        # a bare "\input foo" that matches nothing is probably prose, not an include
        target = sources.resolve(argument, rel, root_file)
        if target is None and match.group("arg") is None:
            return match.group(0)
        return _inline(match.group(0), target, argument)

    def _inline(original, target, argument):
        if target is None:
            state["missing"].append(argument)
            return ""
        if target in stack or target == rel:
            state["warnings"].append(f"include cycle: {rel} -> {target}")
            return ""
        if state["size"] > MAX_FLAT_CHARS:
            state["warnings"].append("flattened text too large, includes truncated")
            return ""
        inner = _expand(target, sources, root_file, stack | {rel}, state, depth + 1)
        state["size"] += len(inner)
        return "\n" + inner + "\n"

    text = IMPORT_RE.sub(replace_import, text)
    return INCLUDE_RE.sub(replace_include, text)


def _flatten(root_file, sources):
    state = {"included": [], "missing": [], "warnings": [], "size": 0}
    text = _expand(root_file, sources, root_file, frozenset(), state)
    return text, state


# ----------------------------------------------------------------------------- root detection
def find_root(sources):
    """Pick the publication root among the files that contain \\documentclass.

    Several candidates happen (a copy of the paper, a standalone figure, a response letter).
    Preference: has \\begin{document} > is not included by any other file > flattens to
    the largest text > shallowest path. Returns (root or None, list of candidates).
    """
    candidates = [rel for rel, text in sources.raw.items() if DOCCLASS.search(text)]
    if not candidates:
        return None, []
    with_body = [rel for rel in candidates if BEGIN_DOC.search(sources.raw[rel])] or candidates
    if len(with_body) == 1:
        return with_body[0], candidates

    included_by_someone = set()
    for rel, text in sources.raw.items():
        for _, argument in _find_includes(text):
            target = sources.resolve(argument, rel, rel)
            if target and target != rel:
                included_by_someone.add(target)
    top_level = [rel for rel in with_body if rel not in included_by_someone] or with_body
    best = max(top_level, key=lambda rel: (len(_flatten(rel, sources)[0]), -rel.count("/")))
    return best, candidates


# ----------------------------------------------------------------------------- public API
def gather_version(version_dir):
    """Gather one version folder. Never raises on malformed TeX; see GatherResult.status."""
    sources = _Sources(version_dir)
    if not sources.raw:
        return GatherResult(status="no_tex")
    root, _ = find_root(sources)
    if root is None:
        return GatherResult(status="no_root", n_tex=len(sources.raw),
                            unreachable=sorted(sources.raw))
    text, state = _flatten(root, sources)
    included = list(dict.fromkeys(state["included"]))
    bib_names = []
    for match in BIBLIOGRAPHY_RE.finditer(text):
        bib_names += [name.strip() for name in match.group(1).split(",") if name.strip()]
    bib_files, bib_missing = [], []
    for name in dict.fromkeys(bib_names):
        resolved = _resolve_bib(sources, name, root)
        if resolved:
            bib_files.append(resolved)
        else:
            bib_missing.append(name)
    return GatherResult(
        status="ok", root=root, text=text, included=included,
        unreachable=sorted(set(sources.raw) - set(included)),
        missing=sorted(set(state["missing"])), warnings=state["warnings"],
        bib_files=list(dict.fromkeys(bib_files)), bib_missing=bib_missing, n_tex=len(sources.raw))
