import { useCallback, useEffect, useRef, useState } from 'react'
import { Archive, ArrowDownToLine, Check, Cpu, Download, FileSpreadsheet, LoaderCircle, RefreshCw, X } from 'lucide-react'
import { api, download, post } from './api'
import TemplatesEditor from './TemplatesEditor'
import type { VisionSettings } from './types'
import './settings.css'

type Profile = 'ocr-only' | '2b' | '4b'
type ProfileInfo = { label: string; model: string | null; minimum_free_gb: number; approx_download_gb: number }
type Capabilities = {
  cpu: string; cpu_count: number | null; ram_gb: number | null; free_disk_gb: number
  nvidia: { name: string; vram_gb: number }[]; recommended_profile: Profile; profiles: Record<Profile, ProfileInfo>
  recommendation_note: string
  workspace: string
  workspace_location?: { location_error?: string; pending?: { target: string } }
}
type SetupStatus = {
  ocr_ready: boolean; runtime_installed: boolean; runtime_running: boolean; runtime_version: string; models: string[]
  profile: Profile; job: { state?: string; stage?: string; completed?: number; total?: number; message?: string; profile?: Profile }
}
type Backup = { name: string; bytes: number; created_at: string }
type Tab = 'models' | 'formats' | 'backups'
type Props = { onClose: () => void; onTemplatesChanged: () => void }

const PROFILES: { id: Profile; name: string; detail: string }[] = [
  { id: 'ocr-only', name: 'OCR only', detail: 'The smallest download. Read printed text, then enter or correct the expense details.' },
  { id: '2b', name: '2B vision', detail: 'A lighter local vision model for reading pages and selected regions. Start here on a modest PC.' },
  { id: '4b', name: '4B vision', detail: 'A larger local vision model that needs more memory and disk space. Processing may take longer.' },
]
const TABS: { id: Tab; name: string; icon: typeof Cpu }[] = [
  { id: 'models', name: 'Vision & models', icon: Cpu }, { id: 'formats', name: 'CSV formats', icon: FileSpreadsheet },
  { id: 'backups', name: 'Backups', icon: Archive },
]
function size(bytes: number) {
  return bytes >= 1024 ** 3 ? `${(bytes / 1024 ** 3).toFixed(1)} GB` : `${(bytes / 1024 ** 2).toFixed(1)} MB`
}

export default function WorkspaceSettings({ onClose, onTemplatesChanged }: Props) {
  const dialog = useRef<HTMLDialogElement>(null)
  const [tab, setTab] = useState<Tab>('models')
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null)
  const [status, setStatus] = useState<SetupStatus | null>(null)
  const [profile, setProfile] = useState<Profile>('2b')
  const [visionSettings, setVisionSettings] = useState<VisionSettings | null>(null)
  const [visionSaving, setVisionSaving] = useState(false)
  const [visionMessage, setVisionMessage] = useState('')
  const [provider, setProvider] = useState<'local' | 'google'>('local')
  const [googleModel, setGoogleModel] = useState('gemini-3.8-flash')
  const [cloudConsent, setCloudConsent] = useState(false)
  const [apiKey, setApiKey] = useState('')
  const [providerMessage, setProviderMessage] = useState('')
  const [loading, setLoading] = useState(true)
  const [installing, setInstalling] = useState(false)
  const [error, setError] = useState('')
  const [backups, setBackups] = useState<Backup[] | null>(null)
  const [backupsLoading, setBackupsLoading] = useState(false)
  const [backupBusy, setBackupBusy] = useState('')
  const [backupMessage, setBackupMessage] = useState('')
  const [restoreFile, setRestoreFile] = useState<File | null>(null)
  const [confirmRestore, setConfirmRestore] = useState(false)
  const [staged, setStaged] = useState(false)
  const [location, setLocation] = useState('')
  const [confirmMove, setConfirmMove] = useState(false)
  const [locationBusy, setLocationBusy] = useState(false)
  const [locationMessage, setLocationMessage] = useState('')
  const [locationStaged, setLocationStaged] = useState(false)
  const activeSetup = status?.job.state === 'queued' || status?.job.state === 'running'
  const setupBusy = installing || activeSetup

  useEffect(() => {
    const element = dialog.current
    if (element && !element.open) element.showModal()
    return () => { if (element?.open) element.close() }
  }, [])

  const loadSetup = useCallback(async () => {
    setLoading(true); setError('')
    try {
      const [system, setup] = await Promise.all([api<Capabilities>('/system/capabilities'), api<SetupStatus>('/setup/status')])
      setCapabilities(system); setStatus(setup)
      setLocation(system.workspace)
      setProfile(setup.ocr_ready ? setup.profile : system.recommended_profile)
    } catch (cause) { setError((cause as Error).message) }
    finally { setLoading(false) }
  }, [])
  useEffect(() => { void loadSetup() }, [loadSetup])
  useEffect(() => {
    void api<VisionSettings>('/vision/settings').then(saved => {
      setVisionSettings(saved); setProvider(saved.provider || 'local')
      setGoogleModel(saved.google_model || 'gemini-3.8-flash'); setCloudConsent(!!saved.google_consent)
    }).catch(cause => setError((cause as Error).message))
  }, [])

  function acceptSettings(saved: VisionSettings) {
    setVisionSettings(saved)
    window.dispatchEvent(new CustomEvent('receiptiq:vision-settings', { detail: saved }))
  }
  async function saveProvider() {
    setVisionSaving(true); setError(''); setProviderMessage('')
    try {
      const saved = await api<VisionSettings>('/vision/settings', { method: 'PATCH', body: JSON.stringify({
        provider, google_model: googleModel.trim(), google_consent: provider === 'google' && cloudConsent,
      }) })
      acceptSettings(saved); setCloudConsent(saved.google_consent)
      setProviderMessage(saved.provider === 'google' ? 'Google enabled for new uploads and manual scans.' :
        'Local Qwen selected. Queued Google scans will stop; request them again to use Qwen.')
    } catch (cause) { setError((cause as Error).message) }
    finally { setVisionSaving(false) }
  }
  async function changeGoogleKey(remove = false) {
    setVisionSaving(true); setError(''); setProviderMessage('')
    try {
      const saved = await api<VisionSettings>('/vision/google-key', { method: remove ? 'DELETE' : 'PUT',
        ...(remove ? {} : { body: JSON.stringify({ api_key: apiKey }) }),
      })
      acceptSettings(saved); setApiKey('')
      if (remove) { setProvider('local'); setCloudConsent(false) }
      setProviderMessage(remove ? 'Key removed. Local Qwen is selected.' : 'Key saved securely on this PC. Save the provider to enable Google.')
    } catch (cause) { setError((cause as Error).message) }
    finally { setVisionSaving(false) }
  }
  async function changeVision(key: 'amount_first' | 'capture_regions', checked: boolean) {
    const previous = visionSettings
    setVisionSettings(value => value ? { ...value, [key]: checked } : value)
    setVisionSaving(true); setVisionMessage(''); setError('')
    try {
      const saved = await api<VisionSettings>('/vision/settings', {
        method: 'PATCH', body: JSON.stringify({ [key]: checked }),
      })
      acceptSettings(saved)
      setVisionMessage('Saved. These options apply to the next Vision run.')
    } catch (cause) { setVisionSettings(previous); setError((cause as Error).message) }
    finally { setVisionSaving(false) }
  }
  useEffect(() => {
    if (!activeSetup) return
    let cancelled = false
    const timer = window.setTimeout(async () => {
      try {
        const value = await api<SetupStatus>('/setup/status')
        if (!cancelled) setStatus(value)
      } catch (cause) {
        if (!cancelled) {
          setError((cause as Error).message)
          setStatus(value => value ? { ...value, job: { ...value.job, state: 'interrupted',
            message: 'Setup status could not be refreshed. Reload status to check the local job.' } } : value)
        }
      }
    }, 1500)
    return () => { cancelled = true; window.clearTimeout(timer) }
  }, [activeSetup, status])

  const loadBackups = useCallback(async () => {
    setBackupsLoading(true)
    try { setBackups(await api<Backup[]>('/backups')) }
    finally { setBackupsLoading(false) }
  }, [])
  useEffect(() => {
    if (tab === 'backups' && backups === null) void loadBackups().catch(cause => setError((cause as Error).message))
  }, [tab, backups, loadBackups])

  async function install() {
    setInstalling(true); setError('')
    try {
      await post('/setup/install', { profile })
      setStatus(await api<SetupStatus>('/setup/status'))
    } catch (cause) { setError((cause as Error).message) }
    finally { setInstalling(false) }
  }
  async function createBackup() {
    setBackupBusy('create'); setError(''); setBackupMessage('')
    try {
      await post('/backups')
      await loadBackups()
      setBackupMessage('Backup created. Download a copy to keep outside this workspace.')
    } catch (cause) { setError((cause as Error).message) }
    finally { setBackupBusy('') }
  }
  async function downloadBackup(backup: Backup) {
    setBackupBusy(backup.name); setError('')
    try { await download('/backups/' + encodeURIComponent(backup.name) + '/download', backup.name) }
    catch (cause) { setError((cause as Error).message) }
    finally { setBackupBusy('') }
  }
  async function stageRestore() {
    if (!restoreFile || !confirmRestore) return
    setBackupBusy('restore'); setError(''); setBackupMessage('')
    try {
      if (restoreFile.size > 2 * 1024 ** 3) throw new Error('Choose a ReceiptIQ backup ZIP no larger than 2 GB.')
      const form = new FormData(); form.append('file', restoreFile)
      const result = await api<{ staged: boolean; message: string }>('/backups/restore', { method: 'POST', body: form })
      setStaged(result.staged)
      setBackupMessage(result.message || 'Backup validated. Close and restart ReceiptIQ to apply it. The previous workspace is retained.')
    } catch (cause) { setError((cause as Error).message) }
    finally { setBackupBusy('') }
  }
  async function stageLocation() {
    if (!confirmMove) return
    setLocationBusy(true); setError(''); setLocationMessage('')
    try {
      const target = location.trim()
      if (!(/^[A-Za-z]:[\\/]/.test(target) || target.startsWith('/') && !target.startsWith('//'))) {
        throw new Error('Enter an absolute local folder path, such as C:\\ReceiptIQ-data.')
      }
      const result = await post<{ message: string }>('/system/location', { target })
      setLocationStaged(true)
      setLocationMessage(result.message || 'Workspace move staged. Close and restart ReceiptIQ to apply it. The original workspace remains available.')
    } catch (cause) { setError((cause as Error).message) }
    finally { setLocationBusy(false) }
  }
  function switchTab(next: Tab) { setTab(next); setError('') }

  return <dialog ref={dialog} className="settings-dialog" aria-labelledby="settings-title"
    onCancel={event => { event.preventDefault(); onClose() }}>
    <div className="settings-dialog-heading"><div><h2 id="settings-title">Workspace settings</h2>
      <p>Vision provider, local models, export formats, and recovery.</p></div>
      <button type="button" className="icon-button" aria-label="Close workspace settings" onClick={onClose}><X size={20} /></button></div>
    <div className="settings-tabs" role="tablist" aria-label="Workspace settings sections">{TABS.map((entry, index) => {
      const Icon = entry.icon
      return <button key={entry.id} type="button" role="tab" id={`settings-tab-${entry.id}`}
        aria-selected={tab === entry.id} aria-controls={`settings-panel-${entry.id}`} tabIndex={tab === entry.id ? 0 : -1}
        onClick={() => switchTab(entry.id)} onKeyDown={event => {
          const next = event.key === 'ArrowRight' ? (index + 1) % TABS.length : event.key === 'ArrowLeft' ?
            (index + TABS.length - 1) % TABS.length : event.key === 'Home' ? 0 : event.key === 'End' ? TABS.length - 1 : null
          if (next !== null) { event.preventDefault(); switchTab(TABS[next].id); document.getElementById(`settings-tab-${TABS[next].id}`)?.focus() }
        }}><Icon size={16} />{entry.name}</button>
    })}</div>
    <div className="settings-dialog-content">
      {error && <div className="notice error" role="alert">{error}</div>}
      <section id="settings-panel-models" className="settings-panel" role="tabpanel" aria-labelledby="settings-tab-models" hidden={tab !== 'models'}>
        <fieldset className="settings-plain-fieldset settings-vision-provider" disabled={!visionSettings || visionSaving}>
          <legend>Receipt vision provider</legend>
          <p className="settings-muted">Choose how new uploads and manual scans are read. Every result still needs your verification.</p>
          <label className="settings-field"><span>Vision provider</span><select value={provider} aria-label="Vision provider"
            onChange={event => { setProvider(event.target.value as 'local' | 'google'); setProviderMessage('') }}>
            <option value="local">Local Qwen · offline</option><option value="google">Google AI Studio · your API key</option>
          </select></label>
          {provider === 'google' && <div className="settings-google">
            <p>Google receives the selected receipt images and their OCR text, including automatic scans of new uploads.
              Internet is required. Requests use your Google quota and may incur charges. Saved retrieval examples stay local.</p>
            <p><a href="https://aistudio.google.com/apikey" target="_blank" rel="noreferrer">Get an AI Studio API key</a>
              {' · '}<a href="https://ai.google.dev/gemini-api/terms" target="_blank" rel="noreferrer">Google API data terms</a></p>
            <label className="settings-field"><span>Google API key</span><input type="password" value={apiKey}
              autoComplete="new-password" spellCheck={false} maxLength={256}
              placeholder={visionSettings?.google_key_saved ? 'Key saved · enter a replacement' : 'Paste your AI Studio key'}
              onChange={event => setApiKey(event.target.value)} /></label>
            <div className="settings-key-actions">
              <button type="button" disabled={!apiKey.trim()} onClick={() => void changeGoogleKey()}>Save API key</button>
              {visionSettings?.google_key_saved && <button type="button" onClick={() => void changeGoogleKey(true)}>Remove API key</button>}
              <span className="settings-muted">{visionSettings?.google_key_saved ? 'Key saved' : 'No key saved'}</span>
            </div>
            <p className="settings-muted">Protected for your Windows user. The key is never shown again or included in backups; re-enter it after restoring a backup.</p>
            <label className="settings-field"><span>Gemini model</span><input value={googleModel} maxLength={100} list="gemini-model-options"
              spellCheck={false} onChange={event => setGoogleModel(event.target.value)} aria-describedby="gemini-model-help" /></label>
            <datalist id="gemini-model-options"><option value="gemini-3.8-flash" /><option value="gemini-3.5-flash-lite" />
              <option value="gemini-3.1-flash-lite" /></datalist>
            <p id="gemini-model-help" className="settings-muted">Use an image-capable Gemini model with structured JSON support from your AI Studio project.</p>
            <label className="settings-confirm"><input type="checkbox" checked={cloudConsent}
              onChange={event => setCloudConsent(event.target.checked)} />
              Allow receipt images and OCR text to be sent to Google for automatic uploads and manual scans.</label>
          </div>}
          {provider === 'local' && <p className="settings-muted">Qwen processes receipts on this PC using the installed local profile below.</p>}
          <div className="settings-save-row"><span className="settings-muted">Active: {visionSettings?.provider === 'google' ? 'Google AI Studio' : 'Local Qwen'}</span>
            <button type="button" className="primary" disabled={provider === 'google' && (!visionSettings?.google_key_saved || !cloudConsent || !googleModel.trim())}
              onClick={() => void saveProvider()}>Save vision provider</button></div>
          <p role="status" aria-live="polite">{visionSaving ? 'Saving…' : providerMessage}</p>
        </fieldset>
        <div className="settings-section-heading"><div><h3>Set up local processing</h3>
          <p>Downloads start only when you choose Install. Local Qwen and OCR work offline after setup. Google vision does not need Qwen installed.</p></div>
          <button type="button" className="quiet" onClick={() => void loadSetup()} disabled={loading || setupBusy}><RefreshCw size={15} /> Reload status</button></div>
        {loading ? <p className="settings-loading" role="status"><LoaderCircle size={16} className="spin" /> Checking this PC…</p> : <>
          {capabilities && <div className="settings-hardware">
            <h4>This PC</h4><dl><div><dt>Memory</dt><dd>{capabilities.ram_gb === null ? 'Not detected' : `${capabilities.ram_gb} GB RAM`}</dd></div>
              <div><dt>Free space</dt><dd>{capabilities.free_disk_gb} GB</dd></div>
              <div><dt>Graphics</dt><dd>{capabilities.nvidia.length ? capabilities.nvidia.map(gpu => `${gpu.name} · ${gpu.vram_gb} GB`).join(', ') : 'No supported NVIDIA GPU detected'}</dd></div></dl>
            <p>{capabilities.recommendation_note}</p>
          </div>}
          <fieldset className="settings-plain-fieldset settings-profile-list" disabled={setupBusy || !capabilities}>
            <legend>Processing profile</legend>
            {PROFILES.map(option => {
              const info = capabilities?.profiles[option.id]
              return <label key={option.id} className={'settings-profile ' + (profile === option.id ? 'selected' : '')}>
                <input type="radio" name="local-profile" value={option.id} checked={profile === option.id} onChange={() => setProfile(option.id)} />
                <span><strong>{option.name}{capabilities?.recommended_profile === option.id && <span className="settings-recommended">Recommended for this PC</span>}</strong>
                  <span>{option.detail}</span>{info && <small>About {info.approx_download_gb} GB download · {info.minimum_free_gb} GB free space needed</small>}</span>
              </label>
            })}
          </fieldset>
          <p className="settings-muted">2B is the lighter starting point. 4B suits a PC with more memory. Both can misread receipts; every result still needs your verification.</p>
          {status && <div className="settings-installed"><span><Check size={14} aria-hidden="true" /> OCR: {status.ocr_ready ? 'Installed' : 'Not installed'}</span>
            <span>Vision: {status.models.some(value => value.startsWith('qwen3-vl:')) ? 'Installed' : 'Not installed'}</span>
            {status.runtime_installed && <span>Local runtime {status.runtime_version}{status.runtime_running ? ' · running' : ''}</span>}</div>}
          {status?.job.state && <div className={'settings-setup-status ' + (['failed', 'interrupted'].includes(status.job.state) ? 'settings-warning' : '')} role="status" aria-live="polite">
            <strong>{activeSetup ? 'Setup in progress' : status.job.state === 'complete' ? 'Setup complete' : status.job.state === 'failed' ? 'Setup failed' : 'Setup interrupted'}</strong>
            <p>{status.job.message || status.job.stage?.replaceAll('-', ' ')}</p>
            {activeSetup && <progress aria-label="Local model download progress" max={status.job.total || 1}
              value={status.job.total ? Math.min(status.job.completed || 0, status.job.total) : undefined} />}
          </div>}
          <p className="settings-muted settings-model-terms">Vision profiles also download a model for finding similar saved receipts. The <a href="https://ai.google.dev/gemma/terms" target="_blank" rel="noreferrer">Gemma terms</a> apply to that model. Qwen3-VL is provided under Apache 2.0.</p>
          <div className="settings-save-row"><span className="settings-muted">Setup can continue while you review receipts.</span>
            <button type="button" className="primary" disabled={setupBusy || !capabilities || locationBusy} onClick={() => void install()}>
              {setupBusy ? <LoaderCircle size={16} className="spin" /> : <Download size={16} />}{setupBusy ? 'Installing…' : 'Install selected profile'}</button></div>
          <fieldset className="settings-plain-fieldset vision-options" disabled={!visionSettings || visionSaving}>
            <legend>Vision reading options</legend>
            <label><input type="checkbox" checked={!!visionSettings?.amount_first}
              onChange={event => void changeVision('amount_first', event.target.checked)} /> Read Amount first</label>
            <p>Use a clear Amount and one readable factor to fill a missing Quantity or Unit price exactly. Written values and possible discounts stay available for review.</p>
            <label><input type="checkbox" checked={!!visionSettings?.capture_regions}
              onChange={event => void changeVision('capture_regions', event.target.checked)} /> Show AI line numbers on photos</label>
            <p>Locate item rows during Vision so numbered outlines can be compared with the receipt details. May increase processing time; existing receipts need another Vision run.</p>
            <p role="status">{visionSaving ? 'Saving reading options…' : visionMessage}</p>
          </fieldset>
          <div className="settings-location"><h3>Workspace location</h3>
            {capabilities?.workspace_location?.location_error && <p className="settings-inline-error" role="alert">
              {capabilities.workspace_location.location_error}</p>}
            <p className="settings-help">A move runs after closing and reopening ReceiptIQ. Leave it open during that startup;
              copying to another drive can take several minutes. The original folder stays available.</p>
            <p>Choose a local folder for receipts, edits, photos, and model files. The move is staged now and applied only after ReceiptIQ closes and restarts.</p>
            <label className="settings-field"><span>Data workspace folder</span><input type="text" value={location}
              placeholder="C:\ReceiptIQ-data" disabled={locationBusy || locationStaged || !capabilities}
              onChange={event => { setLocation(event.target.value); setConfirmMove(false); setLocationMessage('') }} /></label>
            <label className="settings-confirm"><input type="checkbox" checked={confirmMove}
              disabled={locationBusy || locationStaged || !location.trim() || location.trim() === capabilities?.workspace}
              onChange={event => setConfirmMove(event.target.checked)} />
              <span>Move workspace on next restart; original remains available.</span></label>
            <button type="button" disabled={!confirmMove || locationBusy || locationStaged || setupBusy || staged}
              onClick={() => void stageLocation()}>{locationBusy && <LoaderCircle size={15} className="spin" />}
              {locationStaged ? 'Move staged — restart ReceiptIQ' : locationBusy ? 'Staging workspace move…' : 'Stage workspace move'}</button>
            {locationMessage && <div className="settings-success" role="status">{locationMessage}</div>}
          </div>
        </>}
      </section>
      <section id="settings-panel-formats" className="settings-panel" role="tabpanel" aria-labelledby="settings-tab-formats" hidden={tab !== 'formats'}>
        <TemplatesEditor onTemplatesChanged={onTemplatesChanged} />
      </section>
      <section id="settings-panel-backups" className="settings-panel" role="tabpanel" aria-labelledby="settings-tab-backups" hidden={tab !== 'backups'}>
        <div className="settings-section-heading"><div><h3>Workspace backups</h3>
          <p>Keep receipts, photos, saved edits, and export records together in a local ZIP.</p></div>
          <div className="settings-backup-actions"><button type="button" className="quiet" disabled={!!backupBusy || backupsLoading}
            onClick={() => { setError(''); void loadBackups().catch(cause => setError((cause as Error).message)) }}>
            <RefreshCw size={15} /> Reload</button>
            <button type="button" className="primary" disabled={!!backupBusy} onClick={() => void createBackup()}>
              {backupBusy === 'create' ? <LoaderCircle size={15} className="spin" /> : <Archive size={15} />}Create backup</button></div></div>
        {backupMessage && <div className="settings-success" role="status">{backupMessage}</div>}
        {backupsLoading ? <p className="settings-loading" role="status"><LoaderCircle size={15} className="spin" /> Loading backups…</p> :
          backups === null ? <p className="settings-muted">Reload to check the workspace backups.</p> : !backups.length ?
          <p className="settings-empty">No backups yet. Create one before making major workspace changes.</p> :
          <ul className="settings-backup-list">{backups.map(backup => <li key={backup.name}>
            <div><strong>{backup.name}</strong><span>{new Date(backup.created_at).toLocaleString()} · {size(backup.bytes)}</span></div>
            <button type="button" disabled={!!backupBusy} aria-label={`Download ${backup.name}`} onClick={() => void downloadBackup(backup)}>
              <ArrowDownToLine size={15} /> Download</button></li>)}</ul>}
        <div className="settings-restore"><h3>Restore a backup</h3>
          <p>The backup is validated and staged first. It replaces this workspace only after you close and restart ReceiptIQ. The previous workspace is retained for recovery.</p>
          <label className="settings-field"><span>ReceiptIQ backup ZIP (up to 2 GB)</span>
            <input type="file" accept=".zip,application/zip" disabled={!!backupBusy || staged} onChange={event => {
              setRestoreFile(event.target.files?.[0] || null); setConfirmRestore(false); setError('')
            }} /></label>
          <label className="settings-confirm"><input type="checkbox" checked={confirmRestore} disabled={!restoreFile || !!backupBusy || staged}
            onChange={event => setConfirmRestore(event.target.checked)} />
            <span>I understand this backup will replace the workspace when ReceiptIQ restarts.</span></label>
          <button type="button" disabled={!restoreFile || !confirmRestore || !!backupBusy || staged} onClick={() => void stageRestore()}>
            {backupBusy === 'restore' ? <LoaderCircle size={15} className="spin" /> : <Archive size={15} />}
            {staged ? 'Restore staged — restart ReceiptIQ' : backupBusy === 'restore' ? 'Validating backup…' : 'Validate and stage restore'}</button>
        </div>
      </section>
    </div>
    <div className="settings-dialog-footer"><button type="button" onClick={onClose}>Done</button></div>
  </dialog>
}
