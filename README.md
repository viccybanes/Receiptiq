# ReceiptIQ

ReceiptIQ turns batches of receipt images into editable local records and verified CSV exports. Version 0.4.0 adds optional local vision, opt-in retrieval, reviewer handoff, CSV formats, backups, and a Windows desktop installer. This is an unsigned preview; see [VALIDATION.md](VALIDATION.md) for measured checks and remaining release gates.

## Windows desktop

Install the versioned **ReceiptIQ Desktop** setup executable from `artifacts/desktop` after building. The installer includes Python, PaddleOCR, and native dependencies; end users need no Python, Node, or Rust installation. WebView2 is required and its bootstrapper can download it during installation if absent. The default program directory is `%LOCALAPPDATA%\ReceiptIQ Desktop`, separate from receipt data in `%LOCALAPPDATA%\ReceiptIQ`.

Open **Workspace settings → Local models**. Choose **OCR only** for printed text and manual entry, or **2B vision** for optional Qwen3-VL readings and similar verified examples. Setup explicitly downloads OCR assets, a pinned private Ollama runtime, and selected models. Downloads need internet; ordinary receipt processing uses local files and loopback only after setup. A failed model setup never prevents manual review or CSV export.

The development laptop's detected 15.7 GB RAM and 6 GB NVIDIA GPU receive the conservative 2B recommendation. Recommendations are capacity checks, not a latency or accuracy guarantee. CPU-only machines can use OCR/manual review; CPU vision and the optional 4B profile have not been validated on a separate machine.

## Run on Windows

Development setup requires an existing Python installation for bootstrap and Node.js 22. Setup downloads a managed Python 3.11 environment, pinned dependencies, and OCR assets. Internet access is required initially.

From this folder in PowerShell:

~~~powershell
.\scripts\setup.ps1
.\scripts\start.ps1
~~~

Open **http://127.0.0.1:8765**. Stop with Ctrl+C. For frontend hot reload, keep the backend running and run npm run dev inside frontend, then open http://127.0.0.1:5173.

After setup, receipt processing uses no cloud service or API key and works offline. PaddleX brings cloud SDKs as upstream dependencies; ReceiptIQ does not call them. OCR is English-oriented initially. Handwritten or unusual layouts often need manual entry.

Development launch and installed desktop use the same backend and workspace format. The desktop supplies a per-launch token, supervises its bundled backend, and closes owned child processes when the window exits or crashes. Its private Ollama port is 11435; another Ollama installation on 11434 is untouched.

## Process a batch

Purchase date is displayed and entered as **MM/DD/YYYY**. Stored dates remain ISO for sorting and existing CSV templates.
OCR and vision normalize clear numeric dates such as `8/27/26` or `27/8/2026`;
ambiguous readings such as `08/09/26` need review rather than a guessed month/day.
Vision dates appear in the suggestion first and fill the form when it is applied.

Local vision is instructed to resolve ambiguous handwritten ordinary words from
the image and context (for example, PIVUT/PIVOT), while preserving printed text,
brands, codes, and numbers. This runs through **Use vision**, not the initial OCR
pass, and remains a reviewable suggestion. Handwriting classification and spelling
are model judgments, not guaranteed rules; check the source before applying.
Vendor extraction favors the first-page header and skips common receipt titles,
contact details, and customer labels instead of falling back to a priced item.
Vision suggestions include brief reading cautions for unclear dates, overwritten
dimensions, and handwritten interpretations. These stay outside saved Notes and
CSV fields. Python checks the suggested line arithmetic and Total without filling
missing prices or allocating a discount. Retrieved verified wording corrections
are supplied as before/after examples, never as historical numeric facts. These
changes guide the local model; they do not train its weights or establish accuracy
on real handwriting. Correct, verify, and opt in receipts to improve references.

1. Choose or drop tens of JPG/PNG images. PDFs are supported. Each image represents one receipt; a PDF represents one receipt across its pages.
2. Files transfer individually into durable preview jobs while the current review stays open. Vision runs automatically after each uploaded preview and applies to untouched receipts; use Run OCR explicitly when needed. One processing task runs at a time.
3. Select a receipt. Compare the image and fields, inspect recognized text, and correct values. Fields autosave; failed saves retain the draft in the page. Resolve conflicts before navigating away.
4. Enter the reviewer name, correct blocking issues, and acknowledge remaining warnings with a reason. Verify the receipt.
5. Select verified receipts, preview the CSV, and download. Unfinished/unverified receipts are excluded. Editing verified values removes export eligibility until verified again.

Batch controls cancel unfinished work or retry failed processing. Failed uploads are separate: open batch details and reselect missing files. Accepted files survive restart; files never transferred must be selected again. Cancelling native OCR takes effect at a safe boundary, usually after the current page.

Limits: 25 MB per uploaded file, 32 megapixels per image/rendered page, 30 pages per PDF, and 500 files per batch. Tests exercise 50- and 100-image queues. The interface lists up to 500 receipts in a view; use the batch selector for older work.

EXIF rotation and small-angle deskew run automatically. The rotate control turns the preview 90 degrees without OCR and remembers the view in this browser. New uploads prepare previews, then queue and automatically apply local vision using the configured model. One worker processes these jobs sequentially. Manual edits during processing are preserved and leave the result as a suggestion; verification is always manual. Use Run OCR when needed. Reprocessing preserves human edits and stores a separate OCR draft. Selecting a field highlights its source region on the enhanced image; source view and untouched-original download remain available.

## Optional reading and review tools

- **Use vision** reads the selected page. **Select region**, then **Read this region again**, limits the reading to a crop. Current OCR and names/units from eligible examples provide context. A proposal shows changed values and provenance; nothing changes until **Apply vision suggestion**. A saved intervening edit makes the proposal stale. Model errors, cancellation, and timeouts preserve the current receipt.
- **Similar saved receipts** searches only current verified revisions explicitly allowed for future suggestions. Results cite source receipt/revision and offer Vendor or item wording; quantities and historical prices are excluded. Opting out, editing, deleting, or requesting a second review immediately excludes that receipt, even if a stale vector exists. Retrieval supplies context, not model training.
- **Review assignment** stores a local reviewer label and a second-review flag. The assigned name must verify a second-review receipt. Names are handoff labels, not accounts or access controls. Queue filters and priority help work through uncertain receipts. Ctrl+S saves, Alt+Right selects the next receipt, and Alt+Down focuses the next uncertain field.
- Identical original hashes show duplicate warnings without silently discarding files. A multi-page PDF can be split into explicit page groups; every source page must belong to exactly one group. The original stays available as split evidence and the new receipts require review.

One worker runs heavy OCR, vision, or indexing work at a time. Index failure never undoes verification or blocks export. Local search stores 256-dimensional vectors in SQLite and scans at most 10,000 eligible entries; no vector server is needed.

## CSV behavior

The expense template emits one row per item, with receipt fields repeated. At least one line item is required for verification. **Do not sum repeated receipt-level totals across item rows; group by Receipt ID.**

Columns: Receipt ID, Purchase date, Vendor, Receipt number, Receipt photo, Notes, Item number, Description, Quantity, Unit, Unit price, Amount, Type, Photo of item purchased, Total.

The review form follows the expense-entry wording. Type defaults to Materials and accepts workplace-specific text. Receipt numbers combine distinct labeled codes in reading order, for example TRANS #: 00-0045 and SI #: 00-0029 become 00-0045 / 00-0029. Item numbers are assigned locally on save and remain stable across edits and reordering.

Tax, subtotal, and discount controls are omitted. Existing legacy values remain stored for compatibility. Compare Amount against Quantity × Unit price, then compare the sum of Amount against Total. Differences are review warnings, never automatic discounts. If a receipt-level difference is confirmed as a discount, the explicit allocation action distributes net Amounts proportionally in cents and recalculates effective Unit prices. Notes record the confirmed adjustment. An overage is flagged for missing items/prices/charges instead.

JPG/PNG item photos are optional. Receipt photo uses the uploaded original by default; a replacement photo can be attached. Download CSV produces filenames only. Download CSV + photos produces a ZIP containing receipts.csv and the referenced original receipt images/PDFs and optional item photos under relative paths. A receiving application's importer must resolve those paths to attach the files; CSV itself cannot carry image attachments.

Import CSV + photos accepts either one ZIP containing exactly one CSV and its images, or one CSV with referenced photos selected together. Headers use the column names above; Purchase date, Vendor, Description, Quantity, Unit price, and Amount are required columns. Receipt ID groups rows; without it, matching receipt details group rows. Supply Total explicitly; if omitted, it is inferred from Amount and marked in Notes for checking. ISO and day/month/year dates are accepted. Missing referenced photos are reported in Notes. Imports always require fresh verification and do not rerun OCR over the structured fields. Limits: 2 MB CSV, 5,000 rows, 500 receipts, 200 MB uploaded bundle, 500 MB expanded ZIP, and 25 MB per photo. New local item numbers are assigned on import.

Values use decimal strings and Python Decimal for validation, ISO dates, UTF-8 with BOM, comma separators, and standard CSV quoting. Empty values stay blank. Text that could execute as a spreadsheet formula receives a leading apostrophe. Generated files and verified snapshots remain reproducible after later edits.

The default expense format remains unchanged. **Workspace settings → CSV formats** can create workplace templates with renamed/reordered columns, constants, required fields, one row per item or receipt, comma/semicolon/tab separators, UTF-8/BOM, date format, and decimal separator. Select the format before previewing. Preview/export checks the template version; changing it requires another preview. Each export retains its own template and verified data snapshots. Custom export formats are not automatically import formats; import uses the documented expense headers above.

## Data and privacy

Default workspace: %LOCALAPPDATA%\ReceiptIQ\. It contains SQLite, untouched originals, rendered pages, model assets, and exports. Application code and user data are separate. Set RECEIPTIQ_DATA_DIR before setup/start to use another local directory; do not put an active SQLite workspace on a network share.

~~~powershell
$env:RECEIPTIQ_DATA_DIR = 'D:\ReceiptIQData'
.\scripts\setup.ps1
.\scripts\start.ps1
~~~

ReceiptIQ does not encrypt data or provide user accounts. Protect the Windows account and disk. **Workspace settings → Backups** creates a consistent SQLite snapshot with originals, pages, attachments, exports, and settings. Downloaded ZIPs contain sensitive receipt data. Models and runtime caches are omitted. The newest ten completed backups are retained; migration/recovery snapshots remain.

Restore validates ZIP paths, checksums, database version, and referenced files before staging. Closing and reopening ReceiptIQ applies the staged restore offline, retains the previous workspace beside it, and reuses model caches. Limits are 2 GB compressed, 4 GB expanded, and 20,000 files. Larger workspaces require a stopped full-directory backup. Do not manually swap a running SQLite database.

Schema v3 preserves existing data, verification revisions, and export bytes. Opening v1/v2 first creates a full workspace ZIP plus a consistent `before-v2-*.sqlite3` or `before-v3-*.sqlite3` database snapshot. Older binaries reject the newer schema. Restore a stopped full pre-upgrade workspace to roll back; do not combine an old database with unrelated new files.

The Backups tab can stage a new empty local storage location. Restart copies and verifies the workspace, then changes `%LOCALAPPDATA%\ReceiptIQ-launch.json` only after success. The source remains for recovery. An explicit `RECEIPTIQ_DATA_DIR` overrides this setting. Installer upgrades and uninstall do not delete the external workspace. Updates are manual; no updater feed is configured.

The API binds to loopback, validates host/origin, and requires a per-launch session token. Files are served by database ID. Uploaded originals never enter Git. Export history contains the data exported at that time; removing a receipt does not erase downloaded CSV copies or historical export snapshots.

## Architecture

- frontend/src: React batch workspace, image evidence, editable review, export preview.
- backend/app/main.py: FastAPI routes and local access controls.
- backend/app/db.py: SQLite schema, transactions, audit storage.
- backend/app/imaging.py, ocr.py, extraction.py: preprocessing, local Paddle adapter, conservative parsing and validation.
- backend/app/worker.py: durable single-worker queue and restart recovery.
- backend/app/service.py: review revisions, verification rules, CSV snapshots.
- backend/app/transfer.py: bounded CSV/ZIP import and owned photo attachments.
- backend/app/vision.py, retrieval.py: bounded local model adapters and revision-checked wording evidence.
- backend/app/templates.py, features.py: workplace CSV formats and milestone API routes.
- backend/app/system.py, backups.py: hardware/setup/runtime ownership, storage relocation, snapshots and offline restore.
- src-tauri: Windows host, single-instance lifecycle, authenticated backend bootstrap, NSIS installer configuration.

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
npm run test:milestones
Pop-Location
~~~

Browser checks require a running server and generated samples. Set `RECEIPTIQ_TEST_URL` to a private test server (for example `http://127.0.0.1:8766`). They add synthetic receipts and templates to that workspace. Use a separate workspace to avoid mixing tests with real receipts.

~~~powershell
.\.venv\Scripts\python.exe scripts\make_samples.py --count 60
.\.venv\Scripts\python.exe scripts\evaluate_ocr.py --limit 60
.\.venv\Scripts\python.exe scripts\evaluate_vision.py --manage-runtime --with-ocr --out artifacts/vision-evaluation-final-ocr.json
.\.venv\Scripts\python.exe scripts\evaluate_retrieval.py
~~~

Evaluators use generated samples and write ignored reports under artifacts. Vision/retrieval need an installed 2B profile and exclusive use of its private runtime port; close ReceiptIQ first. Their Python socket guards block non-loopback connections while inference runs. Cursive fonts simulate a difficult type style; they do **not** establish real handwriting accuracy. See [VALIDATION.md](VALIDATION.md) for executed checks and limits.

## Build Windows distribution

~~~powershell
.\scripts\package.ps1
.\dist\receiptiq\receiptiq.exe --ocr-smoke .\artifacts\samples\001-printed.png
.\scripts\build_installer.ps1
.\.venv\Scripts\python.exe scripts\check_package.py
.\.venv\Scripts\python.exe scripts\check_desktop.py --executable '<installed ReceiptIQ executable>'
~~~

Build prerequisites are Windows x64, the development setup above, the Rust MSVC toolchain, and Visual Studio C++ build tools. `build_installer.ps1` builds frontend, freezes the complete Python directory bundle, and invokes pinned Tauri CLI with the Cargo lock. Use `-SkipBackendBuild` only when that bundle already matches current source. The NSIS installer and SHA-256 build manifest are copied to `artifacts/desktop`. Ship the installer, or the entire portable `dist/receiptiq` folder rather than its executable alone.

For Authenticode signing, set `RECEIPTIQ_CERT_THUMBPRINT` to an existing CurrentUser\My certificate with its private key and `RECEIPTIQ_TIMESTAMP_URL` to the issuer's timestamp service. No certificate is generated or imported. Without credentials the build is explicitly unsigned; clean Windows/CPU-only testing and a real handwritten corpus remain release gates. Pinned runtime/model versions and upstream license references are in [runtime-manifest.json](packaging/runtime-manifest.json) and [THIRD_PARTY_NOTICES.md](packaging/THIRD_PARTY_NOTICES.md).

Missing Quantity, Unit price, and Amount are completed when the other two values are available. Calculated amounts and matching totals update during edits; differing written amounts and totals remain for discount review. A missing Total uses the sum only when every line has an Amount. Uploads keep the current review open and show temporary uploading rows.

Failed vision jobs wait behind remaining queued work, then retry in failure order. Each job gets at most three attempts (the initial attempt plus two retries). Persistent failures remain available for manual retry. Cancellation stops deferred retries; retry state survives restart.

Inference remains sequential, with one loaded model, an 8,192-token vision context and two-minute keep-alive. Ollama can share model weights across concurrent requests, but context memory grows with parallelism: https://docs.ollama.com/faq#how-does-ollama-handle-concurrent-requests. The development GPU has 6 GB VRAM and currently selects Qwen3-VL 4B. Parallel throughput, latency, memory use and extraction quality need benchmarking before enabling two requests.
