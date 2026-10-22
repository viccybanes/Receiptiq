import { useCallback, useEffect, useRef, useState } from 'react'
import { ArrowDownToLine, Check, CheckCheck, ChevronRight, FileImage, FileText, FolderOpen,
  LoaderCircle, Pause, Plus, ReceiptText, RefreshCw, Settings, ShieldCheck, Upload, X } from 'lucide-react'
import { api, connect, download, post } from './api'
import { localDay, receiptDays } from './receiptDays'
import Review from './Review'
import type { Batch, Summary } from './types'
import WorkspaceSettings from './WorkspaceSettings'
import type { Template } from './TemplatesEditor'

export const statusNames: Record<string, string> = {
  queued: 'Queued', processing: 'Reading', needs_review: 'Needs review', verified: 'Verified',
  failed: 'Failed', cancelled: 'Cancelled', split: 'Split into receipts',
}

export default function App() {
  const [ready, setReady] = useState(false)
  const [modelsReady, setModelsReady] = useState(true)
  const [batches, setBatches] = useState<Batch[]>([])
  const [batchId, setBatchId] = useState('')
  const [selectedDay, setSelectedDay] = useState('')
  const [batchInfo, setBatchInfo] = useState<Batch | null>(null)
  const [receipts, setReceipts] = useState<Summary[]>([])
  const [selected, setSelected] = useState('')
  const [filter, setFilter] = useState('all')
  const [assigneeFilter, setAssigneeFilter] = useState('')
  const [priority, setPriority] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [templates, setTemplates] = useState<Template[]>([])
  const [templateId, setTemplateId] = useState('default')
  const [error, setError] = useState('')
  const [uploading, setUploading] = useState(false)
  const [pendingUploads, setPendingUploads] = useState<{ id: number; name: string }[]>([])
  const [uploadProgress, setUploadProgress] = useState({ done: 0, total: 0 })
  const [exportIds, setExportIds] = useState<Set<string>>(new Set())
  const [preview, setPreview] = useState<{ columns: string[]; rows: Record<string, string>[]; row_count: number; template: Template } | null>(null)
  const [exporting, setExporting] = useState(false)
  const [exportError, setExportError] = useState('')
  const [dragging, setDragging] = useState(false)
  const [queueOpen, setQueueOpen] = useState(true)
  const input = useRef<HTMLInputElement>(null)
  const importInput = useRef<HTMLInputElement>(null)
  const [importMessage, setImportMessage] = useState('')
  const resumeBatch = useRef<Batch | null>(null)
  const saveBeforeLeave = useRef<(() => Promise<void>) | null>(null)
  const dialog = useRef<HTMLDialogElement>(null)
  const activeBatch = useRef(batchId + selectedDay)
  activeBatch.current = batchId + selectedDay

  useEffect(() => {
    if (preview && dialog.current && !dialog.current.open) dialog.current.showModal()
  }, [preview])

  useEffect(() => {
    connect().then(() => setReady(true)).catch((e: Error) => setError(e.message))
  }, [])
  const refreshTemplates = useCallback(async () => {
    const rows = await api<Template[]>('/templates')
    setTemplates(rows)
    setTemplateId(current => rows.some(row => row.id === current) ? current : 'default')
  }, [])
  useEffect(() => {
    if (ready) void refreshTemplates().catch((e: Error) => setError(e.message))
  }, [ready, refreshTemplates])

  const refresh = useCallback(async () => {
    const [allBatches, health] = await Promise.all([
      api<Batch[]>('/batches'),
      api<{ ocr_models_ready: boolean }>('/health'),
    ])
    async function readRows(id = '') {
      const rows: Summary[] = []
      for (let offset = 0; ; offset += 500) {
        const page = await api<Summary[]>('/receipts?limit=500&offset=' + offset + (id ? '&batch_id=' + id : ''))
        rows.push(...page)
        if (page.length < 500) return rows
      }
    }
    const rows: Summary[] = []
    if (selectedDay) {
      for (const batch of allBatches.filter(batch => localDay(batch.created_at) === selectedDay)) {
        rows.push(...await readRows(batch.id))
      }
    } else rows.push(...await readRows())
    if (activeBatch.current !== batchId + selectedDay) return
    setBatches(allBatches)
    setReceipts(rows)
    setModelsReady(health.ocr_models_ready)
    const details = batchId ? await api<Batch>('/batches/' + batchId) : null
    if (activeBatch.current === batchId + selectedDay) setBatchInfo(details)
    const eligible = new Set(rows.filter(r => r.status === 'verified' && r.revision === r.verified_revision).map(r => r.id))
    setExportIds(current => new Set([...current].filter(id => eligible.has(id))))
  }, [batchId, selectedDay])

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
      setSelectedDay(id)
      setBatchId('')
      setExportIds(new Set())
    } catch (e) { setError((e as Error).message) }
  }

  async function uploadFiles(files: File[]) {
    if (!files.length || uploading || !ready) return
    setUploading(true)
    setError('')
    setUploadProgress({ done: 0, total: files.length })
    setPendingUploads(files.map((file, id) => ({ id, name: file.name })))
    try {
      await saveBeforeLeave.current?.()
      const previous = resumeBatch.current
      resumeBatch.current = null
      const batch = previous ? await api<Batch>('/batches/' + previous.id) :
        await post<Batch>('/batches', { name: new Date().toLocaleString(undefined,
          { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }), files: files.map(f => f.name) })
      setBatchId(batch.id)

      const entries = [...(batch.files || [])]
      const work = files.map((file, indexInUpload) => {
        const index = entries.findIndex(entry => entry.name === file.name && entry.state !== 'accepted' && entry.state !== 'deleted')
        return { file, indexInUpload, entry: index >= 0 ? entries.splice(index, 1)[0] : null }
      })
      let cursor = 0
      let failures = 0
      async function transfer() {
        while (cursor < work.length) {
          const { file, entry, indexInUpload } = work[cursor++]
          try {
            if (!entry) throw new Error('File is not an unfinished entry in this batch.')
            const form = new FormData()
            form.append('file', file)
            await api('/receipts?batch_id=' + batch.id + '&upload_id=' + entry.id, { method: 'POST', body: form })
          } catch { failures++ }
          setPendingUploads(current => current.filter(entry => entry.id !== indexInUpload))
          await refresh().catch(() => {})
          setUploadProgress(current => ({ ...current, done: current.done + 1 }))
        }
      }
      await Promise.all([transfer(), transfer()])
      if (failures) setError(failures + ' file(s) could not upload. Check batch details and reselect failed files.')
      setBatchInfo(await api<Batch>('/batches/' + batch.id))
    } catch (e) { setError((e as Error).message) }
    finally {
      setUploading(false)
      setPendingUploads([])
      if (input.current) input.current.value = ''
      await refresh().catch(() => {})
    }
  }

  async function importFiles(files: File[]) {
    if (!files.length || uploading || !ready) return
    setUploading(true); setError(''); setImportMessage('')
    try {
      await saveBeforeLeave.current?.()
      const documents = files.filter(file => /\.(csv|zip)$/i.test(file.name))
      if (documents.length !== 1) throw new Error('Choose one CSV or ZIP, plus any photos referenced by the CSV.')
      const form = new FormData(); form.append('file', documents[0])
      files.filter(file => file !== documents[0]).forEach(file => form.append('photos', file))
      const result = await api<{ id: string; receipt_count: number; warnings: string[] }>('/imports', { method: 'POST', body: form })
      setSelected(''); setBatchId(result.id); setSelectedDay(localDay(new Date().toISOString())); setExportIds(new Set())
      setImportMessage(result.receipt_count + ' receipts imported for review. ' +
        (result.warnings.length ? result.warnings.length + ' missing photo(s); details saved in Notes.' : ''))
    } catch (e) { setError((e as Error).message) }
    finally {
      setUploading(false)
      if (importInput.current) importInput.current.value = ''
    }
  }

  const counts = receipts.reduce<Record<string, number>>((result, receipt) => {
    result[receipt.status] = (result[receipt.status] || 0) + 1
    return result
  }, {})
  const verified = receipts.filter(r => r.status === 'verified')
  const visible = receipts.filter(r => (filter === 'all' || (filter === 'second_review' ? r.second_review : r.status === filter))
    && (!assigneeFilter || (assigneeFilter === '__unassigned__' ? !r.assignee : r.assignee === assigneeFilter)))
  if (priority) visible.sort((left, right) => {
    const rank = (row: Summary) => (row.second_review ? 8 : 0) + (row.status === 'failed' ? 6 : 0) +
      (row.status === 'needs_review' ? 3 : 0) + (row.error ? 2 : 0)
    return rank(right) - rank(left)
  })
  const failures = batchInfo?.files?.filter(f => ['failed', 'pending', 'uploading'].includes(f.state)) || []

  async function batchAction(action: string) {
    try { await post('/batches/' + batchId + '/' + action); await refresh() }
    catch (e) { setError((e as Error).message) }
  }

  async function previewExport() {
    try {
      await saveBeforeLeave.current?.()
      setExportError('')
      setPreview(await post('/exports/preview', { receipt_ids: [...exportIds], template_id: templateId }))
    } catch (e) { setError((e as Error).message) }
  }

  async function exportCsv(includePhotos = false) {
    setExporting(true)
    setExportError('')
    try {
      const result = await post<{ id: string }>('/exports', { receipt_ids: [...exportIds], include_photos: includePhotos,
        template_id: preview?.template.id || templateId, template_version: preview?.template.version })
      await download('/exports/' + result.id + '/download', 'receiptiq-' + result.id.slice(0, 8) + (includePhotos ? '.zip' : '.csv'))
      setPreview(null)
    } catch (e) { setExportError((e as Error).message) }
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
      <button className="quiet" disabled={!ready} onClick={async () => {
        try { await saveBeforeLeave.current?.(); setSettingsOpen(true) } catch (e) { setError((e as Error).message) }
      }}><Settings size={16} /> Settings</button>
    </header>
    <main>
      <div className="workspace-heading">
        <div><h1>Add expense</h1><p>Record a receipt with one or more line items. Each line gets its own item number on save.</p></div>
        <button disabled={!ready || uploading} onClick={() => importInput.current?.click()}>Import CSV + photos</button>
        <input ref={importInput} type="file" multiple accept=".csv,.zip,image/jpeg,image/png,application/pdf"
          className="visually-hidden" aria-label="Choose CSV or ZIP and optional photos"
          onChange={e => void importFiles([...e.target.files || []])} />
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
      {importMessage && <div className="notice" role="status">{importMessage}</div>}
      <p className="import-help">Import one CSV with its referenced photos selected together, or one ZIP containing both.
        CSV alone stores image filenames; CSV + photos export includes the files.</p>
      {!modelsReady && <div className="notice warning"><FileText size={18} />
        <span>OCR models aren’t installed. Open Settings → Local models to finish setup, then retry failed receipts.
          Manual entry remains available.</span></div>}
      <section className="batch-bar" aria-label="Batch controls">
        <FolderOpen size={19} />
        <label className="visually-hidden" htmlFor="batch">Upload day</label>
        <select id="batch" value={selectedDay} onChange={e => void chooseBatch(e.target.value)}>
          <option value="">All receipts</option>{receiptDays(batches).map(({ day, files }) =>
            <option key={day} value={day}>{new Date(day + 'T12:00:00').toLocaleDateString(undefined,
              { month: 'short', day: 'numeric', year: 'numeric' })} · {files} files</option>)}
        </select>
        <span className="batch-count">{receipts.length} receipts</span>
        <div className="batch-states" aria-live="polite">
          <span><LoaderCircle size={14} /> {(counts.queued || 0) + (counts.processing || 0)} in queue</span>
          <span><FileText size={14} /> {counts.needs_review || 0} to review</span>
          <span className="verified-count"><CheckCheck size={15} /> {counts.verified || 0} verified</span>
        </div>
        {batchId && <div className="batch-actions">
          <span>Latest upload:</span>
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
              <option value="second_review">Needs second review</option>
              {Object.entries(statusNames).map(([value, name]) => <option key={value} value={value}>{name}</option>)}
            </select>
            <label className="visually-hidden" htmlFor="assignee-filter">Filter by reviewer</label>
            <select id="assignee-filter" className="filter assignee-filter" value={assigneeFilter} onChange={e => setAssigneeFilter(e.target.value)}>
              <option value="">All reviewers</option><option value="__unassigned__">Unassigned</option>
              {[...new Set(receipts.map(row => row.assignee).filter(Boolean))].sort().map(name => <option key={name}>{name}</option>)}
            </select>
            <label className="priority-filter"><input type="checkbox" checked={priority} onChange={e => setPriority(e.target.checked)} /> Review priority first</label>
            <div className="queue-list">
              {pendingUploads.map(file => <div className="queue-row" key={'upload-' + file.id} role="status">
                <LoaderCircle size={18} className="spin" /><div className="receipt-select">
                  <span className="receipt-name">{file.name}</span><span className="receipt-file">Uploading...</span>
                </div></div>)}
              {visible.map((receipt, index) => {
                const data = JSON.parse(receipt.data_json) as { supplier: string; total: string; currency: string }
                return <div key={receipt.id} className={'queue-row ' + (selected === receipt.id ? 'active' : '')}>
                  {receipt.status === 'verified' ? <input type="checkbox" aria-label={'Select ' + receipt.original_name + ' for export'}
                    checked={exportIds.has(receipt.id)} onChange={e => setExportIds(current => {
                      const next = new Set(current); if (e.target.checked) next.add(receipt.id); else next.delete(receipt.id); return next
                    })} /> : <span className="row-index">{String(index + 1).padStart(2, '0')}</span>}
                  <button className="receipt-select" data-receipt-id={receipt.id} onClick={() => void chooseReceipt(receipt.id)}>
                    <span className="receipt-name">{data.supplier || receipt.original_name}</span>
                    <span className="receipt-file">{receipt.original_name}</span>
                    {receipt.assignee && <span className="receipt-file">Assigned to {receipt.assignee}</span>}
                    {!!receipt.second_review && <span className="receipt-file">Needs second review</span>}
                    {receipt.duplicate_count > 0 && <span className="receipt-file">Duplicate image exists</span>}
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
          onDeleted={() => {
            setReceipts(current => current.filter(receipt => receipt.id !== selected))
            setExportIds(current => { const next = new Set(current); next.delete(selected); return next })
            setSelected('')
            void refresh()
          }}
          onOpenReceipt={id => void chooseReceipt(id)}
            onSplit={id => { setSelected(''); setBatchId(id); setSelectedDay(localDay(new Date().toISOString())); setExportIds(new Set()) }}
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
          <label className="csv-format-picker"><span>CSV format</span><select aria-label="CSV format" value={templateId}
            onChange={e => setTemplateId(e.target.value)}>{templates.map(template => <option key={template.id}
              value={template.id}>{template.name}</option>)}</select></label>
          <button className="quiet" disabled={!verified.length} onClick={() => setExportIds(new Set(verified.map(r => r.id)))}>Select all verified</button>
          <button className="primary" disabled={!exportIds.size || exporting} onClick={() => void previewExport()}>
            <ArrowDownToLine size={16} /> Preview CSV</button>
        </div>
      </footer>
    </main>
    {dragging && <div className="drop-overlay"><Upload size={40} /><h2>Drop your receipt images</h2><p>A batch can contain tens of images.</p></div>}
    {preview && <dialog ref={dialog} className="export-dialog" aria-labelledby="export-title" onCancel={() => setPreview(null)}>
      <div className="panel-title"><h2 id="export-title">Check your CSV</h2><button aria-label="Close preview" onClick={() => setPreview(null)}><X size={20} /></button></div>
      {exportError && <div className="notice error" role="alert">{exportError}</div>}
      <p>{exportIds.size} verified receipts · {preview.row_count} rows · {preview.template.name}</p>
      <p className="muted">Receipt-level totals repeat on each item row. Group by receipt ID before summing totals.</p>
      <div className="csv-scroll"><table><thead><tr>{preview.columns.map(c => <th key={c}>{c}</th>)}</tr></thead>
        <tbody>{preview.rows.map((row, i) => <tr key={i}>{preview.columns.map(c => <td key={c}>{row[c]}</td>)}</tr>)}</tbody></table></div>
      {preview.row_count > preview.rows.length && <p>Showing first {preview.rows.length} rows. Download includes all rows.</p>}
      <div className="dialog-actions"><button onClick={() => setPreview(null)}>Back to review</button>
        <button disabled={exporting} onClick={() => void exportCsv(true)}><ArrowDownToLine size={16} /> Download CSV + photos</button>
        <button className="primary" disabled={exporting} onClick={() => void exportCsv()}><ArrowDownToLine size={16} /> Download CSV</button></div>
    </dialog>}
    {settingsOpen && <WorkspaceSettings onClose={() => setSettingsOpen(false)}
      onTemplatesChanged={() => void refreshTemplates().catch((e: Error) => setError(e.message))} />}
  </div>
}
