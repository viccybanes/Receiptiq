# Receiptor agent handoff

Percentage discounts (2026-10-29): the user requested optional **Total Discount (%)** beside Total and **Discount (%)** on each line. Blank line overrides inherit the total percentage; manual values, including zero, survive changes to the total percentage. Clearing a manual override restores inheritance. The form retains gross Unit price; net Amounts round half up to cents and Total follows their sum when the discount is edited. CSV/ZIP keep the existing columns and export net Unit price (up to 12 decimal places) and net Amount, with no percentage columns or added Notes. Percentages accept 0–100 with up to six decimals; invalid rates and inconsistent discounted amounts block verification. The older implicit-discount allocation is available only without percentage discounts.

Discount storage uses additive `total_discount_percent` / item `discount_percent` fields in the existing JSON records, with blank defaults for old receipts. Omitted fields from an older client preserve existing percentages; explicit blanks clear them. Vision retains matched manual percentages and avoids deriving gross values from net amounts. No SQLite table/schema migration is needed. A workspace backup was created before enabling this change; an older application build cannot read the new JSON fields, so rollback must use a pre-discount workspace backup while retaining the newer workspace separately for recovery. No installed desktop update is implied.

Discount validation: the full backend suite passed (448 tests), followed by 25 focused discount checks after the final omission/zero-rounding adjustments. Checks cover percentage bounds, rounding, inheritance/overrides, new/reordered rows, stale verification, CSV/ZIP column preservation, discounted export prices, backup/restore, and legacy reads. Decimal calculation scripts, production frontend build, Ruff, and isolated desktop/mobile browser checks passed. The development backend was restarted while idle; health and acceptance of the new fields were checked without changing user receipts.

Upload fallback update (2026-10-29): when Google is enabled with a key and consent, **new uploads** pin Gemini 3.5 Flash-Lite → Gemini 3.1 Flash-Lite → Gemini 3.8 Flash → local Qwen 2B. A failed step durably queues the next model behind fresh work; the first successful extraction stops the sequence. Exhausting all four requires manual retry. Cancellation or disabling Google stops the sequence. Existing queued jobs keep their original policy, and local-only uploads/manual scans keep their previous behavior. Settings now labels its Gemini model as the manual default and discloses the fixed upload order. This user request supersedes older no-fallback/manual-cloud-retry statements below. Provider labels, local-only retrieval, revision guards, and human verification remain in force; no schema migration or installer rebuild.

Upload fallback validation: 83 focused Google/workflow checks passed, including every success position, exhaustion, worker reconstruction, queue fairness, edits, cancellation, consent revocation, and local-only history. Ruff, production frontend build, and isolated desktop/mobile browser checks passed. Google responses were mocked; no live cloud failure was induced. The idle development backend was restarted after stopping a verified orphan runtime from this workspace; health reports a live worker and an owned runtime with Qwen 2B installed. No user receipt was changed for validation.

Brand update (2026-10-29): the visible app name is **Receiptor**, with evergreen **tor** in the header wordmark. Browser/desktop titles, settings, startup/recovery messages, and downloaded CSV/ZIP names use the new name. Existing `ReceiptIQ` workspace paths, launch preferences, database/backup formats, environment variables, desktop identifier, and executable names remain compatible. Future installer builds use **Receiptor Desktop**; this source change does not update an already-installed app. Historical names and paths below remain as recorded.

Rebrand validation: frontend production build, isolated browser checks at desktop/mobile widths, 29 backup/system tests, Ruff on changed Python modules, and `cargo check --locked --offline` passed. The Windows installer was not rebuilt or upgrade-tested.

Main baseline (2026-10-29): at the user's request, `main` now includes the selected Google-vision version `bb27516`, promoted by fast-forward. Use only `C:\Users\ruthl\Desktop\Codink\ReceiptIQ` for ongoing work. The two linked worktrees have been removed from Git; their prior branches remain available as history. Old main-worktree edits, linked-worktree artifacts, and a Git history bundle are preserved locally in ignored `.local/worktree-recovery/20261029-main/`. The existing `artifacts/dev-workspace` receipt data and shared `.venv` remain in place. The later `experiment/ocr-layout-evidence` commits after this version were intentionally not merged. Older branch/worktree status statements below are historical. This source promotion does not rebuild or update the installed Windows app.

Optional Google vision update (2026-10-28): this checkout adds Google AI Studio BYOK alongside default local Qwen. This requested exception supersedes the older no-cloud statements below. See [GOOGLE_VISION_PLAN.md](GOOGLE_VISION_PLAN.md) for the implementation plan and validation. Settings → Vision & models requires a saved Windows-user-protected API key and explicit cloud consent. Google receives current images and selected OCR evidence, never historical retrieval examples. Provider/model are pinned when work is queued; legacy jobs stay local and disabling Google blocks pending/in-flight cloud requests. Cloud failures require manual retry; local retries are unchanged. Keys are excluded from backups and API responses; encrypted keys follow same-PC workspace moves. Restores require re-entering the key. Existing revision, money, uncertainty, and verification guards apply to both providers. The frontend viewer also repairs two misplaced JSX blocks already present at `29dd1e5`. No installer update is implied.

Manual vision model selection (2026-10-29): the receipt's **Vision model** dropdown offers Qwen 2B, Qwen 4B, and the Gemini model configured in Workspace settings. Gemini is selectable only while Google is enabled with a saved key and cloud consent. The choice applies to manual page/region scans without changing the automatic-upload provider; choosing Qwen stays local even when Google is the workspace default. Manual API requests may pin a provider; requests omitting it retain the workspace-default behavior.

Google live-test fix (2026-10-29): the manual model dropdown also offers `gemini-3.5-flash-lite`; Workspace settings suggests it for the upload default. Google rejected the receipt schema's `items.maxItems=1000` with HTTP 400. The Google adapter now omits only that generation constraint; local validation and response limits remain intact. An authorized synthetic-image test with the saved key passed the full Flash-Lite extraction in 2.23 seconds. Gemini 3.8 separately timed out and returned HTTP 503 high demand; it remains available without automatic retries or fallback. Connection timeout, response timeout, and model-unavailable messages are distinct. See `GOOGLE_VISION_PLAN.md` for evidence and limits.

Experimental branch update (2026-10-26): `experiment/ocr-layout-evidence` adds reversible OCR geometry assistance and one bounded missing-Amount crop reread. See [LAYOUT_EXPERIMENT.md](LAYOUT_EXPERIMENT.md) for exact scope, rollback, measured accuracy/latency, and validation. On this branch, missing OCR runs before vision independently of row outlines unless `RECEIPTIQ_LAYOUT_ASSISTANCE=0`; this supersedes the older outline-only behavior described below. Main and its uncommitted edits remain in the original worktree.

Handwriting follow-up (2026-10-26): dense, uncertain single-page tables can now use one full-page read plus up to four sequential image-section reads, replacing the ordinary two-call path for that case. Compact OCR hints avoid the observed context overflow; uncertain dense-table OCR text is omitted from prompts and used only to choose sections. Numeric Unit responses can recover a shifted Unit price when Quantity already contains a unit, with an explicit review notice and no arithmetic-derived correction. The same environment switch disables these additions. A private real-image check improved the configured 2B model from 35/45 to 41/45 numeric fields against visual labels, but some cells regressed and others remain wrong. This is experimental, not a general handwriting-accuracy claim. The main checkout, installed app, model profile, and user records were not updated.

Updated 2026-10-23. Implementation snapshot: `main` at `d457240` before this documentation commit. Recheck Git and running processes before acting; this is not a live service-status report.

## Start here

1. Work in `C:\Users\ruthl\Desktop\Codink\ReceiptIQ`, not the adjacent `Frutiger Aero` project. Remote: `https://github.com/viccybanes/Receiptiq.git`.
2. Read `AGENTS.md`, this file, then the relevant source. This file records current behavior; `PLAN.md` preserves the original four milestones and their release gates. Earlier manual-only vision statements in planning/product documents are superseded by the upload behavior below.
3. Check `git status --short` and the branch. Preserve user edits. At handoff, `AGENTS.md` and `PRODUCT.md` already had user changes; `.vscode/` and `ReceiptIQ-PLAN.md` were untracked user files. Do not stage them wholesale.
4. Implement and validate requested work end to end. Use focused changes, existing utilities, Conventional Commits (`feat`, `fix`, `docs`, etc.), and push completed work to the configured remote. Never rewrite published history just to tidy names.
5. The active conversation asks to address the user as **Sect leader**; assistant name **Chae**. Keep reports concise, warm, and honest. Explicit current user instructions take precedence over older wording in files.

## Product and boundaries

ReceiptIQ is a distributable, local Windows receipt-processing application. Its main challenge is hard-to-read handwriting. Users upload **tens of images per batch**, compare images with editable fields, correct and verify receipts, then export workplace CSV files. Printed, handwritten, and mixed receipts are supported, with manual entry always available.

- Stack: React + TypeScript + Vite; Python + FastAPI; SQLite; Pillow/OpenCV/PDF rendering; CPU PaddleOCR; local Ollama Qwen3-VL; Tauri Windows host and PyInstaller backend bundle.
- No n8n, paid AI API, cloud fallback, or mandatory network access after setup/model downloads. End users need no Python installation.
- Development hardware supplied by the user: Windows 11, i7-12700H, RTX 3060 Laptop GPU, 16 GB RAM. Earlier measured GPU memory was 6 GB; verify live hardware/settings before capacity claims.
- One receipt per uploaded image/document by default. PDFs can contain multiple pages and support explicit page grouping/splitting. Do not assume automatic splitting of several receipts photographed together.
- All four milestone implementations exist: (1) ingestion/OCR/review/CSV, (2) local vision, (3) local retrieval and review/batch tools, (4) Windows packaging/setup/recovery. This does **not** mean all distribution or accuracy gates have passed.
- Existing version 0.4.0 installer is an unsigned preview built before the recent source changes. No auto-update feed is configured. A source build/push does not update an installed executable.

## Current processing behavior on main

1. Multi-file uploads transfer individually, persist accepted originals/jobs, retain the current review page, and show animated uploading placeholders. Corrupt files fail independently. Accepted jobs survive restart.
2. Default uploads prepare previews, **queue vision automatically**, and process it sequentially. Automatic OCR is disabled; **Run OCR** remains available. Explicit API `auto_ocr=true` retains the OCR path.
3. The worker uses the configured allowlisted vision profile (`qwen3-vl:2b-instruct` or `qwen3-vl:4b-instruct`). Automatic full-receipt vision is limited to six pages; oversized inputs produce an actionable failure.
4. Upload vision is automatically applied only while the receipt remains unverified and its revision matches the queued/start revision. Intervening human edits are preserved; the result remains a suggestion instead. Every result retains provenance, and automatic application is audited.
5. Manually requested page/crop vision remains an explicit suggestion/application workflow. Never auto-verify a receipt.
6. A failed vision job goes behind remaining queued work. Retries run in failure order, up to **three attempts total**. Persistent failures remain available for manual retry. Cancellation stops further retries; retry state is durable.
7. Human verification is required before export and retrieval eligibility. Changes to verified values invalidate that eligibility until verified again. Revision checks reject stale saves/applications.

Heavy OCR, vision, and indexing share one worker. The user explicitly rejected concurrent Qwen inference after considering memory costs. Keep `OLLAMA_NUM_PARALLEL=1` and `OLLAMA_MAX_LOADED_MODELS=1`; the vision request uses an 8192-token context and two-minute model keep-alive. Do not enable concurrency as an unsolicited optimization.

### Reversible vision wait policy

Commit **`2b3de0b`** removes the default 180-second inference read timeout. Local connection/write setup remains bounded at five seconds. Socket reads poll for cancellation, including while waiting for headers or the body; this avoids Windows blocking-receive cancellation problems. Connection failures, runtime errors, invalid/oversized replies, and cancellation still stop a request.

- Default: `RECEIPTIQ_VISION_TIMEOUT_SECONDS=0` (unlimited inference read wait).
- Restore the old read limit: set `RECEIPTIQ_VISION_TIMEOUT_SECONDS=180` in the backend process environment, then restart that backend.
- Code rollback: `git revert 2b3de0b`. An independent reverse-patch check passed after the subsequent numeric changes. Check again if later edits touch the same code.
- A connected but hung runtime cannot reliably be distinguished from a slow model; it can occupy the queue until cancelled or restarted. Do not claim crash-only failure detection or guaranteed completion.
- This setting affects vision reads, not embedding, setup/download, or other request timeouts.

## Extraction and expense rules

Use the user's exact form wording:

- Receipt details: **Purchase date, Vendor, Receipt number, Receipt photo, Notes**.
- Line items: **Description, Quantity, Unit, Unit price, Amount, Type, Photo of item purchased**.
- Item photos are optional. Receipt photo comes from the uploaded original. The review UI has no **Replace photo** control. Do not restore Tax/Subtotal/Discount controls; legacy stored fields remain for compatibility.
- Purchase date is **MM/DD/YYYY** in the form, ISO internally. CSV templates control exported date format. Normalize only unambiguous extracted dates; do not guess ambiguous month/day/year.
- Vendor detection favors the seller's heading near the top, excluding customer names, contact details, receipt titles, and item rows.
- Join distinct receipt identifiers in source order, retaining leading zeroes: `TRANS #: 00-0045` and `SI #: 00-0029` become `00-0045 / 00-0029`.
- Vision may resolve ambiguous **handwritten ordinary words** from the image and context, e.g. PIVUT/PIVOT. Preserve printed spelling, brands, abbreviations, and product codes; unfamiliar text alone is not proof of an error. Do not hardcode sample-receipt guesses as ground truth.
- Numeric AI fields interpret a dot followed by **three or more digits** as grouping: `1.000` becomes `1000`, `2.230` becomes `2230`; `12.50` stays `12.50`. The deterministic adapter normalizes proposed numeric fields, limits proposed money to two decimal places, and adds review notices for changed/rejected readings. It does not rewrite dates, receipt codes, descriptions, or existing manual values.
- Quantity is a whole number. Integer-valued legacy strings such as `2.000000` normalize to `2`; genuinely fractional drafts remain editable but block verification. Storage remains strings to support blank/invalid drafts and exact arithmetic, rather than changing the database to floating point.
- Quantities with magnitude **100 or more** receive a nonblocking, field-linked review warning. Keep legitimate values; verification requires the existing warning acknowledgment and reason. Vision explicitly rechecks repeated zeros and large quantities against the image rather than trimming digits or forcing arithmetic to fit.
- Vision inventories distinct item rows, then re-scans bottom to top against its output. `visible_item_count` and persisted `row_audit` compare model-reported, returned, and retained rows within the selected pages/crop. A mismatch triggers at most one additional sequential full extraction with the same images; unresolved discrepancies and unavailable counts produce reviewer notices. `provenance.row_rechecks` records whether this extra pass occurred. Wrapped descriptions count once; distinct rows with their own numbers remain separate, including ditto/blank descriptions. This is model-assisted checking, not independently proven completeness.
- Vision checks units beside quantities (including joined text in the Quantity column) and in dedicated Unit columns. The adapter separates clear combined quantity/unit readings before numeric validation, preserving explicit units and rejecting conflicts or fractional quantities. Missing units retain the existing/default behavior.
- Vision rejects numeric Units and normalizes supported unit names (pcs, mtrs, boxes, gals, kgs, kilos). A combined Quantity/unit can recover even when the separate Unit slot contains a misplaced price. Duplicate final rows are removed only when the reported count shows excess output or both refer to the same source region; distinct source regions take precedence. Ambiguous identical rows get a bounded recount and a review notice. The recount replaces, never appends, its earlier rows.
- Workspace settings ? Local models has two opt-in Vision reading options, persisted under `vision` in the existing backed-up `settings.json`: `amount_first` reads Amount first and derives only missing, exact whole Quantities or cent-precision Unit prices; matching numeric Unit/price column shifts can be resolved using Quantity and Amount. Existing human values and written discounts remain. `capture_regions` supplies numbered row outlines; the receipt viewer toggle updates the same preference. Both default off and apply to subsequent Vision jobs.
- With row outlines enabled, the same worker reuses OCR evidence or runs local OCR sequentially to locate rows before Vision. Distinct OCR-matched description/numeric rows take precedence over model coordinates. Oversized/unknown model regions are omitted rather than invented. Coordinates map crops back to processed pages; stable item IDs and page IDs keep outlines aligned through reordering and preview rotation. Manual suggestions are labeled separately; this shows source locations, not private model reasoning.
- Malformed/truncated model output is recovered field by field using the installed partial JSON parser. Completed fields and readable rows become a visibly flagged partial suggestion (`provenance.partial_response`); unknown metadata, invalid field types, and unfinished strings are omitted. Existing numeric validation and revision/verification guards remain. Recovered replies return immediately rather than re-running the same truncated output. If a row recount is malformed or recovers no more rows than the first extraction, retain the earlier extraction. No usable structured fields, transport/runtime errors, cancellation, and response-size violations still fail normally.
- Missing Quantity/Unit price/Amount are completed from sufficient arithmetic context. Quantity division must be exact; never round a fractional result into an invented count. A missing Total is filled only if every line has a valid Amount.
- Live edits update derived amounts and matching totals. Preserve independently written amounts/totals that differ: they may represent implicit discounts. Explicit discount allocation is a separate human-confirmed action. Its effective unit-price precision is not the same as the two-decimal AI-reading policy.
- Category choices: **Materials, Tools, Equipment, Outside Labor, Services, Permits & Fees, Utilities, Transportation, Logistics, Other**. Vision classifies new items by description; uncertain/unsupported categories default to Materials. Preserve matched existing manual categories; legacy values remain displayable.

## Review and export behavior

- Review issues appear beside the affected fields and within highlighted line items: amber for checks, red for blocking errors, with explanatory text and accessible field descriptions. Receipt-wide sum mismatches appear beside Total. Highlights refresh from server validation after autosave and follow item IDs while deletions save. The bottom acknowledgment/reason controls remain required by the existing verification rules; highlighting does not accept a warning. `frontend/tests/reviewIssues.mjs` checks this using mocked receipts, including desktop/mobile layouts, corrections, and deletion during autosave.
- Each item has Up, Down, Add line below (+), and Remove controls; bottom Add line also remains. New rows default to `unit` and focus Description. Moves/inserts use autosave and undo, keep item IDs/numbers/photos/types/money together, and reset warning acceptance. Saved OCR evidence follows stable item IDs through reorder/insert/delete; only corrected fields become manual. Row changes wait for photo uploads.
- The receipt list shows **vendor only**, with **Unknown vendor** when missing. No image filename as a title/subtitle or upload-placeholder title. Status, assignment, duplicate warnings, and other operational indicators remain.
- The receipt selector groups by local **day**, not individual upload timestamp.
- Receipt details scroll independently of the image. The image pans by dragging, supports zoom/reset and crop selection, and hides scrollbars. Crop selection must remain aligned after preview rotation.
- Preview rotation is **view-only** and persists in browser localStorage under `receiptiq-rotation-<id>`. It must not restart OCR or mutate the original image. It is not a cross-browser/device preference.
- Delete uses confirmation. Duplicate detection warns about identical originals rather than silently deleting/rejecting them.
- Reviewer labels, assignment, second-review flags, review filters, and keyboard shortcuts support same-workspace handoff. These are not authenticated accounts or access controls.
- Default CSV has one row per item, repeating receipt fields. Do not sum repeated receipt totals across rows. Versioned templates configure columns/formats. Python CSV quoting and spreadsheet formula protection remain required.
- CSV itself cannot attach image bytes. CSV + photos exports a ZIP with relative paths to receipt originals/PDFs and optional item photos. Import supports CSV with selected photos or a ZIP; imported receipts require verification.
- The destination workplace app has **no CSV importer yet**. The user accepted a proper documented CSV/ZIP format for that app to implement later; no destination API integration is required.

## RAG: implemented versus discussed

Retrieval is **local and opt-in**, using `embeddinggemma:300m-qat-q4_0`, 256-dimensional vectors in SQLite, and a bounded cosine scan of up to 10,000 eligible entries. SQLite is authoritative; vectors are rebuildable. No separate Chroma server is needed.

Only current, explicitly opted-in verified receipts/wording corrections are eligible. Edited, deleted, opted-out, superseded, self-source, or second-review records must not leak through stale vectors. Index failures must not undo verification or block export. Retrieved examples supply wording/context, never historical prices, quantities, dates, or totals as facts about a new receipt.

Feed RAG by correcting, verifying, and explicitly allowing receipts for future suggestions. This **does not train model weights** or improve another PC automatically. Global RAG was discussed but is **not implemented**; any sharing needs separate consent, privacy/authentication, and synchronization design. Never silently upload receipts.

There is no proven receipt count that makes handwriting nearly automatic. Evaluate held-out receipts/writers, critical numeric errors, correction time, and zero-correction rate. Synthetic cursive fonts and unit tests are not real-handwriting accuracy evidence.

## Code map

| Area | Files |
|---|---|
| Queue, uploads, selection, export UI | `frontend/src/App.tsx` |
| Editing, preview transforms, vision/review controls | `frontend/src/Review.tsx` |
| Exact live arithmetic and date display | `frontend/src/amount.ts`, `frontend/src/purchaseDate.ts` |
| Layout and styling | `frontend/src/style.css`; UI branch adds `workspace.css` |
| API/session/endpoints | `backend/app/main.py`, `features.py`, `models.py` |
| SQLite, revisions, queue/data lifecycle | `backend/app/db.py`, `service.py`, `worker.py` |
| Vision prompt, image payload, numeric policy, transport | `backend/app/vision.py` |
| OCR, preprocessing, parsing, validation/discounts | `backend/app/ocr.py`, `imaging.py`, `extraction.py` |
| Verified-example index/retrieval | `backend/app/retrieval.py` |
| CSV templates and photo transfer | `backend/app/templates.py`, `transfer.py` |
| Hardware, model setup/runtime, workspace recovery | `backend/app/system.py`, `backups.py` |
| Desktop distribution | `src-tauri/`, `packaging/`, `scripts/package.ps1`, `scripts/build_installer.ps1` |
| Tests/evaluations | `backend/tests/`, `frontend/tests/`, `scripts/evaluate_*.py` |

API base is `/api/v1` on authenticated loopback. Keep authorization, owned-file checks, bounded input sizes, revision guards, and safe archive import. Original images, verified revisions, audits, CSV snapshots, and user data must survive updates.

## Branches and significant changes

- **`main`** is the active branch and includes the selected Google-vision baseline `bb27516` as of 2026-10-29. Recheck Git for later commits.
- **`ui-overhaul`** at `33cde4d` is a separate, pushed, **unmerged** ergonomic redesign. Git cannot use the requested space-containing name `UI overhaul`. It adds a searchable queue, viewport layout, fitted preview, expandable tools drawer, responsive/accessibility improvements, and isolated GUI QA helpers. Its base predates the latest timeout/numeric/title fixes; do not switch to it and assume those fixes exist. Reconcile both branches deliberately when asked to merge.
- `1ac10f7`: AI numeric normalization and integer quantities.
- `2b3de0b`: independently reversible unlimited/cancellable vision wait; `e43f2bd` keeps its documentation rollback independent.
- `23ab28e`: description-based workplace categories.
- `2d64577`: deferred failed-vision retries.
- `a15f3f0`: sequential automatic upload vision/application.
- `45f032e`, `dcc1741`: independent details scrolling/drag pan; date/arithmetic/upload workflow and opt-in OCR.

## Development, data safety, and checks

Run from the repository root in PowerShell:

```powershell
.\scripts\setup.ps1                  # First setup only; downloads dependencies/assets
npm --prefix frontend run build
.\scripts\start.ps1                  # Backend/static UI on 127.0.0.1:8765
# In another terminal, frontend hot reload on 127.0.0.1:5173:
npm --prefix frontend run dev
```

Vite proxies `/api` to port 8765. App-managed Ollama normally uses 11435; do not interfere with an independently installed Ollama on 11434. Backend Python changes require a backend restart; Vite handles frontend source changes. A build on one branch can leave static assets from that branch after switching: rebuild when testing the other branch's static UI.

Default receipt data is outside the install directory, under `%LOCALAPPDATA%\ReceiptIQ`, unless configured otherwise. `RECEIPTIQ_DATA_DIR` selects an explicit workspace. **`artifacts/dev-workspace` contains user receipts and is not disposable**, despite being Git-ignored. Do not reset or delete it. Prior `artifacts/dev-session.json`/process IDs may be stale; inspect before managing a process. Avoid forced restarts during active receipt work.

Use a fresh temporary workspace and synthetic fixtures for mutating/browser tests. Clean up only IDs created by that test. Some existing browser scripts target port 5173 and can operate on the running workspace: inspect them first. The `ui-overhaul` branch has `scripts/ui_qa_server.py` and `frontend/tests/uiOverhaul.mjs`; these files are not on `main`. Do not assume port 8766 or any prior QA process is still running.

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests -q
.\.venv\Scripts\ruff.exe check backend
npm --prefix frontend run build
node frontend/tests/amount.mjs
node frontend/tests/amountCompletion.mjs
node frontend/tests/purchaseDate.mjs
node frontend/tests/receiptDays.mjs
git diff --check
```

The commands are reproducibility guidance, not a claim they were all rerun for this documentation change. Packaging and real OCR/vision/retrieval evaluators have additional workspace/model requirements; inspect their arguments and `README.md` first. Do not commit receipts, generated artifacts, caches, credentials, or installed bundles.

## Validation and remaining work

Executed during the latest implementation work on 2026-10-23:

- Full backend suite: **204 passed** (107.54s), with one existing Starlette/AnyIO deprecation warning. Includes slow inference, cancellation before headers/during body, runtime disconnect, configurable timeout, numeric normalization, and fractional-quantity verification blocking.
- Relevant changed Python files passed Ruff. Frontend TypeScript/Vite build, decimal Amount checks, and whole-quantity/live arithmetic checks passed.
- Isolated browser check passed: fractional quantity input rejected, whole-number edit updates Amount, save survives reload.
- After `d457240`, production build and isolated mocked-list browser check passed: vendor-only titles and Unknown vendor fallback, with no image filenames. The full backend suite was not rerun for that frontend-only title edit.
- UI branch previously passed its own responsive review/export/settings workflows and rotation/crop checks. Those results do not mean the branch is merged or include the later fixes.
- Recent numeric/prompt changes were tested deterministically, **not benchmarked against a real handwriting corpus**. See `VALIDATION.md` for older real OCR, small synthetic vision/RAG, and installer checks with their exact limitations.

Still unproven/release work: clean Windows installation on an independent machine, separate CPU-only vision, representative 4B quality/memory benchmarks, older-installer upgrade preservation, signed distribution/updates, and real handwriting evaluation. The recent code was not repackaged into a new installer. Global RAG and cross-computer reviewer accounts remain separate future projects.

No further feature implementation was pending from the conversation at this handoff; the current request is to preserve context. Continue from the user's next request rather than inventing a new milestone.
