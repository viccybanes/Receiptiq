/** Milestone browser regression. Real workspace APIs; explicitly marked fixtures for rare AI transport states. */
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import path from 'node:path'
import { chromium } from 'playwright'

const root = path.resolve('..')
const baseURL = process.env.RECEIPTIQ_TEST_URL || 'http://127.0.0.1:8766'
const artifacts = path.join(root, 'artifacts', 'milestone-browser')
await fs.mkdir(artifacts, { recursive: true })
const reviewCaptures = path.join(root, '.impeccable', 'review')
await fs.mkdir(reviewCaptures, { recursive: true })
const browser = await chromium.launch({ headless: true })
const context = await browser.newContext({ baseURL, viewport: { width: 1440, height: 1080 }, acceptDownloads: true })
const page = await context.newPage()
page.setDefaultTimeout(30000)
const exceptions = [], consoleErrors = [], expectedConsoleErrors = [], checks = [], mocked = []
let realVisionUI = null
page.on('pageerror', error => exceptions.push(error.message))
page.on('console', message => {
  if (message.type() !== 'error') return
  const entry = { text: message.text(), url: message.location().url }
  if (/Failed to load resource/.test(entry.text) && /\/backups\/restore|\/vision$/.test(entry.url)) expectedConsoleErrors.push(entry)
  else consoleErrors.push(entry)
})
const token = (await (await context.request.get(baseURL + '/api/v1/session')).json()).token
async function api(route, options = {}) {
  const response = await context.request.fetch(baseURL + '/api/v1' + route, {
    ...options, headers: { Authorization: 'Bearer ' + token, ...options.headers },
  })
  assert.equal(response.ok(), true, `${options.method || 'GET'} ${route}: ${await response.text()}`)
  return response.json()
}
async function until(label, query, timeout = 45000) {
  const deadline = Date.now() + timeout
  while (Date.now() < deadline) {
    const value = await query()
    if (value) return value
    await new Promise(resolve => setTimeout(resolve, 200))
  }
  throw new Error('Timed out: ' + label)
}
async function capture(name) {
  await page.screenshot({ path: path.join(artifacts, name + '.png'), fullPage: true })
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true, name + ': page overflow')
  const dialog = page.locator('.settings-dialog[open]')
  if (await dialog.count()) assert.equal(await dialog.evaluate(element => element.scrollWidth <= element.clientWidth + 1), true, name + ': settings overflow')
}
async function checkProfileControls() {
  const metrics = await page.locator('.settings-profile-list').evaluate(fieldset => {
    const bounds = fieldset.getBoundingClientRect()
    return { fits: fieldset.scrollWidth <= fieldset.clientWidth + 1,
      controls: [...fieldset.querySelectorAll('input[type=radio]')].map(input => {
        const radio = input.getBoundingClientRect()
        const label = input.closest('label').getBoundingClientRect()
        return { width: radio.width, height: radio.height,
          labelInside: label.left >= bounds.left - 1 && label.right <= bounds.right + 1 &&
            label.top >= bounds.top - 1 && label.bottom <= bounds.bottom + 1 }
      }) }
  })
  assert.equal(metrics.controls.length, 3)
  assert.equal(metrics.fits, true, 'Processing profiles overflow their fieldset')
  for (const control of metrics.controls) {
    assert.ok(control.width > 0 && control.width <= 24 && control.height > 0 && control.height <= 24,
      'Profile radio must retain its compact native size: ' + JSON.stringify(control))
    assert.equal(control.labelInside, true, 'Profile label extends beyond its fieldset')
  }
}
async function openReceipt(batchId, id) {
  await page.locator('#status-filter').selectOption('all').catch(() => {})
  await page.locator('#assignee-filter').selectOption('').catch(() => {})
  await page.locator('#batch').selectOption('')
  await page.locator(`[data-receipt-id="${id}"]`).click()
  await page.getByRole('textbox', { name: 'Vendor', exact: true }).waitFor()
}
const fields = ['Receipt ID', 'Purchase date', 'Vendor', 'Receipt number', 'Receipt photo', 'Notes', 'Item number',
  'Description', 'Quantity', 'Unit', 'Unit price', 'Amount', 'Type', 'Photo of item purchased', 'Total']
const runId = Date.now().toString(36)
async function importFixture(name, vendor, prices = ['50.00', '25.00'], total = '125.00') {
  const photoName = `milestone-${runId}-${name}.png`
  const entries = [
    [name, '2026-10-19', vendor, '00017 / 00023', photoName, 'Browser milestone fixture', '',
      name === 'target' ? 'Coffee scan' : 'Coffee', '2', 'cup', prices[0], String(Number(prices[0]) * 2) + '.00', 'Staff meals', '', total],
    [name, '2026-10-19', vendor, '00017 / 00023', photoName, 'Browser milestone fixture', '',
      'Bread', '1', 'loaf', prices[1], prices[1], 'Materials', '', total],
  ]
  const quote = value => '"' + value.replaceAll('"', '""') + '"'
  const csv = [fields, ...entries].map(row => row.map(quote).join(',')).join('\r\n')
  const pending = page.waitForResponse(response => new URL(response.url()).pathname === '/api/v1/imports' && response.request().method() === 'POST')
  await page.getByLabel('Choose CSV or ZIP and optional photos').setInputFiles([
    { name: `milestone-${runId}-${name}.csv`, mimeType: 'text/csv', buffer: Buffer.from('\ufeff' + csv) },
    { name: photoName, mimeType: 'image/png', buffer: await fs.readFile(path.join(root, 'artifacts/samples/001-printed.png')) },
  ])
  const response = await pending
  assert.equal(response.ok(), true, await response.text())
  const batch = await response.json()
  assert.equal(batch.receipt_count, 1)
  assert.deepEqual(batch.warnings, [])
  const receipt = await until('import preview', async () => {
    const rows = await api('/receipts?batch_id=' + batch.id)
    if (rows.length !== 1 || ['queued', 'processing'].includes(rows[0].status)) return null
    const value = await api('/receipts/' + rows[0].id)
    return value.pages.length ? value : null
  })
  assert.equal(receipt.original_mime, 'image/png')
  assert.equal(receipt.data.items.length, 2)
  assert.ok(receipt.data.items.every(item => item.item_number > 0))
  await openReceipt(batch.id, receipt.id)
  return { batchId: batch.id, receipt }
}

try {
  await page.goto(baseURL)
  await page.getByRole('heading', { name: 'Add expense', exact: true }).waitFor()
  await until('session ready', () => page.getByRole('button', { name: 'Settings', exact: true }).isEnabled())

  // Settings and CSV definitions use the actual API, including optimistic version changes.
  await page.getByRole('button', { name: 'Settings', exact: true }).click()
  const settings = page.getByRole('dialog', { name: 'Workspace settings', exact: true })
  await settings.waitFor()
  await until('hardware probe', () => settings.getByLabel('Data workspace folder').inputValue())
  assert.equal(await settings.getByRole('link', { name: 'Gemma terms' }).getAttribute('href'), 'https://ai.google.dev/gemma/terms')
  assert.equal(await settings.getByRole('button', { name: 'Stage workspace move', exact: true }).isDisabled(), true)
  await checkProfileControls()
  await capture('settings-models-desktop')
  await page.setViewportSize({ width: 390, height: 844 })
  await checkProfileControls()
  await settings.locator('.settings-profile-list').scrollIntoViewIfNeeded()
  await capture('settings-models-mobile')
  await page.setViewportSize({ width: 1440, height: 1080 })
  await settings.getByRole('tab', { name: 'CSV formats', exact: true }).click()
  await settings.getByLabel('Format name', { exact: true }).waitFor()
  assert.equal(await settings.locator('.template-column').count(), 15)
  const formatName = `Milestone format ${runId}`
  await settings.getByLabel('Format name', { exact: true }).fill(formatName)
  await settings.getByLabel('Column 3 heading', { exact: true }).fill('Store')
  await settings.getByLabel(/^Column separator/).selectOption(';')
  await settings.getByLabel(/^Purchase date format/).selectOption('DDMMYYYY')
  await settings.getByLabel(/^Decimal separator/).selectOption(',')
  await settings.locator('.settings-required').getByRole('checkbox', { name: 'Vendor', exact: true }).check()
  await settings.getByRole('button', { name: 'Add column', exact: true }).click()
  await settings.getByLabel('Column 16 heading', { exact: true }).fill('Department')
  await settings.getByLabel('Column 16 source', { exact: true }).selectOption('__literal')
  await settings.locator('.template-literal input').fill('Office')
  await settings.getByRole('button', { name: 'Add column', exact: true }).click()
  await settings.getByRole('button', { name: 'Remove column 17', exact: true }).click()
  await settings.getByRole('button', { name: 'Move column 16 up', exact: true }).click()
  const creating = page.waitForResponse(response => new URL(response.url()).pathname === '/api/v1/templates' && response.request().method() === 'POST')
  await settings.getByRole('button', { name: 'Save a copy', exact: true }).click()
  const created = await (await creating).json()
  assert.notEqual(created.id, 'default')
  assert.equal(created.version, 1)
  assert.deepEqual(created.columns[14], { header: 'Department', constant: 'Office' })
  await until('copy saved', async () => (await settings.getByLabel(/^Saved format/).inputValue()) === created.id)
  await settings.getByLabel('Column 3 heading', { exact: true }).fill('Vendor name')
  await settings.locator('.template-literal input').fill('Operations')
  const editing = page.waitForResponse(response => new URL(response.url()).pathname === '/api/v1/templates/' + created.id && response.request().method() === 'PATCH')
  await settings.getByRole('button', { name: 'Save changes', exact: true }).click()
  const edited = await (await editing).json()
  assert.equal(edited.version, 2)
  assert.deepEqual(edited.required_fields, ['Vendor'])
  assert.deepEqual((await api('/templates/default')).columns.map(column => column.header), fields)
  await settings.locator('.settings-dialog-content').evaluate(element => { element.scrollTop = 0 })
  await capture('settings-formats-desktop')
  await fs.copyFile(path.join(artifacts, 'settings-formats-desktop.png'), path.join(reviewCaptures, 'milestone-settings-desktop.png'))
  await page.setViewportSize({ width: 390, height: 844 })
  await capture('settings-formats-mobile')
  await fs.copyFile(path.join(artifacts, 'settings-formats-mobile.png'), path.join(reviewCaptures, 'milestone-settings-mobile.png'))
  await page.setViewportSize({ width: 1440, height: 1080 })
  await settings.getByRole('button', { name: 'Done', exact: true }).click()
  checks.push('Settings hardware/terms; CSV copy/edit/version, mapping rename, literal, add/remove/reorder, required/format options; default immutable')

  const source = await importFixture('source', 'Milestone Market')
  assert.equal(await page.getByRole('textbox', { name: 'Tax', exact: true }).count(), 0)
  assert.equal(await page.getByLabel('Item 1 Type', { exact: true }).inputValue(), 'Staff meals')
  await page.locator('.assistance-assignment > summary').click()
  await page.getByLabel('Assigned reviewer', { exact: true }).fill('Second reviewer')
  await page.getByLabel('Needs a second review', { exact: true }).check()
  await page.getByRole('button', { name: 'Save assignment', exact: true }).click()
  await until('second review assigned', async () => {
    const value = await api('/receipts/' + source.receipt.id)
    return value.assignee === 'Second reviewer' && value.second_review && value.status === 'needs_review'
  })
  await until('reviewer filter updated', async () => (await page.locator('#assignee-filter option').allTextContents()).includes('Second reviewer'))
  await page.locator('#assignee-filter').selectOption('Second reviewer')
  await page.locator('#status-filter').selectOption('second_review')
  assert.equal(await page.locator('.receipt-select').count(), 1)
  await page.locator('#status-filter').selectOption('all')
  await page.locator('#assignee-filter').selectOption('')
  await page.getByLabel('Needs a second review', { exact: true }).uncheck()
  await page.getByRole('button', { name: 'Save assignment', exact: true }).click()
  await until('second review cleared', async () => !(await api('/receipts/' + source.receipt.id)).second_review)
  await page.getByRole('textbox', { name: 'Reviewed by', exact: true }).fill('Milestone QA')
  await page.getByRole('checkbox', { name: 'Use this verified receipt and its corrections for future local suggestions.' }).check()
  await page.getByRole('button', { name: 'Verify receipt', exact: true }).click()
  source.receipt = await until('verified opt-in', async () => {
    const value = await api('/receipts/' + source.receipt.id)
    return value.status === 'verified' && value.retrieval_allowed ? value : null
  })
  assert.equal(source.receipt.data.items[0].item_type, 'Staff meals')
  checks.push('Real CSV plus referenced photo import; stable item numbers, custom Type; assignment/second-review/filter; explicit verification and retrieval opt-in')

  // A saved template is actually used for preview and downloaded bytes.
  await until('CSV picker updated', async () => (await page.getByLabel('CSV format', { exact: true }).locator('option').allTextContents()).includes(formatName))
  await page.getByLabel('CSV format', { exact: true }).selectOption(created.id)
  await page.getByRole('button', { name: 'Select all verified', exact: true }).click()
  await page.getByRole('button', { name: 'Preview CSV', exact: true }).click()
  const preview = page.getByRole('dialog', { name: 'Check your CSV', exact: true })
  await preview.waitFor()
  assert.equal(await preview.locator('th').nth(2).innerText(), 'Vendor name')
  assert.match(await preview.locator('tbody').innerText(), /19\/10\/2026/)
  assert.match(await preview.locator('tbody').innerText(), /50,00/)
  assert.match(await preview.locator('tbody').innerText(), /Operations/)
  const downloading = page.waitForEvent('download')
  await preview.getByRole('button', { name: 'Download CSV', exact: true }).click()
  const csvFile = path.join(artifacts, 'mapped-export.csv')
  await (await downloading).saveAs(csvFile)
  const exported = await fs.readFile(csvFile, 'utf8')
  assert.match(exported, /Vendor name;/)
  assert.match(exported, /19\/10\/2026;Milestone Market;/)
  assert.match(exported, /"50,00"|;50,00;/)
  assert.match(exported, /Operations/)
  checks.push('Real selected-format preview and CSV download: renamed heading, semicolon, DD/MM/YYYY, decimal comma, literal text')

  // Backup creation/download is real; a corrupt upload must be an actionable in-modal failure.
  await page.getByRole('button', { name: 'Settings', exact: true }).click()
  await settings.getByRole('tab', { name: 'Backups', exact: true }).click()
  const making = page.waitForResponse(response => new URL(response.url()).pathname === '/api/v1/backups' && response.request().method() === 'POST')
  await settings.getByRole('button', { name: 'Create backup', exact: true }).click()
  const backupResult = await making
  assert.equal(backupResult.ok(), true, await backupResult.text())
  const backup = await backupResult.json()
  await until('backup listed', () => settings.getByRole('button', { name: 'Download ' + backup.name, exact: true }).count())
  const backupDownload = page.waitForEvent('download')
  await settings.getByRole('button', { name: 'Download ' + backup.name, exact: true }).click()
  const backupPath = path.join(artifacts, 'workspace-backup.zip')
  await (await backupDownload).saveAs(backupPath)
  assert.equal((await fs.readFile(backupPath)).subarray(0, 2).toString(), 'PK')
  await settings.getByLabel('ReceiptIQ backup ZIP (up to 2 GB)', { exact: true }).setInputFiles({
    name: 'corrupt-backup.zip', mimeType: 'application/zip', buffer: Buffer.from('This is not a ZIP backup.'),
  })
  assert.equal(await settings.getByRole('button', { name: 'Validate and stage restore', exact: true }).isDisabled(), true)
  await settings.getByRole('checkbox', { name: 'I understand this backup will replace the workspace when ReceiptIQ restarts.' }).check()
  const restoring = page.waitForResponse(response => new URL(response.url()).pathname === '/api/v1/backups/restore' && response.request().method() === 'POST')
  await settings.getByRole('button', { name: 'Validate and stage restore', exact: true }).click()
  const restoreResponse = await restoring
  assert.equal(restoreResponse.status(), 422, await restoreResponse.text())
  await settings.getByRole('alert').waitFor()
  assert.match(await settings.getByRole('alert').innerText(), /backup|ZIP|archive/i)
  assert.equal(await settings.evaluate(element => element.open), true)
  assert.equal((await api('/receipts/' + source.receipt.id)).status, 'verified')
  await capture('backup-error-desktop')
  await page.setViewportSize({ width: 390, height: 844 })
  await capture('backup-error-mobile')
  await page.setViewportSize({ width: 1440, height: 1080 })
  await settings.getByRole('button', { name: 'Done', exact: true }).click()
  checks.push('Real workspace backup create/download; corrupt ZIP rejected422 with error inside open modal and existing workspace preserved')

  const target = await importFixture('target', 'Milestone Target', ['45.00', '20.00'], '110.00')
  const targetId = target.receipt.id
  if (process.env.RECEIPTIQ_TEST_REAL_VISION === '1') {
    const before = await api('/receipts/' + targetId)
    await page.getByRole('button', { name: 'Use vision', exact: true }).click()
    const actual = await until('real local vision candidate', async () => {
      const value = await api('/receipts/' + targetId)
      if (value.vision_job?.state === 'failed') throw new Error('Real local vision failed: ' + value.vision_job.error)
      return value.candidate ? value : null
    }, 180000)
    assert.deepEqual(actual.data, before.data, 'Actual vision must preserve the human/imported expense data')
    assert.equal(actual.candidate.model, 'qwen3-vl:2b-instruct')
    assert.ok(actual.candidate.draft.items.length > 0)
    await page.getByRole('button', { name: 'Apply vision suggestion', exact: true }).waitFor()
    assert.equal(await page.getByText(/Vision queued\./).count(), 0, 'A completed candidate must not retain a queued notice')
    const totalWidth = await page.locator('.total-field input').evaluate(input => input.getBoundingClientRect().width)
    assert.ok(totalWidth > 0 && totalWidth <= 110, `Desktop Total must retain its compact width; got ${totalWidth}px`)
    realVisionUI = { receiptId: targetId, model: actual.candidate.model, seconds: actual.candidate.seconds,
      preservedHumanData: true, itemCount: actual.candidate.draft.items.length }
    await capture('real-vision-desktop')
    await fs.copyFile(path.join(artifacts, 'real-vision-desktop.png'), path.join(reviewCaptures, 'milestone-desktop.png'))
    await page.setViewportSize({ width: 390, height: 844 })
    await capture('real-vision-mobile')
    await fs.copyFile(path.join(artifacts, 'real-vision-mobile.png'), path.join(reviewCaptures, 'milestone-mobile.png'))
    await page.setViewportSize({ width: 1440, height: 1080 })
    checks.push('Actual selected-page Qwen3-VL2B UI request/poll/candidate with imported human data preserved; no automatic acceptance')
  }
  const targetRoute = '**/api/v1/receipts/' + targetId
  let candidate = null, job = null, requestMode = 'candidate', visionRequests = 0, lastVision = null
  // These fixtures isolate rare UI states. Receipt saves and canonical adoption still use the real backend.
  await page.route(targetRoute, async route => {
    if (route.request().method() !== 'GET') return route.continue()
    const response = await route.fetch()
    const value = await response.json()
    await route.fulfill({ response, json: { ...value, candidate, vision_job: job } })
  })
  await page.route(targetRoute + '/vision', async route => {
    visionRequests++
    lastVision = route.request().postDataJSON()
    if (requestMode === 'failure') return route.fulfill({ status: 503, json: { detail: 'Local vision is unavailable. Manual entry remains available.' } })
    if (requestMode === 'job') {
      candidate = null
      job = { id: 'fixture-job', state: 'processing', stage: 'Fixture local vision processing', error: null, progress: 25 }
    } else {
      const current = await api('/receipts/' + targetId)
      candidate = { id: 'fixture-candidate', base_revision: lastVision.revision, model: lastVision.model, seconds: 1.5,
        notice: 'Synthetic transport fixture. Human verification required.', provenance: { source: 'browser_fixture' },
        draft: { ...current.data, supplier: 'Vision alternate Vendor', items: current.data.items.map((item, index) =>
          index ? item : { ...item, description: 'Vision alternate Coffee' }) } }
      job = null
    }
    await route.fulfill({ status: 202, json: { id: job?.id || 'fixture-job', accepted: true } })
  })
  await page.route(targetRoute + '/candidates/fixture-candidate/apply', async route => {
    assert.equal(route.request().postDataJSON().revision, candidate.base_revision)
    const value = await api('/receipts/' + targetId, { method: 'PATCH', data: {
      revision: candidate.base_revision, data: candidate.draft,
    } })
    candidate = null
    await route.fulfill({ json: value })
  })
  await page.route('**/api/v1/jobs/fixture-job/cancel', async route => {
    job = { ...job, state: 'cancelled', stage: 'Cancelled', progress: 25 }
    await route.fulfill({ json: job })
  })
  const example = { receipt_id: source.receipt.id, revision: source.receipt.revision, vendor: source.receipt.data.supplier,
    items: source.receipt.data.items.map(({ id, description, unit, item_type }) => ({ id, description, unit, item_type })),
    corrections: [{ field: 'supplier', before: 'Milestone Mkt', after: source.receipt.data.supplier }],
    citation: `receipt:${source.receipt.id}@${source.receipt.revision}`, score: 0.987 }
  await page.route(targetRoute + '/suggestions', route => route.fulfill({ json: { examples: [example], notice: 'Synthetic ranking transport fixture; source receipt is real and opted in.' } }))
  mocked.push('Vision candidate/job/cancel/apply transport (canonical PATCH remains real); similarity ranking transport from a real verified opted-in source')

  assert.equal(visionRequests, 0, 'Vision must never run automatically')
  await page.locator('.assistance-examples > summary').click()
  await page.getByRole('button', { name: 'Find similar saved receipts', exact: true }).click()
  await page.getByText(example.citation, { exact: false }).waitFor()
  const originalMoney = target.receipt.data.items.map(({ quantity, unit_price, line_total }) => ({ quantity, unit_price, line_total }))
  await page.getByRole('button', { name: 'Use Vendor', exact: true }).click()
  await page.getByRole('button', { name: 'Use item wording', exact: true }).click()
  await page.getByRole('button', { name: 'Save now', exact: true }).click()
  await until('wording saved', async () => {
    const value = await api('/receipts/' + targetId)
    return value.data.supplier === 'Milestone Market' && value.data.items[0].description === 'Coffee' ? value : null
  })
  const reused = await api('/receipts/' + targetId)
  assert.deepEqual(reused.data.items.map(({ quantity, unit_price, line_total }) => ({ quantity, unit_price, line_total })), originalMoney)
  await page.getByRole('button', { name: 'Open source receipt', exact: true }).click()
  await page.locator('.review-state').filter({ hasText: 'Verified by a person' }).waitFor()
  await page.locator(`[data-receipt-id="${targetId}"]`).click()
  await page.locator('.review-state').filter({ hasText: 'Ready for your review' }).waitFor()
  await page.getByRole('button', { name: 'Use vision', exact: true }).click()
  await page.getByRole('button', { name: 'Apply vision suggestion', exact: true }).waitFor()
  assert.equal(await page.getByRole('textbox', { name: 'Vendor', exact: true }).inputValue(), 'Milestone Market')
  assert.equal((await api('/receipts/' + targetId)).data.supplier, 'Milestone Market')
  assert.equal(lastVision.page_id, target.receipt.pages[0].id)
  assert.equal(lastVision.crop, null)
  await capture('assistance-desktop')
  await page.setViewportSize({ width: 390, height: 844 })
  await capture('assistance-mobile')
  await page.setViewportSize({ width: 1440, height: 1080 })

  let current = await api('/receipts/' + targetId)
  await api('/receipts/' + targetId, { method: 'PATCH', data: { revision: current.revision, data: { ...current.data, notes: 'Intervening human edit' } } })
  await page.reload()
  await page.getByRole('heading', { name: 'Add expense', exact: true }).waitFor()
  await until('reloaded session', () => page.getByRole('button', { name: 'Settings', exact: true }).isEnabled())
  await openReceipt(target.batchId, targetId)
  await page.locator('.assistance-stale').waitFor()
  assert.equal(await page.getByRole('button', { name: 'Apply vision suggestion', exact: true }).isDisabled(), true)
  await page.getByRole('button', { name: 'Use vision', exact: true }).click()
  await until('fresh candidate', () => page.getByRole('button', { name: 'Apply vision suggestion', exact: true }).isEnabled())
  await page.getByRole('button', { name: 'Apply vision suggestion', exact: true }).click()
  await until('candidate adopted', async () => (await page.getByRole('textbox', { name: 'Vendor', exact: true }).inputValue()) === 'Vision alternate Vendor')
  current = await api('/receipts/' + targetId)
  assert.equal(current.status, 'needs_review')
  assert.equal(current.verified_revision, null)
  assert.equal(current.data.items[0].item_type, 'Staff meals')
  assert.deepEqual(current.data.items.map(({ quantity, unit_price, line_total }) => ({ quantity, unit_price, line_total })), originalMoney)

  assert.equal(await page.getByRole('button', { name: 'Read this region again', exact: true }).isDisabled(), true)
  await page.getByRole('button', { name: 'Select region', exact: true }).click()
  const overlay = page.getByRole('img', { name: 'Drag to select receipt region', exact: true })
  await overlay.scrollIntoViewIfNeeded()
  const box = await overlay.boundingBox()
  assert.ok(box && box.width > 20 && box.height > 20)
  await page.mouse.move(box.x + box.width * 0.2, box.y + box.height * 0.2)
  await page.mouse.down()
  await page.mouse.move(box.x + box.width * 0.7, box.y + box.height * 0.7, { steps: 5 })
  await page.mouse.up()
  requestMode = 'job'
  await page.getByRole('button', { name: 'Read this region again', exact: true }).click()
  await page.getByRole('button', { name: 'Cancel vision', exact: true }).waitFor()
  assert.ok(lastVision.crop.every(value => value >= 0 && value <= 1))
  assert.ok(lastVision.crop[2] > lastVision.crop[0] && lastVision.crop[3] > lastVision.crop[1])
  await page.getByRole('button', { name: 'Cancel vision', exact: true }).click()
  await page.getByText('Vision cancelled. Your current expense values are preserved.', { exact: true }).waitFor()
  requestMode = 'failure'
  await page.getByRole('button', { name: 'Use vision', exact: true }).click()
  await page.locator('.receipt-assistance [role=alert]').waitFor()
  assert.match(await page.locator('.receipt-assistance [role=alert]').innerText(), /Manual entry remains available/)
  assert.equal(await page.getByRole('textbox', { name: 'Vendor', exact: true }).isEnabled(), true)
  checks.push('Assistance UI fixtures: explicit page/crop requests, no automatic vision, current data preserved before acceptance, stale apply disabled, canonical explicit apply, cancel/error/manual entry, cited wording reuse preserves all money and custom metadata')

  assert.deepEqual(exceptions, [], 'Uncaught browser exceptions')
  assert.deepEqual(consoleErrors, [], 'Unexpected browser console errors')
  const evaluation = JSON.parse(await fs.readFile(path.join(root, 'artifacts/vision-evaluation-final-ocr.json'), 'utf8'))
  const report = { baseURL, checks, mocked, realVisionUI, exceptions, consoleErrors, expectedConsoleErrors,
    realVisionEvidence: { artifact: 'artifacts/vision-evaluation-final-ocr.json',
      kind: evaluation.kind, headerMatches: evaluation.records.reduce((sum, row) => sum + Object.values(row.field_matches).filter(Boolean).length, 0),
      headerChecks: evaluation.records.reduce((sum, row) => sum + Object.keys(row.field_matches).length, 0),
      itemCountMatches: evaluation.records.filter(row => row.item_count_matches).length },
    screenshots: ['settings-models-desktop', 'settings-models-mobile', 'settings-formats-desktop', 'settings-formats-mobile', 'backup-error-desktop',
      'backup-error-mobile', 'assistance-desktop', 'assistance-mobile'] }
  await fs.writeFile(path.join(artifacts, 'report.json'), JSON.stringify(report, null, 2))
  console.log('Milestone browser checks passed: ' + checks.length + ' groups; real backend flows, labeled AI transport fixtures, desktop/mobile overflow, no unexpected console errors.')
} catch (error) {
  await page.screenshot({ path: path.join(artifacts, 'failure.png'), fullPage: true }).catch(() => {})
  await fs.writeFile(path.join(artifacts, 'failure.json'), JSON.stringify({ error: error.stack, exceptions, consoleErrors,
    expectedConsoleErrors, checks, mocked }, null, 2))
  throw error
} finally {
  await browser.close()
}
