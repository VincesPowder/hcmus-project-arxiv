"""Milestone 2 - LaTeX-aware Sentence Splitter (src/sentences.py).

Splits paragraph text into individual sentences without splitting inside:
- Inline or block mathematics ($...$, \\(...\\), \\begin{equation}...\\end{equation})
- LaTeX commands and arguments (\\cite{...}, \\ref{...}, \\url{...}, \\href{...}{...})
- Abbreviations: i.e., e.g., et al., Fig. 1, Figs. 2, Eq. (3), Sec. 4, Tab. 1,
  vs., cf., Dr., Prof., Mr., Mrs., Ms., Jr., Sr., Inc., Ltd., Co., No., Vol., pp., etc.
- URLs / domains (https://..., arxiv.org/...)
- Decimal numbers (3.14, 0.05) and numbered initials (A. Einstein)
- Preserves \\cite{...} intact inside the sentence where it appears.
"""
import re
from textclean import clean_text

DOT_TOKEN = "\x00DOT\x00"
EXCL_TOKEN = "\x00EXCL\x00"
QUES_TOKEN = "\x00QUES\x00"

# Multi-dot abbreviations (case-insensitive, allowing optional spaces between dots)
MULTI_DOT_ABBREV = re.compile(
    r"\b(?:i\.\s*e\.|e\.\s*g\.|u\.\s*s\.\s*a\.|u\.\s*s\.|u\.\s*k\.|e\.\s*u\.|p\.\s*m\.|a\.\s*m\.|ph\.\s*d\.)",
    re.IGNORECASE,
)

# Academic & standard abbreviations followed by a period
ABBREV_WORDS = re.compile(
    r"\b(?:"
    r"et[\s~]+al|Fig|Figs|Eq|Eqs|Sec|Secs|Subsec|Chap|Chaps|Tab|Tabs|Alg|Algs|"
    r"Thm|Lem|Prop|Cor|Def|Assump|Rem|Ex|App|"
    r"vs|cf|viz|al|ca|approx|resp|wrt|"
    r"Dr|Prof|Mr|Mrs|Ms|Jr|Sr|St|Rev|Hon|"
    r"Inc|Ltd|Co|Corp|Dept|Univ|Assn|"
    r"No|Nos|Vol|Vols|Iss|Ed|Eds|pp|p|ch|art|nr"
    r")\.",
    re.IGNORECASE,
)

# Single uppercase initial before a name: "J. R. R. Tolkien", "A. Einstein"
INITIAL_DOT = re.compile(r"(?<=\b[A-Z])\.(?=\s+[A-Z])")

# Decimal numbers or arXiv IDs: 3.14, 2512.18231, v1.0
DECIMAL_DOT = re.compile(r"(?<=\d)\.(?=\d)")

# Ellipsis: ...
ELLIPSIS_DOT = re.compile(r"\.{2,}")

# URLs in plain text
PLAIN_URL = re.compile(r"https?://[^\s<>\"{}|\\^`\[\]']+|www\.[^\s<>\"{}|\\^`\[\]']+")

# Inline/block math and LaTeX commands with {...} arguments (including \cite{...})
PROTECTED_SPAN = re.compile(
    r"\\begin\{equation\}.*?\\end\{equation\}"
    r"|(?<!\\)\$[^$]+?(?<!\\)\$"
    r"|\\\(.+?\\\)"
    r"|\\\[.+?\\\]"
    r"|\\[A-Za-z@]+\*?(?:\s*\[[^\]]*\])*\s*\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}",
    re.DOTALL,
)


def _mask_dots(segment: str) -> str:
    return (
        segment.replace(".", DOT_TOKEN)
        .replace("!", EXCL_TOKEN)
        .replace("?", QUES_TOKEN)
    )


def _unmask_dots(segment: str) -> str:
    return (
        segment.replace(DOT_TOKEN, ".")
        .replace(EXCL_TOKEN, "!")
        .replace(QUES_TOKEN, "?")
    )


def _mask_url(match: re.Match) -> str:
    url = match.group(0)
    trailing = ""
    while url and url[-1] in ".,;:!?)":
        trailing = url[-1] + trailing
        url = url[:-1]
    return _mask_dots(url) + trailing


def split_sentences(text: str, apply_clean: bool = True) -> list[str]:
    """Split a LaTeX paragraph/block into cleaned sentences while preserving \\cite{} and math."""
    if not text or not text.strip():
        return []

    work = clean_text(text) if apply_clean else text.strip()
    if not work:
        return []

    # 1. Mask dots inside math ($...$), \begin{equation}...\end{equation}, \cite{...}, etc.
    work = PROTECTED_SPAN.sub(lambda m: _mask_dots(m.group(0)), work)

    # 2. Mask dots inside plain URLs
    work = PLAIN_URL.sub(_mask_url, work)

    # 3. Mask ellipsis (...) and decimal numbers (3.14)
    work = ELLIPSIS_DOT.sub(lambda m: _mask_dots(m.group(0)), work)
    work = DECIMAL_DOT.sub(DOT_TOKEN, work)

    # 4. Mask multi-dot abbreviations (i.e., e.g.) and academic abbreviations (et al., Fig. 1, etc.)
    work = MULTI_DOT_ABBREV.sub(lambda m: _mask_dots(m.group(0)), work)
    work = ABBREV_WORDS.sub(lambda m: _mask_dots(m.group(0)), work)

    # 5. Mask single-letter author initials (e.g., "A. Turing")
    work = INITIAL_DOT.sub(DOT_TOKEN, work)

    # 6. Split at true sentence boundaries and around standalone \begin{equation}...\end{equation} blocks
    parts = re.split(
        r"(?:(?<=[.!?])|(?<=[.!?]['\"\)\]])|(?<=\\end\{equation\}))\s+(?=[A-Z0-9$\\(\[\"'`])"
        r"|(?=[\\]begin\{equation\})",
        work,
    )

    sentences = []
    for part in parts:
        restored = _unmask_dots(part)
        restored = " ".join(restored.split())
        if not restored:
            continue
        # Merge back if the previous fragment accidentally ended with an abbreviation
        if sentences and sentences[-1].endswith(("et al.", "Fig.", "Figs.", "Eq.", "Eqs.", "Sec.", "Tab.", "i.e.", "e.g.", "vs.", "cf.", "Dr.", "Prof.")):
            sentences[-1] = f"{sentences[-1]} {restored}"
        else:
            sentences.append(restored)

    return sentences