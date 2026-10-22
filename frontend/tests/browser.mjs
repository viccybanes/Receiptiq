import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import path from 'node:path'
import { chromium } from 'playwright'

const root = path.resolve('..')
const baseURL = process.env.RECEIPTIQ_TEST_URL || 'http://127.0.0.1:8765'
const artifacts = path.join(root, 'artifacts', 'screenshots')
await fs.mkdir(artifacts, { recursive: true })
const browser = await chromium.launch({ headless: true })
const context = await browser.newContext({ baseURL,
  viewport: { width: 1440, height: 1080 }, acceptDownloads: true })
const page = await context.newPage()
const errors = []
page.on('pageerror', error => errors.push(error.message))
await page.goto(baseURL)
await page.getByRole('heading', { name: 'Add expense', exact: true }).waitFor()
await page.waitForFunction(() => !document.querySelector('.workspace-heading button')?.disabled)
await page.screenshot({ path: path.join(artifacts, 'workspace-start.png'), fullPage: true })
const sampleNames = ['001-printed.png', '002-synthetic-cursive.png', '003-mixed.png', 'sample.pdf']
const newBatch = page.waitForResponse(response => response.request().method() === 'POST' &&
  new URL(response.url()).pathname === '/api/v1/batches')
await page.getByLabel('Choose receipt images').setInputFiles(sampleNames.map(name => path.join(root, 'artifacts', 'samples', name)))
const batchId = (await (await newBatch).json()).id
await page.waitForFunction(() => !!document.querySelector('#batch')?.value)
await page.waitForFunction(() => document.querySelectorAll('.receipt-select').length === 4, { timeout: 30000 })
const token = (await (await page.request.get('/api/v1/session')).json()).token
async function request(route, options = {}) {
  const response = await page.request.get('/api/v1' + route, { ...options, headers: { Authorization: 'Bearer ' + token } })
  assert.equal(response.ok(), true, await response.text())
  return response.json()
}
const uploadDay = await page.locator('#batch').inputValue()
const deadline = Date.now() + 240000
let rows = []
while (Date.now() < deadline) {
  rows = await request('/receipts?batch_id=' + batchId)
  if (rows.length === 4 && rows.every(row => !['queued', 'processing'].includes(row.status))) break
  await new Promise(resolve => setTimeout(resolve, 1000))
}
assert.equal(rows.length, 4)
assert.equal(rows.every(row => row.status === 'needs_review'), true, JSON.stringify(rows.map(r => ({ name: r.original_name, status: r.status, error: r.error }))))
await page.locator('[data-receipt-id="' + rows[0].id + '"]').click()
const supplier = page.getByRole('textbox', { name: 'Vendor', exact: true })
await supplier.waitFor()
await supplier.fill('Northside Market — verified')
await page.getByRole('textbox', { name: 'Reviewed by' }).fill('Browser QA')
await page.getByRole('button', { name: 'Save now', exact: true }).click()
await page.waitForFunction(() => document.querySelector('.save-state')?.textContent?.includes('Saved locally'))
let receipt = await request('/receipts/' + rows[0].id)
assert.equal(receipt.data.supplier, 'Northside Market — verified')
await page.screenshot({ path: path.join(artifacts, 'desktop.png'), fullPage: true })
await page.setViewportSize({ width: 390, height: 844 })
await page.screenshot({ path: path.join(artifacts, 'mobile.png'), fullPage: true })
assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true, 'Mobile horizontal overflow')
await page.setViewportSize({ width: 1440, height: 1080 })
// Correct the whole draft through the same API contract, then verify and export through the UI.
const corrected = { supplier: 'Northside Market — verified', receipt_number: 'NS-001', receipt_date: '2026-10-19',
  currency: 'PHP', subtotal: '125.00', tax: '0.00', discount: '', total: '125.00', items: [
    { description: 'Coffee', quantity: '2', unit_price: '50.00', line_total: '100.00' },
    { description: 'Bread', quantity: '1', unit_price: '25.00', line_total: '25.00' },
  ] }
const patched = await page.request.patch('/api/v1/receipts/' + rows[0].id, {
  headers: { Authorization: 'Bearer ' + token }, data: { revision: receipt.revision, data: corrected },
})
assert.equal(patched.status(), 200, await patched.text())
await page.reload()
await page.waitForFunction(() => !document.querySelector('.workspace-heading button')?.disabled)
await page.locator('#batch').selectOption(uploadDay)
await page.waitForFunction(() => document.querySelectorAll('.receipt-select').length === 4)
await page.locator('[data-receipt-id="' + rows[0].id + '"]').click()
await page.getByRole('textbox', { name: 'Reviewed by' }).fill('Browser QA')
await page.getByRole('button', { name: 'Verify receipt', exact: true }).click()
await page.getByText('Verified by a person').waitFor()
await page.getByRole('button', { name: 'Select all verified', exact: true }).click()
await page.getByRole('button', { name: 'Preview CSV', exact: true }).click()
await page.getByRole('heading', { name: 'Check your CSV' }).waitFor()
assert.equal(await page.locator('.export-dialog').evaluate(element => element.open), true)
await page.route('**/api/v1/exports', route => route.fulfill({
  status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'Export failed. Retry download.' }),
}))
await page.getByRole('button', { name: 'Download CSV', exact: true }).click()
await page.locator('.export-dialog [role=alert]').waitFor()
assert.match(await page.locator('.export-dialog [role=alert]').innerText(), /Export failed/)
await page.screenshot({ path: path.join(artifacts, 'export-error.png'), fullPage: true })
await page.unroute('**/api/v1/exports')
const downloading = page.waitForEvent('download')
await page.getByRole('button', { name: 'Download CSV', exact: true }).click()
const file = await downloading
await file.saveAs(path.join(root, 'artifacts', 'browser-export.csv'))
const csv = await fs.readFile(path.join(root, 'artifacts', 'browser-export.csv'), 'utf8')
assert.match(csv, /Northside Market/)
assert.match(csv, /Coffee/)
await page.getByRole('textbox', { name: 'Vendor', exact: true }).fill('Edited after verification')
await page.getByRole('button', { name: 'Save now', exact: true }).click()
await page.waitForFunction(() => document.querySelector('.save-state')?.textContent?.includes('Saved locally'))
receipt = await request('/receipts/' + rows[0].id)
assert.equal(receipt.status, 'needs_review')
assert.equal(receipt.verified_revision, null)
const otherEdit = await page.request.patch('/api/v1/receipts/' + receipt.id, {
  headers: { Authorization: 'Bearer ' + token },
  data: { revision: receipt.revision, data: { ...receipt.data, supplier: 'Saved in another window' } },
})
assert.equal(otherEdit.status(), 200)
await page.getByRole('textbox', { name: 'Vendor', exact: true }).fill('My retained draft')
await page.getByRole('button', { name: 'Save now', exact: true }).click()
await page.getByRole('button', { name: 'Keep my draft', exact: true }).waitFor()
assert.equal(await page.getByRole('textbox', { name: 'Vendor', exact: true }).inputValue(), 'My retained draft')
await page.getByRole('button', { name: 'Keep my draft', exact: true }).click()
await page.waitForFunction(() => document.querySelector('.save-state')?.textContent?.includes('Saved locally'))
assert.equal((await request('/receipts/' + receipt.id)).data.supplier, 'My retained draft')
// Expense fields, optional photos, and an explicitly confirmed implicit discount.
assert.equal(await page.getByRole('textbox', { name: 'Tax', exact: true }).count(), 0)
await page.getByRole('textbox', { name: 'Notes', exact: true }).fill('Check delivery')
await page.getByRole('textbox', { name: 'Item 1 Unit', exact: true }).fill('bag')
await page.getByLabel('Photo of item purchased — Add photo').first().setInputFiles(path.join(root, 'artifacts/samples/001-printed.png'))
await page.getByRole('button', { name: 'Remove photo', exact: true }).first().waitFor()
await page.getByRole('textbox', { name: 'Total', exact: true }).fill('100.01')
await page.getByRole('button', { name: 'Save now', exact: true }).click()
await page.getByRole('button', { name: 'Confirm discount and apply to line items', exact: true }).click()
await page.waitForFunction(() => document.querySelector('[aria-label="Item 1 Amount"]')?.value === '80.01')
await page.waitForFunction(() => document.querySelector('.save-state')?.textContent?.includes('Saved locally'))
receipt = await request('/receipts/' + receipt.id)
assert.equal(receipt.data.items[0].line_total, '80.01')
assert.ok(receipt.data.items[0].photo_id)
assert.match(receipt.data.notes, /Confirmed discount 24.99/)
await page.getByRole('button', { name: 'Verify receipt', exact: true }).click()
await page.getByText('Verified by a person').waitFor()
await page.getByRole('button', { name: 'Select all verified', exact: true }).click()
await page.getByRole('button', { name: 'Preview CSV', exact: true }).click()
const zipDownload = page.waitForEvent('download')
await page.getByRole('button', { name: 'Download CSV + photos', exact: true }).click()
await (await zipDownload).saveAs(path.join(root, 'artifacts/browser-export.zip'))
await page.getByLabel('Choose CSV or ZIP and optional photos').setInputFiles(path.join(root, 'artifacts/browser-export.zip'))
await page.getByRole('status').filter({ hasText: '1 receipts imported for review' }).waitFor()
await page.waitForFunction(() => document.querySelectorAll('.receipt-select').length === 1)
await page.locator('.receipt-select').click()
await page.locator('.review-state').filter({ hasText: 'Ready for your review' }).waitFor()
assert.equal(await page.getByRole('textbox', { name: 'Item 1 Amount', exact: true }).inputValue(), '80.01')
assert.equal(await page.getByRole('textbox', { name: 'Item 1 Unit', exact: true }).inputValue(), 'bag')
assert.match(await page.getByRole('textbox', { name: 'Notes', exact: true }).inputValue(), /Confirmed discount/)
assert.equal(await page.getByRole('button', { name: 'Verify receipt', exact: true }).isEnabled(), true)
await page.screenshot({ path: path.join(artifacts, 'expense-desktop.png'), fullPage: true })
await page.setViewportSize({ width: 390, height: 844 })
await page.screenshot({ path: path.join(artifacts, 'expense-mobile.png'), fullPage: true })
assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true, 'Expense mobile overflow')
assert.deepEqual(errors, [], 'Browser exceptions')
console.log('Browser checks passed: 4-file real OCR including PDF; autosave, verify, CSV, invalidation, conflicts, export retry; expense fields, optional photo, explicit discount allocation, ZIP photo round trip; desktop/mobile overflow.')
await browser.close()
