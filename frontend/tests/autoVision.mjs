import assert from 'node:assert/strict'
import path from 'node:path'
import { chromium } from 'playwright'
const browser=await chromium.launch(), page=await browser.newPage()
let id
try {
  await page.goto('http://127.0.0.1:5173')
  await page.getByRole('heading',{name:'Add expense',exact:true}).waitFor()
  const uploaded=page.waitForResponse(r=>r.request().method()==='POST' && new URL(r.url()).pathname==='/api/v1/receipts')
  await page.getByLabel('Choose receipt images').setInputFiles(path.resolve('../artifacts/samples/001-printed.png'))
  id=(await (await uploaded).json()).id
  await page.locator(`[data-receipt-id="${id}"]`).click()
  await page.getByText('Vision applied automatically. Check the fields before verifying.',{exact:true}).waitFor({timeout:180000})
  assert.ok(await page.locator('[data-field="supplier"]').inputValue())
  assert.equal(await page.locator('.image-stage').evaluate(element=>getComputedStyle(element).scrollbarWidth),'none')
  assert.equal(await page.getByRole('button',{name:'Apply vision suggestion',exact:true}).isDisabled(),true)
  await page.reload()
  await page.locator(`[data-receipt-id="${id}"]`).click()
  await page.getByText('Vision applied automatically. Check the fields before verifying.',{exact:true}).waitFor()
  assert.ok(await page.locator('[data-field="supplier"]').inputValue())
  console.log('PASS: real local vision runs and applies after upload, survives reload, hidden image scrollbar')
} finally {
  if(id) await page.evaluate(async id=>{
    const {token}=await (await fetch('/api/v1/session')).json()
    await fetch('/api/v1/receipts/'+id,{method:'DELETE',headers:{Authorization:'Bearer '+token}})
  },id).catch(()=>{})
  await browser.close()
}
