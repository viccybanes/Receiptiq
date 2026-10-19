# Product
<!-- impeccable:product-schema 1 -->

## Platform
web

## Stack
User specified React, TypeScript, Python, FastAPI, SQLite, PaddleOCR; Tauri in a later milestone.

## Users
People who enter receipt data at work, reviewing tens of receipt images per session on Windows.

## Product Purpose
Convert receipt images into verified structured records and CSV files while keeping receipt data local.

## Operating Context
Users upload 20-100 images, review completed receipts while processing continues, correct fields, and export verified records. Single receipt per image is the initial assumption.

## Capabilities and Constraints
Milestone 1 covers CPU OCR, image preprocessing, PDFs, durable batch jobs, manual review, revision history, and a fixed export format. Vision, RAG, workplace templates, and desktop distribution follow later. No paid or cloud AI services.

## Brand Commitments
Name: ReceiptIQ. No binding visual assets or aesthetic supplied. Independent application from the adjacent Frutiger Aero website.

## Product Principles
Human verification precedes export. Preserve originals and corrections. Represent uncertainty honestly. Keep batches responsive and recoverable.

## Open Decisions
Receipt languages beyond English, workplace-specific fields, and measured minimum hardware remain undecided. Initial interface uses English and requires users to confirm currency and dates.
