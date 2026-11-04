"""Milestone 2 - Text cleanup & math normalization (src/textclean.py).

Responsibilities:
1. Strip purely presentational/formatting LaTeX commands without semantic meaning:
   \\centering, placement specifiers [htpb]/[t!]/[H], booktabs/table rules (\\toprule,
   \\midrule, \\bottomrule, \\hline, \\cline{...}), spacing commands (\\vspace, \\hspace,
   \\noindent, \\smallskip, \\medskip, \\bigskip, \\newpage, \\clearpage, etc.).
2. Normalize mathematical expressions:
   - Inline math: \\( ... \\) and \\begin{math}...\\end{math} -> $ ... $
   - Block math: $$ ... $$, \\[ ... \\], \\begin{displaymath}...\\end{displaymath},
     \\begin{equation*}...\\end{equation*} -> \\begin{equation} ... \\end{equation}
3. Preserve all \\cite{...} (and \\citet, \\citep, \\citeauthor, \\citeyear, \\parencite, etc.)
   commands intact inside sentences.
"""
import re

# 1. Presentational / non-semantic commands (no argument)
NO_ARG_FORMAT_CMD = re.compile(
    r"\\(?:"
    r"centering|raggedright|raggedleft|noindent|indent|"
    r"toprule|midrule|bottomrule|hline|vline|hdashline|"
    r"smallskip|medskip|bigskip|vfill|hfill|linebreak|pagebreak|nopagebreak|"
    r"newpage|clearpage|cleardoublepage|allowbreak|sloppy|fussy|"
    r"footnotesize|scriptsize|tiny|small|normalsize|large|Large|LARGE|huge|Huge|"
    r"normalfont|itshape|bfseries|scshape|ttfamily|rmfamily|sffamily|"
    r"protect|relax|strut|null"
    r")(?![A-Za-z@])"
)

# 2. Presentational commands taking {...} argument (supporting up to 2 levels of nested braces)
NESTED_BRACE_ARG = r"\{(?:[^{}]|\{(?:[^{}]|\{[^{}]*\})*\})*\}"
ONE_ARG_DROP_CMD = re.compile(
    r"\\(?:"
    r"vspace\*?|hspace\*?|vskip|hskip|kern|mkern|mskip|"
    r"cline|cmidrule(?:\s*\([^)]*\))?|addlinespace|morecmidrules|"
    r"bibliographystyle|pagenumbering|thispagestyle|pagestyle|"
    r"setlength|addtolength|settowidth"
    r")\s*(?:\[[^\]]*\])?\s*(?:" + NESTED_BRACE_ARG + r"|[+-]?\d+(?:\.\d+)?\s*(?:pt|mm|cm|in|ex|em|bp|dd|pc|sp)\b)?"
)

# Bare TeX dimension arguments after \vskip / \hskip / \vspace without braces
BARE_SKIP_CMD = re.compile(
    r"\\(?:vspace\*?|hspace\*?|vskip|hskip|kern|mkern|mskip)(?![A-Za-z@])"
)

# 3. Placement specifiers after environments or standalone, e.g. [htbp], [t!], [!htb], [H]
PLACEMENT_OPT = re.compile(r"\[\s*[!htbpH\s]+\s*\]")

# 4. Unwrap text-styling wrappers while keeping their inner text: \textbf{a} -> a, \emph{a} -> a
TEXT_STYLE_WRAPPER = re.compile(
    r"\\(?:textbf|textit|textrm|texttt|textsf|textsc|emph|underline|mbox|hbox)\s*\{([^{}]*)\}"
)

# 5. Math normalization patterns
PAREN_INLINE_MATH = re.compile(r"\\\((.+?)\\\)", re.DOTALL)
ENV_INLINE_MATH = re.compile(r"\\begin\s*\{math\}(.+?)\\end\s*\{math\}", re.DOTALL)

BRACKET_BLOCK_MATH = re.compile(r"\\\[(.+?)\\\]", re.DOTALL)
DOLLAR_BLOCK_MATH = re.compile(r"(?<!\\)\$\$(.+?)(?<!\\)\$\$", re.DOTALL)
ENV_BLOCK_MATH = re.compile(
    r"\\begin\s*\{(displaymath|equation\*|align\*?|gather\*?|flalign\*?|multline\*?|eqnarray\*?)\}"
    r"(.+?)"
    r"\\end\s*\{\1\}",
    re.DOTALL,
)


def normalize_inline_math(text: str) -> str:
    """Convert \\(...\\) and \\begin{math}...\\end{math} into $...$."""
    if not text:
        return ""
    for _ in range(3):
        new_text = PAREN_INLINE_MATH.sub(lambda m: f"${m.group(1).strip()}$", text)
        new_text = ENV_INLINE_MATH.sub(lambda m: f"${m.group(1).strip()}$", new_text)
        if new_text == text:
            break
        text = new_text
    # Fallback for any unclosed/stray \( or \)
    text = text.replace(r"\(", "$").replace(r"\)", "$")
    return text


def normalize_block_math(text: str) -> str:
    """Convert $$...$$, \\[...\\], and display math environments to \\begin{equation}...\\end{equation}."""
    if not text:
        return ""
    for _ in range(3):
        new_text = DOLLAR_BLOCK_MATH.sub(
            lambda m: f"\\begin{{equation}}\n{m.group(1).strip()}\n\\end{{equation}}", text
        )
        new_text = BRACKET_BLOCK_MATH.sub(
            lambda m: f"\\begin{{equation}}\n{m.group(1).strip()}\n\\end{{equation}}", new_text
        )
        new_text = ENV_BLOCK_MATH.sub(
            lambda m: f"\\begin{{equation}}\n{m.group(2).strip()}\n\\end{{equation}}", new_text
        )
        if new_text == text:
            break
        text = new_text
    # Clean any stray unmatched \[ or \] or $$
    text = text.replace(r"\[", r"\begin{equation} ").replace(r"\]", r" \end{equation}")
    text = text.replace("$$", "$ $")
    return text


def normalize_math(text: str) -> str:
    """Normalize both block and inline math in a LaTeX string."""
    if not text:
        return ""
    text = normalize_block_math(text)
    text = normalize_inline_math(text)
    return text


def strip_formatting_commands(text: str) -> str:
    """Remove non-semantic formatting commands while preserving math and \\cite{...}."""
    if not text:
        return ""

    # Remove placement options like [htbp], [!t], [H] globally
    text = PLACEMENT_OPT.sub("", text)

    # Remove one-arg spacing/rule commands (\vspace{...}, \cline{...}, etc.) globally
    text = ONE_ARG_DROP_CMD.sub(" ", text)
    text = BARE_SKIP_CMD.sub(" ", text)

    # Remove no-arg formatting commands (\centering, \midrule, \hline, etc.) globally
    text = NO_ARG_FORMAT_CMD.sub(" ", text)

    # Protect math spans before unwrapping text styles and normalizing ~
    protected = []

    def _stash(match):
        idx = len(protected)
        protected.append(match.group(0))
        return f"@@MATH_HOLD_{idx}@@"

    text = re.sub(r"\\begin\{equation\}.*?\\end\{equation\}", _stash, text, flags=re.DOTALL)
    text = re.sub(r"(?<!\\)\$[^$]+?(?<!\\)\$", _stash, text)

    # Repeatedly unwrap nested font styles (\textbf{\emph{word}} -> word)
    for _ in range(3):
        new_text = TEXT_STYLE_WRAPPER.sub(r"\1", text)
        if new_text == text:
            break
        text = new_text

    # Convert LaTeX non-breaking space '~' into normal space
    text = text.replace("~", " ")

    # Restore protected math segments
    for idx, original in enumerate(protected):
        text = text.replace(f"@@MATH_HOLD_{idx}@@", original)

    # Final pass to ensure no formatting macros, placement opts, or adjacent $$ remain
    text = PLACEMENT_OPT.sub("", text)
    text = ONE_ARG_DROP_CMD.sub(" ", text)
    text = BARE_SKIP_CMD.sub(" ", text)
    text = NO_ARG_FORMAT_CMD.sub(" ", text)
    text = text.replace(r"\[", r"\begin{equation} ").replace(r"\]", r" \end{equation}")
    text = text.replace("$$", "$ $")

    # Normalize redundant whitespace
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def clean_text(text: str, unwrap_styles: bool = True) -> str:
    """Full standardization pipeline for an element or paragraph text."""
    if not text:
        return ""
    text = normalize_math(text)
    text = strip_formatting_commands(text)
    return text