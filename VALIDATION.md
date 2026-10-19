# Milestone 1 validation

Executed October 19, 2026 on the development Windows machine. These are measured checks, not claims of general receipt or handwriting accuracy.

## Application checks

- Backend: 23 pytest cases passed. Includes 50/100-image queues with deterministic OCR, restart recovery, concurrent upload retry idempotency, corrupt-file isolation, two-page PDF rendering, validation, Decimal arithmetic, CSV quoting/Unicode/formula protection, stale revision rejection, manual verification after OCR failure, reprocessing protection, and verified-export invalidation.
- Ruff: backend, scripts, and packaging entry checks passed.
- Frontend: TypeScript and Vite production build passed.
- Frontend Amount checks: 9 decimal-string multiplication/rounding examples passed, including fractional cents, negatives, large values, and invalid inputs.
- Browser: Chromium exercised a four-file upload using real PaddleOCR, including a PDF; autosave, review, verification, CSV preview/download, invalidation after edits, save-conflict recovery, and simulated export failure followed by successful retry passed. Expense wording, optional photo upload, explicit discount allocation, and CSV/photo ZIP export and re-import passed. Root horizontal overflow checks passed at 1440px and 390px. Early browser failures were test timing races; waits now require the new batch, completed allocation, and finished import preview.
- Frontend production dependency audit: npm audit --omit=dev found zero vulnerabilities.
- UI finish review: disposition ship for the follow-up expense form using settled desktop/mobile captures, with no material fixes. Existing placeholder contrast and in-dialog export error behavior remain. The verdict applies to the reviewed UI, not backend or distribution certification.

Expense API checks cover ordered/deduplicated TRANS/SI/receipt codes; stable owned Item numbers; Unit/Type/Notes; exact-cent, explicitly confirmed discount allocation without automatic mutation; overages and zero/invalid/extreme quantities; owned photos and cross-receipt access rejection; optional item photos; CSV+selected images and ZIP round trips; unverified import state; preview without OCR; missing-photo notes; unsafe ZIP paths; malformed headers; transaction/file rollback; and schema-v1 backup/migration preserving verified revisions, old OCR drafts, and existing export bytes. Older summary-only verified records must gain required expense fields before generating the new format.

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
- artifacts/browser-export.zip
- .impeccable/review/desktop.png, mobile.png (settled expense review captures)

## Distribution limits

The PyInstaller directory bundle is an early packaging feasibility artifact. The final package check passed with Python absent from the child process PATH: real OCR, API, native SQLite, current frontend assets, CSV/photo ZIP import, unverified review, explicit verification, and ZIP export with the original photo bytes. Every frozen application module was compared against current source. This caught an archive built during an edit; the final archive was rebuilt and matched. See artifacts/package-check.json for the latest result.

This machine already has developer prerequisites installed. No clean Windows VM, separate CPU-only computer, signed installer, installer/update upgrade, or real handwritten receipt corpus was available for validation. Schema v1-to-v2 migration was tested separately, including backup, preserved verification, and unchanged old exports. The normal local workspace also passed receipt state/revision, document hash, and original-file preservation checks against its migration backup. Tauri and installer/update validation remain milestone 4.

## Reproduce

See README.md for setup, pytest, Ruff, build, browser, sample generation, and evaluation commands. Real batch acceptance is run with:

~~~powershell
.\.venv\Scripts\python.exe scripts\check_real_batch.py
.\.venv\Scripts\python.exe scripts\check_package.py
~~~

Each real batch check creates a separate workspace under artifacts and leaves its evidence intact. Model setup must complete first. Browser tests add fixtures to the running server's workspace; use a test workspace.
