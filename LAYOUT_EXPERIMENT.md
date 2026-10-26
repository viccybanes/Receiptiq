# OCR layout experiment

Branch: `experiment/ocr-layout-evidence`, based on `b696557`.
Worktree: `C:\Users\ruthl\Desktop\Codink\ReceiptIQ-ocr-layout`.
The original ReceiptIQ checkout remains on main with its pre-existing edits. This branch does not include those uncommitted UI/review changes. No database migration, model download, cloud request, or installed-app update is involved.

## Behavior

- Preserve PaddleOCR polygons alongside the existing boxes and confidence scores. Previously saved OCR runs without polygons remain readable.
- Use shared row grouping for OCR parsing, vision context, and optional overlays. Match disjoint fragments using text height and baseline centers; estimate residual tilt from multiple usable polygons. Keep pages and overlapping same-column rows separate. These remain fallible geometric candidates.
- Preserve detailed normalized OCR geometry internally within a 16,000-character budget. Send compact row centers and `[x, text, confidence]` cells to Qwen within a separate 6,000-character budget. Centers use 0..1000 coordinates relative to the supplied image/crop; crossing/outside fragments are excluded. Report shortened context. This is a character bound, not a guarantee that every multi-page request fits the model's token context.
- During a vision job, read missing OCR pages sequentially even when outlines are off. Reuse existing OCR evidence when present. A failed OCR pass leaves vision available and adds a notice. Reject over-limit receipts before running OCR.
- Ordinary receipts share the existing two-inference-call budget: either the full receipt completeness recheck or one focused missing-Amount reread. The focused path requires a uniquely matched, high-confidence OCR row; skips repeated/overlapping/large rows; and changes only an absent Amount. The reread must agree with the item's name, existing numeric/unit values, and the rightmost OCR money token. Conflicts, malformed replies, or failures retain the original result with a notice. It does not reconcile prices to make totals balance.
- Dense uncertain tables have a separate, bounded detail path described below: at most five calls total, sequentially. No model profile or persisted setting changes are required.
- Preserve cancellation, single-worker operation, human-edit revision guards, warning acknowledgment, optional outlines, and human verification. OCR confidence and model row counts are not proof of correctness.
- Record layout/focused-reread provenance and OCR timing. The evaluator now checks labeled item fields in source order, so a matching total cannot hide swapped prices.

## Reversibility

The experiment is enabled by default only in this branch. To disable its OCR acquisition, enriched context, adaptive grouping, focused/detail rereads and new column recovery, set this in the shell starting the backend, then restart that backend:

```powershell
$env:RECEIPTIQ_LAYOUT_ASSISTANCE = '0'
```

Use `'1'` or remove the variable to enable it again. Existing outline settings still work and may request OCR as they did before. Extra optional polygon/provenance fields do not require a data migration.

For complete code rollback, leave this worktree and continue using the original main checkout. Nothing was merged into main. The feature commit can also be reverted independently; see the branch log. Keep receipt data and the original checkout's uncommitted work in place.

## Local model comparison, 2026-10-26

Installed `qwen3-vl:2b-instruct`, existing local Ollama on port 11435, no non-loopback inference connections. Three generated receipts were evaluated once per mode, in order. Baseline used image-only vision with the experiment disabled; assisted used the same images plus actual local PaddleOCR and layout context. This compares the combined workflow, not the isolated contribution of geometry.

| Sample | Baseline item fields | Assisted item fields | Baseline seconds | Assisted seconds including OCR |
|---|---:|---:|---:|---:|
| Printed | 8/8 | 8/8 | 10.594 | 62.515 |
| Synthetic cursive | 8/8 | 8/8 | 34.141 | 10.157 |
| Mixed typography | 6/8 | 8/8 | 4.375 | 9.125 |
| Total | 22/24 | 24/24 | 49.110 | 81.797 |

The two baseline errors included quantity/prices inside Description. Every checked Quantity, Unit price and Amount matched in both modes. All 15 header-field checks and all three item-count checks passed in both modes. Units were not labeled in this small fixture set. None of these complete samples triggered the focused missing-Amount reread; that path has controlled-response tests.

OCR took 21.109 seconds on the first sample, then 4.891 and 5.156 seconds. Inference timings also varied considerably. These are single runs without controlled warmup or repeated timing trials, not a reliable speed benchmark. Overall this run was slower; there is no throughput-improvement claim. Synthetic cursive typography is not real handwriting, and three easy receipts cannot establish production accuracy.

Raw local results and generated samples remain ignored under `artifacts/layout-baseline.json`, `artifacts/layout-assisted.json`, and `artifacts/layout-samples/`. No receipt images or raw model results are committed.

To reproduce on this PC from this worktree using the existing environment and running local runtime:

```powershell
$env:PYTHONPATH = "$PWD\backend"
& ..\ReceiptIQ\.venv\Scripts\python.exe scripts/make_samples.py --count 3 --out artifacts/layout-samples
$env:RECEIPTIQ_LAYOUT_ASSISTANCE = '0'
& ..\ReceiptIQ\.venv\Scripts\python.exe scripts/evaluate_vision.py --samples artifacts/layout-samples --limit 3 --port 11435 --timeout 240 --out artifacts/layout-baseline.json
$env:RECEIPTIQ_LAYOUT_ASSISTANCE = '1'
& ..\ReceiptIQ\.venv\Scripts\python.exe scripts/evaluate_vision.py --samples artifacts/layout-samples --limit 3 --port 11435 --timeout 240 --with-ocr --out artifacts/layout-assisted.json
```

The evaluator reads installed model assets and writes its own results; it does not process the user's saved receipt records. Do not run model evaluation alongside active receipt processing.

## Validation and next decision

The original branch baseline passed 311 backend tests. The final experiment passed all **342 backend tests** (130.85 seconds), Ruff on the backend, and `git diff --check`. One existing Starlette/AnyIO deprecation warning remains. Tests added for this experiment cover resolution scaling, tilted columns, adjacent/repeated rows, invalid geometry, crop/page boundaries, context limits, focus conflicts and cancellation, OCR failure, and edits during processing. Workflow tests exercise both switch settings. The local evaluator's money comparison was checked against equivalent formatting, swapped prices, and missing rows. Frontend code was not changed; no UI or installer rebuild was performed.

Keep this branch experimental until a labeled, representative set of real printed and handwritten receipts shows acceptable critical-field accuracy and latency. Existing manual receipt edits, discounts and large legitimate quantities must remain intact. Do not use arithmetic agreement or a model-reported count as an accuracy gate on its own.

## Handwriting follow-up, 2026-10-26

A user-supplied handwritten receipt exposed three weaknesses: oversized OCR context, garbled OCR text influencing recognition, and dense rows being skipped or shifted between numeric columns. The prior assisted request failed with 10,241 input tokens against the 8,192-token context. The image-only baseline produced a usable but inaccurate draft.

The new detail planner uses geometry and confidence to identify one run of 8–20 likely table rows on a single page: at least four column fragments, broad page coverage, multiple digit-bearing fragments, and median OCR confidence below 0.85. These are bounded heuristics, not a handwriting classifier or proof of item count. Unsupported layouts, multiple pages, explicit user crops, or confident/sparse text retain the ordinary path.

The detail path keeps a full-page extraction for headers and fallback, then reads two to four image sections with a shorter item-only prompt. Sections partition the entire page, including areas above and below the detected table. It omits OCR wording and prior candidate values from the rereads. All sections must return usable, non-partial results without their own count mismatch before replacing the item suggestions. Any failure retains the entire earlier extraction. Cancellation propagates; no partial section set is applied. The existing worker revision/verification guards still decide whether a suggestion can be applied. Header fields retain the full-page result. Optional row boxes are mapped back to page coordinates.

Default numeric normalization also recovers a narrowly identifiable column shift: Quantity contains its own text unit, the model supplied a numeric Unit, and Unit price duplicates Amount. The numeric Unit response becomes Unit price with an explicit recovery notice. This uses an already-read cell, not multiplication/division or a total-balancing rule. It retains discounts and legitimate large quantities. The existing opt-in Amount-first derivation path remains separate and unchanged.

Against an assistant visual transcription of 15 scored rows, excluding one overlapping row at the user's request:

| Model | Image-only baseline numeric fields | Final numeric fields | Baseline rows | Final rows | Baseline inference seconds | Final inference seconds |
|---|---:|---:|---:|---:|---:|---:|
| Installed 2B (configured) | 35/45 | 41/45 | 14 | 15 | 15.281 | 30.203 |
| Installed 4B | 33/45 | 41/45 | 13 | 15 | 36.719 | 64.344 |

Each row contributes Quantity, Unit price, and Amount. Missing rows count as three errors; baseline rows were manually aligned so omissions do not shift later comparisons. These scores exclude units, descriptions, dates, receipt codes, classification, and the overlapping row. The user confirmed the two queried reference readings on 2026-10-26; the labels and scores remain unchanged. The remaining reference labels are assistant visual transcriptions. There is no overall-accuracy or generalization claim. OCR initially took 27.937 seconds and its evidence was reused; the table excludes OCR time. These single runs had uncontrolled warmup, and the detail workflow costs more inference time.

The configured 2B model recovered a missing row and several ordinary handwritten words, but four numeric fields remain wrong/blank. One row's Quantity and Unit price regressed from a correct baseline reading. Units and product codes also still need correction. The 4B run was not consistently better and misread a header date, so the profile was not changed. Keep this work experimental; do not treat any count, matching arithmetic, or model confidence as independent verification.

Private references, raw responses, and a field-by-field comparison remain ignored under `artifacts/handwriting-case/`. No user receipt image or transcription is committed. No database migration, new dependency, model download, cloud inference, UI build, installer update, or service restart was performed.

The synthetic regression check retained 24/24 labeled item fields, 15/15 header fields, and all three expected row counts. The repaired evaluator `--raw-responses` option captured each actual local HTTP response separately. Backend validation passed 368 tests; focused checks were rerun after the final notice-only adjustment. Ruff and `git diff --check` passed. One existing Starlette/AnyIO deprecation remains.
