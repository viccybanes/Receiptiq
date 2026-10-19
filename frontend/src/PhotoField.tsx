import { useEffect, useState } from 'react'
import { Camera, LoaderCircle } from 'lucide-react'
import { api } from './api'

type Props = { receiptId: string; photoId: string; label: string; original?: boolean;
  onChange: (id: string) => void; onBusy: (busy: boolean) => void; onError: (message: string) => void }

export default function PhotoField({ receiptId, photoId, label, original, onChange, onBusy, onError }: Props) {
  const [busy, setBusy] = useState(false)
  const [image, setImage] = useState('')
  useEffect(() => {
    setImage('')
    if (!photoId) return
    let alive = true
    let url = ''
    void api<Blob>('/receipts/' + receiptId + '/photos/' + photoId,
      { headers: { Accept: 'application/octet-stream' } }).then(blob => {
      if (alive) { url = URL.createObjectURL(blob); setImage(url) }
    }).catch((e: Error) => onError(e.message))
    return () => { alive = false; if (url) URL.revokeObjectURL(url) }
  }, [receiptId, photoId]) // eslint-disable-line

  return <div className="photo-field"><span>{label}</span>
    <div>{image && <img src={image} alt={label} />}
      {original && !photoId && <small>Original attached</small>}
      <label className="photo-button">{busy ? <LoaderCircle size={14} className="spin" /> : <Camera size={14} />}
        {photoId || original ? 'Replace photo' : 'Add photo'}
        <input type="file" accept="image/jpeg,image/png" aria-label={label + ' — Add photo'} disabled={busy}
          className="visually-hidden" onChange={async e => {
            const file = e.target.files?.[0]
            e.target.value = ''
            if (!file) return
            setBusy(true); onBusy(true)
            try {
              const form = new FormData(); form.append('file', file)
              const result = await api<{ id: string }>('/receipts/' + receiptId + '/photos', { method: 'POST', body: form })
              onChange(result.id)
            } catch (e) { onError((e as Error).message) }
            finally { setBusy(false); onBusy(false) }
          }} /></label>
      {photoId && <button type="button" className="quiet" onClick={() => onChange('')}>Remove photo</button>}
    </div>
  </div>
}
