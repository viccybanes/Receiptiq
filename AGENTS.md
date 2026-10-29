# ReceiptIQ project rules

- Read [CONTEXT.md](CONTEXT.md) for the current handoff, behavior changes, branch boundaries, validation, and operational cautions before making changes.

- Preserve the original four-milestone plan in PLAN.md and workplace details below.
- React/TypeScript, Python/FastAPI, SQLite, local PaddleOCR and Ollama vision. Tauri owns Windows distribution. Google AI Studio is an optional bring-your-own-key vision provider requiring explicit cloud consent; local Qwen remains the default. With Google enabled, new uploads use the requested sequence: Gemini 3.5 Flash-Lite, 3.1 Flash-Lite, 3.8 Flash, then local Qwen 2B on failure. Manual scans keep their selected model. No n8n.
- Primary workload is tens of receipt images per batch. Keep processing bounded, durable, and independent from human review.
- Keep expense wording: Purchase date, Vendor, Receipt number, Receipt photo, Notes; Description, Quantity, Unit, Unit price, Amount, Type, Photo of item purchased. Item photos are optional. Do not restore Tax/Subtotal/Discount controls.
- Join distinct receipt codes in source order with " / ", retaining leading zeroes.
- Flag implicit discounts using both line arithmetic and the paid total. Never allocate a discount without human acceptance. Upload vision is automatically applied only to unchanged, unverified receipts, as requested; preserve intervening human edits.
- Retain original files, edits, verified revisions, CSV snapshots, and local user data across updates. Back up before schema changes.
- CSV uses one row per item by default. ZIP carries CSV plus relative image paths for the receiving application's future importer.
- Only current, explicitly opted-in verified records may feed retrieval. Never copy historical prices as facts about a new receipt.
- End users must not install Python. Models download during explicit setup; local processing works offline. Google vision requires internet and uses the user's quota/billing. Never send historical retrieval examples to Google or include API keys in receipt records, logs, exports, or backups.
- Keep code simple; reuse existing modules and standard libraries. Test meaningful money, privacy, lifecycle, and migration behavior.
- Commit meaningful completed slices and push to the configured origin. Never commit user receipts, model caches, credentials, or generated bundles.
- Use Conventional Commits for every commit: feat, fix, docs, test, refactor, chore, build, ci, or perf; add a scope when useful. Keep subjects concise and imperative.
- Address the user as bro; assistant name Chae. Keep progress and completion reports concise and honest.
