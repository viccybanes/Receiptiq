let token = ''

export class ApiError extends Error {
  constructor(message: string, public status: number) { super(message) }
}

export async function connect() {
  const desktopToken = (window as Window & { __RECEIPTIQ_TOKEN__?: string }).__RECEIPTIQ_TOKEN__
  const response = await fetch('/api/v1/session', { cache: 'no-store',
    headers: desktopToken ? { Authorization: 'Bearer ' + desktopToken } : {} })
  if (!response.ok) throw new Error('Cannot connect to ReceiptIQ. Start the local server, then reload.')
  token = (await response.json()).token
}

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers)
  headers.set('Authorization', 'Bearer ' + token)
  if (options.body && !(options.body instanceof FormData)) headers.set('Content-Type', 'application/json')
  const response = await fetch('/api/v1' + path, { ...options, headers, cache: 'no-store' })
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    const message = typeof body.detail === 'string' ? body.detail : Array.isArray(body.detail) ?
      body.detail[0]?.msg : body.detail?.message
    throw new ApiError(message || 'Request failed. Retry.', response.status)
  }
  if (options.headers && new Headers(options.headers).get('Accept') === 'application/octet-stream') {
    return await response.blob() as T
  }
  return response.json()
}

export function post<T>(path: string, data: unknown = {}) {
  return api<T>(path, { method: 'POST', body: JSON.stringify(data) })
}

export async function download(path: string, name: string) {
  const blob = await api<Blob>(path, { headers: { Accept: 'application/octet-stream' } })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = name
  link.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
