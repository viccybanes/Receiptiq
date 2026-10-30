import { useEffect, useRef, useState } from 'react'
import { ArrowLeft, ArrowRight, Check, ExternalLink, LoaderCircle, X } from 'lucide-react'
import { api, post } from './api'
import type { VisionSettings } from './types'
import './gemini-setup.css'

type Props = { onClose: () => void }
type TestResult = { ok: boolean; model: string }

export default function GeminiSetup({ onClose }: Props) {
  const dialog = useRef<HTMLDialogElement>(null)
  const heading = useRef<HTMLHeadingElement>(null)
  const [step, setStep] = useState<'guide' | 'key' | 'done'>('guide')
  const [settings, setSettings] = useState<VisionSettings | null>(null)
  const [key, setKey] = useState('')
  const [tested, setTested] = useState<TestResult | null>(null)
  const [consent, setConsent] = useState(false)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    const element = dialog.current
    element?.showModal()
    void api<VisionSettings>('/vision/settings').then(setSettings).catch(() => {
      setError('Could not check saved settings. Close this guide and reopen it from Settings to retry.')
    })
    return () => element?.close()
  }, [])
  useEffect(() => { heading.current?.focus() }, [step])

  async function close() {
    if (busy) return
    if (step === 'done') { onClose(); return }
    setBusy('skip'); setError('')
    try {
      await post('/vision/google-setup', { status: 'skipped' })
      onClose()
    } catch { setError('Could not remember this choice. Try closing the guide again.') }
    finally { setBusy('') }
  }

  async function testKey() {
    setBusy('test'); setError(''); setTested(null); setConsent(false)
    try {
      const result = await post<TestResult>('/vision/google-key/test', key.trim() ? { api_key: key.trim() } : {})
      if (!result.ok) throw new Error('The connection test did not succeed. Try again.')
      setTested(result)
    } catch (cause) { setError((cause as Error).message) }
    finally { setBusy('') }
  }

  async function finish() {
    if (!tested || !consent) return
    setBusy('save'); setError('')
    try {
      if (key.trim()) await api('/vision/google-key', { method: 'PUT', body: JSON.stringify({ api_key: key.trim() }) })
      const saved = await api<VisionSettings>('/vision/settings', { method: 'PATCH', body: JSON.stringify({
        provider: 'google', google_model: tested.model, google_consent: true,
      }) })
      window.dispatchEvent(new CustomEvent('receiptiq:vision-settings', { detail: saved }))
      await post('/vision/google-setup', { status: 'completed' })
      setKey(''); setStep('done')
    } catch (cause) { setError((cause as Error).message) }
    finally { setBusy('') }
  }

  return <dialog ref={dialog} className="gemini-setup" aria-labelledby="gemini-setup-title"
    onCancel={event => { event.preventDefault(); void close() }}>
    <header className="gemini-setup-heading">
      <div><p className="gemini-setup-progress">Gemini Vision {step !== 'done' && `· Step ${step === 'guide' ? '1' : '2'} of 2`}</p>
        <h2 ref={heading} tabIndex={-1} id="gemini-setup-title">{step === 'guide' ? 'Hello! Welcome to Receiptor.' :
          step === 'key' ? 'Connect Gemini Vision' : 'Gemini is ready'}</h2></div>
      <button type="button" className="icon-button" aria-label="Close Gemini setup" disabled={!!busy} onClick={() => void close()}><X size={20} /></button>
    </header>
    <div className="gemini-setup-body" aria-busy={!!busy}>
      {error && <p className="notice error" role="alert">{error}</p>}
      {step === 'guide' ? <>
        <p>Follow the steps below in order to use Gemini Vision.</p>
        <ol className="gemini-setup-steps">
          <li><strong>Sign in to Google AI Studio</strong><p>Open <a href="https://aistudio.google.com/apikey" target="_blank" rel="noreferrer">Google AI Studio <ExternalLink size={12} aria-hidden="true" /></a> with your Google account and accept its terms if asked.
            If you do not have an account, <a href="https://accounts.google.com/signup" target="_blank" rel="noreferrer">create a Google account</a> first.</p></li>
          <li><strong>Choose your Google project</strong><p>New accounts may already have a default project. To use an existing company project, go to Dashboard → Projects → Import projects and select it.
            If you need access, ask your project administrator.</p></li>
          <li><strong>Create an API key</strong><p>Open the API Keys page, choose Create API key, and select your project. You can also use a key that AI Studio has already created for you.</p></li>
          <li><strong>Copy your key and return here</strong><p>Keep it private. On the next screen, paste it into Receiptor and run a sample test.</p></li>
        </ol>
        <p className="gemini-setup-note">Gemini needs internet and uses your Google quota; charges may apply.
          Review <a href="https://ai.google.dev/gemini-api/terms" target="_blank" rel="noreferrer">Google’s API data terms</a> before sending work receipts.</p>
        <p className="gemini-setup-note"><a href="https://ai.google.dev/gemini-api/docs/api-key" target="_blank" rel="noreferrer">Google’s API-key guide</a> has more help if your account looks different.</p>
      </> : step === 'key' ? <>
        <p>Paste your API key, then check that Gemini can read a sample receipt.</p>
        <label className="gemini-setup-key"><span>Google API key</span><input type="password" autoComplete="new-password"
          value={key} spellCheck={false} maxLength={256} disabled={!!busy || !settings}
          placeholder={settings?.google_key_saved ? 'Use your saved key, or paste a replacement' : 'Paste your AI Studio API key'}
          aria-describedby="gemini-key-help" onChange={event => {
            setKey(event.target.value); setTested(null); setConsent(false); setError('')
          }} /></label>
        <p id="gemini-key-help" className="gemini-setup-note">{settings?.google_key_saved ? 'A key is saved on this PC. Leave the field blank to test it. ' : ''}
          Your key is protected for your Windows user when saved and is excluded from backups.</p>
        <div className="gemini-setup-sample"><strong>A small sample test</strong>
          <p>We send one built-in sample image to Google: 2 items at PHP 10.00, total PHP 20.00.
            This uses your Google quota. Your receipts stay untouched, and the test does not save the key.</p>
          <button type="button" disabled={!!busy || !settings || (!key.trim() && !settings.google_key_saved)} onClick={() => void testKey()}>
            {busy === 'test' ? <LoaderCircle size={16} className="spin" /> : tested ? <Check size={16} /> : null}
            {busy === 'test' ? 'Testing connection…' : tested ? 'Test again' : 'Test API key'}</button>
        </div>
        <div className="gemini-setup-result" role="status" aria-live="polite">
          {busy === 'test' ? 'Reading the sample image. This can take up to 45 seconds.' : tested ?
            'Connection successful. Gemini read the sample receipt.' : 'A successful test is required before enabling Gemini here.'}
        </div>
        <label className="gemini-setup-consent"><input type="checkbox" checked={consent} disabled={!!busy || !tested}
          onChange={event => setConsent(event.target.checked)} /><span>Allow my receipt images and OCR text to be sent to Google for automatic uploads and manual scans.
          Requests use my Google quota and may incur charges.</span></label>
        <p className="gemini-setup-note">{tested ? `${tested.model} will be the manual scan default. ` : ''}You can change the provider or remove your key in Settings.</p>
      </> : <div className="gemini-setup-complete"><Check size={28} aria-hidden="true" />
        <p>Your key is saved and Gemini Vision is enabled.</p>
        <p>Upload your receipts, then review the extracted details and verify each receipt before exporting.</p>
      </div>}
    </div>
    <footer className="gemini-setup-footer">
      {step === 'done' ? <button type="button" className="primary" onClick={onClose}>Start reviewing <ArrowRight size={16} /></button> : <>
        <div className="gemini-setup-secondary">{step === 'key' && <button type="button" className="quiet" disabled={!!busy}
          onClick={() => { setError(''); setStep('guide') }}><ArrowLeft size={15} /> Back</button>}
          <button type="button" className="quiet" disabled={!!busy} onClick={() => void close()}>Skip for now</button></div>
        {step === 'guide' ? <button type="button" className="primary" disabled={!!busy} onClick={() => setStep('key')}>I have an API key <ArrowRight size={16} /></button> :
          <button type="button" className="primary" disabled={!!busy || !tested || !consent} onClick={() => void finish()}>
            {busy === 'save' && <LoaderCircle size={16} className="spin" />}Save and enable Gemini</button>}
        <p className="gemini-setup-note">Reopen this guide anytime in Settings → Gemini setup guide.</p>
      </>}
    </footer>
  </dialog>
}
