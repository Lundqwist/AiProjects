"""Create made-up test invoices in inbox/. All companies and numbers are fictional.

The last one is deliberately wrong (its total doesn't add up) and should end up in needs-review.
"""
from pathlib import Path

import pymupdf as fitz

INBOX = Path(__file__).parent / "inbox"

SAMPLES = [
    ("Nordlys Kontorsmaterial AB", "F-10231", "2026-10-02", "2026-11-01", 25,
     [("Kopieringspapper A4, 500 ark", 10, 59.0), ("Whiteboardpennor, 4-pack", 3, 89.0)]),
    ("Fjällbyte IT-konsult HB", "2026-117", "2026-10-15", "2026-11-14", 25,
     [("Konsulttimmar, nätverk", 6, 950.0), ("Switch 8 portar", 1, 1290.0)]),
    ("Bokhyllan Förlag AB", "INV-5542", "2026-09-20", "2026-10-20", 6,
     [("Lärobok: C# från grunden", 2, 449.0), ("Lärobok: Algoritmer", 1, 529.0)]),
    ("Kaffekvarnen Café", "K-0981", "2026-10-28", "2026-11-27", 12,
     [("Fika för 8 personer", 8, 65.0), ("Smörgåstårta, bit", 8, 95.0)]),
    # Deliberately wrong: printed total is 500 too high
    ("Granskog Städ AB", "GS-3310", "2026-10-05", "2026-11-04", 25,
     [("Kontorsstädning, oktober", 4, 1200.0)], 500.0),
]


def money(x: float) -> str:
    return f"{x:,.2f}".replace(",", " ").replace(".", ",") + " kr"


def make(supplier, number, inv_date, due, rate, lines, error=0.0) -> Path:
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)  # A4
    y = 60
    def text(x, s, size=11, bold=False):
        page.insert_text((x, y), s, fontsize=size, fontname="hebo" if bold else "helv")
    text(50, supplier, 18, True); y += 30
    text(50, "FAKTURA", 14, True); y += 25
    for label, value in [("Fakturanummer", number), ("Fakturadatum", inv_date), ("Förfallodatum", due)]:
        text(50, label); text(200, value); y += 18
    y += 20
    for x, h in [(50, "Beskrivning"), (330, "Antal"), (390, "À-pris"), (480, "Belopp")]:
        text(x, h, bold=True)
    y += 8; page.draw_line((50, y), (545, y)); y += 18
    subtotal = 0.0
    for desc, qty, price in lines:
        amount = qty * price
        subtotal += amount
        text(50, desc); text(330, str(qty)); text(390, money(price)); text(480, money(amount)); y += 18
    y += 10; page.draw_line((330, y), (545, y)); y += 20
    vat = round(subtotal * rate / 100, 2)
    for label, value, bold in [("Summa exkl. moms", subtotal, False), (f"Moms {rate} %", vat, False),
                               ("Att betala", subtotal + vat + error, True)]:
        text(330, label, bold=bold); text(480, money(value), bold=bold); y += 18
    y += 40
    text(50, "Bankgiro 123-4567 · Exempelföretag, endast testdata", 9)
    path = INBOX / f"{number}.pdf"
    doc.save(path)
    return path


if __name__ == "__main__":
    INBOX.mkdir(exist_ok=True)
    for s in SAMPLES:
        print("created", make(*s).name)
