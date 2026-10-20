# Product
<!-- impeccable:product-schema 1 -->

## Platform
web

## Stack
React, TypeScript, Python, FastAPI, SQLite, PaddleOCR; Tauri Windows host with a bundled Python backend. Optional Ollama Qwen3-VL vision and EmbeddingGemma use app-managed local assets.

## Users
People who enter receipt data at work, reviewing tens of receipt images per session on Windows.

## Product Purpose
Convert receipt images into verified structured records and CSV files while keeping receipt data local.

## Operating Context
Users upload 20-100 images, review completed receipts while processing continues, correct fields, and export verified records. Single receipt per image is the initial assumption.

## Capabilities and Constraints
CPU OCR, image preprocessing, PDFs, durable batches, manual review and revision history form the base workflow. Optional local vision proposes changes for explicit application. Retrieval uses only current verified records with explicit opt-in and never supplies historical prices as facts. Workplace CSV formats, photo bundles, local review assignment, PDF page grouping, backups/restore and Windows packaging extend that workflow. No paid or cloud AI services. The 0.4.0 installer is an unsigned preview; clean-machine and real handwritten-corpus validation remain release gates.

## Brand Commitments
Name: ReceiptIQ. No binding visual assets or aesthetic supplied. Independent application from the adjacent Frutiger Aero website.

## Product Principles
Human verification precedes export. Preserve originals and corrections. Represent uncertainty honestly. Keep batches responsive and recoverable.

## Open Decisions
Receipt languages beyond English and measured minimum hardware remain undecided. The English expense form follows the supplied workplace wording: Purchase date, Vendor, Receipt number, Receipt photo, Notes; Description, Quantity, Unit, Unit price, Amount, Type, and optional Photo of item purchased. Tax/Subtotal/Discount controls are omitted. Multiple receipt codes retain leading zeroes and join with " / ". Implicit discounts require arithmetic review and explicit human confirmation. PHP and Materials are initial defaults; recognized currency is retained internally. Default CSV dates use ISO; workplace templates may select another format. The receiving workplace application has no CSV importer yet; CSV/ZIP follows the documented format for its future importer. Cross-computer sharing and authenticated reviewer accounts are outside scope.
