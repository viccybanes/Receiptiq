import { useCallback, useEffect, useRef, useState } from 'react'
import type { MutableRefObject } from 'react'
import { ArrowDownToLine, Check, ChevronLeft, ChevronRight, FileImage, LoaderCircle, Plus,
  RotateCw, Save, ShieldCheck, Trash2, Undo2, ZoomIn, ZoomOut } from 'lucide-react'
import { ApiError, api, download, post } from './api'
import type { Receipt, ReceiptData } from './types'
import PhotoField from './PhotoField'
import { amount } from './amount'

type Props = { id: string; saveBeforeLeave: MutableRefObject<(() => Promise<void>) | null>;
  onChanged: () => void; onError: (error: string) => void; onNext: () => void }

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
  const photoUploads = useRef(0)
  const [photosBusy, setPhotosBusy] = useState(false)
  function photoBusy(value: boolean) {
    photoUploads.current += value ? 1 : -1
    setPhotosBusy(photoUploads.current > 0)
  }

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
      saved.current = JSON.stringify(result.data)
      if (JSON.stringify(draftRef.current) === JSON.stringify(snapshot)) {
        draftRef.current = result.data
        setDraft(result.data)
      }
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
      if (photoUploads.current) throw new Error('Wait for photos to finish uploading before leaving or verifying.')
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
      if (photoUploads.current || JSON.stringify(draftRef.current) !== saved.current) { event.preventDefault(); event.returnValue = '' }
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
  const replacement = draft?.receipt_photo_id || ''
  const directPhoto = replacement !== (receipt?.evidence.photo_id || '')
  useEffect(() => {
    setImage('')
    if (!currentPage && !replacement) return
    if (directPhoto && !replacement && !receipt?.original_mime.startsWith('image/')) return
    let alive = true
    let url = ''
    const path = directPhoto ? (replacement ? '/photos/' + replacement : '/original') :
      currentPage ? '/pages/' + currentPage.id + '?variant=' + variant : '/photos/' + replacement
    void api<Blob>('/receipts/' + id + path,
      { headers: { Accept: 'application/octet-stream' } }).then(blob => {
      if (alive) { url = URL.createObjectURL(blob); setImage(url) }
    }).catch((e: Error) => onError(e.message))
    return () => { alive = false; if (url) URL.revokeObjectURL(url) }
  }, [id, currentPage?.id, variant, receipt?.rotation, replacement, directPhoto]) // eslint-disable-line

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
        <select aria-label="Image version" value={variant} disabled={directPhoto} onChange={e => setVariant(e.target.value)}>
          <option value="processed">Enhanced image</option><option value="original">Source image</option>
        </select>
        <button className="icon-button" aria-label="Zoom out" disabled={zoom <= 50} onClick={() => setZoom(zoom - 25)}><ZoomOut size={16} /></button>
        <span>{zoom}%</span>
        <button className="icon-button" aria-label="Zoom in" disabled={zoom >= 250} onClick={() => setZoom(zoom + 25)}><ZoomIn size={16} /></button>
        <button className="icon-button" aria-label="Rotate clockwise and rerun OCR" title="Rotate 90° and reread; edits are preserved"
          disabled={pending || busy || photosBusy || (receipt.original_mime === 'text/csv' && !replacement)}
          onClick={() => void rotateAndRead()}><RotateCw size={16} /></button>
      </div>
      <div className="image-stage">
        {image ? <div className="receipt-image" style={{ width: zoom + '%' }}>
          <img src={image} alt={'Receipt page ' + (page + 1)} />
          {!directPhoto && currentPage && variant === 'processed' && highlighted.length > 0 && <svg className="evidence-overlay" viewBox={'0 0 ' + currentPage.width + ' ' + currentPage.height}
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
        <div className="field-grid">{([['receipt_date', 'Purchase date'], ['supplier', 'Vendor'],
          ['receipt_number', 'Receipt number']] as const).map(([key, label]) => {
          const issue = receipt.issues.find(issue => issue.field === key)
          return <label key={key} className="field">
            <span>{label}</span>
            <input value={draft[key]} maxLength={500}
              type={key === 'receipt_date' ? 'date' : 'text'}
              placeholder={key === 'receipt_number' ? 'Vendor reference — leave blank if none' : ''}
              onFocus={() => focus(key)} aria-invalid={!!issue?.blocking}
              onChange={e => change({ ...draft, [key]: e.target.value })} />
            {issue?.blocking && <small className="field-error">{issue.message}</small>}
          </label>
        })}
          <PhotoField receiptId={id} photoId={draft.receipt_photo_id} label="Receipt photo"
            original={receipt.original_mime !== 'text/csv'} onBusy={photoBusy} onError={onError}
            onChange={photoId => change({ ...draftRef.current!, receipt_photo_id: photoId })} />
          <label className="field full-width"><span>Notes</span><textarea value={draft.notes} maxLength={4000}
            placeholder="Anything worth remembering about this receipt"
            onChange={e => change({ ...draft, notes: e.target.value })} /></label>
        </div>
        <div className="items-heading"><h3>Line items</h3>
          <label className="total-field"><span>Total: {draft.currency === 'PHP' ? '₱' : draft.currency}</span>
            <input aria-label="Total" inputMode="decimal" value={draft.total} onFocus={() => focus('total')}
              onChange={e => change({ ...draft, total: e.target.value })} /></label></div>
        {draft.items.map((item, index) => <fieldset className="item-fields" key={item.id || index} disabled={photosBusy}>
          <legend>Line {index + 1}{item.item_number ? ' · Item ' + item.item_number : ''}</legend>
          <label className="item-description"><span>Description</span><input aria-label={'Item ' + (index + 1) + ' description'}
            value={item.description} placeholder="Description" onFocus={() => focus('items.' + index + '.description')}
            onChange={e => change({ ...draft, items: draft.items.map((value, i) => i === index ? { ...value, description: e.target.value } : value) })} /></label>
          {(['quantity', 'unit', 'unit_price', 'line_total', 'item_type'] as const).map((key, position) => <label key={key}><span>{['Quantity', 'Unit', 'Unit price', 'Amount', 'Type'][position]}</span>
            <input aria-label={'Item ' + (index + 1) + ' ' + ['Quantity', 'Unit', 'Unit price', 'Amount', 'Type'][position]}
              inputMode={['unit', 'item_type'].includes(key) ? 'text' : 'decimal'}
              value={item[key]} placeholder={key === 'unit' ? 'bag, pc, m...' : ''}
              list={key === 'item_type' ? 'expense-types' : undefined}
              onFocus={() => focus('items.' + index + '.' + key)}
              onChange={e => {
                const next = { ...item, [key]: e.target.value }
                if (key === 'quantity' || key === 'unit_price') next.line_total = amount(next.quantity, next.unit_price) || ''
                change({ ...draft, items: draft.items.map((value, i) => i === index ? next : value) })
              }} /></label>)}
          <PhotoField receiptId={id} photoId={item.photo_id} label="Photo of item purchased" onBusy={photoBusy} onError={onError}
            onChange={photoId => change({ ...draftRef.current!, items: draftRef.current!.items.map(value =>
              value.id === item.id ? { ...value, photo_id: photoId } : value) })} />
          <button type="button" className="icon-button remove-item" aria-label={'Remove item ' + (index + 1)}
            onClick={() => change({ ...draft, items: draft.items.filter((_, i) => i !== index) })}><Trash2 size={15} /></button>
        </fieldset>)}
        <datalist id="expense-types"><option value="Materials" /></datalist>
        <button type="button" className="quiet" disabled={photosBusy} onClick={() => change({ ...draft, items: [...draft.items,
          { id: crypto.randomUUID(), item_number: null, description: '', quantity: '1', unit: '', unit_price: '',
            line_total: '', item_type: 'Materials', photo_id: '' }] })}><Plus size={14} /> Add line</button>
        {!draft.items.length && <p className="no-items">No items extracted. Add one or more line items.</p>}
        {receipt.reconciliation?.can_allocate && <div className="notice discount-notice"><div>
          <strong>Possible implicit discount: {receipt.reconciliation.possible_discount}</strong>
          <p>Line amounts exceed Total. Confirm this is a discount before distributing it across Amount and Unit price.</p>
          <button type="button" disabled={busy || photosBusy} onClick={async () => {
            setBusy(true)
            try {
              await saveBeforeLeave.current?.()
              const result = await post<{ data: ReceiptData }>('/receipts/' + id + '/discount-preview',
                { revision: revision.current, data: draftRef.current })
              change(result.data)
            } catch (e) { onError((e as Error).message) }
            finally { setBusy(false) }
          }}>Confirm discount and apply to line items</button></div></div>}
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
          <div className="verify-actions"><button className="primary" type="submit" disabled={busy || photosBusy || pending || receipt.status === 'verified'}>
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
