"""Milestone 3 - text cleaning for reference matching.

Everything that turns a messy BibTeX field or a Semantic Scholar field into a comparable form lives
here, so that the labeller (label_auto.py), the features (features.py) and the split (split.py) all
clean text in exactly the same way.

Pure standard library: no pretrained model, no external API.

Main entry points
    normalize_text(s)            lowercase, LaTeX/accents/punctuation removed, whitespace collapsed
    title_tokens(s)              content words of a title (stop words removed, light stemming)
    title_key(s)                 letters+digits only, used for exact title equality
    author_surnames(names)       list of normalized surnames ("Ashish Vaswani" -> "vaswani")
    extract_arxiv_ids(text)      canonical arXiv ids found in free text / url / doi / journal fields
    canonical_arxiv_id(s)        "arXiv:1706.03762v5" -> "1706.03762"; "hep-th/9901001v2" -> "hep-th/9901001"
    arxiv_id_year(aid)           publication year encoded in an arXiv id
    parse_year(s)                year from "2017-06-12", "2017", 2017, ... (None if unusable)
"""
from __future__ import annotations

import re
import unicodedata

# --------------------------------------------------------------------------- stop words
# Small English list: function words plus words that start many titles without carrying meaning.
STOPWORDS = frozenset("""
a an the of in on for to and or nor but with without by from at as is are was were be been being via
using use used into onto over under upon about between among through during within across against
its their this that these those it we our us you your i me my he she they them his her
not no than then so such can may might will would should could do does did has have had
vs versus toward towards new
""".split())

# Particles that belong to a surname ("van der Maaten"): we keep only the last token of the surname,
# but never mistake a particle for the whole surname.
_PARTICLES = frozenset({"van", "von", "de", "der", "den", "di", "da", "du", "la", "le", "bin", "ben",
                        "al", "el", "dos", "das", "del", "della", "ter", "ten", "st"})

# Characters NFKD does not decompose.
_SPECIAL = {"ß": "ss", "ø": "o", "Ø": "O", "đ": "d", "Đ": "D", "ł": "l", "Ł": "L", "æ": "ae", "Æ": "AE",
            "œ": "oe", "Œ": "OE", "ı": "i", "ð": "d", "þ": "th"}
_GREEK = {"α": "alpha", "β": "beta", "γ": "gamma", "δ": "delta", "ε": "epsilon", "ζ": "zeta", "η": "eta",
          "θ": "theta", "κ": "kappa", "λ": "lambda", "μ": "mu", "ν": "nu", "ξ": "xi", "π": "pi",
          "ρ": "rho", "σ": "sigma", "τ": "tau", "φ": "phi", "χ": "chi", "ψ": "psi", "ω": "omega",
          "Δ": "delta", "Σ": "sigma", "Ω": "omega", "Φ": "phi", "Π": "pi", "Λ": "lambda"}

# TeX accent commands: \"o  \'{e}  {\^a}  \c{c} ...  -> base letter
_TEX_ACCENT = re.compile(r"""\\[`'^"~=.uvHckrbdt]\s*\{?\s*([A-Za-z])\s*\}?""")
_TEX_LETTER = {r"\ss": "ss", r"\o": "o", r"\O": "O", r"\l": "l", r"\L": "L", r"\i": "i", r"\j": "j",
               r"\ae": "ae", r"\AE": "AE", r"\oe": "oe", r"\OE": "OE", r"\aa": "a", r"\AA": "A"}
_TEX_LETTER_RE = re.compile("|".join(re.escape(k) + r"(?![A-Za-z])" for k in
                                     sorted(_TEX_LETTER, key=len, reverse=True)))
_TEX_WRAPPER = re.compile(r"\\(?:emph|textit|textbf|textsl|textrm|texttt|textsc|mathrm|mathit|mathbf|"
                          r"mathcal|text|bf|it|em|sl|sc|rm|tt)\b\s*")
_TEX_COMMAND = re.compile(r"\\[A-Za-z@]+\*?")
_URL = re.compile(r"https?://\S+", re.IGNORECASE)


# --------------------------------------------------------------------------- generic cleaning
def fold_accents(text: str) -> str:
    """'Müller' -> 'Muller', 'Łukasz' -> 'Lukasz', 'α' -> 'alpha'. Keeps case."""
    out = []
    for char in text:
        if char in _SPECIAL:
            out.append(_SPECIAL[char])
        elif char in _GREEK:
            out.append(f" {_GREEK[char]} ")
        else:
            out.append(char)
    decomposed = unicodedata.normalize("NFKD", "".join(out))
    return "".join(c for c in decomposed if not unicodedata.combining(c))


_GREEK_NAMES = {name.lower() for name in _GREEK.values()}


def _command_replacement(match) -> str:
    """A Greek-letter command is a word of the title (\\alpha-divergence), any other command is markup."""
    name = match.group(0).lstrip("\\").rstrip("*")
    return f" {name.lower()} " if name.lower() in _GREEK_NAMES else " "


def strip_latex(text: str) -> str:
    """Remove LaTeX residue that survives in BibTeX fields; keeps the words."""
    if not text:
        return ""
    t = str(text)
    t = _TEX_LETTER_RE.sub(lambda m: _TEX_LETTER[m.group(0).rstrip()], t)
    t = _TEX_ACCENT.sub(lambda m: m.group(1), t)
    t = t.replace("\\&", "&").replace("\\%", "%").replace("\\_", "_").replace("\\$", "$")
    t = _TEX_WRAPPER.sub(" ", t)
    t = _TEX_COMMAND.sub(_command_replacement, t)   # \alpha keeps its name, \url/\newblock/... vanish
    t = t.replace("$", " ").replace("{", "").replace("}", "").replace("~", " ")
    return t


def normalize_text(text: str) -> str:
    """Lowercase, accent-free, LaTeX-free, punctuation -> space, whitespace collapsed."""
    if not text:
        return ""
    t = _URL.sub(" ", strip_latex(text))
    t = fold_accents(t).lower()
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return t.strip()


def _stem(token: str) -> str:
    """Very light plural stripping: 'networks' -> 'network', 'analysis'/'class' unchanged."""
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 4 and token.endswith(("sses", "shes", "ches", "xes")):
        return token[:-2]
    if len(token) > 3 and token.endswith("s") and not token.endswith(("ss", "us", "is")):
        return token[:-1]
    return token


# --------------------------------------------------------------------------- titles
def title_tokens(title: str, remove_stopwords: bool = True, stem: bool = True) -> list[str]:
    """Content tokens of a title, in order. Numbers are kept (e.g. 'bert', '3d', '2')."""
    tokens = normalize_text(title).split()
    if remove_stopwords:
        tokens = [t for t in tokens if t not in STOPWORDS]
    if stem:
        tokens = [_stem(t) for t in tokens]
    return tokens


def title_key(title: str) -> str:
    """Letters and digits only. Two titles are 'exactly equal' when their keys are equal."""
    return normalize_text(title).replace(" ", "")


def char_ngrams(text: str, n: int = 3) -> set[str]:
    """Character n-grams of the normalized text (spaces kept inside, so word boundaries count)."""
    s = f" {normalize_text(text)} "
    if len(s) <= n:
        return {s} if s.strip() else set()
    return {s[i:i + n] for i in range(len(s) - n + 1)}


# --------------------------------------------------------------------------- authors
def _is_initial_token(token_original: str) -> bool:
    letters = re.sub(r"[^A-Za-z]", "", token_original)
    if not letters:
        return True
    if len(letters) == 1:
        return True
    return letters.isupper() and len(letters) <= 3 and "." not in token_original.strip(".") and \
        len(token_original.strip(".")) == len(letters)


def surname(name: str) -> str:
    """Normalized surname of one author string.

    'Ashish Vaswani' -> 'vaswani'; 'A. Vaswani' -> 'vaswani'; 'Vaswani A.' -> 'vaswani';
    'Laurens van der Maaten' -> 'maaten'; 'Z. Li' -> 'li'; '' / 'et al.' / initials only -> ''.
    """
    if not name:
        return ""
    cleaned = fold_accents(strip_latex(name))
    cleaned = re.sub(r"[.,;]", " ", cleaned)
    original_tokens = [t for t in cleaned.split() if t]
    if not original_tokens:
        return ""
    lowered = [re.sub(r"[^a-z0-9'\-]", "", t.lower()) for t in original_tokens]
    if lowered[0] in ("et", "others", "etal") or (len(lowered) == 2 and lowered == ["et", "al"]):
        return ""
    # drop trailing initials: "Vaswani A", "Smith AB"
    end = len(original_tokens)
    while end > 1 and _is_initial_token(original_tokens[end - 1]) and not _is_initial_token(original_tokens[0]):
        end -= 1
    candidates = [t for t in lowered[:end] if t]
    if not candidates:
        return ""
    last = candidates[-1]
    if last in _PARTICLES and len(candidates) > 1:       # "Ludwig van" (malformed) -> previous token
        last = candidates[-2]
    if len(last) <= 1:                                   # a lone initial is not a surname
        return ""
    return last.replace("'", "").replace("-", "")


def split_authors(names) -> list[str]:
    """Normalize the container: list/tuple/ndarray/str/None -> list of author strings,
    splitting the 'A and B' / 'A; B' strings that sloppy \\bibitem parsing leaves in one item."""
    if names is None:
        return []
    if isinstance(names, str):
        names = [names]
    out = []
    for item in list(names):
        if item is None:
            continue
        for part in re.split(r"\s+and\s+|;|\s*&\s*", str(item)):
            part = part.strip()
            if part:
                out.append(part)
    return out


def author_surnames(names) -> list[str]:
    """Ordered list of non-empty normalized surnames (duplicates kept, 'et al.' dropped)."""
    return [s for s in (surname(n) for n in split_authors(names)) if s]


def author_tokens(names) -> set[str]:
    """Bag of name words (len > 2) from all authors: robust when the author list was split badly."""
    bag = set()
    for name in split_authors(names):
        for token in normalize_text(name).split():
            if len(token) > 2 and token not in ("and", "al", "etal", "others"):
                bag.add(token)
    return bag


# --------------------------------------------------------------------------- arXiv ids
_YYMM_NEW = r"(?:0[7-9]|1\d|2\d)(?:0[1-9]|1[0-2])"           # 0704 ... 2912
_NEW_ID = re.compile(r"(?<![0-9])(?<![0-9]\.)(" + _YYMM_NEW + r")\.(\d{4,5})(?:v\d+)?(?![0-9])")
_OLD_ARCHIVES = ("astro-ph|cond-mat|gr-qc|hep-ex|hep-lat|hep-ph|hep-th|math-ph|nlin|nucl-ex|nucl-th|"
                 "physics|quant-ph|math|cs|q-bio|q-fin|stat|chao-dyn|solv-int|adap-org|alg-geom|"
                 "comp-gas|dg-ga|funct-an|patt-sol|q-alg|supr-con|atom-ph|chem-ph|cmp-lg|mtrl-th|"
                 "plasm-ph|ao-sci|bayes-an|acc-phys")
_OLD_ID = re.compile(r"(?<![a-z0-9\-])(" + _OLD_ARCHIVES + r")(?:\.[A-Za-z]{2})?\s*/\s*(\d{7})(?:v\d+)?(?![0-9])",
                     re.IGNORECASE)
_ARXIV_CONTEXT = re.compile(r"arxiv|corr\b|abs/|doi\.org/10\.48550|10\.48550", re.IGNORECASE)
_PREFIX = re.compile(r"^\s*(?:https?://(?:www\.)?arxiv\.org/(?:abs|pdf)/|arxiv\s*:\s*|abs/|pdf/)", re.IGNORECASE)


def canonical_arxiv_id(value: str | None) -> str | None:
    """Canonical form used everywhere: no prefix, no version, old ids lowercase without subject class.

    >>> canonical_arxiv_id("arXiv:1706.03762v5")
    '1706.03762'
    >>> canonical_arxiv_id("hep-th/9901001v2")
    'hep-th/9901001'
    """
    if value is None:
        return None
    s = _PREFIX.sub("", str(value).strip())
    s = re.sub(r"\.pdf$", "", s, flags=re.IGNORECASE)
    m = re.fullmatch(r"(\d{4})\.(\d{4,5})(?:v\d+)?", s)
    if m:
        return f"{m.group(1)}.{m.group(2)}"
    m = re.fullmatch(r"([A-Za-z\-]+)(?:\.[A-Za-z]{2})?/(\d{7})(?:v\d+)?", s)
    if m:
        return f"{m.group(1).lower()}/{m.group(2)}"
    return None


def extract_arxiv_ids(*texts: str | None) -> list[str]:
    """Canonical arXiv ids mentioned in the given strings (title, journal, url, doi, ...).

    A bare number such as '2017.12345' is only accepted when the text shows an arXiv context
    ('arXiv', 'CoRR', 'abs/', the 10.48550 DOI prefix), and new-style ids must follow the real
    format: YYMM >= 0704 and 4 digits before 2015, 5 digits from 1501 on.
    """
    found: list[str] = []
    for text in texts:
        if not text:
            continue
        text = str(text)
        has_context = bool(_ARXIV_CONTEXT.search(text))
        for m in _NEW_ID.finditer(text):
            yymm, number = m.group(1), m.group(2)
            if int(yymm) < 1501 and len(number) != 4:
                continue
            if int(yymm) >= 1501 and len(number) != 5:
                continue
            if has_context:
                found.append(f"{yymm}.{number}")
        if has_context or "/" in text:
            for m in _OLD_ID.finditer(text):
                found.append(f"{m.group(1).lower()}/{m.group(2)}")
    return list(dict.fromkeys(found))


def arxiv_id_year(arxiv_id: str | None) -> int | None:
    """Year encoded in an arXiv id: '1706.03762' -> 2017, 'hep-th/9901001' -> 1999, 'cs/0112017' -> 2001."""
    aid = canonical_arxiv_id(arxiv_id)
    if not aid:
        return None
    digits = aid.split("/")[1][:2] if "/" in aid else aid[:2]
    yy = int(digits)
    return 1900 + yy if "/" in aid and yy >= 91 else 2000 + yy


def parse_year(value) -> int | None:
    """Year from an int, '2017', '2017-06-12', 'June 2017' ... None when absent or implausible."""
    if value is None:
        return None
    if isinstance(value, float):
        if value != value:                                   # NaN
            return None
        value = int(value)
    m = re.search(r"(?<!\d)(19[0-9]{2}|20[0-4][0-9])(?!\d)", str(value))
    return int(m.group(1)) if m else None
