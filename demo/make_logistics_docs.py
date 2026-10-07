"""Generate the synthetic logistics pack for container MSKU4417302: three one-page PDFs and a SYNTHETIC marker file.

usage: uv run --project server --group dev python demo/make_logistics_docs.py
The arrival notice's verified gross weight (19,240 kg) differs from the bill of lading (18,420 kg) by 820 kg, or 4.45%,
above the 2% release limit the notice states; the declared value (USD 759,500.00) matches. Every page says it is
synthetic, and the parties are named "Example" so no real company is implied.
"""
import pathlib
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

OUT = pathlib.Path(__file__).resolve().parent / "logistics"
NOTE = "SYNTHETIC DOCUMENT generated for the Traceable Numbers demo. Not a real shipment."
DOCS = {
    "bill_of_lading.pdf": ("BILL OF LADING", [
        "B/L number: MEDU8841207", "Vessel: MV Aurelia", "Port of discharge: Rotterdam",
        "Shipper: Example Controls Ltd", "Consignee: Example Automation B.V.",
        "Container MSKU4417302, 1 x 40' HC", "Description: Industrial control units (HS 8537.10)",
        "Packages: 1,240 cartons", "Gross weight 18,420 kg"]),
    "commercial_invoice.pdf": ("COMMERCIAL INVOICE", [
        "Invoice number: EX-2024-0917", "Seller: Example Controls Ltd", "Buyer: Example Automation B.V.",
        "Container MSKU4417302", "Industrial control units (HS 8537.10)", "1,240 units at USD 612.50",
        "Total declared value USD 759,500.00", "Incoterm CIF Rotterdam"]),
    "arrival_notice.pdf": ("ARRIVAL NOTICE", [
        "B/L number: MEDU8841207", "Vessel: MV Aurelia, port of discharge Rotterdam", "Container MSKU4417302",
        "Gross weight 19,240 kg (verified weight at terminal)", "Declared value USD 759,500.00",
        "Release is withheld where verified gross weight differs from the B/L by more than 2%."]),
}

# The shipped PDFs predate invariant mode, so regenerating them once changes their bytes and orphans the shipped OCR
# cache (the demo then calls OCR and the eval stops). After that, runs are byte-identical. Do not regenerate for a demo.
def write(path: pathlib.Path, title: str, lines: list[str]) -> None:
    c = canvas.Canvas(str(path), pagesize=A4, invariant=1)   # no creation date: the same inputs give the same bytes
    _, height = A4
    c.setFont("Helvetica-Bold", 18)
    c.drawString(60, height - 80, title)
    c.setFont("Helvetica", 11)
    for i, line in enumerate(lines):
        c.drawString(60, height - 120 - 22 * i, line)
    c.setFont("Helvetica-Oblique", 9)
    c.drawString(60, 60, NOTE)
    c.save()

def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, (title, lines) in DOCS.items():
        write(OUT / name, title, lines)
    (OUT / "SYNTHETIC").write_text("")
    print(f"wrote {len(DOCS)} PDFs and SYNTHETIC to {OUT}")

if __name__ == "__main__":
    main()
