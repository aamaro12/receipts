# tests/test_ocr.py
import json, pytest
from conftest import FIXTURES
from traceable.ledger import Ledger
from traceable.ocr import read_pdf, listing

class FakeOcr:
    """Answers like the OCR API: `pages` are 0-based and each returned page keeps its document index."""
    def __init__(self, fixture="relx_is.json"): self.calls, self.pages, self.fixture = 0, [], fixture
    def ocr(self, pdf_bytes, pages=None):
        self.calls += 1
        self.pages.append(pages)
        resp = json.load(open(FIXTURES / self.fixture))
        if pages is not None:
            resp["pages"] = [p for p in resp["pages"] if p["index"] in pages]
        return resp

class BrokenOcr:
    def ocr(self, pdf_bytes, pages=None): raise RuntimeError("network down")

def test_read_pdf_blocks_listing_and_cache(tmp_path):
    lg, cl = Ledger(tmp_path), FakeOcr()
    doc_id, blocks = read_pdf(FIXTURES / "relx_is.pdf", lg, cl)
    assert doc_id == "D1"
    table = [b for b in blocks if b["type"] == "table"][0]
    assert table["block_id"] == "D1:p1:b3" and table["block_scale"] == 6 and "9,434" in table["text"]
    lst = listing(blocks)
    assert "RELX Annual Report 2024" not in lst and "(heading, not citable) Consolidated income statement" in lst
    assert "[D1:p1:b3] | Revenue | 2 | 8,553 | 9,161 | 9,434 |" in lst and "[D1:p1:b2]" not in lst
    read_pdf(FIXTURES / "relx_is.pdf", lg, cl)
    assert cl.calls == 1
    assert (tmp_path / ".traceable/pages/D1-p1.png").exists()

def test_ocr_failure_leaves_ledger_unchanged(tmp_path):
    lg = Ledger(tmp_path)
    with pytest.raises(RuntimeError):
        read_pdf(FIXTURES / "relx_is.pdf", lg, BrokenOcr())
    assert lg.load()["documents"] == {}


def test_pages_are_one_based_passed_to_ocr_and_merged(tmp_path):
    lg, cl = Ledger(tmp_path), FakeOcr()
    doc_id, blocks = read_pdf(FIXTURES / "relx_is.pdf", lg, cl, pages=[2])
    assert doc_id == "D1" and cl.pages == [[1]] and {b["page_index"] for b in blocks} == {1}
    assert "[D1:p2:b4]" in listing(blocks) and "[D1:p1:" not in listing(blocks)
    doc_id, blocks = read_pdf(FIXTURES / "relx_is.pdf", lg, cl, pages=[1])
    assert doc_id == "D1" and "[D1:p1:b3]" in listing(blocks) and "[D1:p2:" not in listing(blocks)
    read_pdf(FIXTURES / "relx_is.pdf", lg, cl)                      # whole document: nothing dropped or duplicated
    docs = lg.load()["documents"]
    ids = [b["block_id"] for b in list(docs.values())[0]["blocks"]]
    assert len(docs) == 1 and len(ids) == len(set(ids)) and lg.block("D1:p1:b3") and lg.block("D1:p2:b4")
    assert sorted(f.name for f in (tmp_path / ".traceable/ocr").iterdir()) == sorted(
        f"{lg.load()['documents'].popitem()[0]}-mistral-ocr-latest-{k}-blocks.json" for k in ("p1", "p2", "all"))
    read_pdf(FIXTURES / "relx_is.pdf", lg, cl, pages=[2])            # already in the ledger: no OCR call
    assert cl.calls == 3
