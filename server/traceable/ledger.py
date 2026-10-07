"""Per-folder JSON ledger. Standard library only."""
from __future__ import annotations
import json, os, pathlib, tempfile, time

class Ledger:
    def __init__(self, root: pathlib.Path):
        self.root = pathlib.Path(root)

    @property
    def dir(self) -> pathlib.Path:
        d = self.root / ".traceable"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def path(self) -> pathlib.Path:
        return self.root / ".traceable" / "ledger.json"

    def exists(self) -> bool:
        return self.path.exists()

    def load(self) -> dict:
        if self.path.exists():
            return json.loads(self.path.read_text())
        return {"documents": {}, "calculations": [], "checks": []}

    def save(self, data: dict) -> None:
        fd, tmp = tempfile.mkstemp(dir=self.dir, suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=1, default=str)
        os.replace(tmp, self.path)

    def add_document(self, sha: str, path: str, model: str, pages: list[dict], blocks: list[dict]) -> str:
        """Add a document, or merge new pages into it (same doc_id); pages already present are kept as they are."""
        data = self.load()
        doc = data["documents"].get(sha)
        created = doc is None
        if created:
            doc = {"doc_id": f"D{len(data['documents']) + 1}", "path": path, "model": model, "pages": [], "blocks": []}
            data["documents"][sha] = doc
        have = {p["index"] for p in doc["pages"]} | {b["page_index"] for b in doc["blocks"]}
        new = ({p["index"] for p in pages} | {b["page_index"] for b in blocks}) - have
        if not new and not created:
            return doc["doc_id"]
        doc["pages"] = sorted(doc["pages"] + [p for p in pages if p["index"] in new], key=lambda p: p["index"])
        for b in blocks:
            if b["page_index"] in new:
                doc["blocks"].append({**b, "block_id": f"{doc['doc_id']}:p{b['page_index'] + 1}:b{b['n']}"})
        doc["blocks"].sort(key=lambda b: (b["page_index"], b["n"]))
        self.save(data)
        return doc["doc_id"]

    def add_blocks(self, sha: str, blocks: list[dict]) -> None:
        """Add or refresh blocks of a document, by block_id (verified quotes from ask_document)."""
        data = self.load()
        doc = data["documents"][sha]
        have = {b["block_id"]: i for i, b in enumerate(doc["blocks"])}
        for b in blocks:
            if b["block_id"] in have:
                doc["blocks"][have[b["block_id"]]] = b
            else:
                doc["blocks"].append(b)
        self.save(data)

    def add_source(self, key: str, kind: str, path: str, block: dict) -> str:
        """A web or library source: one document holding one citable block whose ID is the block's (W1, L1)."""
        data = self.load()
        data["documents"][key] = {"doc_id": block["block_id"], "path": path, "kind": kind, "pages": [], "blocks": [block]}
        self.save(data)
        return block["block_id"]

    def add_calculation(self, record: dict) -> str:
        data = self.load()
        calc_id = f"C{len(data['calculations']) + 1}"
        data["calculations"].append({**record, "calc_id": calc_id, "created_at": time.time()})
        self.save(data)
        return calc_id

    def add_check(self, record: dict) -> None:
        data = self.load()
        data["checks"].append({**record, "at": time.time()})
        self.save(data)

    def block(self, block_id: str) -> dict | None:
        for doc in self.load()["documents"].values():
            for b in doc["blocks"]:
                if b["block_id"] == block_id:
                    return b
        return None
