# ReceiptIQ validation

## Partial vision recovery — 2026-10-25

- Backend: **284 tests passed** (115.12s), with one existing Starlette/AnyIO deprecation warning. Changed Python files passed Ruff and `git diff --check`.
- Mocked runtime replies prove recovery of completed fields/rows from truncated JSON, fenced/prefixed JSON, numeric JSON values, invalid counts/types, and item arrays. Unknown metadata and oversized fields are excluded; unfinished numeric strings are not completed. Malformed or less complete recounts retain the earlier extraction. Transport errors, response bounds, and cancellation remain enforced.
- An isolated FastAPI/SQLite upload test confirms recovered rows reach the review form, persist with a partial-response notice, finish without failure retries, and remain unverified. Intervening human edits are preserved as before. No user receipts or real model inference were used for these recovery checks.

## Vision unit and row checks — 2026-10-25

- Backend: **273 tests passed** (110.63s), with one existing Starlette/AnyIO deprecation warning. Changed Python files passed Ruff and `git diff --check`.
- Mocked local-runtime checks cover combined quantity/unit readings, conflicting units, fractional quantities, preservation of current data, one sequential image recheck on row-count mismatch, unresolved/unknown-count notices, and cancellation. The recheck resubmits the same selected images and returns a complete suggestion; it adds inference time only when the first audit finds a mismatch.
- These are deterministic adapter checks, not a new real-handwriting benchmark. Counts remain model-reported: matching counts cannot prove that every image row was read. The prompt now checks units beside quantities and scans occupied item rows from bottom to top, including faint, ditto, and final rows.

## Version 0.4.0 — milestones 2–4

Executed October 19–20, 2026 on the development Windows 11/NVIDIA laptop. This preview extends the milestone-1 evidence retained below. Small synthetic evaluations do not establish real-world receipt or handwriting accuracy.

- Backend: all 147 pytest cases passed after final changes (56.84s; one existing upstream Starlette/AnyIO deprecation warning). Coverage includes schema-v2 preservation, vision proposals/stale rejection/cancellation, opt-in retrieval and stale-source exclusion, template versions/formats, reviewer handoff, duplicate hashes, PDF grouping, backup validation/offline restore, runtime ownership, resumable setup, and storage relocation. Final regressions prove a selected page's vision suggestion retains other PDF-page items and all eight mutation routes reject retained split sources without changing data/files.
- Ruff and the pinned Python dependency consistency check passed. Frontend TypeScript/Vite build and all 9 decimal Amount checks passed. Existing Chromium expense/OCR/CSV/photo round-trip checks passed against the private test workspace.
- Runtime setup downloaded and verified the pinned Ollama 0.40.6 Windows archive SHA-256, Qwen3-VL 2B instruct, and EmbeddingGemma Q4. A Windows atomic JSON replace/read race found during polling was fixed and regression-tested. The private runtime was shut down after each standalone evaluation.
- Real Qwen3-VL 2B with real OCR context matched 15/15 header fields and 3/3 item counts on one printed, one cursive-font, and one mixed-typography sample. Model calls took 5.484s, 2.625s, and 2.688s respectively; complete evaluation including OCR/startup took 31.297s. Item Type remains application metadata and defaulted to Materials. This measures three generated examples, not all line-item fields or handwriting.
- Real EmbeddingGemma, 256 dimensions, ranked the intended source first for 8/8 held-out synthetic typo/context queries (8/8 top three). Median query time was 54.5ms after warmup; total successful run 22.937s. Observed Python RSS was 43 MB and the runtime process-tree RSS 771.1 MB. Opt-out, superseded revision, second-review/unverified, deleted/self-source, citation/correction, absence of historical prices, and unchanged target checks passed. Vendor/item text participates in cosine ranking; no explicit supplier/category filter is implemented.
- One initial embedding request hit its 30s production timeout and was preserved as a failed report. A separate measured 6.954s warmup and rerun passed; first normal index took 11.203s, later calls were faster. Cold-load latency on other machines is unproven; retry/manual workflow remains available.
- Real vision and retrieval evaluations blocked Python non-loopback socket connections, with Ollama cloud features disabled. This is application-level evidence, not a whole-OS firewall audit. No actual user receipts were used for retrieval evaluation.
- The normal local workspace migrated from schema 2 to 3 after a full ZIP and SQLite backup. Receipt status, revision, verified revision/data, document hashes/original bytes, and export snapshots were unchanged.
- New Chromium milestone checks passed six groups: hardware/settings and versioned format editing; real CSV/photo import and local assignment/verification/opt-in; real selected-format preview/download; real backup ZIP download and corrupt-restore rejection inside the modal; a real 2B page request returning a candidate in 7.406s without changing human data; and deterministic rare AI-state UI checks. Stale/apply/crop/cancel/error/similarity transport fixtures are explicitly labeled in the report; they are not claimed as real model inference. No uncaught JavaScript exceptions, unexpected console errors, or root/dialog horizontal overflow at 1440px and 390px. Regression checks cover compact profile radios, contained labels, the existing compact Total input, and removal of stale queued wording after completion.
- Fresh UI review returned **ship** for the listed setup/status fixes and their selector-specificity regression. It covers those scored fixes, not a general release certification. Design documentation records the resulting assistance/settings surfaces. The one bounded detector run returned zero findings; a stale prior comp-phase metadata warning is unrelated to this incumbent UI extension.
- Frontend production dependency audit found zero vulnerabilities. Native Rust format/check and the exact external-license allowlist test passed; PowerShell build scripts parsed successfully.

Evidence (generated locally, ignored by Git): `artifacts/vision-evaluation-final-ocr.json`, `artifacts/retrieval-evaluation.json`, `artifacts/retrieval-evaluation-cold-timeout.json`, `artifacts/workspace-v3-migration.json`, `artifacts/milestone-browser/report.json`, and `.impeccable/review/milestone-*.png`.

## Version 0.4.0 distribution

The complete PyInstaller bundle built successfully. The final package checker passed with Python absent from its child PATH: real CPU OCR, API and native SQLite, exact current frontend assets, CSV/photo ZIP import → verification → export, hardware/profile metadata, a custom CSV format, and a full workspace backup containing the original photo. Every frozen application module matched current source. Report: `artifacts/package-check.json`.

The NSIS installer built successfully: `artifacts/desktop/ReceiptIQ Desktop_0.4.0_x64-setup.exe`, 186,759,692 bytes. SHA-256: `2fe63c5637ce3a4110b35eece95deac1656957fc9ae4b9c8fce453e134611abf`. The build manifest records source commit `50a0248`, lock/runtime-manifest hashes, and `NotSigned`. The source tree was dirty only for documentation/design records and preserved user files at build time. Default installation uses `%LOCALAPPDATA%\ReceiptIQ Desktop`, separate from the `%LOCALAPPDATA%\ReceiptIQ` workspace.

An isolated silent installation launched the actual Tauri/WebView2 application with Python and Node absent from the child PATH. Unauthenticated session/health/batch requests returned 401; the rendered window's injected session authenticated protected requests with 200. Settings loaded real hardware/profile metadata, SQLite lived outside the installation, and killing the host removed its owned backend. The developer checker itself uses Node/Playwright to inspect the installed WebView; the application does not require either tool. Screenshot: `artifacts/desktop/desktop-smoke.png`.

All 7,680 installed backend resource files matched the portable bundle byte-for-byte. Same-version silent reinstall and uninstall both exited successfully and preserved SHA-256 hashes of four external synthetic fixtures: SQLite, original, settings, and template. The isolated program installation was removed; global workspace preferences and real user data were untouched. This is not an older-version installer upgrade test. Final evidence: `artifacts/desktop/release-check.json`, `desktop-check.json`, and `preservation-check.json`.

Publisher signing credentials, a clean Windows VM, a separate CPU-only computer, a 4B benchmark, and a real handwritten receipt corpus are unavailable. No automated update feed is configured. These remain release gates; this is an unsigned preview, not a signed general-availability release.

## Milestone 1 baseline

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

## Milestone 1 packaging baseline

The PyInstaller directory bundle is an early packaging feasibility artifact. The final package check passed with Python absent from the child process PATH: real OCR, API, native SQLite, current frontend assets, CSV/photo ZIP import, unverified review, explicit verification, and ZIP export with the original photo bytes. Every frozen application module was compared against current source. This caught an archive built during an edit; the final archive was rebuilt and matched. See artifacts/package-check.json for the latest result.

At milestone 1, no Tauri installer was available. This machine already had developer prerequisites installed. No clean Windows VM, separate CPU-only computer, signed installer, installer/update upgrade, or real handwritten receipt corpus was available for validation. Schema v1-to-v2 migration was tested separately, including backup, preserved verification, and unchanged old exports. The normal local workspace also passed receipt state/revision, document hash, and original-file preservation checks against its migration backup. Version 0.4.0 distribution results supersede this packaging baseline.

## Reproduce

See README.md for setup, pytest, Ruff, build, browser, sample generation, and evaluation commands. Real batch acceptance is run with:

~~~powershell
.\.venv\Scripts\python.exe scripts\check_real_batch.py
.\.venv\Scripts\python.exe scripts\check_package.py
~~~

Each real batch check creates a separate workspace under artifacts and leaves its evidence intact. Model setup must complete first. Browser tests add fixtures to the running server's workspace; use a test workspace.
