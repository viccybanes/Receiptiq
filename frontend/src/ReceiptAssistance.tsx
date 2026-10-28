import { useEffect, useState } from 'react'
import { Check, Crop, ExternalLink, LoaderCircle, ScanText, Search, Square, UserRound } from 'lucide-react'
import { api, post } from './api'
import type { Example, Receipt, ReceiptData } from './types'
import './assistance.css'

type Props = { receipt: Receipt; draft: ReceiptData; pageId: string | undefined; crop: number[] | null
  onSelectRegion: () => void; saveDraft: () => Promise<void>; onReceipt: (result: Receipt) => void
  onApplied: (result: Receipt) => void; onChange: (next: ReceiptData) => void; onBusy: (value: boolean) => void
  onError: (message: string) => void; onOpenReceipt: (id: string) => void }
type Difference = { label: string; before: string; after: string }

const HEADERS = [['supplier', 'Vendor'], ['receipt_date', 'Purchase date'], ['receipt_number', 'Receipt number'],
  ['currency', 'Currency'], ['total', 'Total'], ['notes', 'Notes']] as const
const ITEM_FIELDS = [['description', 'Description'], ['quantity', 'Quantity'], ['unit', 'Unit'],
  ['unit_price', 'Unit price'], ['line_total', 'Amount'], ['item_type', 'Type']] as const

function differences(current: ReceiptData, proposed: ReceiptData): Difference[] {
  const changes: Difference[] = []
  for (const [field, label] of HEADERS) {
    if (current[field] !== proposed[field]) changes.push({ label, before: current[field], after: proposed[field] })
  }
  if (current.items.length !== proposed.items.length) changes.push({ label: 'Line items',
    before: String(current.items.length), after: String(proposed.items.length) })
  for (const [index, item] of proposed.items.entries()) {
    const old = current.items.find(value => value.id === item.id) || current.items[index]
    for (const [field, label] of ITEM_FIELDS) {
      if ((old?.[field] || '') !== item[field]) changes.push({ label: `Item ${item.item_number || index + 1} · ${label}`,
        before: old?.[field] || '', after: item[field] })
    }
  }
  return changes
}

export default function ReceiptAssistance({ receipt, draft, pageId, crop, onSelectRegion, saveDraft,
  onReceipt, onApplied, onChange, onBusy, onError, onOpenReceipt }: Props) {
  const [model, setModel] = useState('qwen3-vl:2b-instruct')
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [examples, setExamples] = useState<Example[] | null>(null)
  const [exampleNotice, setExampleNotice] = useState('')
  const [sourceItems, setSourceItems] = useState<Record<string, number>>({})
  const [targetItems, setTargetItems] = useState<Record<string, string>>({})
  const [assignee, setAssignee] = useState(receipt.assignee || '')
  const [secondReview, setSecondReview] = useState(!!receipt.second_review)
  const candidate = receipt.candidate
  const googleVision = receipt.vision_settings?.provider === 'google'
  const job = receipt.vision_job
  const activeJob = !!job && ['queued', 'processing', 'running'].includes(job.state)
  const stale = !!candidate && candidate.base_revision !== receipt.revision
  const changes = candidate ? differences(draft, candidate.draft) : []
  const needsHelp = receipt.issues.some(issue => issue.blocking || issue.field === 'item_sum' ||
    issue.field.endsWith('.arithmetic') || issue.id.endsWith('.ocr'))

  useEffect(() => { setAssignee(receipt.assignee || ''); setSecondReview(!!receipt.second_review) },
    [receipt.id, receipt.assignee, receipt.second_review])
  useEffect(() => { setExamples(null); setExampleNotice(''); setError(''); setMessage('') }, [receipt.id])

  async function action(name: string, run: () => Promise<void>) {
    setBusy(name); setError(''); setMessage(''); onBusy(true)
    try { await run() }
    catch (cause) { const text = (cause as Error).message; setError(text); onError(text) }
    finally { setBusy(''); onBusy(false) }
  }
  async function freshReceipt() {
    await saveDraft()
    const current = await api<Receipt>('/receipts/' + receipt.id)
    onReceipt(current)
    return current
  }
  function requestVision(region: number[] | null) {
    if (!pageId) return
    void action(region ? 'region' : 'vision', async () => {
      const current = await freshReceipt()
      await post('/receipts/' + receipt.id + '/vision', { revision: current.revision, page_id: pageId, crop: region, model })
      onReceipt(await api<Receipt>('/receipts/' + receipt.id))
    })
  }
  function applyCandidate() {
    if (!candidate || stale) return
    void action('apply', async () => {
      const current = await freshReceipt()
      if (current.revision !== candidate.base_revision) throw new Error('The receipt changed after this suggestion. Run vision again before applying it.')
      const result = await post<Receipt>('/receipts/' + receipt.id + '/candidates/' + candidate.id + '/apply', {
        revision: current.revision })
      onApplied(result)
      setMessage('Vision suggestion applied. Check the source and verify this receipt again.')
    })
  }
  function cancelVision() {
    if (!job) return
    void action('cancel', async () => {
      await post('/jobs/' + job.id + '/cancel')
      onReceipt(await api<Receipt>('/receipts/' + receipt.id))
      setMessage('Vision cancelled. Your current expense values are preserved.')
    })
  }
  function findExamples() {
    void action('examples', async () => {
      await freshReceipt()
      const result = await api<{ examples: Example[]; notice: string }>('/receipts/' + receipt.id + '/suggestions')
      setExamples(result.examples); setExampleNotice(result.notice)
    })
  }
  function useItem(example: Example) {
    const source = example.items[sourceItems[example.receipt_id] || 0]
    const target = targetItems[example.receipt_id] || draft.items[0]?.id
    if (!source || !target) return
    onChange({ ...draft, items: draft.items.map(item => item.id !== target ? item : { ...item,
      description: source.description || item.description, unit: source.unit || item.unit,
      item_type: source.item_type || item.item_type }) })
    setMessage('Saved item wording added to the selected line. Quantity, Unit price, and Amount are preserved.')
  }
  function saveAssignment() {
    void action('assignment', async () => {
      const current = await freshReceipt()
      const result = await post<Receipt>('/receipts/' + receipt.id + '/review-options', { revision: current.revision,
        assignee, second_review: secondReview, retrieval_allowed: !!current.retrieval_allowed })
      onReceipt(result)
      setMessage('Review assignment saved.')
    })
  }
  function renderChanges(values: Difference[]) {
    return <dl className="assistance-differences">{values.map((change, index) => <div key={index}>
      <dt>{change.label}</dt><dd><span className="assistance-old">{change.before || 'Blank'}</span>
        <span className="assistance-change-label">Suggested</span><span>{change.after || 'Blank'}</span></dd>
    </div>)}</dl>
  }

  return <section className="receipt-assistance" aria-label="Receipt assistance">
    <div className="assistance-heading"><ScanText size={17} /><h3>Read with {googleVision ? 'Google' : 'local'} vision</h3></div>
    <p className="assistance-help">{needsHelp ? 'Some fields need a closer look. Read this page or select a region to compare another suggestion.' :
      'Ask the selected model for another reading of the selected page or a specific region.'}</p>
    <div className="assistance-vision-controls">
      {googleVision ? <p className="assistance-help">Google · {receipt.vision_settings?.google_model}</p> :
        <label><span>Vision model</span><select value={model} disabled={!!busy || activeJob} onChange={event => setModel(event.target.value)}>
          <option value="qwen3-vl:2b-instruct">Qwen3-VL 2B · lighter</option><option value="qwen3-vl:4b-instruct">Qwen3-VL 4B · larger</option></select></label>}
      <div className="assistance-actions"><button type="button" disabled={!pageId || !!busy || activeJob} onClick={() => requestVision(null)}>
        {busy === 'vision' ? <LoaderCircle size={15} className="spin" /> : <ScanText size={15} />}Use vision</button>
        <button type="button" disabled={!pageId || !!busy || activeJob} onClick={onSelectRegion}><Crop size={15} />Select region</button>
        <button type="button" disabled={!pageId || !crop || !!busy || activeJob} onClick={() => requestVision(crop)}>
          {busy === 'region' ? <LoaderCircle size={15} className="spin" /> : <ScanText size={15} />}Read this region again</button></div>
    </div>
    {crop && <p className="assistance-region-note">Region selected. Read it again to get a focused suggestion.</p>}
    <p className="assistance-help">{googleVision ? 'These scans send receipt images and OCR text to Google using your saved API key.' :
      'Local vision needs a downloaded model in Workspace settings.'} Manual entry is always available. Suggestions require human verification.</p>
    {job && <div className={'assistance-job ' + (job.state === 'failed' ? 'assistance-job-error' : '')} role="status">
      <div>{activeJob && <LoaderCircle size={14} className="spin" />}<strong>{job.stage || job.state}</strong>
        {activeJob && <button type="button" className="quiet" disabled={!!busy} onClick={cancelVision}><Square size={12} />Cancel vision</button>}</div>
      {job.error && <p>{job.error}</p>}
    </div>}
    {error && <div className="notice error assistance-notice" role="alert">{error}</div>}
    {message && <p className="assistance-message" role="status">{message}</p>}
    {candidate && <details className="assistance-candidate" open>
      <summary>Vision suggestion <span>{candidate.model.startsWith('gemini-') ? `Google · ${candidate.model}` :
        candidate.model.includes(':4b') ? 'Qwen3-VL 4B' : 'Qwen3-VL 2B'} · {candidate.seconds.toFixed(1)}s</span></summary>
      <p className="assistance-help">{candidate.notice || 'Compare these values with the source. Applying still requires human verification.'}</p>
      {changes.length ? <>{renderChanges(changes.slice(0, 8))}{changes.length > 8 && <details className="assistance-more-changes">
        <summary>Show {changes.length - 8} more changes</summary>{renderChanges(changes.slice(8))}</details>}</> :
        <p className="assistance-help">No visible differences from the current expense values.</p>}
      {stale && !candidate.auto_applied && <p className="assistance-stale">This suggestion is from an earlier saved revision. Run vision again to compare current values.</p>}
      {candidate.auto_applied && <p className="assistance-message">Vision applied automatically. Check the fields before verifying.</p>}
      <button type="button" className="primary" disabled={!!busy || stale || activeJob || candidate.auto_applied} onClick={applyCandidate}>
        {busy === 'apply' ? <LoaderCircle size={15} className="spin" /> : <Check size={15} />}Apply vision suggestion</button>
    </details>}
    <details className="assistance-examples">
      <summary>Similar saved receipts</summary><p className="assistance-help">Use wording from verified receipts that were explicitly shared with local examples. Historical prices are never copied.</p>
      <button type="button" disabled={!!busy} onClick={findExamples}>
        {busy === 'examples' ? <LoaderCircle size={15} className="spin" /> : <Search size={15} />}Find similar saved receipts</button>
      {exampleNotice && <p className="assistance-help" role="status">{exampleNotice}</p>}
      {examples !== null && !examples.length && <p className="assistance-help">No similar opted-in receipts are available yet. You can continue entering this receipt manually.</p>}
      {examples && examples.length > 0 && <p className="assistance-help">Similarity ranks wording; it does not measure extraction accuracy.</p>}
      {examples?.map(example => <article className="assistance-example" key={example.citation}>
        <div className="assistance-example-heading"><strong>{example.vendor || 'Saved receipt'}</strong>
          <button type="button" className="quiet" disabled={!!busy} onClick={() => onOpenReceipt(example.receipt_id)}>
            <ExternalLink size={13} />Open source receipt</button></div>
        <p className="assistance-citation">Verified revision {example.revision} · Similarity {example.score.toFixed(3)}<br />{example.citation}</p>
        {example.vendor && <button type="button" disabled={!!busy} onClick={() => {
          onChange({ ...draft, supplier: example.vendor }); setMessage('Saved Vendor wording added. Check it against this receipt.')
        }}>Use Vendor</button>}
        {!!example.items.length && <div className="assistance-item-wording">
          <label><span>Saved item wording</span><select value={sourceItems[example.receipt_id] || 0} disabled={!!busy}
            onChange={event => setSourceItems(values => ({ ...values, [example.receipt_id]: Number(event.target.value) }))}>
            {example.items.map((item, index) => <option key={item.id || index} value={index}>{item.description || `Item ${index + 1}`}{item.unit ? ' · ' + item.unit : ''}{item.item_type ? ' · ' + item.item_type : ''}</option>)}</select></label>
          <label><span>Apply wording to</span><select value={targetItems[example.receipt_id] || draft.items[0]?.id || ''}
            disabled={!!busy || !draft.items.length} onChange={event => setTargetItems(values => ({ ...values, [example.receipt_id]: event.target.value }))}>
            {!draft.items.length && <option value="">Add a line item first</option>}
            {draft.items.map((item, index) => <option key={item.id} value={item.id}>Item {item.item_number || index + 1} · {item.description || 'No description'}</option>)}</select></label>
          <button type="button" disabled={!!busy || !draft.items.length} onClick={() => useItem(example)}>Use item wording</button>
        </div>}
        {!!example.corrections.length && <details className="assistance-corrections"><summary>Verified wording corrections ({example.corrections.length})</summary>
          <ul>{example.corrections.map((correction, index) => <li key={index}><span>{correction.field === 'supplier' ? 'Vendor' :
            correction.field.replace(/items\.(\d+)\./, (_, position: string) => `Item ${Number(position) + 1} · `).replaceAll('_', ' ')}</span>
            <del>{correction.before || 'Blank'}</del><ins>{correction.after}</ins></li>)}</ul></details>}
      </article>)}
    </details>
    <details className="assistance-assignment"><summary><UserRound size={14} />Review assignment</summary>
      <div className="assistance-assignment-fields"><label><span>Assigned reviewer</span><input value={assignee} maxLength={100}
        disabled={!!busy} onChange={event => setAssignee(event.target.value)} placeholder="Reviewer name (optional)" /></label>
        <label className="assistance-checkbox"><input type="checkbox" checked={secondReview} disabled={!!busy}
          onChange={event => setSecondReview(event.target.checked)} /><span>Needs a second review</span></label>
        <button type="button" disabled={!!busy} onClick={saveAssignment}>
          {busy === 'assignment' && <LoaderCircle size={14} className="spin" />}Save assignment</button></div>
    </details>
  </section>
}
