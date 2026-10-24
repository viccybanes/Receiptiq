import type { Receipt, ReceiptData } from './types'

function validBox(box?: number[]): box is number[] {
  return !!box && box.length === 4 && box.every(value => Number.isFinite(value) && value >= 0 && value <= 1) &&
    box[0] < box[2] && box[1] < box[3]
}

export function visionOverlay(receipt: Receipt, draft: ReceiptData, pageId: string) {
  const current = draft.items.flatMap((item, index) => {
    const savedIndex = receipt.data.items.findIndex(saved => saved.id === item.id)
    const metadata = Object.entries(receipt.field_meta).find(([field, evidence]) =>
      field.startsWith(`items.${savedIndex}.`) && evidence.page_id === pageId && validBox(evidence.source_box))?.[1]
    return metadata && validBox(metadata.source_box) ? [{ box: metadata.source_box, line: index + 1,
      title: `Line ${index + 1}: ${item.description}`, suggested: false }] : []
  })
  if (current.length) return current
  const candidate = receipt.candidate
  if (!candidate || candidate.photo_id !== undefined && candidate.photo_id !== draft.receipt_photo_id) return []
  return (candidate.source_regions || []).flatMap(region => {
    if (region.page_id !== pageId || !validBox(region.box)) return []
    const source = candidate.draft.items.find(item => item.id === region.item_id)
    const index = draft.items.findIndex(item => item.id === region.item_id)
    const keys = ['description', 'quantity', 'unit', 'unit_price', 'line_total'] as const
    if (source && index >= 0 && keys.every(key => source[key] === draft.items[index][key])) {
      return [{ box: region.box, line: index + 1, title: `Line ${index + 1}: ${source.description}`, suggested: false }]
    }
    return candidate.base_revision === receipt.revision ? [{ box: region.box, line: region.item_index + 1,
      title: `Suggested line ${region.item_index + 1}: ${source?.description || ''}`, suggested: true }] : []
  })
}
