"""Read invoices (PDF or image) with a local vision model and check the result."""
from __future__ import annotations

import csv
import json
import shutil
from datetime import date
from pathlib import Path

import ollama
import pymupdf
from pydantic import BaseModel, Field, ValidationError, field_validator

MODEL = "gemma3:12b"
VALID_VAT_RATES = {0, 6, 12, 25}  # Swedish VAT rates, in percent
TOLERANCE = 1.0  # rounding slack, in the invoice's currency
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}

PROMPT = """Extract the data from this invoice. Use only what is printed on it.
Dates as YYYY-MM-DD. Amounts as plain numbers without currency or thousands separators.
'amount' on a line is quantity x unit price, excluding VAT.
'total' is the final amount to pay, including VAT. 'vat_rate' is the VAT percentage."""


class Line(BaseModel):
    description: str
    quantity: float
    unit_price: float
    amount: float


class Invoice(BaseModel):
    supplier: str
    invoice_number: str
    invoice_date: date
    due_date: date
    currency: str = Field(description="ISO code such as SEK or EUR")
    lines: list[Line]

    @field_validator("currency")
    @classmethod
    def iso_currency(cls, v: str) -> str:
        v = v.strip().upper().rstrip(".")
        return {"KR": "SEK", ":-": "SEK", "€": "EUR", "$": "USD"}.get(v, v)
    vat_rate: float
    vat_amount: float
    total: float


def page_images(path: Path, dpi: int = 150) -> list[bytes]:
    """PDF pages (max 3) as PNG bytes; an image file as itself."""
    if path.suffix.lower() in IMAGE_SUFFIXES:
        return [path.read_bytes()]
    with pymupdf.open(path) as doc:
        return [page.get_pixmap(dpi=dpi).tobytes("png") for page in list(doc)[:3]]


def extract(path: Path) -> Invoice:
    resp = ollama.chat(
        model=MODEL,
        messages=[{"role": "user", "content": PROMPT, "images": page_images(path)}],
        format=Invoice.model_json_schema(),
        options={"temperature": 0},
    )
    return Invoice.model_validate_json(resp.message.content)


def check(inv: Invoice) -> list[str]:
    """Arithmetic and sanity checks the model can't be trusted with."""
    problems = []
    for i, line in enumerate(inv.lines, 1):
        if abs(line.quantity * line.unit_price - line.amount) > TOLERANCE:
            problems.append(f"line {i}: {line.quantity} x {line.unit_price} != {line.amount}")
    subtotal = sum(l.amount for l in inv.lines)
    if inv.vat_rate not in VALID_VAT_RATES:
        problems.append(f"VAT rate {inv.vat_rate}% is not a Swedish rate")
    if abs(subtotal * inv.vat_rate / 100 - inv.vat_amount) > TOLERANCE:
        problems.append(f"VAT {inv.vat_amount} != {inv.vat_rate}% of {subtotal:.2f}")
    if abs(subtotal + inv.vat_amount - inv.total) > TOLERANCE:
        problems.append(f"lines {subtotal:.2f} + VAT {inv.vat_amount} != total {inv.total}")
    if inv.due_date < inv.invoice_date:
        problems.append("due date is before invoice date")
    if not inv.lines:
        problems.append("no line items found")
    return problems


CSV_FIELDS = ["file", "supplier", "invoice_number", "invoice_date", "due_date",
              "currency", "subtotal", "vat_rate", "vat_amount", "total"]


def process(path: Path, out_dir: Path) -> tuple[bool, str]:
    """Extract, check, and file one invoice. Returns (ok, message)."""
    review = out_dir / "needs-review"
    done = out_dir / "processed"
    review.mkdir(parents=True, exist_ok=True)
    done.mkdir(parents=True, exist_ok=True)
    try:
        inv = extract(path)
        problems = check(inv)
    except (ValidationError, ValueError) as e:
        inv, problems = None, [f"could not read invoice: {e}"]

    if problems:
        shutil.move(path, review / path.name)
        (review / f"{path.name}.txt").write_text(
            "\n".join(problems) + ("\n\n" + inv.model_dump_json(indent=2) if inv else ""), encoding="utf-8")
        return False, "; ".join(problems)

    csv_path = out_dir / "invoices.csv"
    new = not csv_path.exists()
    # utf-8-sig writes a BOM on a new file so Excel shows å, ä, ö correctly
    with csv_path.open("a", newline="", encoding="utf-8-sig" if new else "utf-8") as f:
        w = csv.DictWriter(f, CSV_FIELDS)
        if new:
            w.writeheader()
        w.writerow({"file": path.name, **inv.model_dump(include=set(CSV_FIELDS)),
                    "subtotal": round(sum(l.amount for l in inv.lines), 2)})
    (done / f"{path.stem}.json").write_text(inv.model_dump_json(indent=2), encoding="utf-8")
    shutil.move(path, done / path.name)
    return True, f"{inv.supplier} {inv.invoice_number}: {inv.total} {inv.currency}"
