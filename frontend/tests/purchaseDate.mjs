import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import ts from 'typescript'

const source = await fs.readFile(new URL('../src/purchaseDate.ts', import.meta.url), 'utf8')
const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ES2020 } })
const { displayDate, inputDate } = await import('data:text/javascript;base64,' + Buffer.from(compiled.outputText).toString('base64'))
assert.equal(displayDate('2026-08-27'), '08/27/26')
assert.equal(inputDate('08/27/26'), '2026-08-27')
assert.equal(inputDate('02/29/24'), '2024-02-29')
assert.equal(inputDate('02/29/25'), '02/29/25')
assert.equal(inputDate('13/01/26'), '13/01/26')
assert.equal(inputDate('08/'), '08/')
assert.equal(inputDate(''), '')
assert.equal(displayDate(inputDate('01/02/26')), '01/02/26')
console.log('Purchase date formatting: 8 checks passed')
