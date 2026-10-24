import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import { chromium } from 'playwright'

// All API calls are mocked: this check never reads or changes a user's receipts.
const data = {
  supplier: 'Review highlight sample', receipt_number: '001', receipt_date: '2026-09-01',
  currency: 'PHP', total: '999.00', subtotal: '', tax: '', discount: '', notes: '', receipt_photo_id: '',
  items: Array.from({ length: 12 }, (_, index) => ({
    id: 'item-' + index, item_number: null, description: index === 1 ? '' : 'Sample material ' + (index + 1),
    quantity: index === 3 ? '' : '2', unit: 'unit', unit_price: index === 3 ? '' : '5.00',
    line_total: index === 0 ? '9.00' : '10.00', item_type: 'Materials', photo_id: '',
  })),
}
function issuesFor(data) {
  const issues = [{ id: 'item_sum', field: 'item_sum', blocking: false,
    message: 'Total exceeds line Amounts. Check missing items, prices, or charges.' }]
  data.items.forEach((item, index) => {
    const add = (key, message, blocking = false, suffix = '') => issues.push({
      id: `items.${index}.${key}${suffix}`, field: `items.${index}.${key}`, message, blocking,
    })
    if (item.id === 'item-0' && item.line_total === '9.00')
      add('arithmetic', `Line ${index + 1}: Amount is 1.00 below Quantity × Unit price; check a possible discount.`)
    if (!item.description) add('description', `Item ${index + 1} needs a description.`, true)
    if (item.id === 'item-2') add('description', 'OCR had difficulty reading this description.', false, '.ocr')
    if (item.id === 'item-3') add('pricing', 'Quantity or unit price is missing. Check the original.')
  })
  return issues
}
let receipt = {
  id: 'review-sample', batch_id: 'sample-batch', status: 'needs_review', revision: 1,
  verified_revision: null, original_name: 'synthetic.png', original_mime: 'image/png',
  data, data_json: JSON.stringify(data), issues: issuesFor(data), evidence: { lines: [] },
  field_meta: {}, pages: [], duplicates: [], candidate: null, vision_job: null, reconciliation: null,
  retrieval_allowed: false, assignee: '', second_review: false, duplicate_count: 0, rotation: 0,
}
let saveGate = Promise.resolve()
const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1440, height: 1080 } })
const errors = []
page.on('pageerror', error => errors.push(error.message))
await page.route('**/api/v1/**', async route => {
  const pathname = new URL(route.request().url()).pathname.replace('/api/v1', '')
  let body
  if (pathname === '/session') body = { token: 'synthetic-test' }
  else if (pathname === '/health') body = { ocr_models_ready: true }
  else if (pathname === '/templates' || pathname === '/batches') body = []
  else if (pathname === '/receipts') body = [receipt]
  else if (pathname === '/receipts/review-sample') {
    if (route.request().method() === 'PATCH') {
      await saveGate
      const { data } = route.request().postDataJSON()
      receipt = { ...receipt, revision: receipt.revision + 1, data, data_json: JSON.stringify(data), issues: issuesFor(data) }
    }
    body = receipt
  } else {
    errors.push('Unexpected API request: ' + pathname)
    return route.fulfill({ status: 404, json: { detail: 'Unexpected test request' } })
  }
  await route.fulfill({ json: body })
})

try {
  await page.goto(process.env.RECEIPTIQ_TEST_URL || 'http://127.0.0.1:5173')
  await page.locator('[data-receipt-id="review-sample"]').click()
  const rows = page.locator('.item-fields')
  await rows.nth(11).waitFor()
  assert.match(await rows.nth(0).getAttribute('class'), /has-review-warning/)
  assert.match(await rows.nth(0).locator('.inline-issues').innerText(), /below Quantity/)
  assert.match(await rows.nth(1).getAttribute('class'), /has-review-error/)
  assert.equal(await page.getByLabel('Item 2 description', { exact: true }).getAttribute('aria-invalid'), 'true')
  assert.match(await rows.nth(2).getAttribute('class'), /has-review-warning/)
  assert.match(await rows.nth(3).getAttribute('class'), /has-review-warning/)
  assert.doesNotMatch(await rows.nth(10).getAttribute('class'), /has-review/)
  assert.match(await page.getByLabel('Item 1 Amount', { exact: true }).getAttribute('aria-describedby'), /arithmetic/)
  assert.match(await page.getByLabel('Total', { exact: true }).getAttribute('aria-describedby'), /item_sum/)
  assert.equal(await page.locator('.validation-panel input:checked').count(), 0)
  await page.locator('.validation-panel input[type="checkbox"]').first().check()
  assert.match(await rows.nth(0).getAttribute('class'), /has-review-warning/, 'Acknowledging must not hide a warning')
  assert.equal(await page.evaluate(() => [...document.querySelectorAll('[aria-describedby]')].every(element =>
    element.getAttribute('aria-describedby').split(' ').every(id => document.getElementById(id)))), true)

  // Capture both supported layouts in one pass using the actual app and CSS.
  const screenshots = new URL('../../artifacts/review-issues/', import.meta.url)
  await fs.mkdir(screenshots, { recursive: true })
  for (const [name, width, height] of [['desktop', 1440, 1080], ['mobile', 390, 844]]) {
    await page.setViewportSize({ width, height })
    await rows.first().scrollIntoViewIfNeeded()
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, name + ' overflow')
    await page.screenshot({ path: new URL(name + '.png', screenshots).pathname.replace(/^\/(\w:)/, '$1') })
  }
  await page.setViewportSize({ width: 1440, height: 1080 })
  await page.getByLabel('Item 1 Amount', { exact: true }).fill('10.00')
  await page.waitForFunction(() => !document.querySelector('.item-fields')?.className.includes('has-review'))
  assert.equal(await page.locator('.validation-panel input:checked').count(), 0, 'An edit resets acknowledgments')

  // Pause autosave to prove a removed row cannot shift warnings onto the wrong item.
  let releaseSave
  saveGate = new Promise(resolve => { releaseSave = resolve })
  await page.getByRole('button', { name: 'Remove item 1', exact: true }).click()
  assert.match(await rows.nth(0).getAttribute('class'), /has-review-error/)
  assert.match(await rows.nth(1).locator('.inline-issues').innerText(), /OCR had difficulty/)
  assert.match(await rows.nth(2).locator('.inline-issues').innerText(), /Quantity or unit price/)
  assert.doesNotMatch(await rows.nth(3).getAttribute('class'), /has-review/)
  releaseSave()
  await page.waitForFunction(() => document.querySelector('.save-state')?.textContent === 'Saved locally')
  await page.getByLabel('Item 1 description', { exact: true }).fill('Corrected material')
  await page.waitForFunction(() => !document.querySelector('.item-fields')?.className.includes('has-review'))
  assert.match(await rows.nth(1).getAttribute('class'), /has-review-warning/, 'Other warnings remain')
  assert.deepEqual(errors, [])
  console.log('PASS: inline arithmetic/OCR/pricing/errors, total warning, acknowledgments, correction, deletion during autosave, desktop/mobile layout')
} finally {
  await browser.close()
}
