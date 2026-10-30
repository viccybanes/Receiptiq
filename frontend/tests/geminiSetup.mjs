import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import http from 'node:http'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright'

// Serve only the compiled UI. Every API response is synthetic; no backend is contacted.
const dist = fileURLToPath(new URL('../dist/', import.meta.url))
const server = http.createServer(async (req, res) => {
  const name = new URL(req.url, 'http://localhost').pathname
  const file = path.join(dist, name === '/' ? 'index.html' : name)
  try {
    res.setHeader('Content-Type', file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html')
    res.end(await fs.readFile(file))
  } catch { res.statusCode = 404; res.end() }
})
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } })
const errors = []
const writes = []
const testKey = 'AQ.synthetic-onboarding-key-never-real'
let status = 'pending'
let settings = { amount_first: false, capture_regions: false, provider: 'local', google_model: 'gemini-3.8-flash',
  google_consent: false, google_key_saved: false }
let rejectTest = true
let rejectSave = false
let testCalls = 0
page.on('pageerror', error => errors.push(error.message))
page.on('console', message => { assert.ok(!message.text().includes(testKey), 'Key leaked to console') })
await page.route('**/api/v1/**', async route => {
  const pathname = new URL(route.request().url()).pathname.replace('/api/v1', '')
  const method = route.request().method()
  let body
  if (pathname === '/session') body = { token: 'synthetic-test' }
  else if (pathname === '/health') body = { ocr_models_ready: true }
  else if (['/templates', '/batches', '/receipts'].includes(pathname)) body = []
  else if (pathname === '/vision/google-setup') {
    if (method === 'POST') { status = route.request().postDataJSON().status; writes.push('marker:' + status) }
    body = { status }
  } else if (pathname === '/vision/settings') {
    if (method === 'PATCH') { settings = { ...settings, ...route.request().postDataJSON() }; writes.push('provider') }
    body = settings
  } else if (pathname === '/vision/google-key/test') {
    testCalls++
    const sent = route.request().postDataJSON()
    assert.deepEqual(sent, settings.google_key_saved ? {} : { api_key: testKey })
    await new Promise(resolve => setTimeout(resolve, 100))
    if (rejectTest) return route.fulfill({ status: 422, json: { detail: 'Google quota or rate limit reached. Check AI Studio quota/billing and retry later.' } })
    body = { ok: true, model: 'gemini-3.5-flash-lite' }
  } else if (pathname === '/vision/google-key') {
    assert.equal(method, 'PUT', 'Never fetch an API key')
    assert.equal(route.request().postDataJSON().api_key, testKey)
    if (rejectSave) return route.fulfill({ status: 422, json: { detail: 'Synthetic key save failure. Retry.' } })
    settings = { ...settings, google_key_saved: true }; writes.push('key'); body = settings
  } else if (pathname === '/system/capabilities') body = { cpu: 'Test CPU', ram_gb: 16, free_disk_gb: 100,
    nvidia: [], recommended_profile: '2b', profiles: { 'ocr-only': {}, '2b': {}, '4b': {} }, workspace: 'Synthetic', recommendation_note: 'Synthetic test' }
  else if (pathname === '/setup/status') body = { ocr_ready: true, runtime_installed: true, runtime_running: true,
    runtime_version: 'test', models: ['qwen3-vl:2b-instruct'], profile: '2b', job: {} }
  else { errors.push('Unexpected API request: ' + pathname); return route.fulfill({ status: 404, json: {} }) }
  await route.fulfill({ json: body })
})
const folder = new URL('../../artifacts/gemini-setup/', import.meta.url)
await fs.mkdir(folder, { recursive: true })
async function screenshots(state) {
  for (const [name, width, height] of [['desktop', 1440, 1000], ['mobile', 390, 844]]) {
    await page.setViewportSize({ width, height })
    await page.locator('.gemini-setup-body').evaluate(element => { element.scrollTop = 0 })
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true)
    assert.equal(await page.locator('.gemini-setup-body').evaluate(element => element.scrollWidth <= element.clientWidth), true)
    const box = await page.locator('.gemini-setup').boundingBox()
    assert.ok(box.y >= 0 && box.y + box.height <= height, 'Dialog must fit the viewport')
    await page.screenshot({ path: fileURLToPath(new URL(`${state}-${name}.png`, folder)) })
  }
  await page.setViewportSize({ width: 1440, height: 1000 })
}
async function reopen() {
  await page.getByRole('button', { name: 'Settings', exact: true }).click()
  await page.getByRole('button', { name: 'Gemini setup guide', exact: true }).click()
  await page.getByRole('heading', { name: 'Hello! Welcome to Receiptor.' }).waitFor()
  assert.equal(await page.locator('dialog[open]').count(), 1, 'Do not nest dialogs')
}
const enable = page.getByRole('button', { name: 'Save and enable Gemini', exact: true })
const keyInput = page.getByLabel('Google API key', { exact: true })
const consent = page.getByRole('checkbox', { name: /Allow my receipt images/ })
try {
  await page.goto('http://127.0.0.1:' + server.address().port)
  await page.getByRole('heading', { name: 'Hello! Welcome to Receiptor.' }).waitFor()
  assert.equal(await page.locator('.gemini-setup-steps li').count(), 4)
  assert.equal(await page.getByRole('link', { name: 'Google AI Studio', exact: true }).getAttribute('href'), 'https://aistudio.google.com/apikey')
  assert.equal(await page.getByRole('link', { name: 'Google’s API-key guide', exact: true }).getAttribute('rel'), 'noreferrer')
  assert.deepEqual(writes, [])
  assert.equal(testCalls, 0, 'No cloud call on welcome')
  await screenshots('guide')
  await page.getByRole('button', { name: 'I have an API key' }).click()
  assert.equal(await keyInput.getAttribute('type'), 'password')
  assert.equal(await enable.isDisabled(), true)
  assert.equal(await consent.isDisabled(), true)
  assert.equal(await page.getByRole('button', { name: 'Test API key', exact: true }).isDisabled(), true)
  await screenshots('key')
  await keyInput.fill(' ' + testKey + ' ')
  await page.getByRole('button', { name: 'Test API key', exact: true }).click()
  await page.getByRole('alert').filter({ hasText: 'quota' }).waitFor()
  assert.deepEqual(writes, [])
  assert.equal(settings.provider, 'local')
  assert.equal(await enable.isDisabled(), true)
  rejectTest = false
  await page.getByRole('button', { name: 'Test API key', exact: true }).click()
  await page.getByText('Connection successful. Gemini read the sample receipt.').waitFor()
  assert.equal(await enable.isDisabled(), true, 'Testing is not cloud consent')
  await consent.check()
  assert.equal(await enable.isEnabled(), true)
  await keyInput.fill(testKey + '-changed')
  assert.equal(await enable.isDisabled(), true, 'Changing the key invalidates its test')
  assert.equal(await consent.isChecked(), false)
  await keyInput.fill(testKey)
  await page.getByRole('button', { name: 'Test API key', exact: true }).click()
  await page.getByText('Connection successful. Gemini read the sample receipt.').waitFor()
  await consent.check()
  await screenshots('tested')
  assert.deepEqual(writes, [], 'A successful test alone must not save or enable anything')
  rejectSave = true
  await enable.click()
  await page.getByRole('alert').filter({ hasText: 'key save failure' }).waitFor()
  assert.equal(status, 'pending')
  assert.equal(settings.provider, 'local')
  rejectSave = false
  await enable.click()
  await page.getByRole('heading', { name: 'Gemini is ready', exact: true }).waitFor()
  assert.deepEqual(writes, ['key', 'provider', 'marker:completed'])
  assert.equal(settings.provider, 'google')
  assert.equal(settings.google_consent, true)
  assert.equal(settings.google_model, 'gemini-3.5-flash-lite')
  await page.getByRole('button', { name: 'Start reviewing' }).click()
  await page.reload()
  await page.getByRole('heading', { name: 'Add expense' }).waitFor()
  assert.equal(await page.locator('.gemini-setup').count(), 0)
  await reopen()
  await page.getByRole('button', { name: 'I have an API key' }).click()
  assert.equal(await keyInput.inputValue(), '', 'Saved keys are never read back')
  assert.equal(await page.getByRole('button', { name: 'Test API key', exact: true }).isEnabled(), true)
  await page.getByRole('button', { name: 'Test API key', exact: true }).click()
  await page.getByText('Connection successful. Gemini read the sample receipt.').waitFor()
  await page.keyboard.press('Escape')
  await page.locator('.gemini-setup').waitFor({ state: 'detached' })
  assert.equal(settings.provider, 'google', 'Dismissing the guide preserves current provider')

  // A fresh local workspace can skip once, continue offline, and replay from Settings.
  status = 'pending'; settings = { ...settings, provider: 'local', google_consent: false, google_key_saved: false }
  await page.reload()
  await page.getByRole('button', { name: 'Skip for now', exact: true }).click()
  await page.locator('.gemini-setup').waitFor({ state: 'detached' })
  assert.equal(status, 'skipped')
  assert.equal(settings.provider, 'local')
  await page.reload()
  await page.getByRole('heading', { name: 'Add expense' }).waitFor()
  assert.equal(await page.locator('.gemini-setup').count(), 0)
  await reopen()
  await page.getByRole('button', { name: 'Close Gemini setup', exact: true }).click()
  await page.locator('.gemini-setup').waitFor({ state: 'detached' })
  assert.equal(await page.evaluate(key => JSON.stringify([localStorage, sessionStorage]).includes(key), testKey), false)
  assert.deepEqual(errors, [])
  console.log('Gemini onboarding passed: first run, steps, synthetic test, failure/retry, consent, changed-key reset, save failure, completion, skip, replay, saved key, privacy, desktop/mobile layout.')
} finally { await browser.close(); await new Promise(resolve => server.close(resolve)) }
