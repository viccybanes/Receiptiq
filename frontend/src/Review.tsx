import { useCallback, useEffect, useRef, useState } from 'react'
import type { MutableRefObject } from 'react'
import { ArrowDownToLine, Check, ChevronLeft, ChevronRight, FileImage, LoaderCircle, Plus,
  RotateCw, Save, ShieldCheck, Trash2, Undo2, ZoomIn, ZoomOut } from 'lucide-react'
import { ApiError, api, download, post } from './api'
import type { Receipt, ReceiptData } from './types'

type Props = { id: string; saveBeforeLeave: MutableRefObject<(() => Promise<void>) | null>;
  onChanged: () => void; onError: (error: string) => void; onNext: () => void }

const labels: [keyof Omit<ReceiptData, 'items'>, string][] = [
  ['supplier', 'Supplier'], ['receipt_number', 'Receipt number'], ['receipt_date', 'Receipt date'],
  ['currency', 'Currency'], ['subtotal', 'Subtotal'], ['tax', 'Tax'], ['discount', 'Discount'], ['total', 'Total'],
]

export default function Review({ id, saveBeforeLeave, onChanged, onError, onNext }: Props) {
  const [receipt, setReceipt] = useState<Receipt | null>(null)
  const [draft, setDraft] = useState<ReceiptData | null>(null)
  const draftRef = useRef<ReceiptData | null>(null)
  const saved = useRef('')
  const revision = useRef(0)
  const saving = useRef<Promise<void> | null>(null)
  const [saveState, setSaveState] = useState('Saved locally')
  const [page, setPage] = useState(0)
  const [variant, setVariant] = useState('processed')
  const [image, setImage] = useState('')
  const [zoom, setZoom] = useState(100)
  const [highlight, setHighlight] = useState<string[]>([])
  const [reviewer, setReviewer] = useState(() => localStorage.getItem('receiptiq-reviewer') || '')
  const [acknowledged, setAcknowledged] = useState<Set<string>>(new Set())
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [conflict, setConflict] = useState(false)
  const conflictRef = useRef(false)
  const [history, setHistory] = useState<{ revision: number; action: string; reviewer: string; created_at: string }[]>([])
  const undo = useRef<ReceiptData[]>([])

  const load = useCallback(async () => {
    const result = await api<Receipt>('/receipts/' + id)
    setReceipt(result)
    setDraft(result.data)
    draftRef.current = result.data
    saved.current = JSON.stringify(result.data)
    revision.current = result.revision
  }, [id])

  useEffect(() => { void load().catch((e: Error) => onError(e.message)) }, [load]) // eslint-disable-line

  const save = useCallback(async () => {
    if (conflictRef.current) throw new Error('Your draft is kept. Resolve the saved-version conflict before leaving.')
    while (saving.current) await saving.current
    if (!draftRef.current || JSON.stringify(draftRef.current) === saved.current) return
    const snapshot = structuredClone(draftRef.current)
    setSaveState('Saving…')
    const request = api<Receipt>('/receipts/' + id, {
      method: 'PATCH', body: JSON.stringify({ revision: revision.current, data: snapshot }),
    }).then(result => {
      revision.current = result.revision
      saved.current = JSON.stringify(snapshot)
      setReceipt(result)
      setSaveState(JSON.stringify(draftRef.current) === saved.current ? 'Saved locally' : 'Unsaved changes')
      onChanged()
    }).catch((error: Error) => {
      if (error instanceof ApiError && error.status === 409) {
        conflictRef.current = true
        setConflict(true)
      }
      setSaveState('Save failed — retry')
      throw error
    })
    saving.current = request
    try { await request } finally { if (saving.current === request) saving.current = null }
  }, [id, onChanged])

  useEffect(() => {
    const flush = async () => {
      await save()
      if (JSON.stringify(draftRef.current) !== saved.current) await save()
    }
    saveBeforeLeave.current = flush
    return () => { if (saveBeforeLeave.current === flush) saveBeforeLeave.current = null }
  }, [save, saveBeforeLeave])

  useEffect(() => {
    if (conflict) return
    const timer = setTimeout(() => void save().catch((e: Error) => onError(e.message)), 700)
    return () => clearTimeout(timer)
  }, [draft, save, onError, conflict])

  useEffect(() => {
    const handler = (event: BeforeUnloadEvent) => {
      if (JSON.stringify(draftRef.current) !== saved.current) { event.preventDefault(); event.returnValue = '' }
    }
    window.addEventListener('beforeunload', handler)
    return () => window.removeEventListener('beforeunload', handler)
  }, [])

  useEffect(() => {
    if (!receipt || !['queued', 'processing'].includes(receipt.status)) return
    const interval = setInterval(() => {
      if (saving.current || JSON.stringify(draftRef.current) !== saved.current) return
      const expected = revision.current
      void api<Receipt>('/receipts/' + id).then(result => {
        if (revision.current !== expected || saving.current || JSON.stringify(draftRef.current) !== saved.current) return
        setReceipt(result)
        setDraft(result.data)
        draftRef.current = result.data
        saved.current = JSON.stringify(result.data)
        revision.current = result.revision
      }).catch((e: Error) => onError(e.message))
    }, 1800)
    return () => clearInterval(interval)
  }, [receipt?.status, id, onError]) // eslint-disable-line

  const currentPage = receipt?.pages[page]
  useEffect(() => {
    setImage('')
    if (!currentPage) return
    let alive = true
    let url = ''
    void api<Blob>('/receipts/' + id + '/pages/' + currentPage.id + '?variant=' + variant,
      { headers: { Accept: 'application/octet-stream' } }).then(blob => {
      if (alive) { url = URL.createObjectURL(blob); setImage(url) }
    }).catch((e: Error) => onError(e.message))
    return () => { alive = false; if (url) URL.revokeObjectURL(url) }
  }, [id, currentPage?.id, variant, receipt?.rotation]) // eslint-disable-line

  function change(next: ReceiptData) {
    if (draftRef.current) {
      undo.current.push(structuredClone(draftRef.current))
      if (undo.current.length > 50) undo.current.shift()
    }
    draftRef.current = next
    setDraft(next)
    setSaveState('Unsaved changes')
    setAcknowledged(new Set())
    setReason('')
  }

  function focus(field: string) {
    const ids = receipt?.field_meta[field]?.line_ids || []
    setHighlight(ids)
    const line = receipt?.evidence.lines.find(line => ids.includes(line.id))
    if (line) { setPage(line.page); setVariant('processed') }
  }

  async function verify() {
    setBusy(true)
    try {
      await saveBeforeLeave.current?.()
      const result = await post<Receipt>('/receipts/' + id + '/verify', {
        revision: revision.current, reviewer, acknowledged: [...acknowledged], reason,
      })
      setReceipt(result)
      localStorage.setItem('receiptiq-reviewer', reviewer)
      onChanged()
    } catch (e) { onError((e as Error).message) }
    finally { setBusy(false) }
  }

  async function rotateAndRead() {
    setBusy(true)
    try {
      await saveBeforeLeave.current?.()
      await post('/receipts/' + id + '/process', { rotation: ((receipt?.rotation || 0) + 90) % 360 })
      await load()
      onChanged()
    } catch (e) { onError((e as Error).message) }
    finally { setBusy(false) }
  }

  if (!receipt || !draft) return <div className="review-placeholder"><LoaderCircle size={24} className="spin" /><p>Opening receipt…</p></div>
  const pending = ['queued', 'processing'].includes(receipt.status)
  const warnings = receipt.issues.filter(issue => !issue.blocking)
  const highlighted = receipt.evidence.lines.filter(line => highlight.includes(line.id) && line.page === page)

  return <>
    <section className="image-panel" aria-label="Receipt image">
      <div className="panel-title"><h2><FileImage size={17} /> Original receipt</h2>
        <button className="icon-button" title="Download untouched original" aria-label="Download original"
          onClick={() => void download('/receipts/' + id + '/original', receipt.original_name).catch((e: Error) => onError(e.message))}>
          <ArrowDownToLine size={16} /></button></div>
      <div className="image-toolbar">
        <select aria-label="Image version" value={variant} onChange={e => setVariant(e.target.value)}>
          <option value="processed">Enhanced image</option><option value="original">Source image</option>
        </select>
        <button className="icon-button" aria-label="Zoom out" disabled={zoom <= 50} onClick={() => setZoom(zoom - 25)}><ZoomOut size={16} /></button>
        <span>{zoom}%</span>
        <button className="icon-button" aria-label="Zoom in" disabled={zoom >= 250} onClick={() => setZoom(zoom + 25)}><ZoomIn size={16} /></button>
        <button className="icon-button" aria-label="Rotate clockwise and rerun OCR" title="Rotate 90° and reread; edits are preserved"
          disabled={pending || busy} onClick={() => void rotateAndRead()}><RotateCw size={16} /></button>
      </div>
      <div className="image-stage">
        {image && currentPage ? <div className="receipt-image" style={{ width: zoom + '%' }}>
          <img src={image} alt={'Receipt page ' + (page + 1)} />
          {variant === 'processed' && highlighted.length > 0 && <svg className="evidence-overlay" viewBox={'0 0 ' + currentPage.width + ' ' + currentPage.height}
            aria-label="Highlighted OCR source">
            {highlighted.map(line => <rect key={line.id} x={line.box[0]} y={line.box[1]} width={line.box[2] - line.box[0]}
              height={line.box[3] - line.box[1]} />)}
          </svg>}
        </div> : <div className="image-empty"><FileImage size={36} strokeWidth={1} />
          <p>{pending ? 'Preparing receipt preview…' : 'No preview available. Download the original to inspect it.'}</p></div>}
      </div>
      <div className="page-bar"><button aria-label="Previous page" disabled={page === 0} onClick={() => setPage(page - 1)}><ChevronLeft size={15} /></button>
        <span>Page {receipt.pages.length ? page + 1 : 0} of {receipt.pages.length}</span>
        <button aria-label="Next page" disabled={page + 1 >= receipt.pages.length} onClick={() => setPage(page + 1)}><ChevronRight size={15} /></button></div>
      <details className="ocr-evidence"><summary>Recognized text · {receipt.evidence.lines.length} regions</summary>
        <p>OCR scores describe recognition, not verified accuracy. Select a region to inspect its source.</p>
        {receipt.evidence.lines.map(line => <button key={line.id} className="ocr-line" onClick={() => {
          setPage(line.page); setHighlight([line.id]); setVariant('processed')
        }}><span>{line.text}</span><small>{Math.round(line.score * 100)}% OCR</small></button>)}
        {receipt.evidence.draft && revision.current > 0 && <button onClick={() => {
          if (window.confirm('Replace the current fields with the latest OCR draft? You can undo this edit.')) change(receipt.evidence.draft!)
        }}>Use latest OCR draft</button>}
      </details>
    </section>
    <section className="fields-panel" aria-label="Receipt review">
      <div className="panel-title"><h2>Receipt details</h2><span className={'save-state ' + (saveState.startsWith('Save failed') ? 'save-error' : '')} aria-live="polite">
        {saveState === 'Saved locally' ? <Check size={13} /> : <Save size={13} />}{saveState}</span></div>
      <div className="review-intro"><span className={'review-state ' + (receipt.status === 'verified' ? 'is-verified' : '')}>
        {receipt.status === 'verified' ? <ShieldCheck size={16} /> : <FileImage size={16} />}
        {receipt.status === 'verified' ? 'Verified by a person' : pending ? 'OCR is running' : 'Ready for your review'}</span>
        <p>{receipt.status === 'verified' ? 'Editing these fields requires verification again.' :
          'Compare each field with the receipt. Select a field to see its source.'}</p></div>
      {receipt.error && <div className="notice warning">{receipt.error} You can enter the values manually.</div>}
      {conflict && <div className="conflict-panel" role="alert"><strong>A newer version was saved. Your draft is kept here.</strong>
        <p>Choose which values to keep before leaving this receipt.</p>
        <div><button type="button" onClick={async () => {
          try {
            const latest = await api<Receipt>('/receipts/' + id)
            revision.current = latest.revision
            conflictRef.current = false
            setConflict(false)
            await save()
          } catch (e) { onError((e as Error).message) }
        }}>Keep my draft</button>
        <button type="button" onClick={() => {
          if (window.confirm('Replace your unsaved edits with the latest saved version?')) {
            conflictRef.current = false
            setConflict(false)
            void load().catch((e: Error) => onError(e.message))
          }
        }}>Load saved version</button></div></div>}
      <form onSubmit={e => { e.preventDefault(); void verify() }}>
        <div className="field-grid">{labels.map(([key, label]) => {
          const issue = receipt.issues.find(issue => issue.field === key)
          return <label key={key} className={'field ' + (key === 'supplier' ? 'full-width' : '')}>
            <span>{label}{['supplier', 'receipt_date', 'currency', 'total'].includes(key) && <span className="required"> *</span>}</span>
            <input value={draft[key]} maxLength={key === 'currency' ? 3 : key === 'supplier' ? 500 : 100}
              type={key === 'receipt_date' ? 'date' : 'text'} inputMode={['subtotal', 'tax', 'discount', 'total'].includes(key) ? 'decimal' : 'text'}
              placeholder={key === 'currency' ? 'e.g. PHP' : key === 'receipt_number' ? 'Optional' : 'Not extracted'}
              onFocus={() => focus(key)} aria-invalid={!!issue?.blocking}
              onChange={e => change({ ...draft, [key]: key === 'currency' ? e.target.value.toUpperCase() : e.target.value })} />
            {issue?.blocking && <small className="field-error">{issue.message}</small>}
          </label>
        })}</div>
        <div className="items-heading"><h3>Line items <span>{draft.items.length}</span></h3>
          <button type="button" className="quiet" onClick={() => change({ ...draft, items: [...draft.items,
            { description: '', quantity: '', unit_price: '', line_total: '' }] })}><Plus size={14} /> Add item</button></div>
        {draft.items.map((item, index) => <fieldset className="item-fields" key={index}>
          <legend className="visually-hidden">Item {index + 1}</legend>
          <label className="item-description"><span>Description</span><input aria-label={'Item ' + (index + 1) + ' description'}
            value={item.description} onFocus={() => focus('items.' + index + '.description')}
            onChange={e => change({ ...draft, items: draft.items.map((value, i) => i === index ? { ...value, description: e.target.value } : value) })} /></label>
          {(['quantity', 'unit_price', 'line_total'] as const).map((key, position) => <label key={key}><span>{['Qty', 'Unit price', 'Line total'][position]}</span>
            <input aria-label={'Item ' + (index + 1) + ' ' + key.replace('_', ' ')} inputMode="decimal"
              value={item[key]} onFocus={() => focus('items.' + index + '.' + key)}
              onChange={e => change({ ...draft, items: draft.items.map((value, i) => i === index ? { ...value, [key]: e.target.value } : value) })} /></label>)}
          <button type="button" className="icon-button remove-item" aria-label={'Remove item ' + (index + 1)}
            onClick={() => change({ ...draft, items: draft.items.filter((_, i) => i !== index) })}><Trash2 size={15} /></button>
        </fieldset>)}
        {!draft.items.length && <p className="no-items">No items extracted. Add them above, or confirm a summary-only export below.</p>}
        <div className="edit-actions"><button type="button" className="quiet" disabled={!undo.current.length} onClick={() => {
          const previous = undo.current.pop()
          if (previous) { draftRef.current = previous; setDraft(previous); setSaveState('Unsaved changes') }
        }}><Undo2 size={14} /> Undo edit</button><button type="button" className="quiet" onClick={() => void save().catch((e: Error) => onError(e.message))}>
          <Save size={14} /> Save now</button></div>
        {receipt.issues.length > 0 && <div className="validation-panel"><h3>Check before verifying</h3>
          {receipt.issues.filter(issue => issue.blocking).map(issue => <p key={issue.id} className="field-error">{issue.message}</p>)}
          {warnings.map(issue => <label className="acknowledgment" key={issue.id}><input type="checkbox"
            checked={acknowledged.has(issue.id)} onChange={e => setAcknowledged(current => {
              const next = new Set(current); if (e.target.checked) next.add(issue.id); else next.delete(issue.id); return next
            })} /><span>{issue.message}</span></label>)}
          {warnings.length > 0 && <label className="field"><span>Reason for accepting warnings</span><textarea value={reason}
            placeholder="Explain what you checked against the receipt." onChange={e => setReason(e.target.value)} /></label>}
        </div>}
        <div className="verification"><label className="field"><span>Reviewed by</span><input value={reviewer} required maxLength={100}
          placeholder="Your name or initials" onChange={e => setReviewer(e.target.value)} /></label>
          <p>Verify only after checking the image and resolving the flagged fields.</p>
          <div className="verify-actions"><button className="primary" type="submit" disabled={busy || pending || receipt.status === 'verified'}>
            {busy ? <LoaderCircle className="spin" size={16} /> : <Check size={17} />}
            {receipt.status === 'verified' ? 'Verified' : 'Verify receipt'}</button>
            <button type="button" onClick={async () => {
              try { await saveBeforeLeave.current?.(); onNext() } catch (e) { onError((e as Error).message) }
            }}>Next receipt <ChevronRight size={16} /></button></div></div>
      </form>
      <details className="history" onToggle={e => {
        if (e.currentTarget.open) void api<typeof history>('/receipts/' + id + '/history').then(setHistory).catch((e: Error) => onError(e.message))
      }}><summary>Review history</summary>{history.map((event, index) => <p key={index}>Revision {event.revision} · {event.action}
        {event.reviewer ? ' · ' + event.reviewer : ''}<small>{new Date(event.created_at).toLocaleString()}</small></p>)}</details>
    </section>
  </>
}
