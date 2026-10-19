import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import ts from 'typescript'

const source = await fs.readFile(new URL('../src/amount.ts', import.meta.url), 'utf8')
const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ES2020 } })
const { amount } = await import('data:text/javascript;base64,' + Buffer.from(compiled.outputText).toString('base64'))
for (const [quantity, price, expected] of [
  ['2', '50', '100.00'], ['0.1', '0.2', '0.02'], ['3', '0.335', '1.01'],
  ['-2', '1.005', '-2.01'], ['3', '26.67', '80.01'], ['0', '50', '0.00'],
  ['999999999999', '0.01', '9999999999.99'], ['', '5', null], ['NaN', '5', null],
]) assert.equal(amount(quantity, price), expected)
console.log('Decimal Amount checks passed (9 cases).')
