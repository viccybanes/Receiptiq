export type Item = { id: string; item_number: number | null; description: string; quantity: string; unit: string;
  unit_price: string; line_total: string; item_type: string; photo_id: string }
export type ReceiptData = {
  supplier: string; receipt_number: string; receipt_date: string; currency: string
  subtotal: string; tax: string; discount: string; total: string; items: Item[]
  notes: string; receipt_photo_id: string
}
export type Issue = { id: string; field: string; message: string; blocking: boolean }
export type Summary = {
  id: string; batch_id: string; status: string; revision: number; verified_revision: number | null
  error: string | null; original_name: string; data_json: string; stage: string
  assignee: string; second_review: boolean; duplicate_count: number
}
export type Example = { receipt_id: string; revision: number; vendor: string; score: number; citation: string;
  items: { id: string; description: string; unit: string; item_type: string }[];
  corrections: { field: string; before: string; after: string }[] }
export type Line = { id: string; text: string; page: number; score: number; box: number[] }
export type Receipt = Summary & {
  data: ReceiptData; field_meta: Record<string, { source: string; line_ids: string[] }>
  pages: { id: string; page_number: number; width: number; height: number }[]
  original_mime: string
  retrieval_allowed: boolean
  duplicates: { id: string; original_name: string; status: string }[]
  vision_job: { id: string; state: string; stage: string; error: string | null; progress: number } | null
  candidate: { id: string; base_revision: number; draft: ReceiptData; model: string; seconds: number;
    notice: string; provenance: Record<string, unknown> } | null
  reconciliation: { item_sum: string; total: string; possible_discount: string; can_allocate: boolean } | null
  issues: Issue[]; evidence: { lines: Line[]; draft?: ReceiptData; photo_id?: string; examples?: Example[];
    retrieval_notice?: string }; rotation: 0 | 90 | 180 | 270
}
export type Batch = { id: string; name: string; created_at: string; file_count: number; cancelled: number;
  counts?: Record<string, number>; files?: { id: string; name: string; state: string; error: string | null }[] }
