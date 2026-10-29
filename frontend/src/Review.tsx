import { useCallback, useEffect, useRef, useState } from 'react'
import type { MutableRefObject } from 'react'
import { ArrowDown, ArrowDownToLine, ArrowUp, Check, ChevronLeft, ChevronRight, FileImage, LoaderCircle, Plus,
  RotateCw, Save, ShieldCheck, Trash2, Undo2, ZoomIn, ZoomOut } from 'lucide-react'
import { ApiError, api, download, post } from './api'
import { displayDate, inputDate } from './purchaseDate'
import type { Issue, Receipt, ReceiptData, VisionSettings } from './types'
import { visionOverlay } from './visionOverlay'
import PhotoField from './PhotoField'
import { completeAmounts, effectiveDiscount } from './amount'
import ReceiptAssistance from './ReceiptAssistance'

const ITEM_TYPES = ['Materials', 'Tools', 'Equipment', 'Outside Labor', 'Services', 'Permits & Fees',
  'Utilities', 'Transportation', 'Logistics', 'Other']

function issueProps(issues: Issue[]) {
  return {
    className: issues.length ? (issues.some(issue => issue.blocking) ? 'has-review-error' : 'has-review-warning') : '',
    'aria-invalid': issues.some(issue => issue.blocking) || undefined,
    'aria-describedby': issues.length ? issues.map(issue => 'review-issue-' + issue.id).join(' ') : undefined,
  }
}

function InlineIssues({ issues }: { issues: Issue[] }) {
  if (!issues.length) return null
  return <ul className="inline-issues">{issues.map(issue =>
    <li key={issue.id} id={'review-issue-' + issue.id} className={issue.blocking ? 'field-error' : 'field-warning'}>
      <strong>{issue.blocking ? 'Fix: ' : 'Check: '}</strong>{issue.message}
    </li>)}</ul>
}

type Props = { id: string; saveBeforeLeave: MutableRefObject<(() => Promise<void>) | null>;
  onChanged: () => void; onError: (error: string) => void; onNext: () => void;
  onOpenReceipt: (id: string) => void; onSplit: (batchId: string) => void; onDeleted: () => void }

export default function Review({ id, saveBeforeLeave, onChanged, onError, onNext, onOpenReceipt, onSplit, onDeleted }: Props) {
  const [receipt, setReceipt] = useState<Receipt | null>(null)
  const [draft, setDraft] = useState<ReceiptData | null>(null)
  const draftRef = useRef<ReceiptData | null>(null)
  const saved = useRef('')
  const revision = useRef(0)
  const saving = useRef<Promise<void> | null>(null)
  const [saveState, setSaveState] = useState('Saved locally')
  const [page, setPage] = useState(0)
  const [readingActions, setReadingActions] = useState<HTMLDivElement | null>(null)
  const [variant, setVariant] = useState('processed')
  const [image, setImage] = useState('')
  const [zoom, setZoom] = useState(100)
  const imageStage = useRef<HTMLDivElement>(null)
  const panStart = useRef<{ x: number; y: number; left: number; top: number } | null>(null)
  const [panning, setPanning] = useState(false)
  const [viewRotation, setViewRotation] = useState(() => {
    const value = Number(localStorage.getItem('receiptiq-rotation-' + id))
    return [0, 90, 180, 270].includes(value) ? value : 0
  })
  const [imageSize, setImageSize] = useState([1, 1])
  const [highlight, setHighlight] = useState<string[]>([])
  const [reviewer, setReviewer] = useState(() => localStorage.getItem('receiptiq-reviewer') || '')
  const [acknowledged, setAcknowledged] = useState<Set<string>>(new Set())
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [overlayBusy, setOverlayBusy] = useState(false)
  const [conflict, setConflict] = useState(false)
  const conflictRef = useRef(false)
  const [history, setHistory] = useState<{ revision: number; action: string; reviewer: string; created_at: string }[]>([])
  const undo = useRef<ReceiptData[]>([])
  const photoUploads = useRef(0)
  const [photosBusy, setPhotosBusy] = useState(false)
  const [allowRetrieval, setAllowRetrieval] = useState(false)
  const [selecting, setSelecting] = useState(false)
  const [crop, setCrop] = useState<number[] | null>(null)
  const cropStart = useRef<number[] | null>(null)
  const [pageGroups, setPageGroups] = useState('')
  const flaggedField = useRef(0)
  function photoBusy(value: boolean) {
    photoUploads.current += value ? 1 : -1
    setPhotosBusy(photoUploads.current > 0)
  }

  const load = useCallback(async () => {
    const result = await api<Receipt>('/receipts/' + id)
    setReceipt(result)
    const completed = result.status === 'verified' ? result.data : completeAmounts(result.data)
    setDraft(completed)
    draftRef.current = completed
    saved.current = JSON.stringify(result.data)
    revision.current = result.revision
    setAllowRetrieval(!!result.retrieval_allowed)
  }, [id])

  useEffect(() => { void load().catch((e: Error) => onError(e.message)) }, [load]) // eslint-disable-line
  useEffect(() => {
    const changed = (event: Event) => setReceipt(value => value ? {
      ...value, vision_settings: (event as CustomEvent<VisionSettings>).detail,
    } : value)
    window.addEventListener('receiptiq:vision-settings', changed)
    return () => window.removeEventListener('receiptiq:vision-settings', changed)
  }, [])

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
    if (!receipt || (!['queued', 'processing'].includes(receipt.status) &&
      !['queued', 'processing'].includes(receipt.vision_job?.state || ''))) return
    const interval = setInterval(() => {
      if (saving.current || JSON.stringify(draftRef.current) !== saved.current) return
      const expected = revision.current
      void api<Receipt>('/receipts/' + id).then(result => {
        if (revision.current !== expected || saving.current || JSON.stringify(draftRef.current) !== saved.current) return
        if (result.revision !== revision.current) { setAcknowledged(new Set()); setReason('') }
        setReceipt(result)
        const completed = completeAmounts(result.data)
        setDraft(completed)
        draftRef.current = completed
        saved.current = JSON.stringify(result.data)
        revision.current = result.revision
      }).catch((e: Error) => onError(e.message))
    }, 1800)
    return () => clearInterval(interval)
  }, [receipt?.status, receipt?.vision_job?.state, id, onError]) // eslint-disable-line

  useEffect(() => { setCrop(null); setSelecting(false) }, [page, id])
  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') {
        event.preventDefault(); void save().catch((e: Error) => onError(e.message))
      } else if (event.altKey && event.key === 'ArrowRight') {
        event.preventDefault()
        void saveBeforeLeave.current?.().then(onNext).catch((e: Error) => onError(e.message))
      } else if (event.altKey && event.key === 'ArrowDown' && receipt?.issues.length) {
        event.preventDefault()
        const issue = receipt.issues[flaggedField.current++ % receipt.issues.length]
        const field = issue.field.replace('.pricing', '.unit_price').replace('.arithmetic', '.line_total')
        const input = document.querySelector<HTMLInputElement>('[data-field="' +
          CSS.escape(field === 'item_sum' ? 'total' : field) + '"]')
        input?.focus()
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [save, receipt?.issues, onNext, onError, saveBeforeLeave]) // eslint-disable-line

  const currentPage = receipt?.pages[page]
  const replacement = draft?.receipt_photo_id || ''
  const directPhoto = replacement !== (receipt?.evidence.photo_id || '')
  const pending = !!receipt && ['queued', 'processing'].includes(receipt.status)
  useEffect(() => {
    setImage('')
    // Page IDs survive reprocessing. Wait for the saved pixels before fetching;
    // rotation changes when queued, before the worker replaces the image files.
    if (pending && !directPhoto) return
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
  }, [id, currentPage?.id, variant, receipt?.rotation, replacement, directPhoto, pending]) // eslint-disable-line

  function change(next: ReceiptData) {
    next = completeAmounts(next, draftRef.current || undefined)
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

  function insertItem(index: number) {
    if (!draftRef.current || busy || photoUploads.current) return
    const items = [...draftRef.current.items]
    items.splice(index, 0, { id: crypto.randomUUID(), item_number: null, description: '', quantity: '1',
      unit: 'unit', unit_price: '', line_total: '', item_type: 'Materials', photo_id: '' })
    change({ ...draftRef.current, items })
    requestAnimationFrame(() => document.querySelector<HTMLInputElement>(
      '[data-field="items.' + index + '.description"]')?.focus())
  }

  function moveItem(index: number, direction: number) {
    if (!draftRef.current || busy || photoUploads.current) return
    const items = [...draftRef.current.items], target = index + direction
    if (target < 0 || target >= items.length) return
    ;[items[index], items[target]] = [items[target], items[index]]
    change({ ...draftRef.current, items })
  }

  async function verify() {
    setBusy(true)
    try {
      await saveBeforeLeave.current?.()
      const result = await post<Receipt>('/receipts/' + id + '/verify', {
        revision: revision.current, reviewer, acknowledged: [...acknowledged], reason, retrieval_allowed: allowRetrieval,
      })
      setReceipt(result)
      localStorage.setItem('receiptiq-reviewer', reviewer)
      onChanged()
    } catch (e) { onError((e as Error).message) }
    finally { setBusy(false) }
  }

  function rotatePreview() {
    const next = (viewRotation + 90) % 360
    localStorage.setItem('receiptiq-rotation-' + id, String(next))
    setViewRotation(next)
    setCrop(null)
    cropStart.current = null
  }

  function sourcePoint(x: number, y: number) {
    switch (viewRotation) {
      case 90: return [y, 1 - x]
      case 180: return [1 - x, 1 - y]
      case 270: return [1 - y, x]
      default: return [x, y]
    }
  }

  async function deleteReceipt() {
    if (!window.confirm(`Delete "${receipt?.original_name}" and its saved fields and photos? This cannot be undone.`)) return
    setBusy(true)
    try {
      await saveBeforeLeave.current?.()
      await api('/receipts/' + id, { method: 'DELETE' })
      localStorage.removeItem('receiptiq-rotation-' + id)
      saveBeforeLeave.current = null
      onDeleted()
    } catch (e) { onError((e as Error).message) }
    finally { setBusy(false) }
  }

  if (!receipt || !draft) return <div className="review-placeholder"><LoaderCircle size={24} className="spin" /><p>Opening receipt…</p></div>
  const warnings = receipt.issues.filter(issue => !issue.blocking)
  const totalIssues = receipt.issues.filter(issue => issue.field === 'total' || issue.field === 'item_sum')
  const discountIssues = receipt.issues.filter(issue => issue.field === 'total_discount_percent')
  const highlighted = receipt.evidence.lines.filter(line => highlight.includes(line.id) && line.page === page)
  const overlay = currentPage ? visionOverlay(receipt, draft, currentPage.id) : []
  const showOverlay = !!receipt.vision_settings?.capture_regions

  async function toggleOverlay(checked: boolean) {
    const previous = receipt?.vision_settings
    setReceipt(value => value ? { ...value, vision_settings: {
      provider: 'local', google_model: 'gemini-3.8-flash', google_consent: false, google_key_saved: false,
      ...value.vision_settings,
      amount_first: value.vision_settings?.amount_first || false, capture_regions: checked,
    } } : value)
    setOverlayBusy(true)
    try {
      const settings = await api<VisionSettings>('/vision/settings', {
        method: 'PATCH', body: JSON.stringify({ capture_regions: checked }),
      })
      setReceipt(value => value ? { ...value, vision_settings: settings } : value)
    } catch (error) {
      setReceipt(value => value ? { ...value, vision_settings: previous } : value)
      onError((error as Error).message)
    }
    finally { setOverlayBusy(false) }
  }

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
        <button onClick={() => {
          setZoom(100)
          imageStage.current?.scrollTo({ left: 0, top: 0 })
        }}>Reset view</button>
        <button aria-label="Rotate preview clockwise" title="Rotate preview 90 degrees without rerunning OCR"
          disabled={!image}
          onClick={rotatePreview}><RotateCw size={16} /> Rotate</button>
        <button aria-label="Delete receipt" title={pending ? 'Wait for processing to finish before deleting' : 'Delete this receipt'}
          disabled={pending || busy || photosBusy} onClick={() => void deleteReceipt()}><Trash2 size={16} /> Delete</button>
      </div>
      <div className="vision-overlay-controls">
        <label><input type="checkbox" checked={showOverlay} disabled={overlayBusy}
          onChange={event => void toggleOverlay(event.target.checked)} /> Show AI line numbers</label>
        {showOverlay && <p>{!overlay.length ? 'Run Vision again to locate the item rows.' :
          overlay.some(region => region.suggested) ? 'Suggested lines. Apply vision to place them in the receipt details.' :
          'Numbers match the receipt detail lines. Check the image before verifying.'}</p>}
      </div>
      <div ref={imageStage} className={'image-stage' + (image && !selecting ? ' pannable' : '') + (panning ? ' panning' : '')}
        tabIndex={0} role="region" aria-label="Receipt preview: zoom then drag to pan, or use arrow keys"
        onPointerDown={event => {
          if (!image || selecting || event.button !== 0) return
          event.preventDefault()
          event.currentTarget.focus({ preventScroll: true })
          panStart.current = { x: event.clientX, y: event.clientY,
            left: event.currentTarget.scrollLeft, top: event.currentTarget.scrollTop }
          event.currentTarget.setPointerCapture(event.pointerId)
          setPanning(true)
        }} onPointerMove={event => {
          if (!panStart.current) return
          event.currentTarget.scrollLeft = panStart.current.left + panStart.current.x - event.clientX
          event.currentTarget.scrollTop = panStart.current.top + panStart.current.y - event.clientY
        }} onPointerUp={event => {
          panStart.current = null; setPanning(false)
          if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId)
        }} onPointerCancel={() => { panStart.current = null; setPanning(false) }}
        onLostPointerCapture={() => { panStart.current = null; setPanning(false) }}>
        {image ? <div className="receipt-image" style={{ width: zoom + '%',
          aspectRatio: viewRotation % 180 ? imageSize[1] / imageSize[0] : imageSize[0] / imageSize[1] }}>
          <div style={{ position: 'absolute', left: '50%', top: '50%',
            width: (viewRotation % 180 ? imageSize[0] / imageSize[1] * 100 : 100) + '%',
            transform: `translate(-50%, -50%) rotate(${viewRotation}deg)` }}>
          {showOverlay && !directPhoto && currentPage && variant === 'processed' && overlay.length > 0 &&
            <svg className="vision-row-overlay" viewBox="0 0 1000 1000" preserveAspectRatio="none"
              role="img" aria-label="AI detected item rows">
              {overlay.map((region, index) => <g key={index}><title>{region.title}</title>
                <rect x={region.box[0] * 1000} y={region.box[1] * 1000}
                  width={(region.box[2] - region.box[0]) * 1000} height={(region.box[3] - region.box[1]) * 1000} />
                <text x={region.box[0] * 1000 + 5} y={region.box[1] * 1000 + 21}>{region.line}</text>
              </g>)}
            </svg>}
          <img src={image} draggable={false} alt={'Receipt page ' + (page + 1)}
            onLoad={event => setImageSize([event.currentTarget.naturalWidth, event.currentTarget.naturalHeight])} />
          {!directPhoto && currentPage && variant === 'processed' && highlighted.length > 0 && <svg className="evidence-overlay" viewBox={'0 0 ' + currentPage.width + ' ' + currentPage.height}
            aria-label="Highlighted OCR source">
            {highlighted.map(line => <rect key={line.id} x={line.box[0]} y={line.box[1]} width={line.box[2] - line.box[0]}
              height={line.box[3] - line.box[1]} />)}
          </svg>}
          {selecting && !directPhoto && currentPage && variant === 'processed' && <svg className="crop-overlay"
            viewBox="0 0 1 1" preserveAspectRatio="none" role="img" aria-label="Drag to select receipt region"
            onPointerDown={event => {
              event.preventDefault()
              const bounds = event.currentTarget.getBoundingClientRect()
              cropStart.current = sourcePoint((event.clientX - bounds.left) / bounds.width, (event.clientY - bounds.top) / bounds.height)
              event.currentTarget.setPointerCapture(event.pointerId)
              setCrop(null)
            }} onPointerMove={event => {
              if (!cropStart.current) return
              const bounds = event.currentTarget.getBoundingClientRect()
              const screenX = Math.max(0, Math.min(1, (event.clientX - bounds.left) / bounds.width))
              const screenY = Math.max(0, Math.min(1, (event.clientY - bounds.top) / bounds.height))
              const [x, y] = sourcePoint(screenX, screenY)
              const [startX, startY] = cropStart.current
              setCrop([Math.min(x, startX), Math.min(y, startY), Math.max(x, startX), Math.max(y, startY)])
            }} onPointerUp={() => { cropStart.current = null }}
            onPointerCancel={() => { cropStart.current = null }}>
            <rect x={crop?.[0] || 0} y={crop?.[1] || 0} width={crop ? crop[2] - crop[0] : 0}
              height={crop ? crop[3] - crop[1] : 0} />
          </svg>}
          </div>
        </div> : <div className="image-empty"><FileImage size={36} strokeWidth={1} />
          <p>{pending ? 'Preparing receipt preview…' : 'No preview available. Download the original to inspect it.'}</p></div>}
      </div>
      <div className="page-bar"><button aria-label="Previous page" disabled={page === 0} onClick={() => setPage(page - 1)}><ChevronLeft size={15} /></button>
        <span>Page {receipt.pages.length ? page + 1 : 0} of {receipt.pages.length}</span>
        <button aria-label="Next page" disabled={page + 1 >= receipt.pages.length} onClick={() => setPage(page + 1)}><ChevronRight size={15} /></button></div>
      <div className="reading-actions" ref={setReadingActions}><button disabled={pending || busy || photosBusy} onClick={async () => {
        setBusy(true)
        try {
          await saveBeforeLeave.current?.()
          await post('/receipts/' + id + '/process', { rotation: receipt.rotation })
          await load(); onChanged()
        } catch (e) { onError((e as Error).message) }
        finally { setBusy(false) }
      }}>Run OCR</button></div>
      <details className="ocr-evidence"><summary>Recognized text · {receipt.evidence.lines.length} regions</summary>
        <p>OCR scores describe recognition, not verified accuracy. Select a region to inspect its source.</p>
        {receipt.evidence.lines.map(line => <button key={line.id} className="ocr-line" onClick={() => {
          setPage(line.page); setHighlight([line.id]); setVariant('processed')
        }}><span>{line.text}</span><small>{Math.round(line.score * 100)}% OCR</small></button>)}
        {receipt.evidence.draft && revision.current > 0 && <button onClick={() => {
          if (window.confirm('Replace the current fields with the latest OCR draft? You can undo this edit.')) change(receipt.evidence.draft!)
        }}>Use latest OCR draft</button>}
      </details>
      {receipt.original_mime === 'application/pdf' && receipt.pages.length > 1 && <details className="pdf-groups">
        <summary>Group PDF pages into receipts</summary>
        <p>Assign every page once. Separate receipts with semicolons, for example 1,2;3;4,5.
          The source PDF stays in history and is excluded from new exports.</p>
        <label className="field"><span>Page groups</span><input value={pageGroups} placeholder="1,2;3"
          onChange={e => setPageGroups(e.target.value)} /></label>
        <button type="button" disabled={pending || busy || !pageGroups.trim()} onClick={async () => {
          setBusy(true)
          try {
            await saveBeforeLeave.current?.()
            const groups = pageGroups.split(';').map(group => group.split(',').map(value => Number(value.trim()) - 1))
            if (groups.some(group => group.some(value => !Number.isInteger(value) || value < 0)))
              throw new Error('Use positive page numbers separated by commas and semicolons.')
            const result = await post<{ id: string }>('/receipts/' + id + '/split',
              { revision: revision.current, groups })
            onSplit(result.id)
          } catch (e) { onError((e as Error).message) }
          finally { setBusy(false) }
        }}>Create grouped receipts</button>
      </details>}
      <ReceiptAssistance receipt={receipt} draft={draft} pageId={directPhoto ? undefined : currentPage?.id} crop={crop}
        readingActions={readingActions}
        onSelectRegion={() => { setSelecting(value => !value); setVariant('processed') }}
        saveDraft={async () => { await saveBeforeLeave.current?.() }}
        onReceipt={result => { setReceipt(result); onChanged() }}
        onApplied={result => {
          setReceipt(result); const completed = completeAmounts(result.data); setDraft(completed); draftRef.current = completed
          revision.current = result.revision; saved.current = JSON.stringify(result.data)
          setSaveState('Saved locally'); setAcknowledged(new Set()); setReason(''); onChanged()
        }} onChange={change} onBusy={setBusy} onError={onError} onOpenReceipt={onOpenReceipt} />
    </section>
    <section className="fields-panel" aria-label="Receipt review">
      <div className="panel-title"><h2>Receipt details</h2><span className={'save-state ' + (saveState.startsWith('Save failed') ? 'save-error' : '')} aria-live="polite">
        {saveState === 'Saved locally' ? <Check size={13} /> : <Save size={13} />}{saveState}</span></div>
      <div className="review-intro"><span className={'review-state ' + (receipt.status === 'verified' ? 'is-verified' : '')}>
        {receipt.status === 'verified' ? <ShieldCheck size={16} /> : <FileImage size={16} />}
        {receipt.status === 'verified' ? 'Verified by a person' : pending ? 'Preparing receipt' : 'Ready for your review'}</span>
        <p>{receipt.status === 'verified' ? 'Editing these fields requires verification again.' :
          'Compare each field with the receipt. Select a field to see its source.'}</p></div>
      {receipt.error && <div className="notice warning">{receipt.error} You can enter the values manually.</div>}
      {receipt.duplicates.length > 0 && <div className="notice warning duplicate-notice"><div>
        <strong>This image already exists in your workspace.</strong>
        <p>Check the earlier record before exporting another copy.</p>
        {receipt.duplicates.map(row => <button key={row.id} type="button" onClick={() => onOpenReceipt(row.id)}>
          Open {row.original_name}</button>)}
      </div></div>}
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
      <form inert={busy} onSubmit={e => { e.preventDefault(); void verify() }}>
        <div className="field-grid">{([['receipt_date', 'Purchase date'], ['supplier', 'Vendor'],
          ['receipt_number', 'Receipt number']] as const).map(([key, label]) => {
          const issues = receipt.issues.filter(issue => issue.field === key)
          return <label key={key} className="field">
            <span>{label}</span>
            <input value={key === 'receipt_date' ? displayDate(draft[key]) : draft[key]} maxLength={key === 'receipt_date' ? 10 : 500} data-field={key}
              type="text"
              placeholder={key === 'receipt_date' ? 'MM/DD/YYYY' : key === 'receipt_number' ? 'Vendor reference — leave blank if none' : ''}
              onFocus={() => focus(key)} {...issueProps(issues)}
              onChange={e => change({ ...draft, [key]: key === 'receipt_date' ? inputDate(e.target.value) : e.target.value })} />
            {key === 'receipt_date' && <small>MM/DD/YYYY</small>}
            <InlineIssues issues={issues} />
          </label>
        })}
          <label className="field full-width"><span>Notes</span><textarea value={draft.notes} maxLength={4000}
            placeholder="Anything worth remembering about this receipt"
            onChange={e => change({ ...draft, notes: e.target.value })} /></label>
        </div>
        <div className="items-heading"><h3>Line items</h3>
          <div className="items-totals">
          <label className="total-discount-field"><span>Total Discount (%)</span>
            <input aria-label="Total Discount (%)" inputMode="decimal" maxLength={32} placeholder="Optional"
              value={draft.total_discount_percent || ''} data-field="total_discount_percent" {...issueProps(discountIssues)}
              onChange={e => change({ ...draft, total_discount_percent: e.target.value })} /></label>
          <label className="total-field"><span>Total: {draft.currency === 'PHP' ? '₱' : draft.currency}</span>
            <input aria-label="Total" inputMode="decimal" value={draft.total} data-field="total" onFocus={() => focus('total')}
              {...issueProps(totalIssues)}
              onChange={e => change({ ...draft, total: e.target.value })} /></label></div></div>
        <p className="discount-help">Blank line discounts follow Total Discount; enter 0 to exclude a line. Unit price is before discount.
          CSV uses discounted prices and amounts, without discount columns.</p>
        <InlineIssues issues={discountIssues} />
        <InlineIssues issues={totalIssues} />
        {draft.items.map((item, index) => {
          // Issues describe the saved order. Keep them on the same item during row edits.
          const savedIndex = item.id ? receipt.data.items.findIndex(savedItem => savedItem.id === item.id) : index
          const prefix = 'items.' + savedIndex + '.'
          const itemIssues = receipt.issues.filter(issue => issue.field.startsWith(prefix))
          const fieldIssues = (key: string) => itemIssues.filter(issue => issue.field === prefix + key ||
            (issue.field === prefix + 'arithmetic' && ['quantity', 'unit_price', 'discount_percent', 'line_total'].includes(key)) ||
            (issue.field === prefix + 'pricing' && ['quantity', 'unit_price'].includes(key)))
          return <fieldset key={item.id || index} disabled={busy || photosBusy}
            {...issueProps(itemIssues)} className={'item-fields ' + issueProps(itemIssues).className}>
          <legend>Line {index + 1}{item.item_number ? ' · Item ' + item.item_number : ''}
            {itemIssues.length > 0 && <span className="item-review-label"> · {itemIssues.some(issue => issue.blocking)
              ? 'Fix before verifying' : 'Check this line'}</span>}</legend>
          <div className="item-actions" role="group" aria-label={'Line ' + (index + 1) + ' actions'}>
            <button type="button" className="icon-button" title="Move line up" aria-label={'Move item ' + (index + 1) + ' up'}
              disabled={busy || photosBusy || index === 0} onClick={() => moveItem(index, -1)}><ArrowUp size={15} /></button>
            <button type="button" className="icon-button" title="Move line down" aria-label={'Move item ' + (index + 1) + ' down'}
              disabled={busy || photosBusy || index === draft.items.length - 1} onClick={() => moveItem(index, 1)}><ArrowDown size={15} /></button>
            <button type="button" className="icon-button" title="Add line below" aria-label={'Add line below item ' + (index + 1)}
              disabled={busy || photosBusy} onClick={() => insertItem(index + 1)}><Plus size={15} /></button>
            <button type="button" className="icon-button remove-item" title="Remove line" aria-label={'Remove item ' + (index + 1)}
              disabled={busy || photosBusy} onClick={() => change({ ...draft, items: draft.items.filter((_, i) => i !== index) })}><Trash2 size={15} /></button>
          </div>
          <InlineIssues issues={itemIssues} />
          <label className="item-description"><span>Description</span><input aria-label={'Item ' + (index + 1) + ' description'}
            {...issueProps(fieldIssues('description'))}
            value={item.description} placeholder="Description" data-field={'items.' + index + '.description'} onFocus={() => focus(prefix + 'description')}
            onChange={e => change({ ...draft, items: draft.items.map((value, i) => i === index ? { ...value, description: e.target.value } : value) })} /></label>
          <label className="item-discount"><span>Discount (%)</span>
            <input aria-label={'Item ' + (index + 1) + ' Discount (%)'} inputMode="decimal" maxLength={32} placeholder="Optional"
              data-field={'items.' + index + '.discount_percent'} value={effectiveDiscount(item, draft)}
              {...issueProps(fieldIssues('discount_percent'))}
              onChange={e => change({ ...draft, items: draft.items.map((value, i) => i === index
                ? { ...value, discount_percent: e.target.value } : value) })} />
            {!item.discount_percent && draft.total_discount_percent && <small>From total</small>}
          </label>
          {(['quantity', 'unit', 'unit_price', 'line_total', 'item_type'] as const).map((key, position) => <label key={key}><span>{['Quantity', 'Unit', 'Unit price', 'Amount', 'Type'][position]}</span>
            {key === 'item_type' ? <select aria-label={'Item ' + (index + 1) + ' Type'}
              {...issueProps(fieldIssues(key))}
              data-field={'items.' + index + '.item_type'} value={item.item_type}
              onChange={e => change({ ...draft, items: draft.items.map((value, i) =>
                i === index ? { ...value, item_type: e.target.value } : value) })}>
              {!ITEM_TYPES.includes(item.item_type) && <option value={item.item_type}>{item.item_type}</option>}
              {ITEM_TYPES.map(type => <option key={type} value={type}>{type}</option>)}
            </select> : <input aria-label={'Item ' + (index + 1) + ' ' + ['Quantity', 'Unit', 'Unit price', 'Amount', 'Type'][position]}
              {...issueProps(fieldIssues(key))}
              inputMode={key === 'quantity' ? 'numeric' : ['unit', 'item_type'].includes(key) ? 'text' : 'decimal'}
              pattern={key === 'quantity' ? '-?[0-9]*' : undefined}
              data-field={'items.' + index + '.' + key}
              value={item[key]} placeholder={key === 'unit' ? 'bag, pc, m...' : ''}
              onFocus={() => focus(prefix + key)}
              onChange={e => {
                if (key === 'quantity' && !/^-?\d*$/.test(e.target.value)) return
                const next = { ...item, [key]: e.target.value }
                change({ ...draft, items: draft.items.map((value, i) => i === index ? next : value) })
              }} />}</label>)}
          <PhotoField receiptId={id} photoId={item.photo_id} label="Photo of item purchased" onBusy={photoBusy} onError={onError}
            onChange={photoId => change({ ...draftRef.current!, items: draftRef.current!.items.map(value =>
              value.id === item.id ? { ...value, photo_id: photoId } : value) })} />
        </fieldset>})}
        <button type="button" className="quiet" disabled={busy || photosBusy} onClick={() => insertItem(draft.items.length)}><Plus size={14} /> Add line</button>
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
        <div className="edit-actions"><button type="button" className="quiet" disabled={busy || photosBusy || !undo.current.length} onClick={() => {
          const previous = undo.current.pop()
          if (previous) {
            draftRef.current = previous; setDraft(previous); setSaveState('Unsaved changes')
            setAcknowledged(new Set()); setReason('')
          }
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
          <label className="acknowledgment"><input type="checkbox" checked={allowRetrieval} onChange={async e => {
            const allowed = e.target.checked
            setAllowRetrieval(allowed)
            if (receipt.status !== 'verified') return
            try {
              const result = await post<Receipt>('/receipts/' + id + '/review-options', {
                revision: revision.current, assignee: receipt.assignee, second_review: false, retrieval_allowed: allowed,
              })
              setReceipt(result)
            } catch (e) { setAllowRetrieval(!allowed); onError((e as Error).message) }
          }} /><span>Use this verified receipt and its corrections for future local suggestions.</span></label>
          <p>Verify only after checking the image and resolving the flagged fields.</p>
          <div className="verify-actions"><button className="primary" type="submit" disabled={busy || photosBusy || pending || receipt.status === 'verified'}>
            {busy ? <LoaderCircle className="spin" size={16} /> : <Check size={17} />}
            {receipt.status === 'verified' ? 'Verified' : 'Verify receipt'}</button>
            <button type="button" onClick={async () => {
              try { await saveBeforeLeave.current?.(); onNext() } catch (e) { onError((e as Error).message) }
            }}>Next receipt <ChevronRight size={16} /></button></div></div>
      </form>
      <p className="keyboard-help">Ctrl+S saves · Alt+Right opens next receipt · Alt+Down selects next flagged field</p>
      <details className="history" onToggle={e => {
        if (e.currentTarget.open) void api<typeof history>('/receipts/' + id + '/history').then(setHistory).catch((e: Error) => onError(e.message))
      }}><summary>Review history</summary>{history.map((event, index) => <p key={index}>Revision {event.revision} · {event.action}
        {event.reviewer ? ' · ' + event.reviewer : ''}<small>{new Date(event.created_at).toLocaleString()}</small></p>)}</details>
    </section>
  </>
}
