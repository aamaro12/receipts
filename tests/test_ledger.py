# tests/test_ledger.py
from traceable.ledger import Ledger

def test_documents_numbered_by_first_read_and_idempotent(tmp_path):
    lg = Ledger(tmp_path)
    b = [{"page_index": 0, "n": 1, "text": "a"}]
    assert lg.add_document("sha1", "/a.pdf", "m", [{"index": 0}], b) == "D1"
    assert lg.add_document("sha2", "/b.pdf", "m", [{"index": 0}], b) == "D2"
    assert lg.add_document("sha1", "/a.pdf", "m", [{"index": 0}], b) == "D1"

def test_block_ids_are_rewritten_with_doc_id(tmp_path):
    lg = Ledger(tmp_path)
    lg.add_document("s", "/a.pdf", "m", [], [{"page_index": 0, "n": 1, "text": "x"}, {"page_index": 1, "n": 1, "text": "y"}])
    assert lg.block("D1:p2:b1")["text"] == "y"

def test_calculations_and_checks(tmp_path):
    lg = Ledger(tmp_path)
    assert lg.add_calculation({"title": "t"}) == "C1"
    assert lg.add_calculation({"title": "u"}) == "C2"
    lg.add_check({"decision": "allow"})
    assert len(lg.load()["checks"]) == 1

def test_atomic_save_leaves_no_temp_files(tmp_path):
    lg = Ledger(tmp_path); lg.add_calculation({"title": "t"})
    assert [p.name for p in lg.dir.iterdir() if p.name.endswith(".tmp")] == []


def test_pages_merge_into_the_same_document(tmp_path):
    lg = Ledger(tmp_path)
    p1, p2 = [{"page_index": 0, "n": 1, "text": "a"}], [{"page_index": 1, "n": 1, "text": "b"}]
    assert lg.add_document("s", "/a.pdf", "m", [{"index": 0}], p1) == "D1"
    assert lg.add_document("s", "/a.pdf", "m", [{"index": 1}], p2) == "D1"
    assert lg.add_document("s", "/a.pdf", "m", [{"index": 0}, {"index": 1}], p1 + p2) == "D1"
    doc = lg.load()["documents"]["s"]
    assert [p["index"] for p in doc["pages"]] == [0, 1]
    assert [b["block_id"] for b in doc["blocks"]] == ["D1:p1:b1", "D1:p2:b1"]

def test_load_does_not_create_the_folder(tmp_path):  # a missing ledger is detected, not created
    lg = Ledger(tmp_path)
    assert lg.load()["documents"] == {} and not lg.exists() and not (tmp_path / ".traceable").exists()
