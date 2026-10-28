# Optional Google AI Studio vision

Requested 2026-10-28. This feature explicitly supersedes the older no-cloud-AI rule for an optional, user-selected Google provider. The original four milestones in `PLAN.md` remain unchanged. Work is scoped to this checkout; the main checkout and installed app are separate.

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

Live Google inference, account/model availability, billing behavior, and real handwriting quality still require separate validation with an authorized key and test images. No real receipts were uploaded to Google during this work. The feature is committed separately on `feat/google-ai-studio-vision` from this checkout's `29dd1e5` base.
