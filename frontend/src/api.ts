const token = () => sessionStorage.getItem('ac-agent-token') || ''
export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch('/api' + path, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...(token() ? { Authorization: 'Bearer ' + token() } : {}),
      ...options.headers,
    },
  })
  if (!response.ok) {
    let message = `HTTP ${response.status}`
    try {
      const data = await response.json()
      message = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail)
    } catch {}
    throw new Error(message)
  }
  return response.json()
}
export async function download(path: string, name: string) {
  const r = await fetch('/api' + path, {
    headers: token() ? { Authorization: 'Bearer ' + token() } : {},
  })
  if (!r.ok) throw new Error(await r.text())
  const url = URL.createObjectURL(await r.blob())
  const a = document.createElement('a')
  a.href = url
  a.download = name
  a.click()
  setTimeout(() => URL.revokeObjectURL(url), 5000)
}
export function websocket() {
  return new WebSocket(
    `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`,
    token() ? ['ac-agent', token()] : ['ac-agent'],
  )
}
export function onboardSocket(role: 'sender' | 'viewer') {
  return new WebSocket(
    `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/onboard?role=${role}`,
    token() ? ['ac-agent', token()] : ['ac-agent'],
  )
}
/** Range-capable video URL; a <video> element cannot send the access header. */
export function videoUrl(recordingId: string) {
  const t = token()
  return `/api/videos/${encodeURIComponent(recordingId)}${t ? '?token=' + encodeURIComponent(t) : ''}`
}
/** Sound file (WAV) recorded with a video segment. */
export function audioUrl(recordingId: string) {
  const t = token()
  return `/api/audio/${encodeURIComponent(recordingId)}${t ? '?token=' + encodeURIComponent(t) : ''}`
}
/** POST raw bytes (video chunks); never throws. */
export async function uploadBytes(path: string, body: Blob) {
  try {
    const r = await fetch('/api' + path, {
      method: 'POST',
      body,
      headers: {
        'Content-Type': 'application/octet-stream',
        ...(token() ? { Authorization: 'Bearer ' + token() } : {}),
      },
    })
    let detail = ''
    let data: unknown
    try {
      data = await r.json()
      const d = (data as { detail?: unknown })?.detail
      if (d) detail = typeof d === 'string' ? d : JSON.stringify(d)
    } catch {}
    return { ok: r.ok, status: r.status, detail: detail || `HTTP ${r.status}`, body: data }
  } catch (e) {
    return { ok: false, status: 0, detail: String(e) }
  }
}
export function saveToken(value: string) {
  sessionStorage.setItem('ac-agent-token', value)
}
