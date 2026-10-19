# Milestone 1 validation

Executed October 19, 2026 on the development Windows machine. These are measured checks, not claims of general receipt or handwriting accuracy.

## Application checks

- Backend: 15 pytest cases passed. Includes 50/100-image queues with deterministic OCR, restart recovery, concurrent upload retry idempotency, corrupt-file isolation, two-page PDF rendering, validation, Decimal arithmetic, CSV quoting/Unicode/formula protection, stale revision rejection, manual verification after OCR failure, reprocessing protection, and verified-export invalidation.
- Ruff: backend, scripts, and packaging entry checks passed.
- Frontend: TypeScript and Vite production build passed.
- Browser: Chromium exercised a four-file upload using real PaddleOCR, including a PDF; autosave, review, verification, CSV preview/download, invalidation after edits, save-conflict recovery, and simulated export failure followed by successful retry passed. Root horizontal overflow checks passed at 1440px and 390px.
- Frontend production dependency audit: npm audit --omit=dev found zero vulnerabilities.
- UI finish review: disposition ship after correcting placeholder contrast and making export errors visible inside the dialog. The verdict applies to the reviewed UI and fixes, not backend or distribution certification.

One upstream Starlette/AnyIO deprecation warning appeared during pytest. It did not fail tests.

## Real OCR and offline operation

The 60-image evaluation used real CPU PaddleOCR with outbound socket connections blocked. Five labeled header fields per receipt were compared exactly:

| Synthetic category | Header fields matched | Correct item counts |
|---|---:|---:|
| Printed, 20 images | 100 / 100 | 20 / 20 |
| Cursive font, 20 images | 98 / 100 | 20 / 20 |
| Mixed typography, 20 images | 100 / 100 | 20 / 20 |

Elapsed time was 219.328 seconds including startup; observed peak process RSS was 435.6 MB. These samples share a simple generated layout and are not an independent real-world accuracy benchmark. Item count is not a measurement of every item field's accuracy. Cursive typography is not real handwriting.

A separate real-OCR batch check accepted 50 receipt images and rejected one corrupt upload independently. An early receipt was manually corrected, verified, and exported with 49 other jobs still queued/processing. Restarting the API/worker lifecycle preserved that correction and verification; the remaining 49 receipts completed into review without duplicates.

That batch took 191.469 seconds and observed peak RSS was 496.1 MB. External socket connections were blocked; loopback remained available for Windows asyncio. This measures an in-process worker/application lifecycle restart, not forced power loss. Tests use approximately one-megapixel synthetic inputs; 32-megapixel limits are protective bounds, not a measured large-image throughput guarantee.

Generated evidence is local and ignored by Git:

- artifacts/ocr-evaluation.json
- artifacts/real-batch.json
- artifacts/screenshots/desktop.png, mobile.png, export-error.png
- artifacts/browser-export.csv

## Distribution limits

The PyInstaller directory bundle is an early packaging feasibility artifact. A successful OCR smoke check has run with Python absent from the child process PATH. The repeatable package check also validates the packaged API, native SQLite, current index, and its referenced frontend assets. See artifacts/package-check.json for the latest package probe result.

This machine already has developer prerequisites installed. No clean Windows VM, separate CPU-only computer, signed installer, upgrade migration, or real handwritten receipt corpus was available for validation. Those claims must not be inferred from these checks. Tauri and installer/update validation remain milestone 4.

## Reproduce

See README.md for setup, pytest, Ruff, build, browser, sample generation, and evaluation commands. Real batch acceptance is run with:

~~~powershell
.\.venv\Scripts\python.exe scripts\check_real_batch.py
.\.venv\Scripts\python.exe scripts\check_package.py
~~~

Each real batch check creates a separate workspace under artifacts and leaves its evidence intact. Model setup must complete first. Browser tests add fixtures to the running server's workspace; use a test workspace.
