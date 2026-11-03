"""Text utilities: sentence splitting, keyword extraction, slugs, alignment keys."""

from __future__ import annotations

import re
import unicodedata

_ABBREV = {
    "mr", "mrs", "ms", "dr", "st", "jr", "sr", "vs", "etc", "no", "gen", "col", "lt", "sgt",
    "capt", "cmdr", "prof", "inc", "co", "corp", "ltd", "mt", "ft", "approx", "u.s", "u.k",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec",
}

STOPWORDS = set(
    """a about above after again against all almost also am among an and any are aren't as at
    be became because become been before being below between both but by can cannot could
    couldn't did didn't do does doesn't doing don't down during each even ever every few for
    from further get got had hadn't has hasn't have haven't having he he'd he'll he's her here
    here's hers herself him himself his how how's however i i'd i'll i'm i've if in into is
    isn't it it's its itself just let's like made make many may me might more most much must
    mustn't my myself never no nor not now of off on once one only or other ought our ours
    ourselves out over own perhaps quite rather really said same say says she she'd she'll
    she's should shouldn't since so some something still such than that that's the their
    theirs them themselves then there there's these they they'd they'll they're they've thing
    things this those though through thus to too toward towards under until up upon us very
    was wasn't we we'd we'll we're we've were weren't what what's when when's where where's
    whether which while who who's whom why why's will with within without won't would
    wouldn't yet you you'd you'll you're you've your yours yourself yourselves almost around
    away back became began behind beyond came come comes coming day days later little long
    look looked new next often old part put right seemed seen set side small take taken tell
    time times told took two three four five six seven eight nine ten first second third last
    went way ways well year years yes""".split()
)


def split_sentences(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", text.strip())
    if not text:
        return []
    out: list[str] = []
    start = 0
    for m in re.finditer(r"[.!?]+[\"'”’)\]]*(?=\s+|$)", text):
        end = m.end()
        chunk = text[start:end]
        prev = re.search(r"(\b[\w.]+)[.!?]+[\"'”’)\]]*$", chunk)
        token = (prev.group(1) if prev else "").lower().rstrip(".")
        nxt = text[end:end + 3].strip()
        if token in _ABBREV or (len(token) == 1 and token.isalpha()):
            continue
        if re.fullmatch(r"\d+", token) and m.group(0).startswith(".") and nxt[:1].isdigit():
            continue  # decimal number
        if nxt and nxt[0].islower():
            continue
        out.append(chunk.strip())
        start = end
    tail = text[start:].strip()
    if tail:
        out.append(tail)
    return out


def norm_key(s: str) -> str:
    """Lowercase alphanumerics only, accents stripped: the alignment alphabet."""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]", "", s.lower())


def slugify(s: str, maxlen: int = 60) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return s[:maxlen].strip("-") or "video"


def keywords(text: str, limit: int = 4) -> list[str]:
    """Search keywords: capitalised phrases first (names, places), then long nouns."""
    phrases: list[str] = []
    for m in re.finditer(r"(?:(?<=\s)|^)((?:[A-Z][\w'’-]+|[0-9]{4})(?:\s+(?:of|the|de|von|and)?\s*[A-Z][\w'’-]+)*)", text):
        p = m.group(1).strip()
        first = p.split()[0].lower()
        if first in STOPWORDS and len(p.split()) == 1:
            continue
        if p not in phrases:
            phrases.append(p)
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z'-]{3,}", text) if w.lower() not in STOPWORDS]
    words.sort(key=len, reverse=True)
    out = phrases[:limit]
    for w in words:
        if len(out) >= limit:
            break
        if not any(w.lower() in o.lower() for o in out):
            out.append(w.lower())
    return out


_FIG = re.compile(r"^(?P<prefix>[^\d\-+]*?)(?P<num>[-+]?\d[\d,]*(?:\.\d+)?)(?P<suffix>.*)$")


def parse_figure(s: str) -> tuple[float, str, str, int, bool] | None:
    """'$1,250.5 million' -> (1250.5, '$', ' million', 1, True)."""
    m = _FIG.match(s.strip())
    if not m:
        return None
    num = m.group("num")
    decimals = len(num.split(".")[1]) if "." in num else 0
    return float(num.replace(",", "")), m.group("prefix"), m.group("suffix"), decimals, "," in num


def format_figure(value: float, decimals: int = 0, commas: bool = True) -> str:
    if commas:
        return f"{value:,.{decimals}f}"
    return f"{value:.{decimals}f}"


def truncate_words(text: str, n: int) -> str:
    w = text.split()
    return " ".join(w[:n]) + ("…" if len(w) > n else "")


def spoken_seconds(text: str, wpm: float = 155) -> float:
    return len(text.split()) / wpm * 60


_MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"


def kinetic_items(text: str, limit: int = 3) -> list[str]:
    """Words worth putting on screen for a typographic scene: dates, names, figures.
    'In March 1965, Alexei Leonov opened a hatch' -> ['March 1965', 'Alexei Leonov', 'opened a hatch']"""
    items: list[str] = []
    for m in re.finditer(rf"\b(?:(?:{_MONTHS})\s+(?:\d{{1,2}},\s+)?\d{{4}}|\d{{4}})\b", text):
        items.append(m.group(0))
    for m in re.finditer(r"\b\d[\d,.]*\s*(?:%|percent|million|billion|thousand|kilometers|miles|feet|hours|days|minutes|seconds|kilograms)\b", text):
        items.append(m.group(0))
    for m in re.finditer(r"(?<![.!?]\s)(?<!^)\b([A-Z][a-zA-Z'’-]+(?:\s+(?:of\s+)?[A-Z][a-zA-Z'’-]+)+|[A-Z][a-z]{3,})\b", text):
        w = m.group(1)
        if w.split()[0].lower() in STOPWORDS or re.fullmatch(_MONTHS, w):
            continue
        items.append(w)
    out: list[str] = []
    for it in items:
        if not any(it.lower() in o.lower() or o.lower() in it.lower() for o in out):
            out.append(it)
    if len(out) < 2:  # few names/dates: the beat's shortest clause, verbatim
        clauses = [c.strip() for c in re.split(r"[,.;:!?]", text) if len(c.split()) >= 2]
        if clauses:
            c = min(clauses, key=lambda x: len(x.split())).split()
            out.append(" ".join(c if len(c) <= 6 else c[-5:]))
    return out[:limit]
