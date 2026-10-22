import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import ts from 'typescript'

const source = await fs.readFile(new URL('../src/purchaseDate.ts', import.meta.url), 'utf8')
const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ES2020 } })
const { displayDate, inputDate } = await import('data:text/javascript;base64,' + Buffer.from(compiled.outputText).toString('base64'))
assert.equal(displayDate('2026-08-27'), '08/27/2026')
assert.equal(inputDate('08/27/2026'), '2026-08-27')
assert.equal(inputDate('02/29/2024'), '2024-02-29')
assert.equal(inputDate('02/29/2025'), '02/29/2025')
assert.equal(inputDate('13/01/2026'), '13/01/2026')
assert.equal(inputDate('08/'), '08/')
assert.equal(inputDate(''), '')
assert.equal(displayDate(inputDate('01/02/2026')), '01/02/2026')
console.log('Purchase date formatting: 8 checks passed')
