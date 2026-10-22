import assert from 'node:assert/strict'
import { chromium } from 'playwright'

const browser = await chromium.launch()
const page = await browser.newPage({ baseURL: process.env.RECEIPTIQ_TEST_URL || 'http://127.0.0.1:5173', timezoneId: 'Asia/Manila' })
try {
  await page.goto('/')
  await page.locator('#batch option').nth(1).waitFor({ state: 'attached' })
  const options = await page.locator('#batch option').evaluateAll(nodes => nodes.map(node => node.value))
  assert.equal(new Set(options).size, options.length)
  assert.ok(options.every(day => !day || /^\d{4}-\d{2}-\d{2}$/.test(day)))
  const day = options[1]
  const token = (await (await page.request.get('/api/v1/session')).json()).token
  const headers = { Authorization: 'Bearer ' + token }
  const batches = await (await page.request.get('/api/v1/batches', { headers })).json()
  const formatter = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Manila', year: 'numeric', month: '2-digit', day: '2-digit' })
  const expected = []
  for (const batch of batches.filter(batch => formatter.format(new Date(batch.created_at)) === day)) {
    for (let offset = 0; ; offset += 500) {
      const rows = await (await page.request.get(`/api/v1/receipts?limit=500&offset=${offset}&batch_id=${batch.id}`, { headers })).json()
      expected.push(...rows.map(row => row.id))
      if (rows.length < 500) break
    }
  }
  await page.locator('#batch').selectOption(day)
  await page.waitForFunction(count => document.querySelectorAll('.receipt-select').length === count, expected.length)
  const actual = await page.locator('.receipt-select').evaluateAll(nodes => nodes.map(node => node.dataset.receiptId))
  assert.deepEqual(actual.sort(), expected.sort())
  console.log(`PASS: one day option combines ${expected.length} receipts across upload batches`)
} finally {
  await browser.close()
}
