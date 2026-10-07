"""Citations from every tool: uploaded PDFs, web search and Mistral Libraries write checkable blocks to the ledger.

- Uploaded PDF ([D1:p3:c1]): a Mistral model reads the file and answers with [CITE_n] placeholders plus, per fact, the
  page and a verbatim quote. Every quote is then verified against that page's OCR text before it gets an ID; a quote
  found on another page moves there, and one found nowhere gets no ID.
- Web ([W1]): the search gives titles and URLs only, so each page is fetched and the passages that matter are stored.
- Library ([L1]): the matched document's text is read through the Libraries API. That text has no page markers, so the
  page comes from the nearest printed page header, best-effort.

The check then verifies every figure against the stored text, as for OCR blocks and cells.
"""
from __future__ import annotations
import base64, hashlib, html.parser, json, os, pathlib, re
from .ledger import Ledger
from .numbers import normalise, block_scale, row_cells, _unit_cell

API = "https://api.mistral.ai/v1"
QA_MODEL = os.environ.get("TRACEABLE_QA_MODEL", "mistral-medium-latest")
_WORD = re.compile(r"[a-z][a-z&'-]+")
_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").replace("**", " ").replace("|", " ").lower()).strip()


def _nums(s: str) -> set[str]:
    return {n.replace(",", "") for n in _NUM.findall(s or "")}


def _quote_nums(s: str) -> set[str]:
    """The quote's numbers, without the years a model adds for context ("Revenue 2024: 9,434")."""
    return _nums(re.sub(r"(?<![\d,.])(?:19|20)\d{2}(?![\d,.])", " ", s or ""))


def _words(s: str) -> set[str]:
    return set(_WORD.findall(_norm(s)))


def _score(quote: str, line: str) -> float:
    """How well a line holds a quote: every number of the quote, and most of its words."""
    qn, qw = _quote_nums(quote), _words(quote)
    if qn and not qn <= _nums(line):
        return 0.0
    if not qw:
        return 1.0 if qn else 0.0
    return len(qw & _words(line)) / len(qw)


def _lines(block: dict) -> list[str]:
    return [ln for ln in (block.get("text") or "").splitlines() if ln.strip()]


_LABEL_NUM = re.compile(r"\b(?:FY|Q[1-4]\s*(?:FY)?|H[12]\s*(?:FY)?)\s*'?\d{2,4}\b|\b(?:19|20)\d{2}\b|\bQ[1-4]\b|\bH[12]\b", re.I)


def _header(block: dict) -> list[str]:
    """A table block's header rows, so a quoted row keeps its years and units: the rows through the |---| separator,
    and the rows under it that hold no data number (OCR often puts "($ in millions) | Q3 FY24 | ..." there)."""
    lines = _lines(block)
    sep = next((i for i, ln in enumerate(lines) if re.fullmatch(r"[\s|:-]+", ln) and "-" in ln), None)
    if sep is None or not (block.get("type") == "table" or lines[0].lstrip().startswith("|")):
        return []
    end = sep + 1
    while end < len(lines) and not re.search(r"\d", _LABEL_NUM.sub(" ", lines[end])):
        end += 1
    return lines[:end]


def _unit_row(block: dict, line: str) -> list[str]:
    """The nearest row of unit words above a quoted row ("| | | cents | cents |"): it sets the row's unit, so a quote
    without it reads Diageo's 173.2 cents as $173.2 million (seen in a run)."""
    lines, head = _lines(block), len(_header(block))
    if line not in lines:
        return []
    for ln in reversed(lines[head:lines.index(line)]):
        specs = [_unit_cell(c) for k, c in enumerate(row_cells(ln)) if k and c]
        if specs and all(specs):
            return [ln]
    return []


def verify_quote(quote: str, blocks: list[dict], page: int) -> tuple[dict, str, int] | None:
    """(block, the quoted line, page) where the quote is found: its stated page first, then any page."""
    best = None
    usable = [b for b in blocks if b.get("type") not in ("header", "footer", "title", "quote")]
    order = sorted(usable, key=lambda b: (b["page_index"] + 1 != page, b["page_index"], b.get("n", 0)))
    for b in order:
        for ln in _lines(b):
            s = _score(quote, ln)
            if s >= 0.6 and (best is None or s > best[0] + 1e-9):
                best = (s, b, ln)
        if best and best[1]["page_index"] + 1 == page and best[0] >= 0.99:
            break
    if best is None:
        return None
    return best[1], best[2], best[1]["page_index"] + 1


def _expand(answer: str, ids: dict[str, str | None]) -> str:
    def one(m):
        keys = [k.strip() for k in m.group(1).split(",")]
        return "".join(f"[{ids[k]}]" for k in keys if ids.get(k))
    return re.sub(r"\[((?:CITE_\d+)(?:\s*,\s*CITE_\d+)*)\]", one, answer)


def ask_document(path: str, question: str, *, root=None, ocr=None, qa=None) -> str:
    """Answer a question about a PDF with verified page quotes; see the module docstring."""
    from .ocr import read_pdf, MistralOcr
    p = pathlib.Path(path)
    if not p.is_absolute() or not p.exists() or p.suffix.lower() != ".pdf":
        return f"ERROR: {path} must be an absolute path to an existing PDF"
    ledger = Ledger(pathlib.Path(root) if root else pathlib.Path.cwd())
    try:
        doc_id, blocks = read_pdf(p, ledger, ocr or MistralOcr(os.environ.get("MISTRAL_API_KEY", "")))
        resp = (qa or mistral_qa)(p.read_bytes(), question)
    except Exception as e:  # noqa: BLE001
        return f"ERROR: {e}"
    sha = hashlib.sha256(p.read_bytes()).hexdigest()
    doc = ledger.load()["documents"][sha]
    n0 = sum(1 for b in doc["blocks"] if b.get("type") == "quote")
    ids, made, notes, missing = {}, [], [], []
    for i, c in enumerate(resp.get("citations") or [], 1):
        quote, page = str(c.get("quote") or "").strip(), int(c.get("page_number") or 0)
        found = verify_quote(quote, blocks, page) if quote else None
        if found is None:
            ids[f"CITE_{i}"] = None
            missing.append(f"'{quote}' (claimed page {page})")
            continue
        block, line, real = found
        same = next((m for m in made if m["source_block"] == block.get("block_id") and m["quote_line"] == line), None)
        if same:
            ids[f"CITE_{i}"] = same["block_id"]                  # the same line quoted twice: one ID
            continue
        k = n0 + len(made) + 1
        bid = f"{doc_id}:p{real}:c{k}"
        text = "\n".join(_header(block) + _unit_row(block, line) + [line])
        q = {"block_id": bid, "page_index": real - 1, "n": f"c{k}", "type": "quote", "bbox": block.get("bbox"),
             "raw_text": text, "text": normalise(text), "block_scale": block.get("block_scale") or block_scale(text),
             "quote": quote, "source_block": block.get("block_id"), "quote_line": line,
             **({"caption": block["caption"]} if block.get("caption") else {})}   # its table's heading: "(in thousands)"
        if real != page:
            q["moved_from_page"] = page
        made.append(q)
        ids[f"CITE_{i}"] = bid
        shown = line.strip() if _score(quote, line) >= 0.99 and _norm(quote) in _norm(line) else \
            f"{line.strip()} (matched to: '{quote}')"
        notes.append(f"[{bid}] page {real}: {shown}" + (f" (moved from page {page}: the quote is on page {real})"
                                                         if real != page else ""))
    if made:
        ledger.add_blocks(sha, made)
    answer = re.sub(r"\s+([.,;:])", r"\1", _expand(str(resp.get("answer_with_citations") or ""), ids))
    out = [f"{doc_id} = {p.name}", "Answer (each citation is a quote verified on its page; cite these IDs):", answer]
    if notes:
        out += ["", "Quotes:"] + notes
    if missing:
        out += ["", "These quotes were not found in the document, so they have no ID (do not state them without another "
                "source): " + "; ".join(missing)]
    return "\n".join(out)


# --- web ---

class _Text(html.parser.HTMLParser):
    SKIP = {"script", "style", "nav", "header", "footer", "noscript", "svg", "form"}
    BLOCK = {"p", "li", "td", "th", "h1", "h2", "h3", "h4", "div", "tr", "br", "section", "article"}

    def __init__(self):
        super().__init__()
        self.skip, self.parts, self.title, self._in_title = 0, [""], "", False

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        if tag == "title":
            self._in_title = True
        if tag in self.BLOCK:
            self.parts.append("")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        if tag == "title":
            self._in_title = False
        if tag in self.BLOCK:
            self.parts.append("")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self.skip:
            self.parts[-1] += data


def page_text(html_text: str) -> list[str]:
    p = _Text()
    p.feed(html_text)
    return [re.sub(r"\s+", " ", x).strip() for x in p.parts if len(re.sub(r"\s+", " ", x).strip()) > 20]


_STOP = {"the", "and", "for", "from", "with", "was", "were", "this", "that", "year", "years", "its", "their", "which",
         "are", "has", "have", "had", "total", "all", "per", "not", "but", "also", "into", "over", "than"}


def passages(paragraphs: list[str], terms: str, k: int = 6, answer: str = "") -> list[str]:
    """Up to k paragraphs with numbers, ranked by the question's words (weight 3) and the answer's (weight 1), filler
    words ignored and repeats dropped; returned in document order."""
    q, a = _words(terms) - _STOP, _words(answer) - _STOP
    seen, scored = set(), []
    for i, p in enumerate(paragraphs):
        key = _norm(p)
        if not _NUM.search(p) or key in seen:
            continue
        seen.add(key)
        w = _words(p)
        scored.append((3 * len(w & q) + len(w & a), i, p))
    top = sorted((x for x in scored if x[0] > 0), key=lambda x: (-x[0], x[1]))[:k] or scored[:k]
    return [p for _, i, p in sorted(top, key=lambda x: x[1])]


def _source_id(ledger: Ledger, prefix: str) -> str:
    n = sum(1 for d in ledger.load()["documents"].values() if d.get("kind") == {"W": "web", "L": "library"}[prefix])
    return f"{prefix}{n + 1}"


def web_search(query: str, *, root=None, conv=None, fetch=None) -> str:
    ledger = Ledger(pathlib.Path(root) if root else pathlib.Path.cwd())
    try:
        res = (conv or mistral_conversation)(query, "web_search")
    except Exception as e:  # noqa: BLE001
        return f"ERROR: {e}"
    lines = [f"Search answer (not citable; cite the sources below): {res.get('answer', '').strip()}", "", "Sources:"]
    for ref in res.get("references") or []:
        url, title = ref.get("url"), ref.get("title") or ""
        if ref.get("snippets"):
            paras = [re.sub(r"\s+", " ", x).strip() for x in ref["snippets"] if x and x.strip()]
        elif not url:
            continue
        else:
            try:
                paras = page_text((fetch or http_get)(url))
            except Exception as e:  # noqa: BLE001
                lines.append(f"(not citable) {title} ({url}): could not be fetched ({e})")
                continue
        keep = passages(paras, query, answer=res.get("answer", ""))
        if not keep:
            lines.append(f"(not citable) {title} ({url}): no passage with figures on the page")
            continue
        bid = _source_id(ledger, "W")
        text = "\n".join(keep)
        ledger.add_source(f"web:{url or title}:{bid}", "web", url or title, {"block_id": bid, "type": "web", "url": url, "title": title,
                                                             "description": ref.get("description"), "page_index": 0,
                                                             "n": 1, "bbox": None, "raw_text": text, "text": normalise(text),
                                                             "block_scale": block_scale(text)})
        lines.append(f"[{bid}] {title} ({url or 'Mistral web_search data card, no URL'})")
        lines += [f"  {x}" for x in keep]
    return "\n".join(lines)


# --- Mistral Libraries ---

_PAGE_HEAD = [re.compile(r"^\s*(\d{1,4})\s+\S[^|\n]{2,120}\|"), re.compile(r"\|[^|\n]*?\s(\d{1,4})\s*$")]


def page_of(line: str) -> int | None:
    """A printed page number in a running header ('140 RELX Annual Report 2024 | ...' or '... | Governance 57')."""
    for rx in _PAGE_HEAD:
        m = rx.search(line or "")
        if m:
            return int(m.group(1))
    return None


_UNIT_LINE = re.compile(r"(?:GBP|USD|EUR|CHF|£|\$|€)\s?(?:m|bn|k|million|billion|thousand)s?\b|\bin (?:millions|billions|thousands)\b", re.I)

def chunk_page(chunk: str) -> int | None:
    """The page of a Library chunk: a running header near its top ('140 RELX Annual Report 2024 | ...') or a bare
    page number line there ('< > 141')."""
    import html as _html
    top = [ln.strip() for ln in _html.unescape(chunk or "").splitlines() if ln.strip()][:5]
    for ln in top:
        found = page_of(ln)
        if found is not None:
            return found
        m = re.fullmatch(r"[\s<>|]*(\d{1,4})[\s<>|]*", ln)
        if m:
            return int(m.group(1))
    return None


def _chunk_block(chunk: str, terms: str, answer: str = "") -> str | None:
    """The relevant lines of a chunk: a table's header rows plus its matching rows, or the matching lines."""
    import html as _html
    lines = [ln for ln in _html.unescape(chunk).splitlines() if ln.strip()]
    sep = next((i for i, ln in enumerate(lines) if re.fullmatch(r"[\s|:-]+", ln) and "-" in ln), None)
    body = [ln for ln in (lines[sep + 1:] if sep is not None else lines) if page_of(ln) is None]
    keep = passages(body, terms, answer=answer)
    if not keep:
        return None
    head = lines[max(0, sep - 1): sep + 1] if sep is not None else []
    unit = next((ln for ln in lines if _UNIT_LINE.search(ln)), None)
    return "\n".join(([unit] if unit and unit not in head and not head else []) + head + keep)


CHUNK_PAGE_NOTE = ("from the chunk's own printed page header; Mistral Libraries give no page numbers, so a chunk without "
                   "a header has no page")
PAGE_NOTE = ("best-effort: Mistral Libraries text has no page markers (pages are joined), so the page is the nearest "
             "printed page header before the passage")


PAGES_NOTE = "file page, from the Library's extracted pages"


def _squash(s: str) -> str:
    import html as _html
    return re.sub(r"[\s*]+", " ", _html.unescape(s or "")).strip().lower()


def _data_rows(block: str) -> list[str]:
    """A passage's rows that carry figures: not its table header (years, units) and not a running page header."""
    head = set(_header({"type": "table", "text": block}))
    return [_squash(ln) for ln in block.splitlines()
            if ln.strip() and ln not in head and _NUM.search(ln) and page_of(ln) is None]


def page_holding(block: str, pages: list[str]) -> int | None:
    """The one file page (1-based) holding the most of a passage's data rows; None when no page holds any, or when
    two pages hold as many (a row such as 'Net profit for the year' can be on both)."""
    rows, flat = _data_rows(block), [_squash(p) for p in pages]
    counts = [sum(r in f for r in rows) for f in flat]
    best = max(counts, default=0)
    return counts.index(best) + 1 if best and counts.count(best) == 1 else None


def _table_of(lines: list[str], i: int) -> tuple[int, int]:
    """The contiguous table (lines starting with '|') around line i, as [start, end); (i, i + 1) outside a table."""
    if not lines[i].lstrip().startswith("|"):
        return i, i + 1
    start, end = i, i + 1
    while start > 0 and lines[start - 1].lstrip().startswith("|"):
        start -= 1
    while end < len(lines) and lines[end].lstrip().startswith("|"):
        end += 1
    return start, end


def library_search(query: str, library_id: str, *, root=None, conv=None, docs=None, text=None, pages=None) -> str:
    ledger = Ledger(pathlib.Path(root) if root else pathlib.Path.cwd())
    cache: dict[str, list[str] | None] = {}

    def doc_pages(doc_id: str) -> list[str] | None:
        if doc_id not in cache:
            try:
                cache[doc_id] = (pages or library_pages)(library_id, doc_id) or None
            except Exception:  # noqa: BLE001 - no pages: the printed-header rules below still apply
                cache[doc_id] = None
        return cache[doc_id]

    try:
        res = (conv or mistral_conversation)(query, "document_library", library_id)
        listed = (docs or library_documents)(library_id)
    except Exception as e:  # noqa: BLE001
        return f"ERROR: {e}"
    lines = [f"Library answer (not citable; cite the sources below): {res.get('answer', '').strip()}", "", "Sources:"]
    seen = set()
    terms, answer_text = query, res.get("answer", "")
    for ref in res.get("references") or []:
        title = (ref.get("title") or "").strip()
        summary = (ref.get("description") or "").strip()
        chunks = [c for c in ref.get("snippets") or [] if c.strip() and c.strip() != summary]   # the summary is model-written
        if chunks:
            doc_id = ref.get("url") or title                    # a Library reference's url is the document id
            ranked = sorted(((len(_words(c) & (_words(terms) - _STOP)), i, c) for i, c in enumerate(chunks)), reverse=True)
            for _, _, chunk in ranked[:2]:
                text_ = _chunk_block(chunk, terms, answer_text)
                if not text_:
                    continue
                printed, pg = chunk_page(chunk), doc_pages(doc_id)
                page = page_holding(text_, pg) if pg else None
                if page:
                    note = PAGES_NOTE + (f"; printed page {printed}" if printed else "")
                    where = f", page {page} of the file" + (f" (printed page {printed})" if printed else "")
                else:
                    note, where = CHUNK_PAGE_NOTE, f", page {printed} (from the chunk's page header)" if printed else ", page unknown"
                    if pg:
                        rows = _data_rows(text_)
                        on = sum(any(r in _squash(x) for r in rows) for x in pg)
                        why = "on several pages" if on > 1 else "not found on any page"
                        note = f"the passage's rows are {why} of the file's extracted text; {note}"
                        where += f"; its rows are {why} of the file"
                    page = printed
                bid = _source_id(ledger, "L")
                ledger.add_source(f"library:{library_id}:{doc_id}:{bid}", "library", title,
                                  {"block_id": bid, "type": "library", "library_id": library_id, "document_id": doc_id,
                                   "title": title, "page": page, "page_note": note, "page_index": 0, "n": 1,
                                   "bbox": None, "raw_text": text_, "text": normalise(text_), "block_scale": block_scale(text_)})
                lines.append(f"[{bid}] {title}{where}")
                lines += [f"  {x}" for x in text_.splitlines()]
            continue
        d = next((d for d in listed if d["name"] == title or d["name"].lower() in title.lower()
                  or title.lower() in d["name"].lower()), None)
        if d is None or d["id"] in seen:
            continue
        seen.add(d["id"])
        pg = doc_pages(d["id"])
        if pg:
            tagged = [(i + 1, ln) for i, p in enumerate(pg) for ln in p.splitlines() if ln.strip() and page_of(ln) is None]
            keep = passages([ln for _, ln in tagged], query, answer=res.get("answer", ""))
            if not keep:
                continue
            page = next(n for n, ln in tagged if ln == keep[0])
            src = [ln for ln in pg[page - 1].splitlines() if ln.strip() and page_of(ln) is None]
            start, end = _table_of(src, src.index(keep[0]))
            keep = [ln for ln in keep if ln in src[start:end]] if end - start > 1 else [ln for ln in keep if ln in src]
            table = src[start:end]
            head = [h for h in _header({"type": "table", "text": "\n".join(table)}) if h not in keep] if end - start > 1 else []
            unit = next((ln for ln in reversed(src[:start + 1]) if _UNIT_LINE.search(ln)), None)
            keep = ([unit] if unit and unit not in keep and not head else []) + head + keep
            bid = _source_id(ledger, "L")
            joined = "\n".join(keep)
            ledger.add_source(f"library:{library_id}:{d['id']}:{bid}", "library", d["name"],
                              {"block_id": bid, "type": "library", "library_id": library_id, "document_id": d["id"],
                               "title": d["name"], "page": page, "page_note": PAGES_NOTE, "page_index": 0, "n": 1,
                               "bbox": None, "raw_text": joined, "text": normalise(joined), "block_scale": block_scale(joined)})
            lines.append(f"[{bid}] {d['name']}, page {page} of the file")
            lines += [f"  {x}" for x in keep]
            continue
        try:
            body = (text or library_text)(library_id, d["id"])
        except Exception as e:  # noqa: BLE001
            lines.append(f"(not citable) {d['name']}: text could not be read ({e})")
            continue
        all_lines = [ln for ln in body.splitlines() if ln.strip()]
        content = [ln for ln in all_lines if page_of(ln) is None]          # running page headers are not content
        keep = passages(content, query, answer=res.get("answer", ""))
        if not keep:
            continue
        first = all_lines.index(keep[0])
        last = all_lines.index(keep[-1])
        unit = next((ln for ln in reversed(all_lines[:last]) if _UNIT_LINE.search(ln)), None)
        if unit and unit not in keep:
            keep = [unit] + keep                                # the unit the passage's figures are stated in
        page = next((page_of(ln) for ln in reversed(all_lines[:first + 1]) if page_of(ln) is not None), None)
        bid = _source_id(ledger, "L")
        joined = "\n".join(keep)
        ledger.add_source(f"library:{library_id}:{d['id']}:{bid}", "library", d["name"],
                          {"block_id": bid, "type": "library", "library_id": library_id, "document_id": d["id"],
                           "title": d["name"], "page": page, "page_note": PAGE_NOTE, "page_index": 0, "n": 1,
                           "bbox": None, "raw_text": joined, "text": normalise(joined), "block_scale": block_scale(joined)})
        lines.append(f"[{bid}] {d['name']}" + (f", page {page} (best-effort)" if page else ", page unknown"))
        lines += [f"  {x}" for x in keep]
    return "\n".join(lines)


# --- real Mistral clients (the tests use fakes) ---

def _key() -> str:
    k = os.environ.get("MISTRAL_API_KEY")
    if not k:
        raise ValueError("no Mistral API key: set MISTRAL_API_KEY, or log in to Vibe once (macOS Keychain entry "
                         "ai.mistral.vibe / MISTRAL_API_KEY)")
    return k


def _post(path: str, body: dict, timeout: int = 180) -> dict:
    import httpx
    r = httpx.post(f"{API}{path}", json=body, timeout=timeout, headers={"Authorization": f"Bearer {_key()}"})
    if r.status_code != 200:
        raise RuntimeError(f"Mistral {path} HTTP {r.status_code}: {r.text[:200]}")
    return r.json()


def _get(path: str) -> dict:
    import httpx
    r = httpx.get(f"{API}{path}", timeout=60, headers={"Authorization": f"Bearer {_key()}"})
    if r.status_code != 200:
        raise RuntimeError(f"Mistral {path} HTTP {r.status_code}: {r.text[:200]}")
    return r.json()


QA_PROMPT = """Answer the question from the attached document only. Every factual claim needs a citation.
Use placeholders [CITE_1], [CITE_2], ... in the answer, numbered from 1; one citation per atomic fact (two values from
different rows or sentences are two citations). For each placeholder give page_number (1 = first page of the file) and
quote: the exact text of the cited line or sentence copied verbatim from the document, including the figure; for a table,
the row label followed by that row's values. Reply with JSON only:
{"answer_with_citations": "...", "citations": [{"page_number": 1, "quote": "..."}]}"""


def mistral_qa(pdf_bytes: bytes, question: str) -> dict:
    data = "data:application/pdf;base64," + base64.b64encode(pdf_bytes).decode()
    r = _post("/chat/completions", {"model": QA_MODEL, "temperature": 0.1, "response_format": {"type": "json_object"},
                                    "messages": [{"role": "system", "content": QA_PROMPT},
                                                 {"role": "user", "content": [{"type": "text", "text": question},
                                                                              {"type": "document_url", "document_url": data}]}]})
    return json.loads(r["choices"][0]["message"]["content"])


def _strip_tags(t: str) -> str:
    return re.sub(r"<[^>]+>", "", t or "")


def _results(entry: dict):
    """A tool.execution result as parsed JSON: a JSON string, or a list of text chunks holding JSON."""
    res = (entry.get("info") or {}).get("result", entry.get("result"))
    texts = [res] if isinstance(res, str) else [c.get("text", "") for c in res or [] if isinstance(c, dict)]
    for t in texts:
        try:
            yield json.loads(t)
        except (TypeError, ValueError):
            continue


def parse_conversation(r: dict) -> dict:
    """The answer text and its sources. Snippets live in the tool.execution results (per source: url, title,
    description, snippets); the answer's tool_reference chunks carry only title and url. A finance data card (no url)
    becomes a source whose snippets are its description and its series."""
    answer, refs = [], {}
    for e in r.get("outputs") or []:
        if e.get("type") == "tool.execution":
            for j in _results(e):
                if isinstance(j, dict) and "content" in j and "description" in j:
                    card = (j.get("content") or {}).get("card_data") or {}
                    pts = [f"{p.get('x', '?')}: {p['y']:,.0f}" for sr in card.get("series") or []
                           for p in sr.get("points") or [] if isinstance(p, dict) and isinstance(p.get("y"), (int, float))]
                    title = card.get("title") or "Data card"
                    refs[f"card:{title}"] = {"title": title, "url": None, "description": _strip_tags(j["description"]),
                                             "snippets": [_strip_tags(j["description"])] + (["; ".join(pts)] if pts else [])}
                elif isinstance(j, dict):
                    for v in j.values():
                        if isinstance(v, dict) and v.get("url"):
                            refs.setdefault(v["url"], {"title": v.get("title"), "url": v["url"],
                                                       "description": _strip_tags(v.get("description") or ""),
                                                       "snippets": [_strip_tags(x) for x in v.get("snippets") or []]})
        elif e.get("type") == "message.output":
            content = e.get("content")
            chunks = content if isinstance(content, list) else [content] if isinstance(content, dict) else \
                [{"type": "text", "text": content}] if content else []
            for c in (c for c in chunks if isinstance(c, dict)):
                if c.get("type") == "text":
                    answer.append(c.get("text") or "")
                elif c.get("type") == "tool_reference" and c.get("url"):
                    refs.setdefault(c["url"], {"title": c.get("title"), "url": c["url"],
                                               "description": _strip_tags(c.get("description") or ""), "snippets": []})
    return {"answer": "".join(answer), "references": list(refs.values())}


def mistral_conversation(query: str, tool: str, library_id: str | None = None) -> dict:
    """The Conversations API with a built-in tool (web_search or document_library)."""
    t = {"type": tool} if tool == "web_search" else {"type": "document_library", "library_ids": [library_id]}
    return parse_conversation(_post("/conversations", {"model": QA_MODEL, "inputs": query, "tools": [t], "store": False,
                                                       "instructions": "Use the tool to answer; never answer from memory alone."}))


def library_documents(library_id: str) -> list[dict]:
    r = _get(f"/libraries/{library_id}/documents?page_size=100")
    return [{"id": d["id"], "name": d.get("name") or d.get("file_name") or ""} for d in r.get("data") or r.get("documents") or []]


def library_pages(library_id: str, document_id: str, *, get=None, fetch_json=None) -> list[str]:
    """The document's pages as Markdown, in file order: the route returns a signed URL to the extraction
    ([{"pages": [{"markdown": ...}, ...]}]); text_content and search results carry no page numbers."""
    def _fetch(url):
        import httpx
        r = httpx.get(url, timeout=60)
        r.raise_for_status()
        return json.loads(r.content)
    url = (get or _get)(f"/libraries/{library_id}/documents/{document_id}/extracted-text-signed-url")
    data = (fetch_json or _fetch)(url if isinstance(url, str) else url.get("url"))
    first = data[0] if isinstance(data, list) else data
    return [p.get("markdown") or "" for p in first.get("pages") or []]


def library_text(library_id: str, document_id: str) -> str:
    return _get(f"/libraries/{library_id}/documents/{document_id}/text_content").get("text", "")


def http_get(url: str) -> str:
    import httpx
    r = httpx.get(url, timeout=30, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 (Receipts)"})
    r.raise_for_status()
    return r.text
