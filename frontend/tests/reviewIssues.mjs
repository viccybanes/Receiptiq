import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import { chromium } from 'playwright'

// All API calls are mocked: this check never reads or changes a user's receipts.
const data = {
  supplier: 'Review highlight sample', receipt_number: '001', receipt_date: '2026-09-01',
  currency: 'PHP', total: '999.00', subtotal: '', tax: '', discount: '', notes: '', receipt_photo_id: '',
  items: Array.from({ length: 12 }, (_, index) => ({
    id: 'item-' + index, item_number: index + 101, description: index === 1 ? '' : 'Sample material ' + (index + 1),
    quantity: index === 3 ? '' : '2', unit: 'unit', unit_price: index === 3 ? '' : '5.00',
    line_total: index === 0 ? '9.00' : '10.00', item_type: index === 2 ? 'Tools' : 'Materials',
    photo_id: index === 2 ? 'sample-photo' : '',
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
let photoGate = Promise.resolve()
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl6PAAAAABJRU5ErkJggg==', 'base64')
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
  else if (pathname.startsWith('/receipts/review-sample/photos/'))
    return route.fulfill({ contentType: 'image/png', body: png })
  else if (pathname === '/receipts/review-sample/photos') {
    await photoGate
    body = { id: 'uploaded-photo' }
  }
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

  // Row controls keep full item objects (including photos and independent amounts) together.
  await page.waitForFunction(() => document.querySelector('.save-state')?.textContent === 'Saved locally')
  const beforeMove = structuredClone(receipt.data)
  assert.equal(await rows.first().getByRole('button', { name: 'Move item 1 up', exact: true }).isDisabled(), true)
  assert.equal(await rows.last().getByRole('button', { name: 'Move item 11 down', exact: true }).isDisabled(), true)
  saveGate = new Promise(resolve => { releaseSave = resolve })
  await page.getByRole('button', { name: 'Move item 2 up', exact: true }).click()
  assert.equal(await page.getByLabel('Item 1 description', { exact: true }).inputValue(), 'Sample material 3')
  assert.match(await rows.first().locator('.inline-issues').innerText(), /OCR had difficulty/)
  assert.equal(await rows.first().locator('.photo-field img').count(), 1, 'Photo follows its item before save')
  assert.equal(await page.getByLabel('Item 1 Type', { exact: true }).inputValue(), 'Tools')
  assert.match(await rows.first().locator('legend').innerText(), /Item 103/)
  releaseSave()
  await page.waitForFunction(() => document.querySelector('.save-state')?.textContent === 'Saved locally')
  const expectedMove = structuredClone(beforeMove)
  ;[expectedMove.items[0], expectedMove.items[1]] = [expectedMove.items[1], expectedMove.items[0]]
  assert.deepEqual(receipt.data, expectedMove, 'Reorder changes order only, including money and stable identity')
  await page.locator('.validation-panel input[type="checkbox"]').first().check()
  await page.getByLabel('Reason for accepting warnings', { exact: true }).fill('Checked sample')
  await page.getByRole('button', { name: 'Undo edit', exact: true }).click()
  assert.equal(await page.locator('.validation-panel input:checked').count(), 0, 'Undo resets warning acceptance')
  assert.equal(await page.getByLabel('Reason for accepting warnings', { exact: true }).inputValue(), '')
  await page.waitForFunction(() => document.querySelector('.save-state')?.textContent === 'Saved locally')
  assert.deepEqual(receipt.data, beforeMove, 'Undo restores saved order and photo identity')

  await page.getByRole('button', { name: 'Move item 1 down', exact: true }).click()
  await page.waitForFunction(() => document.querySelector('.save-state')?.textContent === 'Saved locally')
  assert.deepEqual(receipt.data, expectedMove, 'Down arrow persists the same adjacent move')
  await page.getByRole('button', { name: 'Add line below item 1', exact: true }).click()
  assert.equal(await rows.count(), 12)
  assert.equal(await page.getByLabel('Item 2 description', { exact: true }).inputValue(), '')
  assert.equal(await page.getByLabel('Item 2 Unit', { exact: true }).inputValue(), 'unit')
  assert.equal(await page.getByLabel('Item 2 description', { exact: true }).evaluate(element => element === document.activeElement), true)
  assert.doesNotMatch(await rows.nth(1).getAttribute('class'), /has-review/, 'New row does not inherit saved warnings')
  await page.waitForFunction(() => document.querySelector('.save-state')?.textContent === 'Saved locally')
  const insertedId = receipt.data.items[1].id
  assert.equal(new Set(receipt.data.items.map(item => item.id)).size, 12)
  assert.match(insertedId, /^[0-9a-f-]{36}$/)
  assert.deepEqual(receipt.data.items.filter(item => item.id !== insertedId), expectedMove.items)
  await page.getByRole('button', { name: 'Undo edit', exact: true }).click()
  await page.waitForFunction(() => document.querySelector('.save-state')?.textContent === 'Saved locally')
  assert.deepEqual(receipt.data, expectedMove, 'Undo removes only the inserted line')
  await page.getByRole('button', { name: 'Add line', exact: true }).click()
  assert.equal(await page.getByLabel('Item 12 Unit', { exact: true }).inputValue(), 'unit')
  await page.waitForFunction(() => document.querySelector('.save-state')?.textContent === 'Saved locally')
  assert.notEqual(receipt.data.items[11].id, insertedId, 'Each inserted line has its own UUID')
  await page.getByRole('button', { name: 'Undo edit', exact: true }).click()
  await page.waitForFunction(() => document.querySelector('.save-state')?.textContent === 'Saved locally')

  // Pause an attachment upload: every row mutation must wait, including Undo and bottom Add line.
  let releasePhoto
  photoGate = new Promise(resolve => { releasePhoto = resolve })
  await rows.first().locator('input[type="file"]').setInputFiles({ name: 'sample.png', mimeType: 'image/png', buffer: png })
  await page.waitForFunction(() => document.querySelector('.item-fields')?.disabled)
  for (const button of await page.locator('.item-actions button').all()) assert.equal(await button.isDisabled(), true)
  assert.equal(await page.getByRole('button', { name: 'Add line', exact: true }).isDisabled(), true)
  assert.equal(await page.getByRole('button', { name: 'Undo edit', exact: true }).isDisabled(), true)
  releasePhoto()
  await page.waitForFunction(() => !document.querySelector('.item-fields')?.disabled)
  await page.waitForFunction(() => document.querySelector('.save-state')?.textContent === 'Saved locally')
  assert.equal(receipt.data.items[0].id, 'item-2')
  assert.equal(receipt.data.items[0].photo_id, 'uploaded-photo')
  const persisted = structuredClone(receipt.data)
  await page.reload()
  await page.locator('[data-receipt-id="review-sample"]').click()
  await rows.nth(10).waitFor()
  assert.equal(await page.getByLabel('Item 1 description', { exact: true }).inputValue(), persisted.items[0].description)
  assert.equal(await rows.first().locator('.photo-field img').count(), 1)
  assert.equal(await page.getByRole('button', { name: 'Move item 1 up', exact: true }).isDisabled(), true)
  assert.equal(await page.getByRole('button', { name: 'Move item 11 down', exact: true }).isDisabled(), true)
  assert.deepEqual(errors, [])
  console.log('PASS: inline issues, deletion/reorder during autosave, up/down/insert/undo, stable item/photo/money identity, upload guards, persistence, desktop/mobile layout')
} finally {
  await browser.close()
}
