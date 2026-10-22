import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import ts from 'typescript'

process.env.TZ = 'Asia/Manila'
const source = await fs.readFile(new URL('../src/receiptDays.ts', import.meta.url), 'utf8')
const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ES2020 } })
const { localDay, receiptDays } = await import('data:text/javascript;base64,' + Buffer.from(compiled.outputText).toString('base64'))
assert.equal(localDay('2026-10-21T16:01:00Z'), '2026-10-22')
assert.deepEqual(receiptDays([
  { created_at: '2026-10-21T16:01:00Z', file_count: 2 },
  { created_at: '2026-10-22T08:00:00Z', file_count: 3 },
  { created_at: '2026-10-21T15:59:00Z', file_count: 1 },
]), [{ day: '2026-10-22', files: 5 }, { day: '2026-10-21', files: 1 }])
assert.deepEqual(receiptDays([]), [])
console.log('PASS: same-day grouping, counts, ordering, and local midnight boundary')
