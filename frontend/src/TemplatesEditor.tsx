import { useCallback, useEffect, useState } from 'react'
import { ArrowDown, ArrowUp, Copy, LoaderCircle, Plus, RefreshCw, Save, Trash2 } from 'lucide-react'
import { api } from './api'

export const CSV_FIELDS = ['Receipt ID', 'Purchase date', 'Vendor', 'Receipt number', 'Receipt photo', 'Notes',
  'Item number', 'Description', 'Quantity', 'Unit', 'Unit price', 'Amount', 'Type',
  'Photo of item purchased', 'Total'] as const
export type SourceField = typeof CSV_FIELDS[number]
export type TemplateColumn = { header: string; field: SourceField; constant?: never } |
  { header: string; constant: string; field?: never }
export type Template = {
  id: string; name: string; version: number; columns: TemplateColumn[]; row_mode: 'line_item' | 'receipt'
  delimiter: ',' | ';' | '\t'; encoding: 'utf-8-sig' | 'utf-8'; date_format: 'ISO' | 'DDMMYYYY' | 'MMDDYYYY'
  decimal_separator: '.' | ','; required_fields: SourceField[]
}

type Props = { onTemplatesChanged: () => void }

export default function TemplatesEditor({ onTemplatesChanged }: Props) {
  const [templates, setTemplates] = useState<Template[]>([])
  const [draft, setDraft] = useState<Template | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const values = await api<Template[]>('/templates')
      setTemplates(values)
      setDraft(values[0] || null)
    } catch (cause) { setError((cause as Error).message) }
    finally { setLoading(false) }
  }, [])
  useEffect(() => { void load() }, [load])

  function change(update: Partial<Template>) {
    setDraft(value => value ? { ...value, ...update } : value)
    setMessage('')
  }
  function changeColumn(index: number, column: TemplateColumn) {
    if (draft) change({ columns: draft.columns.map((value, i) => i === index ? column : value) })
  }
  function moveColumn(index: number, direction: -1 | 1) {
    if (!draft) return
    const columns = [...draft.columns]
    ;[columns[index], columns[index + direction]] = [columns[index + direction], columns[index]]
    change({ columns })
  }
  async function save(copy: boolean) {
    if (!draft) return
    setSaving(true); setError(''); setMessage('')
    const creating = copy || draft.id === 'default'
    const payload = creating ? { ...draft, id: crypto.randomUUID(), version: 1,
      name: templates.some(value => value.name === draft.name) ? `${draft.name.slice(0, 115)} copy` : draft.name } : draft
    try {
      const saved = await api<Template>(creating ? '/templates' : '/templates/' + encodeURIComponent(draft.id), {
        method: creating ? 'POST' : 'PATCH', body: JSON.stringify(payload) })
      setTemplates(values => [...values.filter(value => value.id !== saved.id), saved])
      setDraft(saved)
      setMessage(`Saved “${saved.name}”. Choose it when previewing your CSV.`)
      onTemplatesChanged()
    } catch (cause) { setError((cause as Error).message) }
    finally { setSaving(false) }
  }

  return <div className="templates-editor">
    <div className="settings-section-heading"><div><h3>CSV formats</h3>
      <p>Choose the columns and formatting your receiving app expects.</p></div>
      <button type="button" className="quiet" onClick={() => void load()} disabled={loading || saving}>
        <RefreshCw size={15} /> Reload formats</button></div>
    {error && <div className="notice error" role="alert">{error}</div>}
    {message && <div className="settings-success" role="status">{message}</div>}
    {loading ? <p className="settings-loading" role="status"><LoaderCircle size={16} className="spin" /> Loading CSV formats…</p> :
      !draft ? <p className="settings-muted">No CSV formats are available. Reload formats to try again.</p> :
      <form onSubmit={event => { event.preventDefault(); void save(false) }}>
        <fieldset disabled={saving} className="settings-form-section settings-plain-fieldset">
          <div className="settings-form-grid">
            <label className="settings-field"><span>Saved format</span>
              <select value={draft.id} onChange={event => {
                setDraft(templates.find(value => value.id === event.target.value) || null); setMessage(''); setError('')
              }}>{templates.map(value => <option key={value.id} value={value.id}>{value.name}{value.id === 'default' ? ' (default)' : ''}</option>)}</select></label>
            <label className="settings-field"><span>Format name</span>
              <input value={draft.name} maxLength={120} required onChange={event => change({ name: event.target.value })} /></label>
          </div>
          {draft.id === 'default' && <p className="settings-muted">The default keeps all 15 expense columns. Save your edits as a copy.</p>}
          <div className="settings-form-grid settings-format-grid">
            <label className="settings-field"><span>Rows</span>
              <select value={draft.row_mode} onChange={event => change({ row_mode: event.target.value as Template['row_mode'] })}>
                <option value="line_item">One row per item</option><option value="receipt">One row per receipt</option></select></label>
            <label className="settings-field"><span>Column separator</span>
              <select value={draft.delimiter} onChange={event => change({ delimiter: event.target.value as Template['delimiter'] })}>
                <option value=",">Comma</option><option value=";">Semicolon</option><option value={'\t'}>Tab</option></select></label>
            <label className="settings-field"><span>Text encoding</span>
              <select value={draft.encoding} onChange={event => change({ encoding: event.target.value as Template['encoding'] })}>
                <option value="utf-8-sig">UTF-8 with BOM (Excel)</option><option value="utf-8">UTF-8</option></select></label>
            <label className="settings-field"><span>Purchase date format</span>
              <select value={draft.date_format} onChange={event => change({ date_format: event.target.value as Template['date_format'] })}>
                <option value="ISO">YYYY-MM-DD</option><option value="DDMMYYYY">DD/MM/YYYY</option><option value="MMDDYYYY">MM/DD/YYYY</option></select></label>
            <label className="settings-field"><span>Decimal separator</span>
              <select value={draft.decimal_separator} onChange={event => change({ decimal_separator: event.target.value as Template['decimal_separator'] })}>
                <option value=".">Point (125.50)</option><option value=",">Comma (125,50)</option></select></label>
          </div>
          {draft.row_mode === 'receipt' && <p className="settings-muted">Item descriptions are joined into one cell. Amount uses Total; other item fields stay blank.</p>}
        </fieldset>
        <fieldset className="settings-plain-fieldset settings-columns" disabled={saving}>
          <legend>Columns <span>{draft.columns.length} of 40</span></legend>
          <p className="settings-muted">Set a heading and source for each column. Use literal text for a fixed value.</p>
          {draft.columns.map((column, index) => <div className="template-column" key={index}>
            <span className="template-column-position">{index + 1}</span>
            <label className="settings-field"><span>CSV heading</span><input value={column.header} required maxLength={120}
              aria-label={`Column ${index + 1} heading`} onChange={event => changeColumn(index, { ...column, header: event.target.value })} /></label>
            <label className="settings-field"><span>Value from</span>
              <select value={column.field || '__literal'} aria-label={`Column ${index + 1} source`}
                onChange={event => changeColumn(index, event.target.value === '__literal' ?
                  { header: column.header, constant: '' } : { header: column.header, field: event.target.value as SourceField })}>
                {CSV_FIELDS.map(field => <option key={field}>{field}</option>)}<option value="__literal">Literal text</option></select></label>
            <div className="template-column-actions">
              <button type="button" className="icon-button" disabled={index === 0} aria-label={`Move column ${index + 1} up`}
                onClick={() => moveColumn(index, -1)}><ArrowUp size={15} /></button>
              <button type="button" className="icon-button" disabled={index === draft.columns.length - 1} aria-label={`Move column ${index + 1} down`}
                onClick={() => moveColumn(index, 1)}><ArrowDown size={15} /></button>
              <button type="button" className="icon-button" disabled={draft.columns.length === 1} aria-label={`Remove column ${index + 1}`}
                onClick={() => change({ columns: draft.columns.filter((_, i) => i !== index) })}><Trash2 size={15} /></button>
            </div>
            {'constant' in column && <label className="settings-field template-literal"><span>Literal text</span>
              <input value={column.constant} maxLength={4000} onChange={event => changeColumn(index, { header: column.header, constant: event.target.value })} /></label>}
          </div>)}
          <button type="button" disabled={draft.columns.length >= 40} onClick={() => {
            const field = CSV_FIELDS.find(value => !draft.columns.some(column => column.field === value))
            change({ columns: [...draft.columns, field ? { header: field, field } : { header: `Column ${draft.columns.length + 1}`, constant: '' }] })
          }}><Plus size={15} /> Add column</button>
        </fieldset>
        <fieldset className="settings-plain-fieldset settings-required" disabled={saving}>
          <legend>Required fields</legend><p className="settings-muted">Exports using this format pause when a required field is empty. Item photos remain optional unless you select them here.</p>
          <div className="settings-checkbox-grid">{CSV_FIELDS.map(field => <label key={field}>
            <input type="checkbox" checked={draft.required_fields.includes(field)} onChange={event => change({ required_fields:
              event.target.checked ? [...draft.required_fields, field] : draft.required_fields.filter(value => value !== field) })} />
            <span>{field}</span></label>)}</div>
        </fieldset>
        <div className="settings-save-row">
          <span className="settings-muted">{draft.id === 'default' ? 'Default format · saved copy required' : `Version ${draft.version}`}</span>
          {draft.id !== 'default' && <button type="button" disabled={saving} onClick={() => void save(true)}><Copy size={15} /> Save a copy</button>}
          <button className="primary" type="submit" disabled={saving}>
            {saving ? <LoaderCircle size={15} className="spin" /> : <Save size={15} />}{saving ? 'Saving…' : draft.id === 'default' ? 'Save a copy' : 'Save changes'}</button>
        </div>
      </form>}
  </div>
}
