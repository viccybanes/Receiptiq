# ReceiptIQ

ReceiptIQ turns receipt images into editable, locally stored records and verified CSV exports. This repository implements milestone 1: batch image/PDF uploads, CPU PaddleOCR, manual review, and a fixed CSV format.

## Run on Windows

Development setup requires an existing Python installation for bootstrap and Node.js 22. Setup downloads a managed Python 3.11 environment, pinned dependencies, and OCR assets. Internet access is required initially.

From this folder in PowerShell:

~~~powershell
.\scripts\setup.ps1
.\scripts\start.ps1
~~~

Open **http://127.0.0.1:8765**. Stop with Ctrl+C. For frontend hot reload, keep the backend running and run npm run dev inside frontend, then open http://127.0.0.1:5173.

After setup, receipt processing uses no cloud service or API key and works offline. PaddleX brings cloud SDKs as upstream dependencies; ReceiptIQ does not call them. OCR is English-oriented initially. Handwritten or unusual layouts often need manual entry.

The full Windows installer, Tauri host, hardware-aware setup, vision, and RAG belong to later milestones. The portable Python bundle below is an early feasibility build.

## Process a batch

1. Choose or drop tens of JPG/PNG images. PDFs are supported. Each image represents one receipt; a PDF represents one receipt across its pages.
2. Files transfer individually into durable queued jobs. One OCR task runs at a time. Review completed receipts while the rest process.
3. Select a receipt. Compare the image and fields, inspect recognized text, and correct values. Fields autosave; failed saves retain the draft in the page. Resolve conflicts before navigating away.
4. Enter the reviewer name, correct blocking issues, and acknowledge remaining warnings with a reason. Verify the receipt.
5. Select verified receipts, preview the CSV, and download. Unfinished/unverified receipts are excluded. Editing verified values removes export eligibility until verified again.

Batch controls cancel unfinished work or retry failed processing. Failed uploads are separate: open batch details and reselect missing files. Accepted files survive restart; files never transferred must be selected again. Cancelling native OCR takes effect at a safe boundary, usually after the current page.

Limits: 25 MB per uploaded file, 32 megapixels per image/rendered page, 30 pages per PDF, and 500 files per batch. Tests exercise 50- and 100-image queues. The interface lists up to 500 receipts in a view; use the batch selector for older work.

EXIF rotation and small-angle deskew run automatically. The rotate control rotates 90 degrees and reruns OCR. Reprocessing preserves human edits and stores a separate OCR draft. Selecting a field highlights its source region on the enhanced image; source view and untouched-original download remain available.

## CSV behavior

The default template emits one row per item, with receipt fields repeated. A reviewed receipt without items emits one summary row. **Do not sum repeated receipt-level totals across item rows; group by receipt ID.**

Columns: receipt_id, supplier, receipt_number, receipt_date, currency, item_description, quantity, unit_price, line_total, subtotal, tax, discount, total.

Values use decimal strings and Python Decimal for validation, ISO dates, UTF-8 with BOM, comma separators, and standard CSV quoting. Empty values stay blank. Text that could execute as a spreadsheet formula receives a leading apostrophe. Generated files and verified snapshots remain reproducible after later edits.

## Data and privacy

Default workspace: %LOCALAPPDATA%\ReceiptIQ\. It contains SQLite, untouched originals, rendered pages, model assets, and exports. Application code and user data are separate. Set RECEIPTIQ_DATA_DIR before setup/start to use another local directory; do not put an active SQLite workspace on a network share.

~~~powershell
$env:RECEIPTIQ_DATA_DIR = 'D:\ReceiptIQData'
.\scripts\setup.ps1
.\scripts\start.ps1
~~~

This milestone does not encrypt data or provide user accounts. Protect the Windows account and disk. To back up this development release, stop the application and copy the whole workspace, including originals and SQLite sidecar files if present. Automatic backup/update workflows arrive with desktop distribution.

The API binds to loopback, validates host/origin, and requires a per-launch session token. Files are served by database ID. Uploaded originals never enter Git. Export history contains the data exported at that time; removing a receipt does not erase downloaded CSV copies or historical export snapshots.

## Architecture

- frontend/src: React batch workspace, image evidence, editable review, export preview.
- backend/app/main.py: FastAPI routes and local access controls.
- backend/app/db.py: SQLite schema, transactions, audit storage.
- backend/app/imaging.py, ocr.py, extraction.py: preprocessing, local Paddle adapter, conservative parsing and validation.
- backend/app/worker.py: durable single-worker queue and restart recovery.
- backend/app/service.py: review revisions, verification rules, CSV snapshots.

API routes use /api/v1. Create a batch manifest, upload each file with its batch/upload IDs, poll states, edit using the expected revision, verify, and export verified IDs. See [PLAN.md](PLAN.md) for the complete API and four-milestone architecture.

Dependencies are pinned in backend/requirements.lock and frontend/package-lock.json. Paddle/PaddleX and legacy LangChain pins are intentional: PaddleX 3.0 imports legacy modules even for OCR. Model and support-font downloads happen explicitly during setup.

## Validate

~~~powershell
.\.venv\Scripts\python.exe -m pytest backend/tests -q
.\.venv\Scripts\ruff.exe check backend scripts
Push-Location frontend
npm run build
npx playwright install chromium
npm run test:browser
Pop-Location
~~~

Browser checks require the server on port 8765 and generated samples. They add synthetic test receipts to the active workspace. Use a separate test workspace to avoid mixing them with real receipts.

~~~powershell
.\.venv\Scripts\python.exe scripts\make_samples.py --count 60
.\.venv\Scripts\python.exe scripts\evaluate_ocr.py --limit 60
~~~

The evaluator blocks outbound socket connections and writes artifacts/ocr-evaluation.json. Cursive fonts simulate a difficult type style; they do **not** establish real handwriting accuracy. See [VALIDATION.md](VALIDATION.md) for executed checks and limits.

## Packaging feasibility

~~~powershell
.\scripts\package.ps1
.\dist\receiptiq\receiptiq.exe --ocr-smoke .\artifacts\samples\001-printed.png
~~~

Run setup in the intended workspace first so models/support assets are cached. Ship the whole dist\receiptiq directory, not just its executable. The executable carries Python and native dependencies; models remain in the workspace. This is not yet a signed installer or a clean-machine distribution certification.
