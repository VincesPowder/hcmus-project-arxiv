"""Milestone 2 / B - LaTeX -> hierarchical tree (the real parser).

This module replaces ``dev_stubs.parse_document``.  It implements the parser
module of the Milestone 2 pipeline:

    parse_document(text: str) -> assemble.Node

The input ``text`` is the flattened, comment-free compilation path produced by
``gather.gather_version`` (it may still contain the ``\\documentclass`` preamble
and the ``\\begin{document} ... \\end{document}`` body).  The output is a tree of
``assemble.Node`` whose ``kind`` is one of::

    document | heading | list | item | sentence | formula | figure

and whose ``content`` is already cleaned / normalised text (heading titles,
sentences, ``\\begin{equation} ... \\end{equation}`` for block formulas, and a
``Figure:``/``Table:`` summary for float environments).

The reference section is excluded from the hierarchy (the ``bibtex`` table is the
responsibility of :mod:`refs`); acknowledgements and appendices are included.
"""

from __future__ import annotations

import re
from collections import defaultdict

from assemble import Node

HEADING_LEVELS: dict[str, int] = {
    "part": 10,
    "chapter": 20,
    "section": 30,
    "subsection": 40,
    "subsubsection": 50,
    "paragraph": 60,
    "subparagraph": 70,
}

BLOCK_MATH_ENVIRONMENTS = {
    "equation",
    "equation*",
    "align",
    "align*",
    "alignat",
    "alignat*",
    "gather",
    "gather*",
    "multline",
    "multline*",
    "displaymath",
    "eqnarray",
    "eqnarray*",
    "split",
}

LIST_ENVIRONMENTS = {"itemize", "enumerate"}
FIGURE_ENVIRONMENTS = {"figure", "figure*", "table", "table*"}
ACK_ENVIRONMENTS = {"acknowledgement", "acknowledgements", "acknowledgment", "acknowledgments", "acks"}

FORMATTING_COMMANDS = {
    "textbf",
    "textit",
    "texttt",
    "textrm",
    "textsf",
    "textsc",
    "emph",
    "underline",
    "mbox",
    "mathrm",
    "mathbf",
    "mathit",
    "mathsf",
    "mathtt",
    "operatorname",
}

ABBREVIATIONS = (
    "i.e.",
    "e.g.",
    "et al.",
    "Fig.",
    "Figs.",
    "Eq.",
    "Eqs.",
    "Sec.",
    "Secs.",
    "Dr.",
    "Prof.",
    "Mr.",
    "Mrs.",
    "vs.",
    "cf.",
)

_HEADING_RE = re.compile(
    r"\\(?P<name>part|chapter|section|subsection|subsubsection|paragraph|subparagraph)"
    r"(?P<star>\*)?(?![A-Za-z@])\s*",
    re.IGNORECASE,
)
_BEGIN_RE = re.compile(r"\\begin\s*\{([^{}]+)\}", re.IGNORECASE)


def strip_comments(text: str) -> str:
    """Remove LaTeX comments while preserving escaped percent signs and lines."""

    out: list[str] = []
    for line in text.splitlines(keepends=True):
        cut: int | None = None
        for index, char in enumerate(line):
            if char != "%":
                continue
            backslashes = 0
            cursor = index - 1
            while cursor >= 0 and line[cursor] == "\\":
                backslashes += 1
                cursor -= 1
            if backslashes % 2 == 0:
                cut = index
                break
        if cut is None:
            out.append(line)
        else:
            ending = "\n" if line.endswith("\n") else ""
            out.append(line[:cut] + ending)
    return "".join(out)


def _balanced(text: str, start: int, opener: str, closer: str) -> tuple[str, int] | None:
    """Read one balanced group starting at ``start`` and return (inside, end)."""

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


def _skip_space(text: str, position: int) -> int:
    while position < len(text) and text[position].isspace():
        position += 1
    return position


def _unwrap_command(text: str, command: str) -> str:
    pattern = re.compile(rf"\\{re.escape(command)}\s*\{{")
    while True:
        match = pattern.search(text)
        if not match:
            return text
        group_start = match.end() - 1
        group = _balanced(text, group_start, "{", "}")
        if group is None:
            return text
        inside, end = group
        text = text[: match.start()] + inside + text[end:]


def _protect_fragments(text: str) -> tuple[str, dict[str, str]]:
    """Protect math, URLs and selected semantic commands during cleanup/splitting."""

    protected: dict[str, str] = {}

    def token(value: str) -> str:
        key = f"ZZPROTECTED{len(protected):06d}ZZ"
        protected[key] = value
        return key

    patterns = [
        re.compile(r"https?://[^\s{}]+|www\.[^\s{}]+", re.IGNORECASE),
        re.compile(r"\\(?:url|href)\s*\{[^{}]*\}(?:\s*\{[^{}]*\})?", re.DOTALL),
        re.compile(r"(?<!\\)\$(?!\$)(?:\\.|[^$])*?(?<!\\)\$", re.DOTALL),
        re.compile(r"\\\((?:\\.|[^\\])*?\\\)", re.DOTALL),
        re.compile(r"\\(?:cite\w*|ref|eqref|autoref)\s*\{[^{}]*\}", re.DOTALL),
    ]
    for pattern in patterns:
        text = pattern.sub(lambda match: token(match.group(0)), text)
    return text, protected


def _restore_fragments(text: str, protected: dict[str, str]) -> str:
    for key, value in protected.items():
        if value.startswith("\\(") and value.endswith("\\)"):
            value = "$" + value[2:-2].strip() + "$"
        text = text.replace(key, value)
    return text


def normalize_text(text: str) -> str:
    """Remove non-semantic LaTeX formatting and normalize inline mathematics."""

    text = strip_comments(text)
    text = re.sub(r"\\label\s*\{[^{}]*\}", " ", text)
    text = re.sub(r"\\(?:centering|raggedright|raggedleft|midrule|toprule|bottomrule)\b", " ", text)
    text = re.sub(r"\\(?:vspace|hspace)\*?\s*\{[^{}]*\}", " ", text)
    text = re.sub(r"\\(?:small|footnotesize|scriptsize|tiny|large|Large|LARGE|huge|Huge)\b", " ", text)
    for command in FORMATTING_COMMANDS:
        text = _unwrap_command(text, command)

    text, protected = _protect_fragments(text)
    text = re.sub(r"\\(?:caption|captionof)\*?(?:\s*\[[^\]]*\])?\s*\{", "{", text)
    text = re.sub(r"\\(?:includegraphics|input|include)(?:\s*\[[^\]]*\])?\s*\{[^{}]*\}", " ", text)
    text = re.sub(r"\\begin\s*\{[^{}]+\}(?:\s*\[[^\]]*\])?", " ", text)
    text = re.sub(r"\\end\s*\{[^{}]+\}", " ", text)
    text = re.sub(r"\\(?:hline|cline)\s*(?:\{[^{}]*\})?", " ", text)
    text = text.replace("\\\\", " ")

    # Unwrap remaining simple commands while keeping their argument text.
    simple_command = re.compile(r"\\[A-Za-z@]+\*?\s*\{([^{}]*)\}")
    previous = None
    while previous != text:
        previous = text
        text = simple_command.sub(r"\1", text)

    text = re.sub(r"\\[A-Za-z@]+\*?", " ", text)
    text = text.translate(str.maketrans({"{": " ", "}": " ", "~": " ", "&": " "}))
    text = text.replace("\\%", "%").replace("\\_", "_").replace("\\&", "&")
    text = _restore_fragments(text, protected)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def normalize_math(text: str) -> str:
    """Normalize one display-math body to the required equation environment."""

    text = strip_comments(text)
    text = re.sub(r"\\label\s*\{[^{}]*\}", " ", text)
    text = re.sub(r"\\(?:begin|end)\s*\{(?:aligned|split|gathered)\}", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return f"\\begin{{equation}} {text} \\end{{equation}}"


def split_sentences(text: str) -> list[str]:
    """Split prose without breaking math, commands, URLs, decimals or abbreviations."""

    cleaned = normalize_text(text)
    if not cleaned:
        return []

    protected_text, protected = _protect_fragments(cleaned)
    dot_sentinel = "ZZDOTZZ"
    for abbreviation in ABBREVIATIONS:
        protected_text = re.sub(
            re.escape(abbreviation),
            abbreviation.replace(".", dot_sentinel),
            protected_text,
            flags=re.IGNORECASE,
        )
    protected_text = re.sub(r"(?<=\d)\.(?=\d)", dot_sentinel, protected_text)
    protected_text = re.sub(r"\b([A-Z])\.(?=\s*[A-Z]\.)", rf"\1{dot_sentinel}", protected_text)

    parts = re.split(r"(?<=[.!?])\s+(?=(?:[\"'“‘(\[]?[A-Z0-9]|ZZPROTECTED))", protected_text)
    result: list[str] = []
    for part in parts:
        part = part.replace(dot_sentinel, ".").strip()
        part = _restore_fragments(part, protected)
        if part:
            result.append(re.sub(r"\s+", " ", part))
    return result


def _document_body(text: str) -> str:
    begin = re.search(r"\\begin\s*\{document\}", text, re.IGNORECASE)
    if not begin:
        return text
    end_matches = list(re.finditer(r"\\end\s*\{document\}", text, re.IGNORECASE))
    end = end_matches[-1].start() if end_matches else len(text)
    return text[begin.end() : end]


def _expand_and_remove_simple_macros(text: str) -> str:
    """Apply zero-argument macros and remove their definitions from the body.

    Some submissions put ``\\def`` declarations after ``\\begin{document}`` and
    even redefine structural names (for example ``\\def\\part{\\partial}``).
    Expanding these definitions before structural parsing both follows TeX's
    meaning and prevents a macro name from becoming a false heading.
    """

    definitions: dict[str, str] = {}
    spans: list[tuple[int, int]] = []

    def_commands = re.compile(r"\\(?:def|gdef|edef|xdef)\s*\\([A-Za-z@]+)")
    for match in def_commands.finditer(text):
        position = match.end()
        body_start = text.find("{", position, min(len(text), position + 160))
        if body_start < 0:
            continue
        parameters = text[position:body_start]
        body = _balanced(text, body_start, "{", "}")
        if body is None:
            continue
        if "#" not in parameters:
            definitions[match.group(1)] = body[0]
        spans.append((match.start(), body[1]))

    new_commands = re.compile(r"\\(?:newcommand|renewcommand|providecommand)\*?\s*")
    for match in new_commands.finditer(text):
        position = _skip_space(text, match.end())
        name = ""
        if position < len(text) and text[position] == "{":
            name_group = _balanced(text, position, "{", "}")
            if not name_group:
                continue
            name = name_group[0].strip().lstrip("\\")
            position = _skip_space(text, name_group[1])
        else:
            name_match = re.match(r"\\([A-Za-z@]+)", text[position:])
            if not name_match:
                continue
            name = name_match.group(1)
            position = _skip_space(text, position + name_match.end())

        argument_count = 0
        while position < len(text) and text[position] == "[":
            option = _balanced(text, position, "[", "]")
            if not option:
                break
            if argument_count == 0 and option[0].strip().isdigit():
                argument_count = int(option[0].strip())
            position = _skip_space(text, option[1])
        if position >= len(text) or text[position] != "{":
            continue
        body = _balanced(text, position, "{", "}")
        if not body:
            continue
        if argument_count == 0 and name:
            definitions[name] = body[0]
        spans.append((match.start(), body[1]))

    # Remove definitions before expanding uses, otherwise replacements also
    # rewrite the left-hand side of definitions.
    for start, end in sorted(set(spans), reverse=True):
        text = text[:start] + "\n" + text[end:]

    for _ in range(8):
        previous = text
        for name in sorted(definitions, key=len, reverse=True):
            text = re.sub(rf"\\{re.escape(name)}\b", lambda _: definitions[name], text)
        if text == previous:
            break

    # Declarations that do not create simple textual aliases should never
    # become sentence content.
    text = re.sub(
        r"\\newtheorem\*?\s*\{[^{}]*\}\s*(?:\[[^\]]*\]\s*)?\{[^{}]*\}(?:\s*\[[^\]]*\])?",
        " ",
        text,
    )
    text = re.sub(r"\\(?:newenvironment|renewenvironment)\*?\s*\{[^{}]*\}", " ", text)
    return text


def _find_environment_end(text: str, begin_match: re.Match[str]) -> tuple[int, int] | None:
    env = begin_match.group(1)
    token_re = re.compile(rf"\\(?P<side>begin|end)\s*\{{{re.escape(env)}\}}", re.IGNORECASE)
    depth = 0
    for match in token_re.finditer(text, begin_match.start()):
        if match.group("side").lower() == "begin":
            depth += 1
        else:
            depth -= 1
            if depth == 0:
                return match.start(), match.end()
    return None


def _caption_from_environment(body: str) -> str:
    match = re.search(r"\\caption\*?(?:\s*\[[^\]]*\])?\s*", body, re.IGNORECASE)
    if match:
        position = _skip_space(body, match.end())
        if position < len(body) and body[position] == "{":
            group = _balanced(body, position, "{", "}")
            if group:
                return normalize_text(group[0])
    return ""


def _environment_summary(env: str, body: str) -> str:
    caption = _caption_from_environment(body)
    label_match = re.search(r"\\label\s*\{([^{}]+)\}", body)
    label = label_match.group(1).strip() if label_match else ""
    is_table = env.lower().startswith("table")
    prefix = "Table" if is_table else "Figure"
    content = caption or normalize_text(body)
    if len(content) > 2000:
        content = content[:1999] + "…"
    if label and label not in content:
        content = f"{content} [label: {label}]" if content else f"[label: {label}]"
    return f"{prefix}: {content}" if content else prefix


def _list_items(body: str) -> list[tuple[str, str]]:
    """Split top-level item commands while ignoring items in nested lists."""

    item_starts: list[tuple[int, int, str]] = []
    depth = 0
    token_re = re.compile(r"\\begin\s*\{(itemize|enumerate)\}|\\end\s*\{(itemize|enumerate)\}|\\item\b", re.IGNORECASE)
    for match in token_re.finditer(body):
        token = match.group(0).lower()
        if token.startswith("\\begin"):
            depth += 1
        elif token.startswith("\\end"):
            depth = max(0, depth - 1)
        elif depth == 0:
            position = _skip_space(body, match.end())
            label = ""
            if position < len(body) and body[position] == "[":
                group = _balanced(body, position, "[", "]")
                if group:
                    label, position = group
            item_starts.append((match.start(), position, normalize_text(label)))
    items: list[tuple[str, str]] = []
    for index, (_, content_start, label) in enumerate(item_starts):
        content_end = item_starts[index + 1][0] if index + 1 < len(item_starts) else len(body)
        items.append((label, body[content_start:content_end]))
    return items


def _next_special(text: str, position: int) -> tuple[str, re.Match[str] | int] | None:
    candidates: list[tuple[int, int, str, re.Match[str] | int]] = []
    patterns: list[tuple[int, str, re.Pattern[str]]] = [
        (0, "heading", _HEADING_RE),
        (1, "begin", _BEGIN_RE),
        (2, "appendix", re.compile(r"\\appendix\b", re.IGNORECASE)),
        (3, "bibliography", re.compile(r"\\(?:bibliography|bibliographystyle|printbibliography)\b", re.IGNORECASE)),
        # Negative lookbehind avoids mistaking the LaTeX line-break form
        # ``\\[length]`` for display math ``\[...\]``.
        (4, "display_bracket", re.compile(r"(?<!\\)\\\[")),
    ]
    for priority, name, pattern in patterns:
        match = pattern.search(text, position)
        if match:
            candidates.append((match.start(), priority, name, match))
    display = text.find("$$", position)
    if display >= 0:
        candidates.append((display, 5, "display_dollar", display))
    if not candidates:
        return None
    _, _, name, value = min(candidates, key=lambda item: (item[0], item[1]))
    return name, value


class _Parser:
    def __init__(self, root: Node) -> None:
        self.root = root
        self.heading_stack: list[tuple[int, Node]] = []
        self.current_parent = root
        self.warnings: list[str] = []
        self.in_appendix = False

    def _add_prose(self, parent: Node, text: str) -> None:
        for sentence in split_sentences(text):
            if sentence:
                parent.children.append(Node("sentence", sentence))

    def _add_heading(self, name: str, starred: bool, title: str) -> Node:
        level = HEADING_LEVELS[name.lower()]
        content = normalize_text(title) or f"Untitled {name}"
        while self.heading_stack and self.heading_stack[-1][0] >= level:
            self.heading_stack.pop()
        parent = self.heading_stack[-1][1] if self.heading_stack else self.root
        node = Node("heading", content)
        parent.children.append(node)
        self.heading_stack.append((level, node))
        self.current_parent = node
        return node

    def _ensure_appendix(self) -> None:
        self.in_appendix = True
        if self.heading_stack and re.search(r"\bappendi(?:x|ces)\b", self.heading_stack[-1][1].content, re.IGNORECASE):
            return
        node = Node("heading", "Appendix")
        self.root.children.append(node)
        self.heading_stack = [(HEADING_LEVELS["part"], node)]
        self.current_parent = node

    def _parse_heading(self, text: str, match: re.Match[str]) -> int:
        position = _skip_space(text, match.end())
        if position < len(text) and text[position] == "[":
            short = _balanced(text, position, "[", "]")
            if short:
                position = _skip_space(text, short[1])
        if position >= len(text) or text[position] != "{":
            self.warnings.append(f"heading without a balanced title near offset {match.start()}")
            return match.end()
        title = _balanced(text, position, "{", "}")
        if title is None:
            self.warnings.append(f"unclosed heading title near offset {match.start()}")
            return match.end()
        self._add_heading(match.group("name"), bool(match.group("star")), title[0])
        return title[1]

    def _parse_list(self, env: str, body: str, parent: Node) -> None:
        items = _list_items(body)
        summary = normalize_text(body)
        list_content = f"{env}: {summary[:240]}" if summary else env
        list_node = Node("list", list_content)
        parent.children.append(list_node)
        if not items:
            self._add_prose(list_node, body)
            return
        for ordinal, (label, item_body) in enumerate(items, start=1):
            item_summary = normalize_text(item_body)
            item_content = label or item_summary[:240] or f"Item {ordinal}"
            item_node = Node("item", item_content)
            list_node.children.append(item_node)
            self._parse_fragment(item_body, item_node)

    def _parse_fragment(self, text: str, parent: Node) -> None:
        """Parse leaves and nested environments without modifying heading state."""

        cursor = 0
        while cursor < len(text):
            special = _next_special(text, cursor)
            if special is None:
                self._add_prose(parent, text[cursor:])
                return
            name, value = special
            start = value if isinstance(value, int) else value.start()
            self._add_prose(parent, text[cursor:start])
            if name == "begin" and isinstance(value, re.Match):
                env = value.group(1).strip().lower()
                end = _find_environment_end(text, value)
                if end is None:
                    self.warnings.append(f"unclosed environment {env!r} near offset {start}")
                    cursor = value.end()
                    continue
                body = text[value.end() : end[0]]
                if env in LIST_ENVIRONMENTS:
                    self._parse_list(env, body, parent)
                elif env in FIGURE_ENVIRONMENTS:
                    parent.children.append(Node("figure", _environment_summary(env, body)))
                elif env in BLOCK_MATH_ENVIRONMENTS:
                    parent.children.append(Node("formula", normalize_math(body)))
                elif env == "thebibliography":
                    pass
                else:
                    self._parse_fragment(body, parent)
                cursor = end[1]
            elif name == "display_dollar" and isinstance(value, int):
                end = text.find("$$", value + 2)
                if end < 0:
                    cursor = value + 2
                else:
                    parent.children.append(Node("formula", normalize_math(text[value + 2 : end])))
                    cursor = end + 2
            elif name == "display_bracket" and isinstance(value, re.Match):
                end = text.find("\\]", value.end())
                if end < 0:
                    cursor = value.end()
                else:
                    parent.children.append(Node("formula", normalize_math(text[value.end() : end])))
                    cursor = end + 2
            elif name == "bibliography" and isinstance(value, re.Match):
                cursor = self._skip_command_arguments(text, value.end())
            elif name == "heading" and isinstance(value, re.Match):
                # A heading inside a list/item is prose rather than a global level change.
                position = _skip_space(text, value.end())
                group = _balanced(text, position, "{", "}") if position < len(text) else None
                if group:
                    self._add_prose(parent, group[0])
                    cursor = group[1]
                else:
                    cursor = value.end()
            elif name == "appendix" and isinstance(value, re.Match):
                cursor = value.end()
            else:
                cursor = start + 1

    @staticmethod
    def _skip_command_arguments(text: str, position: int) -> int:
        position = _skip_space(text, position)
        if position < len(text) and text[position] == "[":
            group = _balanced(text, position, "[", "]")
            if group:
                position = _skip_space(text, group[1])
        if position < len(text) and text[position] == "{":
            group = _balanced(text, position, "{", "}")
            if group:
                return group[1]
        return position

    def parse(self, text: str) -> Node:
        cursor = 0
        while cursor < len(text):
            special = _next_special(text, cursor)
            if special is None:
                self._add_prose(self.current_parent, text[cursor:])
                break
            name, value = special
            start = value if isinstance(value, int) else value.start()
            self._add_prose(self.current_parent, text[cursor:start])

            if name == "heading" and isinstance(value, re.Match):
                cursor = self._parse_heading(text, value)
            elif name == "appendix" and isinstance(value, re.Match):
                self._ensure_appendix()
                cursor = value.end()
            elif name == "bibliography" and isinstance(value, re.Match):
                cursor = self._skip_command_arguments(text, value.end())
            elif name == "display_dollar" and isinstance(value, int):
                end = text.find("$$", value + 2)
                if end < 0:
                    self.warnings.append(f"unclosed $$ near offset {value}")
                    cursor = value + 2
                else:
                    self.current_parent.children.append(
                        Node("formula", normalize_math(text[value + 2 : end]))
                    )
                    cursor = end + 2
            elif name == "display_bracket" and isinstance(value, re.Match):
                end = text.find("\\]", value.end())
                if end < 0:
                    self.warnings.append(f"unclosed \\[ near offset {value.start()}")
                    cursor = value.end()
                else:
                    self.current_parent.children.append(
                        Node("formula", normalize_math(text[value.end() : end]))
                    )
                    cursor = end + 2
            elif name == "begin" and isinstance(value, re.Match):
                env = value.group(1).strip().lower()
                end = _find_environment_end(text, value)
                if end is None:
                    self.warnings.append(f"unclosed environment {env!r} near offset {value.start()}")
                    cursor = value.end()
                    continue
                body = text[value.end() : end[0]]
                if env == "thebibliography":
                    pass
                elif env in LIST_ENVIRONMENTS:
                    self._parse_list(env, body, self.current_parent)
                elif env in FIGURE_ENVIRONMENTS:
                    self.current_parent.children.append(
                        Node("figure", _environment_summary(env, body))
                    )
                elif env in BLOCK_MATH_ENVIRONMENTS:
                    self.current_parent.children.append(Node("formula", normalize_math(body)))
                elif env in ACK_ENVIRONMENTS:
                    ack = self._add_heading("section", True, "Acknowledgements")
                    self._parse_fragment(body, ack)
                else:
                    self._parse_fragment(body, self.current_parent)
                cursor = end[1]
            else:
                cursor = start + 1
        return self.root


def parse_document(text: str) -> Node:
    """Parse flattened LaTeX into a tree of :class:`assemble.Node`.

    ``text`` is the flattened, comment-free compilation path (from ``gather``).
    The returned root has ``kind == "document"``.
    """

    body = _document_body(strip_comments(text))
    body = _expand_and_remove_simple_macros(body)
    root = Node("document")
    parser = _Parser(root)
    parser.parse(body)
    return root
