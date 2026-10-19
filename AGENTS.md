# ReceiptIQ project rules

- Preserve the original four-milestone plan in PLAN.md and workplace details below.
- React/TypeScript, Python/FastAPI, SQLite, local PaddleOCR and Ollama vision. Tauri owns Windows distribution. No cloud AI, paid API, or n8n.
- Primary workload is tens of receipt images per batch. Keep processing bounded, durable, and independent from human review.
- Keep expense wording: Purchase date, Vendor, Receipt number, Receipt photo, Notes; Description, Quantity, Unit, Unit price, Amount, Type, Photo of item purchased. Item photos are optional. Do not restore Tax/Subtotal/Discount controls.
- Join distinct receipt codes in source order with " / ", retaining leading zeroes.
- Flag implicit discounts using both line arithmetic and the paid total. Never apply a discount or suggestion without human acceptance.
- Retain original files, edits, verified revisions, CSV snapshots, and local user data across updates. Back up before schema changes.
- CSV uses one row per item by default. ZIP carries CSV plus relative image paths for the receiving application's future importer.
- Only current, explicitly opted-in verified records may feed retrieval. Never copy historical prices as facts about a new receipt.
- End users must not install Python. Models download during explicit setup; normal operation works offline.
- Keep code simple; reuse existing modules and standard libraries. Test meaningful money, privacy, lifecycle, and migration behavior.
- Commit meaningful completed slices and push to the configured origin. Never commit user receipts, model caches, credentials, or generated bundles.
- Address the user as bro; assistant name Chae. Keep progress and completion reports concise and honest.
