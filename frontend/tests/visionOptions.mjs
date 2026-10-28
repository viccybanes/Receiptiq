import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import http from 'node:http'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import ts from 'typescript'
import { chromium } from 'playwright'

const source = await fs.readFile(new URL('../src/visionOverlay.ts', import.meta.url), 'utf8')
const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText
const { visionOverlay } = await import('data:text/javascript;base64,' + Buffer.from(compiled).toString('base64'))
let settings = { amount_first: false, capture_regions: false, provider: 'local', google_model: 'gemini-3.8-flash',
  google_consent: false, google_key_saved: false }
let rejectProvider = false
const testKey = 'synthetic-ui-key-never-a-real-credential'
const data = { supplier: 'Synthetic hardware', receipt_number: '001', receipt_date: '2026-10-25', currency: 'PHP',
  subtotal: '', tax: '', discount: '', total: '60.00', notes: '', receipt_photo_id: '',
  items: ['Cable', 'Connector'].map((description, index) => ({ id: 'item-' + index, item_number: index + 1,
    description, quantity: '2', unit: 'pcs', unit_price: index ? '5.00' : '25.00',
    line_total: index ? '10.00' : '50.00', item_type: 'Materials', photo_id: '' })) }
const metadata = { 'items.0.description': { source: 'local_vision', line_ids: [], page_id: 'p1', source_box: [0.1, 0.25, 0.9, 0.31] },
  'items.1.description': { source: 'local_vision', line_ids: [], page_id: 'p1', source_box: [0.1, 0.35, 0.9, 0.41] } }
let receipt = { id: 'vision-sample', batch_id: 'sample', status: 'needs_review', revision: 2, verified_revision: null,
  original_name: 'synthetic.png', original_mime: 'image/png', data, data_json: JSON.stringify(data),
  issues: [], evidence: { lines: [] }, field_meta: metadata,
  pages: [{ id: 'p1', page_number: 0, width: 800, height: 1000 }], duplicates: [], candidate: null,
  vision_job: null, reconciliation: null, retrieval_allowed: false, assignee: '', second_review: false, duplicate_count: 0,
  rotation: 0, vision_settings: settings }
assert.equal(visionOverlay(receipt, data, 'p1').length, 2)
assert.equal(visionOverlay(receipt, data, 'other-page').length, 0)
const reordered = { ...data, items: [...data.items].reverse() }
assert.equal(visionOverlay(receipt, reordered, 'p1')[0].title, 'Line 1: Connector')
assert.deepEqual(visionOverlay(receipt, reordered, 'p1')[0].box, metadata['items.1.description'].source_box)
const applied = { ...receipt, field_meta: {}, candidate: { id: 'candidate', photo_id: '', base_revision: 1, draft: data,
  source_regions: [{ item_id: 'item-0', item_index: 0, page_id: 'p1', box: [0.1, 0.25, 0.9, 0.31] }] } }
assert.equal(visionOverlay(applied, data, 'p1')[0].suggested, false)
assert.equal(visionOverlay(applied, { ...data, receipt_photo_id: 'replacement' }, 'p1').length, 0)
assert.equal(visionOverlay(applied, { ...data, items: [{ ...data.items[0], description: 'Edited' }] }, 'p1').length, 0)

// A private static server and mocked API isolate all checks from user workspaces.
const dist = fileURLToPath(new URL('../dist/', import.meta.url))
const server = http.createServer(async (req, res) => {
  const name = new URL(req.url, 'http://localhost').pathname
  const file = path.join(dist, name === '/' ? 'index.html' : name)
  try {
    const bytes = await fs.readFile(file)
    res.setHeader('Content-Type', file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html')
    res.end(bytes)
  } catch { res.statusCode = 404; res.end() }
})
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } })
const errors = []
page.on('pageerror', error => errors.push(error.message))
await page.route('**/api/v1/**', async route => {
  const pathname = new URL(route.request().url()).pathname.replace('/api/v1', '')
  let body
  if (pathname === '/session') body = { token: 'synthetic-test' }
  else if (pathname === '/health') body = { ocr_models_ready: true }
  else if (pathname === '/templates' || pathname === '/batches') body = []
  else if (pathname === '/receipts') body = [receipt]
  else if (pathname === '/vision/settings') {
    if (route.request().method() === 'PATCH' && rejectProvider && route.request().postDataJSON().provider) {
      rejectProvider = false
      return route.fulfill({ status: 422, json: { detail: 'Synthetic provider save failure. Retry.' } })
    }
    if (route.request().method() === 'PATCH') settings = { ...settings, ...route.request().postDataJSON() }
    receipt.vision_settings = settings; body = settings
  } else if (pathname === '/vision/google-key') {
    if (route.request().method() === 'PUT') {
      assert.equal(route.request().postDataJSON().api_key, testKey)
      settings = { ...settings, google_key_saved: true }
    } else if (route.request().method() === 'DELETE') {
      settings = { ...settings, provider: 'local', google_consent: false, google_key_saved: false }
    } else throw new Error('API key must not be fetched')
    receipt.vision_settings = settings; body = settings
  } else if (pathname === '/system/capabilities') body = { cpu: 'Test CPU', ram_gb: 16, free_disk_gb: 100,
    nvidia: [], recommended_profile: '2b', profiles: { 'ocr-only': {}, '2b': {}, '4b': {} }, workspace: 'C:\\Test', recommendation_note: 'Synthetic test' }
  else if (pathname === '/setup/status') body = { ocr_ready: true, runtime_installed: true, runtime_running: true,
    runtime_version: 'test', models: ['qwen3-vl:2b-instruct'], profile: '2b', job: {} }
  else if (pathname.startsWith('/receipts/vision-sample/pages/')) return route.fulfill({ contentType: 'image/svg+xml',
    body: '<svg xmlns="http://www.w3.org/2000/svg" width="800" height="1000"><rect width="800" height="1000" fill="white"/><g font-family="Arial" font-size="28"><text x="80" y="100">SYNTHETIC HARDWARE</text><text x="80" y="290">Cable                 2 pcs        25.00       50.00</text><text x="80" y="390">Connector          2 pcs         5.00       10.00</text><text x="500" y="600">Total 60.00</text></g></svg>' })
  else if (pathname === '/receipts/vision-sample') {
    if (route.request().method() === 'PATCH') {
      receipt = { ...receipt, data: route.request().postDataJSON().data, revision: receipt.revision + 1 }
    }
    body = receipt
  } else { errors.push('Unexpected API request: ' + pathname); return route.fulfill({ status: 404, json: {} }) }
  await route.fulfill({ json: body })
})
try {
  await page.goto('http://127.0.0.1:' + server.address().port)
  await page.locator('[data-receipt-id="vision-sample"]').click()
  await page.getByRole('checkbox', { name: 'Show AI line numbers', exact: true }).check()
  await page.locator('.vision-row-overlay').waitFor()
  assert.equal(await page.locator('.vision-row-overlay rect').count(), 2)
  assert.equal(await page.locator('.vision-row-overlay text').allTextContents().then(values => values.join(',')), '1,2')
  await page.getByRole('button', { name: 'Rotate preview clockwise' }).click()
  assert.match(await page.locator('.vision-row-overlay').evaluate(element => element.parentElement.style.transform), /rotate\(90deg\)/)
  await page.getByRole('button', { name: 'Settings', exact: true }).click()
  await page.getByLabel('Vision provider', { exact: true }).selectOption('google')
  const saveProvider = page.getByRole('button', { name: 'Save vision provider', exact: true })
  assert.equal(await saveProvider.isDisabled(), true)
  await page.getByLabel('Google API key', { exact: true }).fill(testKey)
  assert.equal(await page.getByLabel('Google API key', { exact: true }).getAttribute('type'), 'password')
  await page.getByRole('button', { name: 'Save API key', exact: true }).click()
  await page.getByText('Key saved securely on this PC.', { exact: false }).waitFor()
  assert.equal(await page.getByLabel('Google API key', { exact: true }).inputValue(), '')
  assert.equal(settings.provider, 'local')
  assert.equal(await saveProvider.isDisabled(), true)
  await page.getByRole('checkbox', { name: 'Allow receipt images and OCR text' }).check()
  rejectProvider = true
  await saveProvider.click()
  await page.getByRole('alert').filter({ hasText: 'Synthetic provider save failure' }).waitFor()
  assert.equal(settings.provider, 'local')
  await saveProvider.click()
  await page.getByText('Google enabled for new uploads and manual scans.').waitFor()
  assert.equal(settings.provider, 'google')
  assert.equal(await page.evaluate(key => JSON.stringify(localStorage).includes(key), testKey), false)
  await page.getByRole('button', { name: 'Close workspace settings' }).click()
  await page.getByRole('heading', { name: 'Read with Google vision' }).waitFor()
  await page.getByRole('button', { name: 'Settings', exact: true }).click()
  await page.getByText('Active: Google AI Studio', { exact: true }).waitFor()
  assert.equal(await page.getByLabel('Vision provider', { exact: true }).inputValue(), 'google')
  assert.equal(await page.getByLabel('Google API key', { exact: true }).inputValue(), '')
  const cloudFolder = new URL('../../artifacts/google-vision/', import.meta.url)
  await fs.mkdir(cloudFolder, { recursive: true })
  for (const [name, width, height] of [['desktop', 1440, 1000], ['mobile', 390, 844]]) {
    await page.setViewportSize({ width, height })
    await page.locator('.settings-dialog-content').evaluate(element => { element.scrollTop = 0 })
    await page.screenshot({ path: fileURLToPath(new URL(name + '-settings.png', cloudFolder)) })
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true)
    assert.equal(await page.locator('.settings-dialog-content').evaluate(element => element.scrollWidth <= element.clientWidth), true)
  }
  await page.getByRole('button', { name: 'Remove API key' }).click()
  await page.getByText('Key removed. Local Qwen is selected.').waitFor()
  assert.equal(settings.provider, 'local')
  assert.equal(settings.google_key_saved, false)
  await page.getByRole('button', { name: 'Close workspace settings' }).click()
  await page.getByRole('heading', { name: 'Read with local vision' }).waitFor()
  await page.getByRole('button', { name: 'Settings', exact: true }).click()
  await page.getByRole('checkbox', { name: 'Read Amount first', exact: true }).check()
  await page.getByText('Saved. These options apply to the next Vision run.').waitFor()
  assert.equal(settings.amount_first, true)
  const folder = new URL('../../artifacts/vision-options/', import.meta.url)
  await fs.mkdir(folder, { recursive: true })
  for (const [name, width, height] of [['desktop', 1440, 1000], ['mobile', 390, 844]]) {
    await page.setViewportSize({ width, height })
    await page.locator('.vision-options').scrollIntoViewIfNeeded()
    await page.screenshot({ path: fileURLToPath(new URL(name + '-settings.png', folder)) })
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true)
  }
  await page.getByRole('button', { name: 'Close workspace settings' }).click()
  for (const [name, width, height] of [['desktop', 1440, 1000], ['mobile', 390, 844]]) {
    await page.setViewportSize({ width, height })
    await page.locator('.vision-row-overlay').scrollIntoViewIfNeeded()
    await page.screenshot({ path: fileURLToPath(new URL(name + '-overlay.png', folder)) })
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true)
  }
  await page.getByRole('checkbox', { name: 'Show AI line numbers', exact: true }).uncheck()
  assert.equal(await page.locator('.vision-row-overlay').count(), 0)
  assert.equal(settings.capture_regions, false)
  assert.deepEqual(errors, [])
  console.log('Google key/consent/provider settings, error recovery, overlay mapping, rotation, desktop/mobile controls passed.')
} catch (error) {
  console.error('Settings controls:', await page.locator('.settings-dialog select').evaluateAll(elements => elements.map(element => ({
    html: element.outerHTML, labels: Array.from(element.labels || [], label => label.textContent),
  }))))
  throw error
} finally { await browser.close(); await new Promise(resolve => server.close(resolve)) }
