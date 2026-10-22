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
  const imageHash = () => page.locator('.image-panel img').evaluate(async image => {
    const bytes = await (await fetch(image.src)).arrayBuffer()
    return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)))
  })
  await page.waitForFunction(() => document.querySelector('.image-panel img')?.naturalWidth > 0)
  const before = await dimensions()
  await rotate.click()
  await page.waitForTimeout(2500)
  await page.waitForFunction(() => !document.querySelector('[aria-label="Rotate clockwise and rerun OCR"]')?.disabled,
    { }, { timeout: 120000 })
  await page.waitForTimeout(500)
  const after = await dimensions()
  const savedPixels = await imageHash()
  assert.equal(after[0] > after[1], before[0] < before[1], 'Preview must show saved rotation before reload')
  await page.reload()
  await page.locator(`[data-receipt-id="${receiptId}"]`).click()
  await page.waitForFunction(() => document.querySelector('.image-panel img')?.naturalWidth > 0)
  assert.deepEqual(await dimensions(), after, 'Reload must preserve displayed orientation')
  // Dimensions alone cannot detect a 180-degree flip or mirrored image.
  assert.deepEqual(await imageHash(), savedPixels, 'Reload must preserve the exact displayed pixels')
  page.once('dialog', dialog => dialog.dismiss())
  await page.getByRole('button', { name: 'Delete receipt', exact: true }).click()
  assert.equal(await page.locator(`[data-receipt-id="${receiptId}"]`).count(), 1, 'Cancel must keep the receipt')
  page.once('dialog', dialog => dialog.accept())
  const deleted = page.waitForResponse(response => response.request().method() === 'DELETE'
    && new URL(response.url()).pathname === `/api/v1/receipts/${receiptId}`)
  await page.getByRole('button', { name: 'Delete receipt', exact: true }).click()
  assert.equal((await deleted).status(), 200)
  await page.locator(`[data-receipt-id="${receiptId}"]`).waitFor({ state: 'detached' })
  await page.reload()
  await page.getByRole('heading', { name: 'Add expense', exact: true }).waitFor()
  assert.equal(await page.locator(`[data-receipt-id="${receiptId}"]`).count(), 0, 'Deleted receipt must stay removed')
  console.log('PASS: delete cancellation preserves receipt; confirmed deletion survives reload')
  console.log('PASS: rotation appears immediately after processing and stays unchanged on reload')
} finally {
  await browser.close()
}
