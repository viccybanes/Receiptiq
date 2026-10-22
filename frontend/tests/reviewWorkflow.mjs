import assert from 'node:assert/strict'
import path from 'node:path'
import { chromium } from 'playwright'
const browser = await chromium.launch(), page = await browser.newPage(), ids = []
try {
  await page.goto('http://127.0.0.1:5173')
  await page.getByRole('heading', {name:'Add expense',exact:true}).waitFor()
  async function upload() {
    const response = page.waitForResponse(r => r.request().method()==='POST' && new URL(r.url()).pathname==='/api/v1/receipts')
    await page.getByLabel('Choose receipt images').setInputFiles(path.resolve('../artifacts/samples/001-printed.png'))
    const id=(await (await response).json()).id; ids.push(id); return id
  }
  const first=await upload()
  await page.locator(`[data-receipt-id="${first}"]`).click()
  await page.getByRole('button',{name:'Run OCR',exact:true}).waitFor()
  await page.waitForFunction(()=>![...document.querySelectorAll('button')].find(b=>b.textContent==='Run OCR')?.disabled)
  assert.equal(await page.getByRole('button',{name:'Replace photo',exact:true}).count(),0)
  const date=page.locator('[data-field="receipt_date"]')
  await date.fill('08/27/2026')
  await page.getByRole('button',{name:'Add line',exact:true}).click()
  await page.locator('[data-field="items.0.quantity"]').fill('3')
  await page.locator('[data-field="items.0.unit_price"]').fill('25')
  assert.equal(await page.locator('[data-field="items.0.line_total"]').inputValue(),'75.00')
  assert.equal(await page.locator('[data-field="total"]').inputValue(),'75.00')
  const fields = page.locator('.fields-panel')
  const stage = page.locator('.image-stage')
  await fields.scrollIntoViewIfNeeded()
  const before = await stage.boundingBox()
  await fields.evaluate(panel => { panel.scrollTop = 200 })
  assert.ok(await fields.evaluate(panel => panel.scrollTop > 0), 'Details must scroll independently')
  assert.equal((await stage.boundingBox()).y, before.y, 'Details scrolling must leave preview in place')
  for (let i = 0; i < 6; i++) await page.getByRole('button', {name:'Zoom in',exact:true}).click()
  await stage.scrollIntoViewIfNeeded()
  const view = await stage.boundingBox()
  await page.mouse.move(view.x + view.width * .7, view.y + view.height * .7)
  await page.mouse.down()
  await page.mouse.move(view.x + view.width * .4, view.y + view.height * .4, {steps: 5})
  await page.mouse.up()
  assert.ok(await stage.evaluate(element => element.scrollTop > 0 && element.scrollLeft > 0), 'Dragging must pan both axes')
  await page.getByRole('button', {name:'Reset view',exact:true}).click()
  assert.deepEqual(await stage.evaluate(element => [element.scrollLeft, element.scrollTop]), [0,0])
  await fields.evaluate(panel => { panel.scrollTop = 0 })
  console.log('PASS: independent details scrolling, two-axis drag panning, reset view')
  let release; const gate=new Promise(resolve=>{release=resolve})
  await page.route('**/api/v1/receipts?*',async route=>{if(route.request().method()==='POST') await gate; await route.continue()})
  const second=upload()
  await page.getByText('Uploading...', {exact:true}).waitFor()
  assert.equal(await date.inputValue(),'08/27/2026')
  assert.equal(await page.locator(`.queue-row.active [data-receipt-id="${first}"]`).count(),1)
  release(); await second
  await page.waitForTimeout(1200)
  await page.reload()
  await page.locator(`[data-receipt-id="${first}"]`).click()
  assert.equal(await date.inputValue(),'08/27/2026')
  const lines=await page.evaluate(async id=>{
    const {token}=await (await fetch('/api/v1/session')).json()
    return (await (await fetch('/api/v1/receipts/'+id,{headers:{Authorization:'Bearer '+token}})).json()).evidence.lines
  },first)
  assert.deepEqual(lines,[])
  console.log('PASS: date, live arithmetic, no auto-OCR, upload placeholder, retained selection, reload')
} finally {
  await page.evaluate(async ids=>{
    const {token}=await (await fetch('/api/v1/session')).json()
    for(const id of ids) await fetch('/api/v1/receipts/'+id,{method:'DELETE',headers:{Authorization:'Bearer '+token}})
  },ids).catch(()=>{})
  await browser.close()
}
