"""Mistral OCR client, cache, blocks and page PNGs."""
from __future__ import annotations
import base64, hashlib, json, pathlib
from typing import Protocol
import httpx
from .ledger import Ledger
from .numbers import normalise, block_scale, table_captions

MODEL = "mistral-ocr-latest"
_CHROME = {"header", "footer"}

class OcrClient(Protocol):
    def ocr(self, pdf_bytes: bytes, pages: list[int] | None = None) -> dict: ...   # pages: 0-based, as the API expects

class MistralOcr:
    def __init__(self, api_key: str):
        self.api_key = api_key

    def _require_key(self) -> None:
        if not self.api_key:
            raise ValueError("no Mistral API key: set MISTRAL_API_KEY, or log in to Vibe once so the key is in the macOS "
                             "Keychain (service ai.mistral.vibe, account MISTRAL_API_KEY); cached documents need no key")

    def ocr(self, pdf_bytes: bytes, pages: list[int] | None = None) -> dict:
        self._require_key()
        body = {"model": MODEL, "include_blocks": True,
                "document": {"type": "document_url",
                             "document_url": "data:application/pdf;base64," + base64.b64encode(pdf_bytes).decode()}}
        if pages is not None:
            body["pages"] = pages
        r = httpx.post("https://api.mistral.ai/v1/ocr", json=body, timeout=280,
                       headers={"Authorization": f"Bearer {self.api_key}"})
        if r.status_code != 200:
            raise RuntimeError(f"OCR HTTP {r.status_code}: {r.text[:200]}")
        return r.json()

def _render_pages(path: pathlib.Path, pages: list[dict], out_dir: pathlib.Path, doc_id: str) -> None:
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(str(path))
    out_dir.mkdir(parents=True, exist_ok=True)
    for p in pages:
        target = out_dir / f"{doc_id}-p{p['index'] + 1}.png"
        if target.exists():
            continue
        page = pdf[p["index"]]
        w, _ = page.get_size()
        dims = p.get("dimensions") or {}
        img = page.render(scale=(dims.get("width") or 1000) / w).to_pil()
        img.save(target)

def _ocr(ledger: Ledger, client: OcrClient, data: bytes, sha: str, want: list[int] | None) -> dict:
    """OCR response for the 1-based pages `want` (None: whole document), cached per page list."""
    folder = ledger.dir / "ocr"
    cache = folder / f"{sha}-{MODEL}-{'all' if want is None else 'p' + '-'.join(map(str, want))}-blocks.json"
    for f in (cache, folder / f"{sha}-{MODEL}-all-blocks.json"):
        if f.exists():
            return json.loads(f.read_text())
    resp = client.ocr(data) if want is None else client.ocr(data, [p - 1 for p in want])
    folder.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(resp))
    return resp

def read_pdf(path: pathlib.Path, ledger: Ledger, client: OcrClient, pages: list[int] | None = None):
    """Read a PDF (all pages, or the 1-based `pages`) into the ledger; return (doc_id, blocks of those pages)."""
    path = pathlib.Path(path)
    data = path.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    want = sorted(set(pages)) if pages else None
    doc = ledger.load()["documents"].get(sha)
    if doc and want and {p - 1 for p in want} <= {p["index"] for p in doc["pages"]}:
        return doc["doc_id"], [b for b in doc["blocks"] if b["page_index"] + 1 in want]
    resp = _ocr(ledger, client, data, sha, want)
    page_meta, blocks = [], []
    for p in resp["pages"]:
        if want and p["index"] + 1 not in want:
            continue
        page_meta.append({"index": p["index"], "dimensions": p.get("dimensions")})
        page_blocks = p.get("blocks") or []
        captions = table_captions([{"type": b.get("type"), "text": b.get("content") or ""} for b in page_blocks])
        for n, (b, cap) in enumerate(zip(page_blocks, captions), start=1):
            raw = b.get("content") or ""
            blocks.append({"page_index": p["index"], "n": n, "type": b.get("type"),
                           "bbox": [b.get("top_left_x"), b.get("top_left_y"), b.get("bottom_right_x"), b.get("bottom_right_y")],
                           "raw_text": raw, "text": normalise(raw), "block_scale": block_scale(raw),
                           **({"caption": normalise(cap)} if cap else {})})
    if want and len(page_meta) < len(want):
        raise RuntimeError(f"OCR returned {len(page_meta)} of the {len(want)} pages asked for")
    doc_id = ledger.add_document(sha, str(path), resp.get("model", MODEL), page_meta, blocks)
    _render_pages(path, page_meta, ledger.dir / "pages", doc_id)
    shown = {p["index"] for p in page_meta}
    doc = ledger.load()["documents"][sha]
    return doc_id, [b for b in doc["blocks"] if b["page_index"] in shown]

def listing(blocks: list[dict]) -> str:
    """One line per block line, each prefixed with its ID; titles are shown but not citable."""
    out = []
    for b in blocks:
        text = b.get("text", "").strip()
        if not text or b.get("type") in _CHROME:
            continue
        if b.get("type") == "title":
            out.append(f"(heading, not citable) {text.lstrip('# ')}")
            continue
        out.extend(f"[{b['block_id']}] {line}" for line in text.splitlines() if line.strip())
    return "\n".join(out)
