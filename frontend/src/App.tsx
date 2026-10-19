import { useCallback, useEffect, useRef, useState } from 'react'
import { ArrowDownToLine, Check, CheckCheck, ChevronRight, FileImage, FileText, FolderOpen,
  LoaderCircle, Pause, Plus, ReceiptText, RefreshCw, ShieldCheck, Upload, X } from 'lucide-react'
import { api, connect, download, post } from './api'
import Review from './Review'
import type { Batch, Summary } from './types'

export const statusNames: Record<string, string> = {
  queued: 'Queued', processing: 'Reading', needs_review: 'Needs review', verified: 'Verified',
  failed: 'Failed', cancelled: 'Cancelled',
}

export default function App() {
  const [ready, setReady] = useState(false)
  const [modelsReady, setModelsReady] = useState(true)
  const [batches, setBatches] = useState<Batch[]>([])
  const [batchId, setBatchId] = useState('')
  const [batchInfo, setBatchInfo] = useState<Batch | null>(null)
  const [receipts, setReceipts] = useState<Summary[]>([])
  const [selected, setSelected] = useState('')
  const [filter, setFilter] = useState('all')
  const [error, setError] = useState('')
  const [uploading, setUploading] = useState(false)
  const [uploadProgress, setUploadProgress] = useState({ done: 0, total: 0 })
  const [exportIds, setExportIds] = useState<Set<string>>(new Set())
  const [preview, setPreview] = useState<{ columns: string[]; rows: Record<string, string>[]; row_count: number } | null>(null)
  const [exporting, setExporting] = useState(false)
  const [dragging, setDragging] = useState(false)
  const [queueOpen, setQueueOpen] = useState(true)
  const input = useRef<HTMLInputElement>(null)
  const resumeBatch = useRef<Batch | null>(null)
  const saveBeforeLeave = useRef<(() => Promise<void>) | null>(null)
  const dialog = useRef<HTMLDialogElement>(null)
  const activeBatch = useRef(batchId)
  activeBatch.current = batchId

  useEffect(() => {
    if (preview && dialog.current && !dialog.current.open) dialog.current.showModal()
  }, [preview])

  useEffect(() => {
    connect().then(() => setReady(true)).catch((e: Error) => setError(e.message))
  }, [])

  const refresh = useCallback(async () => {
    const [allBatches, rows, health] = await Promise.all([
      api<Batch[]>('/batches'), api<Summary[]>('/receipts?limit=500' + (batchId ? '&batch_id=' + batchId : '')),
      api<{ ocr_models_ready: boolean }>('/health'),
    ])
    if (activeBatch.current !== batchId) return
    setBatches(allBatches)
    setReceipts(rows)
    setModelsReady(health.ocr_models_ready)
    const details = batchId ? await api<Batch>('/batches/' + batchId) : null
    if (activeBatch.current === batchId) setBatchInfo(details)
    const eligible = new Set(rows.filter(r => r.status === 'verified' && r.revision === r.verified_revision).map(r => r.id))
    setExportIds(current => new Set([...current].filter(id => eligible.has(id))))
  }, [batchId])

  useEffect(() => {
    if (!ready) return
    let alive = true
    const load = () => refresh().catch((e: Error) => { if (alive) setError(e.message) })
    void load()
    const interval = setInterval(() => void load(), 2500)
    return () => { alive = false; clearInterval(interval) }
  }, [ready, refresh])

  async function chooseReceipt(id: string) {
    try {
      await saveBeforeLeave.current?.()
      setSelected(id)
      setError('')
    } catch (e) { setError((e as Error).message) }
  }

  async function chooseBatch(id: string) {
    try {
      await saveBeforeLeave.current?.()
      setSelected('')
      setBatchId(id)
      setExportIds(new Set())
    } catch (e) { setError((e as Error).message) }
  }

  async function uploadFiles(files: File[]) {
    if (!files.length || uploading || !ready) return
    setUploading(true)
    setError('')
    setUploadProgress({ done: 0, total: files.length })
    try {
      await saveBeforeLeave.current?.()
      const previous = resumeBatch.current
      resumeBatch.current = null
      const batch = previous ? await api<Batch>('/batches/' + previous.id) :
        await post<Batch>('/batches', { name: new Date().toLocaleString(undefined,
          { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }), files: files.map(f => f.name) })
      setBatchId(batch.id)
      setSelected('')
      const entries = [...(batch.files || [])]
      const work = files.map(file => {
        const index = entries.findIndex(entry => entry.name === file.name && entry.state !== 'accepted' && entry.state !== 'deleted')
        return { file, entry: index >= 0 ? entries.splice(index, 1)[0] : null }
      })
      let cursor = 0
      let failures = 0
      async function transfer() {
        while (cursor < work.length) {
          const { file, entry } = work[cursor++]
          try {
            if (!entry) throw new Error('File is not an unfinished entry in this batch.')
            const form = new FormData()
            form.append('file', file)
            await api('/receipts?batch_id=' + batch.id + '&upload_id=' + entry.id, { method: 'POST', body: form })
          } catch { failures++ }
          setUploadProgress(current => ({ ...current, done: current.done + 1 }))
        }
      }
      await Promise.all([transfer(), transfer()])
      if (failures) setError(failures + ' file(s) could not upload. Check batch details and reselect failed files.')
      setBatchInfo(await api<Batch>('/batches/' + batch.id))
    } catch (e) { setError((e as Error).message) }
    finally {
      setUploading(false)
      if (input.current) input.current.value = ''
      await refresh().catch(() => {})
    }
  }

  const counts = receipts.reduce<Record<string, number>>((result, receipt) => {
    result[receipt.status] = (result[receipt.status] || 0) + 1
    return result
  }, {})
  const verified = receipts.filter(r => r.status === 'verified')
  const visible = receipts.filter(r => filter === 'all' || r.status === filter)
  const failures = batchInfo?.files?.filter(f => ['failed', 'pending', 'uploading'].includes(f.state)) || []

  async function batchAction(action: string) {
    try { await post('/batches/' + batchId + '/' + action); await refresh() }
    catch (e) { setError((e as Error).message) }
  }

  async function previewExport() {
    try {
      await saveBeforeLeave.current?.()
      setPreview(await post('/exports/preview', { receipt_ids: [...exportIds] }))
    } catch (e) { setError((e as Error).message) }
  }

  async function exportCsv() {
    setExporting(true)
    try {
      const result = await post<{ id: string }>('/exports', { receipt_ids: [...exportIds] })
      await download('/exports/' + result.id + '/download', 'receiptiq-' + result.id.slice(0, 8) + '.csv')
      setPreview(null)
    } catch (e) { setError((e as Error).message) }
    finally { setExporting(false) }
  }

  return <div className="app" onDragOver={e => { e.preventDefault(); if (!uploading) setDragging(true) }}
    onDragLeave={e => { if (!e.currentTarget.contains(e.relatedTarget as Node)) setDragging(false) }}
    onDrop={e => { e.preventDefault(); setDragging(false); void uploadFiles([...e.dataTransfer.files]) }}>
    <header className="app-header">
      <a className="brand" href="/" aria-label="ReceiptIQ home"><span className="brand-mark"><ReceiptText size={24} /></span>
        Receipt<span>IQ</span></a>
      <span className="header-divider" />
      <span className="workspace-name">Receipt workspace</span>
      <span className="local-label"><ShieldCheck size={16} /> On this computer</span>
    </header>
    <main>
      <div className="workspace-heading">
        <div><h1>A little less data entry.</h1><p>Upload a batch. Check the details. Get on with your day.</p></div>
        <button className="primary" disabled={!ready || uploading} onClick={() => { resumeBatch.current = null; input.current?.click() }}>
          {uploading ? <LoaderCircle className="spin" size={17} /> : <Plus size={18} />}
          {uploading ? 'Uploading ' + uploadProgress.done + ' / ' + uploadProgress.total : 'Upload receipts'}
        </button>
        <input ref={input} type="file" multiple accept="image/jpeg,image/png,application/pdf"
          aria-label="Choose receipt images" className="visually-hidden"
          onChange={e => void uploadFiles([...e.target.files || []])} />
      </div>
      {error && <div role="alert" className="notice error"><span>{error}</span>
        <button aria-label="Dismiss error" onClick={() => setError('')}><X size={16} /></button></div>}
      {!modelsReady && <div className="notice warning"><FileText size={18} />
        <span>OCR models aren’t installed. Run <code>scripts/setup.ps1</code>, then retry failed receipts.
          Manual entry remains available.</span></div>}
      <section className="batch-bar" aria-label="Batch controls">
        <FolderOpen size={19} />
        <label className="visually-hidden" htmlFor="batch">Current batch</label>
        <select id="batch" value={batchId} onChange={e => void chooseBatch(e.target.value)}>
          <option value="">All receipts</option>{batches.map(b => <option key={b.id} value={b.id}>{b.name} · {b.file_count} files</option>)}
        </select>
        <span className="batch-count">{receipts.length} receipts</span>
        <div className="batch-states" aria-live="polite">
          <span><LoaderCircle size={14} /> {(counts.queued || 0) + (counts.processing || 0)} in queue</span>
          <span><FileText size={14} /> {counts.needs_review || 0} to review</span>
          <span className="verified-count"><CheckCheck size={15} /> {counts.verified || 0} verified</span>
        </div>
        {batchId && <div className="batch-actions">
          <button className="quiet" onClick={() => void batchAction('cancel')} title="Cancel unfinished processing"><Pause size={15} /> Cancel pending</button>
          <button className="quiet" onClick={() => void batchAction('retry-failed')}><RefreshCw size={14} /> Retry failed</button>
        </div>}
      </section>
      {failures.length > 0 && <details className="upload-problems"><summary>{failures.length} unfinished upload(s) · view details</summary>
        <p>Accepted images are saved. Reselect untransferred files to resume; accepted files won’t be uploaded again.</p>
        <ul>{failures.map(f => <li key={f.id}>{f.name}: {f.error || 'Waiting for upload'}</li>)}</ul>
        <button onClick={() => { resumeBatch.current = batchInfo; input.current?.click() }}>Reselect unfinished files</button>
      </details>}
      {!receipts.length && ready && !uploading ? <section className="empty-workspace">
        <div className="empty-illustration"><FileImage size={58} strokeWidth={1} /><Check size={22} /></div>
        <h2>Your receipts, ready for a second look.</h2>
        <p>Drop a whole batch here, or choose images from your computer.<br />You can review the first receipt while the rest are reading.</p>
        <button className="primary" onClick={() => input.current?.click()}><Upload size={17} /> Choose receipt images</button>
        <span>JPG, PNG, or PDF · Up to 25 MB per file · One receipt per image</span>
      </section> : <div className="workbench">
        <aside className={'queue-panel ' + (queueOpen ? '' : 'collapsed')}>
          <div className="panel-title"><h2>Receipts <span>{receipts.length}</span></h2>
            <button className="queue-toggle quiet" onClick={() => setQueueOpen(!queueOpen)}>{queueOpen ? 'Hide' : 'Show'}</button></div>
          <div className="queue-content">
            <label className="visually-hidden" htmlFor="status-filter">Filter receipt status</label>
            <select id="status-filter" value={filter} onChange={e => setFilter(e.target.value)} className="filter">
              <option value="all">All statuses</option>
              {Object.entries(statusNames).map(([value, name]) => <option key={value} value={value}>{name}</option>)}
            </select>
            <div className="queue-list">
              {visible.map((receipt, index) => {
                const data = JSON.parse(receipt.data_json) as { supplier: string; total: string; currency: string }
                return <div key={receipt.id} className={'queue-row ' + (selected === receipt.id ? 'active' : '')}>
                  {receipt.status === 'verified' ? <input type="checkbox" aria-label={'Select ' + receipt.original_name + ' for export'}
                    checked={exportIds.has(receipt.id)} onChange={e => setExportIds(current => {
                      const next = new Set(current); if (e.target.checked) next.add(receipt.id); else next.delete(receipt.id); return next
                    })} /> : <span className="row-index">{String(index + 1).padStart(2, '0')}</span>}
                  <button className="receipt-select" onClick={() => void chooseReceipt(receipt.id)}>
                    <span className="receipt-name">{data.supplier || receipt.original_name}</span>
                    <span className="receipt-file">{receipt.original_name}</span>
                    <span className={'status status-' + receipt.status}>
                      {receipt.status === 'processing' ? <LoaderCircle size={12} className="spin" /> :
                        receipt.status === 'verified' ? <Check size={12} /> : <span className="status-dot" />}
                      {statusNames[receipt.status] || receipt.status}
                    </span>
                  </button>
                  <ChevronRight size={14} className="row-chevron" />
                </div>
              })}
              {!visible.length && <p className="queue-empty">No receipts with this status.</p>}
            </div>
            <div className="queue-footer"><ShieldCheck size={14} /> Originals and edits stay local.</div>
          </div>
        </aside>
        {selected ? <Review key={selected} id={selected} saveBeforeLeave={saveBeforeLeave}
          onChanged={() => void refresh()} onError={setError}
          onNext={() => {
            const next = receipts.find(r => r.id !== selected && ['needs_review', 'failed', 'cancelled'].includes(r.status))
            if (next) void chooseReceipt(next.id)
          }} /> : <section className="review-placeholder"><ReceiptText size={44} strokeWidth={1} />
          <h2>Make room for the details.</h2><p>Select a receipt to compare the image with its extracted fields.</p>
          {visible[0] && <button onClick={() => void chooseReceipt(visible[0].id)}>Open first receipt <ChevronRight size={16} /></button>}
        </section>}
      </div>}
      <footer className="export-bar">
        <div><span className="export-icon"><ArrowDownToLine size={19} /></span>
          <div><strong>{exportIds.size} selected for export</strong><p>{receipts.length - verified.length} unfinished or unverified receipts excluded.</p></div></div>
        <div className="export-actions">
          <button className="quiet" disabled={!verified.length} onClick={() => setExportIds(new Set(verified.map(r => r.id)))}>Select all verified</button>
          <button className="primary" disabled={!exportIds.size || exporting} onClick={() => void previewExport()}>
            <ArrowDownToLine size={16} /> Preview CSV</button>
        </div>
      </footer>
    </main>
    {dragging && <div className="drop-overlay"><Upload size={40} /><h2>Drop your receipt images</h2><p>A batch can contain tens of images.</p></div>}
    {preview && <dialog ref={dialog} className="export-dialog" aria-labelledby="export-title" onCancel={() => setPreview(null)}>
      <div className="panel-title"><h2 id="export-title">Check your CSV</h2><button aria-label="Close preview" onClick={() => setPreview(null)}><X size={20} /></button></div>
      <p>{exportIds.size} verified receipts · {preview.row_count} rows · Receipt line items template</p>
      <p className="muted">Receipt-level totals repeat on each item row. Group by receipt ID before summing totals.</p>
      <div className="csv-scroll"><table><thead><tr>{preview.columns.map(c => <th key={c}>{c}</th>)}</tr></thead>
        <tbody>{preview.rows.map((row, i) => <tr key={i}>{preview.columns.map(c => <td key={c}>{row[c]}</td>)}</tr>)}</tbody></table></div>
      {preview.row_count > preview.rows.length && <p>Showing first {preview.rows.length} rows. Download includes all rows.</p>}
      <div className="dialog-actions"><button onClick={() => setPreview(null)}>Back to review</button>
        <button className="primary" disabled={exporting} onClick={() => void exportCsv()}><ArrowDownToLine size={16} /> Download CSV</button></div>
    </dialog>}
  </div>
}
