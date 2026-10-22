// Real upload/OCR regression: the displayed orientation must survive reload.
import assert from 'node:assert/strict'
import path from 'node:path'
import { chromium } from 'playwright'

const browser = await chromium.launch()
const page = await browser.newPage()
const base = process.env.RECEIPTIQ_TEST_URL || 'http://127.0.0.1:5173'
try {
  await page.goto(base)
  await page.getByRole('heading', { name: 'Add expense', exact: true }).waitFor()
  const uploaded = page.waitForResponse(response => response.request().method() === 'POST'
    && new URL(response.url()).pathname === '/api/v1/receipts')
  await page.getByLabel('Choose receipt images').setInputFiles(path.resolve('../artifacts/samples/001-printed.png'))
  const response = await uploaded
  assert.equal(response.status(), 202)
  const receiptId = (await response.json()).id
  await page.locator(`[data-receipt-id="${receiptId}"]`).click()
  const rotate = page.getByRole('button', { name: 'Rotate clockwise and rerun OCR' })
  await rotate.waitFor()
  await page.waitForFunction(() => !document.querySelector('[aria-label="Rotate clockwise and rerun OCR"]')?.disabled,
    { }, { timeout: 120000 })
  const dimensions = () => page.locator('.image-panel img').evaluate(image => [image.naturalWidth, image.naturalHeight])
  await page.waitForFunction(() => document.querySelector('.image-panel img')?.naturalWidth > 0)
  const before = await dimensions()
  await rotate.click()
  await page.waitForTimeout(2500)
  await page.waitForFunction(() => !document.querySelector('[aria-label="Rotate clockwise and rerun OCR"]')?.disabled,
    { }, { timeout: 120000 })
  await page.waitForTimeout(500)
  const after = await dimensions()
  assert.equal(after[0] > after[1], before[0] < before[1], 'Preview must show saved rotation before reload')
  await page.reload()
  await page.locator(`[data-receipt-id="${receiptId}"]`).click()
  await page.waitForFunction(() => document.querySelector('.image-panel img')?.naturalWidth > 0)
  assert.deepEqual(await dimensions(), after, 'Reload must preserve displayed orientation')
  console.log('PASS: rotation appears immediately after processing and stays unchanged on reload')
} finally {
  await browser.close()
}
