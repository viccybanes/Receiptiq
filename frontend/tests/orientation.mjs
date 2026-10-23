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
  const rotate = page.getByRole('button', { name: 'Rotate preview clockwise' })
  await rotate.waitFor()
  await page.waitForFunction(() => !document.querySelector('[aria-label="Rotate preview clockwise"]')?.disabled,
    { }, { timeout: 120000 })
  const dimensions = () => page.locator('.image-panel img').evaluate(image => { const box = image.getBoundingClientRect(); return [Math.round(box.width), Math.round(box.height)] })
  const imageHash = () => page.locator('.image-panel img').evaluate(async image => {
    const canvas = document.createElement('canvas')
    canvas.width = image.naturalWidth; canvas.height = image.naturalHeight
    const context = canvas.getContext('2d')
    context.drawImage(image, 0, 0)
    const bytes = context.getImageData(0, 0, canvas.width, canvas.height).data
    return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)))
  })
  await page.waitForFunction(() => document.querySelector('.image-panel img')?.naturalWidth > 0)
  const before = await dimensions()
  const processingRequests = []
  page.on('request', request => { if (request.url().endsWith('/process')) processingRequests.push(request.url()) })
  await rotate.click()
  await page.waitForTimeout(100)
  await page.waitForFunction(() => !document.querySelector('[aria-label="Rotate preview clockwise"]')?.disabled,
    { }, { timeout: 120000 })
  await page.waitForTimeout(500)
  assert.equal(processingRequests.length, 0, 'Preview rotation must never request OCR')
  const after = await dimensions()
  const savedTransform = await page.locator('.image-panel img').evaluate(image => image.parentElement.style.transform)
  const savedPixels = await imageHash()
  assert.equal(after[0] > after[1], before[0] < before[1], 'Preview must show saved rotation before reload')
  await page.reload()
  await page.locator(`[data-receipt-id="${receiptId}"]`).click()
  await page.waitForFunction(() => document.querySelector('.image-panel img')?.naturalWidth > 0)
  const reloaded = await dimensions()
  assert.equal(reloaded[0] > reloaded[1], after[0] > after[1], 'Reload must preserve displayed orientation')
  // Fit-to-view may choose a different size when the latest-upload toolbar disappears.
  assert.equal(await page.locator('.image-panel img').evaluate(image => image.parentElement.style.transform), savedTransform)
  // Dimensions alone cannot detect a 180-degree flip or mirrored image.
  assert.deepEqual(await imageHash(), savedPixels, 'Reload must preserve the exact displayed pixels')
  await page.locator('.receipt-tools > summary').click()
  await page.getByRole('button', { name: 'Select region', exact: true }).click()
  const overlay = page.getByRole('img', { name: 'Drag to select receipt region' })
  await overlay.scrollIntoViewIfNeeded()
  const bounds = await overlay.boundingBox()
  await page.mouse.move(bounds.x + bounds.width * .2, bounds.y + bounds.height * .2)
  await page.mouse.down()
  await page.mouse.move(bounds.x + bounds.width * .6, bounds.y + bounds.height * .5)
  await page.mouse.up()
  const region = await overlay.locator('rect').evaluate(rect => ['x', 'y', 'width', 'height'].map(name => Number(rect.getAttribute(name))))
  for (const [index, expected] of [.2, .4, .3, .4].entries()) {
    assert.ok(Math.abs(region[index] - expected) < .02, 'Rotated crop must map to original page coordinates')
  }
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
  console.log('PASS: preview rotation never reruns OCR and stays unchanged on reload')
} finally {
  await browser.close()
}
