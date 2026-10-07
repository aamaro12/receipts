"""Figure grammar, normalisation, exemptions, block parsing and matching. Standard library only."""
from __future__ import annotations
import bisect
import re
from dataclasses import dataclass, field, replace
from decimal import Decimal, ROUND_HALF_UP

DERIVED_WORDS = frozenset({"margin", "growth", "cagr", "ratio", "rate", "difference", "change",
                           "increase", "decrease", "sum", "gap", "variance"})
_NEG_WORDS = {"decline", "declined", "decrease", "decreased", "fell", "fall", "loss", "negative", "down", "lower", "minus",
              "reduction", "reduced", "drop", "dropped"}
_POS_WORDS = {"rose", "rise", "grew", "grow", "up", "increase", "increased", "higher", "gain", "gained"}
# A figure right after one of these words is a change ("fell by £5m", "declined 5%"), not a level ("fell to £5m").
_CHANGE_WORDS = frozenset({"by"} | (_NEG_WORDS | _POS_WORDS) - {"loss", "negative", "minus", "lower", "higher"})
# ... and so is a figure after "<change noun> of" ("an increase of £19m", "a reduction of 19 GBPm").
_CHANGE_NOUNS = frozenset({"increase", "decrease", "rise", "fall", "decline", "drop", "reduction", "growth", "change",
                           "difference"})
_MEASURE_WORDS = {"points", "point", "pp", "bps", "percent", "per"}
_SCALES = {"k": 3, "thousand": 3, "m": 6, "mn": 6, "mm": 6, "mln": 6, "million": 6,
           "b": 9, "bn": 9, "bln": 9, "billion": 9, "tn": 12, "trn": 12, "trillion": 12}
_CODES = "GBP|EUR|USD|CHF|JPY|SEK|NOK|DKK"
_SYMBOLS = {"£": "GBP", "$": "USD", "us$": "USD", "€": "EUR"}
_UNITS = {"%": "%", "percent": "%", "per cent": "%", "percentage point": "pp", "percentage points": "pp",
          "pp": "pp", "ppt": "pp", "ppts": "pp", "pts": "pp", "bp": "bps", "bps": "bps", "x": "x", "×": "x",
          "p": "p", "pence": "p", "c": "c", "cent": "c", "cents": "c", "\u00a2": "c",
          "kg": "kg", "kgs": "kg", "t": "t", "teu": "TEU", "m3": "m3", "m2": "m2", "pcs": "pcs"}
PERCENT_UNITS = frozenset({"%", "pp", "bps"})
_MONTHS = ("january|february|march|april|may|june|july|august|september|october|november|december"
           "|jan|feb|mar|apr|jun|jul|aug|sept|sep|oct|nov|dec")        # "31 Dec 2024" (eval runs read/f3/3, read/f8/1)
_ID = r"(?:D\d+:p\d+:[bc]\d+|D\d+:s\d+:[A-Z]{1,3}\d+|[CWL]\d+)"
CITATION = re.compile(rf"\[\s*({_ID}(?:\s*[,;]\s*{_ID})*)\s*\]")        # [D1:p1:b3], [C1, C2], [C1; C2]

_SCALE_RX = "trillion|billion|million|thousand|bln|mln|trn|bn|mn|mm|tn|[kmb]"
_FIG = re.compile(rf"""
    (?P<minus>(?<![\w.,)\]])-(?=\(|US\$|[£$€\d]|(?:{_CODES})))?
    (?P<open>\()?
    (?P<cur>US\$|[£$€]|(?:{_CODES})\s?)?
    (?P<num>\d{{1,3}}(?:,\d{{3}})+(?:\.\d+)?|\d+(?:\.\d+)?)
    (?:\s?(?P<scale>{_SCALE_RX})(?![a-z0-9]))?
    (?:\s?(?P<unit>%|per\s?cent\b|percentage\s+points?\b|ppts?\b|pp\b|pts\b|bps\b|bp\b|x\b|×|kgs?\b|TEU\b|m3\b|m2\b|pcs\b|pence\b|cents?\b|¢|t\b|p\b|c\b))?
    (?:\s?(?P<cur2>{_CODES})(?P<scale2>{_SCALE_RX})?\b)?
    (?P<close>\))?
    (?:\s?(?P<pct_after>%)(?![\w%]))?
    (?:\s?(?P<cur3>{_CODES})(?P<scale3>{_SCALE_RX})?\b)?
""", re.IGNORECASE | re.VERBOSE)

_MASKS = [
    CITATION,
    re.compile(r"[\w\-./&']+\.(?:xlsx|xlsm|xlsb|xls|ods|csv|pdf|docx?|pptx?|json|txt)\b", re.I),  # file names: fy24_26.xlsx
    re.compile(rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+(?:{_MONTHS})\.?(?:\s+\d{{4}})?\b", re.I),
    re.compile(rf"\b(?:{_MONTHS})\.?\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,\s*\d{{4}})?\b", re.I),
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),                                    # ISO dates
    re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b"),                              # 31/12/2024
    re.compile(r"\((?:FY\s?)?(?:19|20)\d{2}(?:\s?/\s?\d{2,4})?\)", re.I),     # (2024), (FY2024), (2023/24)
    re.compile(r"\bFY\s?\d{2,4}(?:\s?[-–/]\s?\d{2,4})?\b", re.I),            # FY2024, FY2023-24
    re.compile(r"\b(?:19|20)\d{2}\s?[-–/]\s?(?:(?:19|20)\d{2}|\d{2})\b"),    # 2020-2023, 2020-23, 2023/24
    re.compile(r"\b(?:19|20)\d{2}[AEF]\b"),                                 # 2025E
    re.compile(r"\b(?:pages?|pp?\.|notes?)\s?\d+(?:\s?[-–]\s?\d+)?\b", re.I),  # page 140, p. 3, pp. 4-5, Note 15
    re.compile(r"[×*]\s?100\b"),                                          # "×100", "* 100" in a written formula
    re.compile(r"(?:(?<![\w.])|(?<=\d))x\s?100\b"),                        # "x 100", "2557x100"
    re.compile(r"(?<=\d)(?:[×*]|\s[×*]\s)(?=\(?\d)"),                     # "2557×2", "2,861 × 1.17": an operator
    re.compile(r"\b\d{1,2}(?::\d{2})?\s?(?:am|pm)\b", re.I),                 # 4pm
    re.compile(r"\b[A-Za-z]+-\d+\b"),                                        # COVID-19, S-1
    re.compile(r"\b\d+-[A-Za-z]+\b"),                                        # 3-year, 24-month, 10-K
    re.compile(r"\b\d+(?:st|nd|rd|th)\b", re.I),                             # ordinals
    re.compile(r"^\s*\d+[.)]\s", re.M),                                      # list markers
    re.compile(r"\bprofit or loss\b|/\(loss\)", re.I),                       # IFRS captions, not direction words
]
_HS = re.compile(r"\b\d{4}\.\d{2}(?:\.\d{2})?\b")
_HS_KEY = re.compile(r"\b(?:HS|tariff)\b", re.I)
_TAIL = re.compile(r"[A-Za-z]+")
_PREV_WORD = re.compile(r"([A-Za-z]+)\s*$")
_OF_NOUN = re.compile(r"([A-Za-z]+)\s+of\s*$")
_SUPERSCRIPTS = str.maketrans("", "", "\u00b9\u00b2\u00b3\u2074\u2075\u2076\u2077\u2078\u2079\u2070")
_CHARS = str.maketrans({"\u00a0": " ", "\u2009": " ", "\u202f": " ", "\u2007": " ", "\u2212": "-"})

@dataclass
class Figure:
    raw: str
    value: Decimal
    neg: bool
    scale: int
    is_percent: bool
    unit: str | None
    currency: str | None
    decimals: int
    direction: int
    start: int
    end: int
    change: bool = False          # a percentage, or an explicit change
    explicit_change: bool = False # right after "by", a direction verb, or "<change noun> of" ("fell by £5m", "a rise of 3%")
    unparsed: bool = False        # letters glued to the number that are not a known unit or currency
    col: int | None = None        # block figures: table column
    in_header: bool = False       # block figures: in a header row (before the |---| separator)
    label: str | None = None      # block figures: the row label (table) or the words before the figure (text)
    ctx_scale: int | None = None  # block figures: scale set by a unit row above ("| | million | million |"), else None
    ctx_currency: str | None = None  # ... and that row's currency ("" when it states none, as for a share count)

    @property
    def base(self) -> Decimal:
        v = self.value / 100 if self.unit == "bps" else self.value * (Decimal(10) ** self.scale)
        return -v if self.neg else v

@dataclass
class ParsedBlock:
    text: str                     # normalised block text; figure offsets refer to it
    figures: list[Figure]
    scale: int
    currency: str | None
    candidates: list[Figure]      # figures that may be matched or used as inputs
    col_years: dict[int, int] = field(default_factory=dict)
    col_headers: dict[int, str] = field(default_factory=dict)
    note_col: int | None = None

def normalise(text: str) -> str:
    t = text.replace("**", "")
    t = re.sub(r"\$\s*\{\s*\}\s*\^\{[^}]*\}\s*\$", "", t)   # ${ }^{1}$ footnote markers
    t = re.sub(r"\^\{[^}]*\}", "", t)                         # ^{1}
    t = t.replace("\\$", "$")
    t = re.sub(r"\$([^$\n]*\\[^$\n]*)\$", r"\1", t)           # $2 \times$: LaTeX math, not two dollar amounts
    t = t.replace("\\%", "%").replace("\\times", "x")
    t = t.replace("m\u00b3", "m3").replace("m\u00b2", "m2").translate(_SUPERSCRIPTS).translate(_CHARS)
    return re.sub(r"[ \t]+", " ", t)

def currency_code(cur: str | None) -> str | None:
    if not cur:
        return None
    c = cur.strip()
    return _SYMBOLS.get(c.lower(), c.upper())

def _mask(text: str) -> str:
    out = text
    for rx in _MASKS:
        out = rx.sub(lambda m: " " * len(m.group()), out)
    for m in _HS.finditer(out):
        window = out[max(0, m.start() - 20):m.start()]
        if _HS_KEY.search(window):
            out = out[:m.start()] + " " * (m.end() - m.start()) + out[m.end():]
    return out

def _decimals(num: str) -> int:
    return len(num.split(".")[1]) if "." in num else 0

_COMPARATIVES = frozenset({"lower", "higher"})

def _direction(text: str, start: int) -> int:
    """The sign a direction word gives the figure after it: "fell 3%", "grew at 5% a year", "lower by 1.39%". A word that
    describes a level gives none: "fell to 27.0%", "down from 28.7%", "up at 30.3%", and a comparative
    without "by" ("Diageo's was lower at 29.6%")."""
    words = re.findall(r"[a-z]+", text[max(0, start - 40):start].lower())[-3:]
    signs = set()
    for i, w in enumerate(words):
        rest = words[i + 1:]
        if (w in _COMPARATIVES and "by" not in rest) or {"to", "from"} & set(rest) or (w in ("down", "up") and "at" in rest):
            continue
        signs.add(-1 if w in _NEG_WORDS else 1 if w in _POS_WORDS else 0)
    return -1 if -1 in signs else 1 if 1 in signs else 0

def _prev_word(text: str, start: int) -> str:
    m = _PREV_WORD.search(text, max(0, start - 24), start)
    return m.group(1).lower() if m else ""

def _explicit_change(text: str, start: int) -> bool:
    prev = _prev_word(text, start)
    if prev == "of":
        m = _OF_NOUN.search(text, max(0, start - 30), start)
        return bool(m) and m.group(1).lower() in _CHANGE_NOUNS
    return prev in _CHANGE_WORDS

def extract(text: str, exempt_numbers: set[Decimal] = frozenset(), apply_exemptions: bool = True,
            normalised: bool = False) -> list[Figure]:
    norm = text if normalised else normalise(text)
    masked = _mask(norm)
    figs: list[Figure] = []
    for m in _FIG.finditer(masked):
        num = m.group("num")
        s, e = m.start(), m.end()
        opened, closed = m.group("open") is not None, m.group("close") is not None
        if opened and closed and apply_exemptions and figs and not masked[figs[-1].end:s].strip():
            opened = closed = False                              # "820 kg (4.45%)": an aside, not a negative
            s, e = m.end("open"), m.start("close")
        if opened and not closed:
            s = m.end("open")
        elif closed and not opened:
            e = m.start("close")
        after = opened and closed                                # "(323) GBPm": a code after the brackets counts
        if s > 0 and masked[s - 1].isalnum():
            continue                                             # Q3, FY2023, MSKU4417302
        digits = num.replace(",", "")
        sep = digits != num or "." in num
        if len(digits) >= 8 and not sep:
            continue                                             # long reference numbers
        value = Decimal(digits)
        cur = (m.group("cur") or m.group("cur2") or (m.group("cur3") if after else "") or "").strip() or None
        scale_word = m.group("scale") or m.group("scale2") or (m.group("scale3") if after else None)
        scale = _SCALES.get((scale_word or "").lower(), 0)
        unit = _UNITS.get(" ".join(m.group("unit").lower().split())) if m.group("unit") else None
        if m.group("pct_after") and not unit:
            if after:
                unit = "%"                                       # "(27.9) %": a negative percentage, accounting style
            else:
                e = min(e, m.start("pct_after"))                 # "5) %" with no opening bracket: leave the sign alone
        if (scale_word and len(scale_word) == 1 and scale_word.isupper() and m.start("scale") == m.end("num")
                and not (cur or sep or unit) and value <= 12):
            continue                                             # 4K, 5M: a label, not a quantity
        unparsed = False
        tail = _TAIL.match(masked, e)
        if tail:
            if len(tail.group()) == 1 and value <= 12 and not (sep or cur or scale or unit):
                continue                                         # 3D, 5G
            unparsed, e = True, tail.end()                       # 40ft, 12abc: flagged, never matched
        neg = m.group("minus") is not None or (opened and closed)
        is_pct = unit in PERCENT_UNITS
        if (apply_exemptions and cur and not (m.group("cur") or m.group("scale") or neg or unit or sep)
                and 1900 <= value <= 2099):
            continue                                             # "2024 GBPm": a column label, not an amount
        plain = apply_exemptions and not (neg or cur or scale or unit or sep or unparsed)
        if plain and 1900 <= value <= 2099:
            continue
        if plain and value <= 12:
            nxt = re.findall(r"[a-z]+", masked[e:e + 15].lower())[:1]
            in_range = re.match(r"\s?[-–]\s?\d", masked[e:e + 4])
            if not (in_range or (nxt and nxt[0] in _MEASURE_WORDS)):
                continue
        if value in exempt_numbers:
            continue
        explicit = _explicit_change(masked, s)
        change = is_pct or explicit
        figs.append(Figure(raw=norm[s:e], value=value, neg=neg, scale=scale, is_percent=is_pct, unit=unit,
                           currency=cur, decimals=_decimals(num), direction=_direction(masked, s) if change else 0,
                           start=s, end=e, change=change, explicit_change=explicit, unparsed=unparsed))
    return _split_ranges(masked, figs)

def _split_ranges(masked: str, figs: list[Figure]) -> list[Figure]:
    """'10-12%' and '£9.2-9.4bn': both ends share the unit, the scale and the currency."""
    for a, b in zip(figs, figs[1:]):
        if not re.fullmatch(r"\s?[-–]\s?", masked[a.end:b.start]):
            continue
        if b.unit and not a.unit:
            a.unit, a.is_percent = b.unit, b.is_percent
            a.change = a.change or b.is_percent
        if b.scale and not a.scale:
            a.scale = b.scale
        if a.currency and not b.currency:
            b.currency = a.currency
        if a.explicit_change and not b.explicit_change:          # "rose 10-12%": both ends are changes
            b.explicit_change = b.change = True
            b.direction = b.direction or a.direction
    return figs

_SCALE_WORDS = r"(?:m|mn|bn|k|'000s?|000s|millions?|thousands?|billions?)"
_HEADER_SCALE = [
    re.compile(rf"(?:\b(?:{_CODES})|US\$|[£€$])\s?({_SCALE_WORDS})(?![\w'])", re.I),
    re.compile(rf"\((?:US\$|[£€$]|{_CODES})?\s?({_SCALE_WORDS})\)", re.I),
    re.compile(r"\bin\s+('?000s|millions|thousands|billions)(?![\w'])", re.I),     # "$ in 000s", "(in thousands)"
]
_WORD_TO_EXP = {"m": 6, "mn": 6, "million": 6, "millions": 6, "bn": 9, "billion": 9, "billions": 9,
                "k": 3, "thousand": 3, "thousands": 3, "'000": 3, "'000s": 3, "000s": 3}
_HEADER_CUR = re.compile(rf"(?:\b({_CODES})|(US\$|[£$€]))\s?{_SCALE_WORDS}\b"
                         rf"|\((?:({_CODES})|(US\$|[£$€]))\s?{_SCALE_WORDS}?\)", re.I)

def _scale_of(norm: str) -> int:
    for rx in _HEADER_SCALE:
        m = rx.search(norm)
        if m:
            return _WORD_TO_EXP.get(m.group(1).lower(), 3)
    return 0

def _currency_of(norm: str) -> str | None:
    found = {currency_code(next(g for g in m.groups() if g)) for m in _HEADER_CUR.finditer(norm)}
    return found.pop() if len(found) == 1 else None

def parse_source(block: dict) -> ParsedBlock:
    """A ledger block parsed with the heading that states its unit, if one was recorded when the document was read."""
    return parse_block(block["text"], block.get("caption") or "")

_CAPTION_TYPES = ("title", "text")

def table_captions(blocks: list[dict]) -> list[str]:
    """For each block of one page, in reading order: the text of the nearest heading or text block above a table that
    states a scale, when the table states none of its own; "" otherwise. Search stops at the previous table."""
    out = []
    for i, b in enumerate(blocks):
        cap = ""
        if b.get("type") == "table" and not parse_block(b.get("text") or "").scale:
            for prev in reversed(blocks[:i]):
                if prev.get("type") == "table":
                    break
                if prev.get("type") in _CAPTION_TYPES and _scale_of(normalise(prev.get("text") or "")):
                    cap = prev.get("text") or ""
                    break
        out.append(cap)
    return out

def block_scale(text: str) -> int:
    """The scale a block states for its values as a whole (a table's header or caption, or the text around it)."""
    return parse_block(text).scale

_SEP_CELL = re.compile(r":?-{2,}:?")

def row_cells(line: str) -> list[str]:
    s = line.strip()
    s = s[1:] if s.startswith("|") else s
    s = s[:-1] if s.endswith("|") else s
    return [c.strip() for c in s.split("|")]

def is_separator_row(line: str) -> bool:
    cells = row_cells(line)
    return any(cells) and all(_SEP_CELL.fullmatch(c) for c in cells if c)

_UNIT_CELL = re.compile(rf"(?:(?P<code>{_CODES})|(?P<sym>US\$|[£$€]))?\s?(?P<scale>'000|thousands?|millions?|billions?|mn|bn|m|k)?",
                        re.I)
_PER_SHARE = {"c": "c", "cent": "c", "cents": "c", "us cents": "c", "\u00a2": "c", "p": "p", "pence": "p"}

def _unit_cell(cell: str) -> tuple[str | None, int, str | None] | None:
    """A cell that states only a unit ('cents', 'pence', '$ million', '£m', 'million'): (unit, scale, currency)."""
    c = cell.strip()
    if c.lower() in _PER_SHARE:
        return _PER_SHARE[c.lower()], 0, None
    m = _UNIT_CELL.fullmatch(c)
    if not c or not m or not (m.group("code") or m.group("sym") or m.group("scale")):
        return None
    return None, _WORD_TO_EXP.get((m.group("scale") or "").lower(), 0), currency_code(m.group("code") or m.group("sym"))

def _text_label(line: str, upto: int) -> str:
    """The words before a figure in a text line, back to the previous sentence break (at most 6 words)."""
    before = re.split(r"[.;:!?](?:\s|$)|[()]", line[:upto])[-1]
    return " ".join(re.findall(r"[A-Za-z][A-Za-z'&/-]*", before)[-6:])

def _yearish(f: Figure) -> bool:
    """1900 to 2100 written without a comma or decimals ('2022', '2022 GBPm' in a header row): a year, not data."""
    return f.decimals == 0 and "," not in f.raw and not f.is_percent and 1900 <= f.value <= 2100

def _bare_year(f: Figure) -> bool:
    return (f.decimals == 0 and "," not in f.raw and not (f.neg or f.currency or f.scale or f.unit)
            and 1900 <= f.value <= 2099)

def parse_block(text: str, context: str = "") -> ParsedBlock:
    """Parse a source block once: figures with offsets, scale, currency and, for markdown tables, columns. `context` is
    text outside the block that states its unit, such as the heading above a table ("(in thousands, except ...)")."""
    norm = normalise(text)
    figs = extract(norm, apply_exemptions=False, normalised=True)
    lines = norm.split("\n")
    starts, pos = [], 0
    for ln in lines:
        starts.append(pos)
        pos += len(ln) + 1
    table = [ln.lstrip().startswith("|") for ln in lines]
    sep = next((i for i, ln in enumerate(lines) if table[i] and is_separator_row(ln)), None)
    header_lines, col_headers = set(), {}
    for i in range(sep or 0):
        if table[i]:
            header_lines.add(i)
            for col, cell in enumerate(row_cells(lines[i])):
                col_headers[col] = f"{col_headers.get(col, '')} {cell}".strip()
    note_col = next((c for c, h in col_headers.items() if h.lower() in ("note", "notes")), None)
    col_years = {}
    for c, h in col_headers.items():
        years = set(re.findall(r"(?<!\d)(?:19|20)\d{2}(?!\d)", h))
        if len(years) == 1 and "/" not in h:
            col_years[c] = int(years.pop())
    # A body row made only of unit words ("| | | cents | cents |", "| Shares | | million | million |") sets the unit
    # or scale of the rows below it, column by column, until the next such row (Diageo: EPS in US cents, shares in
    # millions, inside a "$ million" table).
    unit_ctx: dict[int, dict[int, tuple]] = {}
    row_scale: dict[int, tuple] = {}
    ctx: dict[int, tuple] = {}
    for i, ln in enumerate(lines):
        if not table[i]:
            ctx = {}
            continue
        if i in header_lines or (sep is not None and i <= sep):
            continue
        specs = {c: _unit_cell(cell) for c, cell in enumerate(row_cells(ln)) if c and cell}
        if specs and all(specs.values()):
            ctx = specs
            continue
        unit_ctx[i] = ctx
        label = (row_cells(ln) or [""])[0]
        if _scale_of(label):                                     # "Gross Merchandise Value ($M)": this row only
            row_scale[i] = (_scale_of(label), _currency_of(label))
    for f in figs:
        i = bisect.bisect_right(starts, f.start) - 1
        if table[i]:
            f.col = lines[i][:f.start - starts[i]].count("|") - 1
            f.in_header = i in header_lines
            f.label = (row_cells(lines[i]) or [""])[0]
            spec = unit_ctx.get(i, {}).get(f.col)
            if spec and not (f.unit or f.is_percent or f.currency or f.scale):
                if spec[0]:
                    f.unit = spec[0]                             # 173.2 under "cents" is 173.2c
                else:
                    f.ctx_scale, f.ctx_currency = spec[1], spec[2] or ""
            elif i in row_scale and not (f.unit or f.is_percent or f.scale):
                f.ctx_scale, f.ctx_currency = row_scale[i][0], row_scale[i][1] or currency_code(f.currency) or ""
        else:
            f.label = _text_label(lines[i], f.start - starts[i])
    cands = [f for f in figs if not (f.in_header or f.unparsed or _bare_year(f))
             and not (note_col is not None and f.col == note_col)]
    # The block's own scale comes from where a table states it for all its values: the header rows and any text
    # around the table. A scale inside a body row ("Gross Merchandise Value ($M)") is that row's, set above.
    # Header rows are the table rows before its first data row: OCR often puts "($ in millions) | Q3 FY24 | ..." under
    # the |---| separator, and a table may have no separator at all ("| FOR THE YEAR | 2022 GBPm | 2024 GBPm |").
    data_rows = [bisect.bisect_right(starts, f.start) - 1 for f in figs
                 if not (f.in_header or f.unparsed or _yearish(f))]
    first_data = min((i for i in data_rows if table[i]), default=len(lines))
    scope = "\n".join(ln for i, ln in enumerate(lines) if not table[i] or i < first_data)
    if not _scale_of(scope) and context:
        scope = f"{normalise(context)}\n{scope}"                  # the block states no scale: its heading's applies
    return ParsedBlock(norm, figs, _scale_of(scope), _currency_of(scope) or _currency_of(norm), cands, col_years,
                       col_headers, note_col)

def _step(fig: Figure) -> Decimal:
    """One unit of the figure's last shown digit, in the units of fig.base."""
    one = Decimal(10) ** -fig.decimals
    if fig.unit == "bps":
        return one / 100
    return one if fig.is_percent else one * (Decimal(10) ** fig.scale)

def close_enough(fig: Figure, target: Decimal) -> bool:
    """Block matching: within half a unit of the last shown digit and at most 2% apart."""
    mine, tgt = abs(fig.base), abs(target)
    half = _step(fig) / 2
    cap = Decimal("0.02") * tgt if tgt else half
    return mine == tgt or abs(mine - tgt) <= min(half, cap)

def close_to_display(fig: Figure, target: Decimal) -> bool:
    """Calc citations: within half a unit of the last shown digit and at most 25% apart."""
    mine, tgt = abs(fig.base), abs(target)
    diff = abs(mine - tgt)
    return diff <= _step(fig) / 2 and (tgt == 0 or diff / tgt <= Decimal("0.25"))

def value_scale(bf: Figure, pb: ParsedBlock) -> int:
    """The power of ten a block value is stated in: its own scale, a unit row above it, or the block's header."""
    if bf.scale or bf.unit or bf.is_percent:
        return bf.scale
    return pb.scale if bf.ctx_scale is None else bf.ctx_scale

def value_currency(bf: Figure, pb: ParsedBlock) -> str | None:
    """The currency of a block value: its own, a unit row's (none for a share count), or the block's."""
    if bf.currency:
        return currency_code(bf.currency)
    if bf.unit or bf.is_percent:
        return None
    return pb.currency if bf.ctx_scale is None else (bf.ctx_currency or None)

def currency_ok(fcur: str, bf: Figure, pb: ParsedBlock) -> bool:
    bcur = value_currency(bf, pb)
    return bcur == fcur or (bcur is None and bf.ctx_scale is None)

def _in_year(pb: ParsedBlock, year: int | None) -> list[Figure]:
    if year is not None:
        cols = {c for c, y in pb.col_years.items() if y == year}
        if cols:
            return [f for f in pb.candidates if f.col in cols]
    return pb.candidates

def matches_in_block(fig: Figure, block: str | ParsedBlock, year: int | None = None) -> list[Figure]:
    """Every block figure the answer figure may stand for. Direction words are checked in check.py."""
    pb = block if isinstance(block, ParsedBlock) else parse_block(block)
    fcur = currency_code(fig.currency)
    out = []
    for bf in _in_year(pb, year):
        if fig.is_percent != bf.is_percent:
            continue
        if not fig.is_percent:
            if fig.unit and fig.unit != bf.unit:
                continue
            if bf.unit and not fig.unit and (fig.currency or fig.scale):
                continue                                         # £103.6 or £103.6m is not 103.6p
        if fcur and not currency_ok(fcur, bf, pb):
            continue
        if fig.neg and not bf.neg:
            continue
        if fig.is_percent:
            target = abs(bf.base)
        elif fig.scale or fig.currency:
            target = bf.value * (Decimal(10) ** value_scale(bf, pb))
        elif bf.scale:
            continue
        else:
            target = bf.value
        if close_enough(fig, target):
            out.append(bf)
    return out

def match_in_block(fig: Figure, block: str | ParsedBlock, year: int | None = None) -> Figure | None:
    hits = matches_in_block(fig, block, year)
    return hits[0] if hits else None

UNIT_NAMES = {"p": "pence", "c": "cents", "x": "multiples (x)", "kg": "kilograms", "t": "tonnes", "TEU": "TEU",
              "m3": "cubic metres", "m2": "square metres", "pcs": "pieces"}
SYMBOLS = {"GBP": "£", "USD": "$", "EUR": "€"}
_SCALE_NAMES = {3: "thousand", 6: "million", 9: "billion", 12: "trillion"}
SUFFIXES = {0: "", 3: "k", 6: "m", 9: "bn", 12: "tn"}
_SHIFTS = (3, -3, 6, -6, 9, -9, 12, -12)

def digits(v: Decimal, places: int) -> str:
    return f"{v:,.{places}f}"

def sig3(v: Decimal) -> str:
    """v with 3 significant digits (never rounding the integer part): 20.269 -> 20.3, 6.001 -> 6.00."""
    places = 0 if v == 0 else max(0, 2 - v.adjusted())
    return f"{v.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP):,f}"

def stated_as(bf: Figure, pb: ParsedBlock) -> str:
    """How the block states a value: 'cents', '$ million', 'millions', '$'."""
    if bf.unit:
        return UNIT_NAMES.get(bf.unit, bf.unit)
    cur, exp = value_currency(bf, pb), value_scale(bf, pb)
    word = _SCALE_NAMES.get(exp)
    if word:
        return f"{SYMBOLS.get(cur, cur)} {word}" if cur else f"{word}s"
    return SYMBOLS.get(cur, cur) if cur else "units"

def rewrites(bf: Figure, pb: ParsedBlock) -> list[str]:
    """Ways to write a block value with its currency and scale: ['$20,269m', '$20.3bn']."""
    cur, exp = value_currency(bf, pb), value_scale(bf, pb)
    pre = (SYMBOLS.get(cur) or f"{cur} ") if cur else ""
    out = [f"{pre}{digits(bf.value, bf.decimals)}{SUFFIXES.get(exp, '')}"]
    if bf.value >= 1000 and exp + 3 in SUFFIXES:
        out.append(f"{pre}{sig3(bf.value / 1000)}{SUFFIXES[exp + 3]}")
    return out

def _sig_digits(fig: Figure) -> int:
    return len(str(fig.value).replace(".", "").lstrip("0"))

def _near(fig: Figure, pb: ParsedBlock, cands: list[Figure]) -> tuple[list[str], Figure] | None:
    """A block value with the figure's digits but another unit, currency, sign or scale: (kinds, value)."""
    fcur, plain, found = currency_code(fig.currency), replace(fig, neg=False), []
    for bf in cands:
        if bf.is_percent != fig.is_percent:
            continue
        kinds = []
        if not fig.is_percent:
            if fig.unit != bf.unit and (fig.unit or fig.currency or fig.scale):
                kinds.append("unit")
            elif fcur and not currency_ok(fcur, bf, pb):
                kinds.append("currency")
        if fig.neg and not bf.neg:
            kinds.append("sign")
        if fig.is_percent:
            ok = close_enough(plain, abs(bf.base))
        elif "unit" in kinds:
            ok = close_enough(replace(plain, scale=0), bf.value)          # the same digits in another unit
        else:
            mag = bf.value * (Decimal(10) ** value_scale(bf, pb))
            ok = close_enough(plain, mag if (fig.scale or fig.currency) else bf.value)
            if not ok and _sig_digits(fig) >= 3:                          # the same digits at another scale
                for n in _SHIFTS:
                    if close_enough(replace(plain, scale=fig.scale + n), mag):
                        kinds.append("scale")
                        ok = True
                        break
        if ok and kinds:
            found.append((len(kinds), kinds, bf))
    if not found:
        return None
    _, kinds, bf = min(found, key=lambda x: x[0])
    return kinds, bf

def explain_miss(fig: Figure, block: str | ParsedBlock, cid: str, year: int | None = None) -> str | None:
    """Why a figure that is not in a block almost is, in words the model can act on; None when nothing is close.

    '£20.269 billion' -> "has currency £ but D2:p1:b7 is in $"; '$20.269' -> "is missing its scale: the table is in
    $ million, so write $20,269m or $20.3bn"; '173.2 pence' -> "is in pence but ... shows 173.2 in cents: write 173.2c"."""
    pb = block if isinstance(block, ParsedBlock) else parse_block(block)
    hit = _near(fig, pb, _in_year(pb, year)) or (_near(fig, pb, pb.candidates) if year is not None else None)
    if not hit:
        return None
    kinds, bf = hit
    shown = digits(bf.value, bf.decimals)
    if "unit" in kinds:
        have = (f"is in {UNIT_NAMES.get(fig.unit, fig.unit)}" if fig.unit
                else f"is an amount in {SYMBOLS.get(currency_code(fig.currency), fig.currency.strip())}" if fig.currency
                else f"is an amount in {_SCALE_NAMES.get(fig.scale, 'unit')}s")
        fix = (f"{shown}{bf.unit}" if bf.unit in ("p", "c", "x") else f"{shown} {bf.unit}" if bf.unit
               else " or ".join(rewrites(bf, pb)))
        return f"{have} but {cid} shows {shown} in {stated_as(bf, pb)}: write {fix}"
    parts = []
    if "currency" in kinds:
        bcur = value_currency(bf, pb)
        parts.append(f"has currency {fig.currency.strip()} but {cid} is in {SYMBOLS.get(bcur, bcur)}" if bcur else
                     f"has currency {fig.currency.strip()} but {cid} shows {shown} in {stated_as(bf, pb)}, with no currency")
    if "sign" in kinds:
        parts.append(f"is negative but {cid} shows {shown} as positive")
    if "scale" in kinds:
        where = "the table" if pb.col_headers else cid
        parts.append(f"{'has the wrong scale' if fig.scale else 'is missing its scale'}: {where} is in {stated_as(bf, pb)}, "
                     f"so write {' or '.join(rewrites(bf, pb))}")
    return ", and ".join(parts)

_NUM = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")

def magnitudes(text: str) -> list[float]:
    """Every size a number in the text could stand for (any scale, or basis points), sorted. No parsing."""
    out = set()
    for n in _NUM.findall(text):
        v = float(n.replace(",", ""))
        out.update((v, v * 1e3, v * 1e6, v * 1e9, v * 1e12, v / 100))
    return sorted(out)

def may_match(fig: Figure, mags: list[float]) -> bool:
    """Cheap superset of matches_in_block: some number in the block is within 2% of the figure's size."""
    size = float(abs(fig.base))
    i = bisect.bisect_left(mags, size / 1.021)
    return i < len(mags) and mags[i] <= size / 0.979
