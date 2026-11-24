# 03 · Invoice automation

Drop an invoice (PDF or photo) in `inbox\` and get a checked row in `output\invoices.csv`. Invoices that don't add up go to `output\needs-review\` with the reason.

**Stack:** Ollama `gemma3:12b` (vision) · Pydantic structured output · PyMuPDF · watchdog

## How it works

1. PDF pages are rendered to PNG (PyMuPDF); photos are used as they are.
2. The images go to `gemma3:12b` with `format=Invoice.model_json_schema()`, so the model must answer in exactly the Pydantic schema: supplier, number, dates, currency, line items, VAT, total.
3. **Checks the model isn't trusted with** (`invoice.check`): quantity × price = line amount, VAT is a Swedish rate (25/12/6/0 %) and matches the subtotal, lines + VAT = total, due date after invoice date.
4. Passed → a CSV row (UTF-8 with BOM, so Excel shows å/ä/ö) and a JSON file in `output\processed\`. Failed → `needs-review\` with a `.txt` listing the problems.

## Run

```powershell
.\.venv\Scripts\Activate.ps1
cd 03-invoices
python make_samples.py    # 5 made-up invoices, the last one deliberately wrong
python run.py             # process inbox\ once
python run.py --watch     # keep processing new files
```

## Results

All 5 sample invoices are read correctly in ~3 s each, including one as a 90 dpi JPG. The deliberately wrong one is caught: `lines 4800.00 + VAT 1200.0 != total 6500.0`. See `05-eval/invoices.yaml` for the automated test.

## What I learned

- A vision model can replace a separate OCR step for clean invoices.
- Structured output (a JSON schema) removes almost all parsing problems; a validator still normalizes things like `kr` → `SEK`.
- The model should extract and the code should do the arithmetic.
