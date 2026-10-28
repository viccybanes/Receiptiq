# Optional Google AI Studio vision

Requested 2026-10-28. This feature explicitly supersedes the older no-cloud-AI rule for an optional, user-selected Google provider. The original four milestones in `PLAN.md` remain unchanged. Promoted to `main` on 2026-10-29 at the user's request; the canonical checkout is `C:\Users\ruthl\Desktop\Codink\ReceiptIQ`. The installed app remains a separate, older build.

## Implementation

1. Extend workspace vision preferences with a provider (`local` by default), Gemini model, and explicit cloud consent. Keep the two existing reading options. Store the API key separately using Windows DPAPI for the current user; never return it, persist it in jobs, or include it in backups. Copy only the encrypted key during workspace relocation.
2. Add a fixed-host HTTPS Gemini `generateContent` adapter using the existing HTTP dependency, inline JPEGs, and structured JSON. Share receipt parsing, numeric validation, row auditing, merge behavior, and revision guards with Qwen. Bound request/response size, elapsed time, and cancellation. Do not follow redirects or use environment proxies. No automatic provider fallback.
3. Snapshot provider/model when uploads and manual scans are accepted. Legacy jobs remain local. Recheck Google selection/consent before and during each cloud request. Keep historical retrieval examples local. Google uses the existing bounded row recount but not the experimental local model's dense-section/focused reread heuristics.
4. Add provider, model, password entry, key save/remove, and cloud-consent controls in Settings. Explain that enabling Google sends new upload scans and manual page/crop scans (images and current OCR evidence) to Google, needs internet, and uses the user's quota/billing. Label results with their actual provider/model.
5. Verify default/legacy local behavior, key secrecy and backup exclusion, queue pinning, consent revocation, error handling/cancellation, JSON recovery, provenance, edit protection, and responsive settings interactions. Run backend tests, lint, frontend build, and isolated browser checks. No live receipt transmission or accuracy claim without a supplied key and authorized test data.

## Acceptance and boundaries

- Existing settings load as local; Qwen continues offline after setup.
- Google works without Qwen installed and never receives historical retrieval examples.
- Saving a key alone never enables cloud processing; deleting it switches back to local.
- Failed cloud requests stay visible for manual retry; do not automatically repeat billable failed requests. An incomplete extraction can retain the existing single row recount.
- Originals, manual edits, verification/export rules, SQLite schema, and the four-milestone plan remain intact.
- No cloud retrieval, account system, Google Files upload, provider framework, SDK, or installer rebuild.

## API references checked

- [Generate content](https://ai.google.dev/api/generate-content)
- [Inline image input](https://ai.google.dev/gemini-api/docs/generate-content/image-understanding)
- [Structured outputs](https://ai.google.dev/gemini-api/docs/generate-content/structured-output)
- [Gemini 3.8 Flash](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash) (initial model; editable Gemini model ID)

## Validation

Completed on 2026-10-28:

- Backend: **391 passed** in 128.70 seconds, with one existing Starlette/AnyIO deprecation warning. Google tests use an intercepted HTTPS client and synthetic images/credentials; Windows DPAPI storage is exercised directly. Covers manual/automatic scans, provider/model pinning and legacy queues, verification/edit protection, historical-example exclusion, key redaction and backup exclusion, restore re-enablement, revocation, cancellation, HTTP errors, timeout, size limits, and partial/blocked output.
- `ruff check backend` and `git diff --check`: passed.
- TypeScript/Vite production build: passed.
- `node frontend/tests/visionOptions.mjs`: passed against a private static server and mocked API, including consent gating, write-only key input, save-error recovery, provider persistence, key removal, receipt-provider labels, overlays, and rotation. Desktop (1440×1000) and mobile (390×844) screenshots inspected; no horizontal overflow. Captures are in ignored `artifacts/google-vision/`.
- Existing Amount, amount-completion, purchase-date, and receipt-day checks: passed.
- Desktop URL allowlist: its actual function and existing Rust test module were extracted and compiled with `rustc --test`; **1 passed**, including exact AI Studio/terms URLs and rejection of lookalikes/query strings. Full Tauri build and installer were not run.
- `PLAN.md` is unchanged. No user workspace or installed app was restarted or migrated.

The checks above did not exercise live Google inference. No real receipts were uploaded to Google during that work. The original feature was committed separately on `feat/google-ai-studio-vision` from this checkout's `29dd1e5` base and subsequently promoted to main.

### Live investigation and model selection, 2026-10-29

- The user explicitly authorized testing their key. The saved key authenticated and Google's model list included both `gemini-3.8-flash` and `gemini-3.5-flash-lite`. Only a generated synthetic receipt image was transmitted; the key was not added to source, logs, or test fixtures.
- Gemini 3.8 exceeded a 90-second diagnostic read timeout on a small image request. A later request returned HTTP 503 with a high-demand message after 57.78 seconds. Network connectivity and key authentication succeeded; model capacity was a separate failure from the schema issue below.
- The production receipt schema caused HTTP 400 on Flash-Lite. Removing only `items.maxItems=1000` yielded HTTP 200 while preserving every other constraint. The adapter now makes that change on a copy of the generation schema; the original schema, local 1,000-item validation, and bounded response size remain unchanged.
- The patched `GoogleVisionEngine.extract` read the synthetic vendor, receipt number, one Cable item, Quantity 2, Unit price 50.00, and Amount/Total 100.00 correctly in 2.23 seconds. This is one synthetic smoke check, not a real-handwriting accuracy or general latency benchmark.
- Manual requests can pin a validated `google_model` independently of the upload default. The review dropdown includes Flash-Lite, and Workspace settings offers it as a model suggestion. Both require the existing key and consent checks. No provider/model fallback or automatic cloud retry was added.
- Validation: **66 backend tests passed**, focused Ruff and diff checks passed, TypeScript/Vite build passed, and isolated browser checks covered both model choices, request payloads, consent gating, settings synchronization, and desktop/mobile layout. The development backend is restarted separately after confirming it is idle; no installer rebuild is included.
- [Google's Flash-Lite model documentation](https://ai.google.dev/gemini-api/docs/models/gemini-3.5-flash-lite) confirms image input and structured output support. Live billing behavior and representative handwriting accuracy remain unproven.
