import { request } from './api.js'

// One cursor and lifecycle per mounted authenticated workspace.
export function subscribeEvents(onChange, { stream = true } = {}) {
  let stopped = false, source = null, timer = null, cursor = null, failures = 0
  const accept = page => {
    if (stopped || !page || typeof page.cursor !== 'string' || !Array.isArray(page.items)) return
    cursor = page.cursor
    if (page.reset || page.items.length) onChange(page)
  }
  const poll = async () => {
    if (stopped) return
    try {
      const page = await request(`/api/_events${cursor === null ? '' : `?cursor=${encodeURIComponent(cursor)}`}`)
      // Records may have loaded while the initial cursor request was unavailable.
      accept(cursor === null ? { ...page, reset: true } : page)
      if (!stopped) timer = setTimeout(poll, page.more ? 50 : 3000)
    } catch { if (!stopped) timer = setTimeout(poll, 5000) }
  }
  const fallback = () => {
    source?.close(); source = null
    if (!stopped && timer === null) timer = setTimeout(() => { timer = null; poll() }, 1000)
  }
  const close = () => { stopped = true; source?.close(); clearTimeout(timer) }
  close.ready = (async () => {
    try {
      // Establish the replay position before the workspace reads its first page.
      accept(await request('/api/_events'))
      if (stopped) return
      if (!stream || typeof EventSource === 'undefined') { timer = setTimeout(poll, 3000); return }
      source = new EventSource(`/api/_events/stream${cursor === null ? '' : `?cursor=${encodeURIComponent(cursor)}`}`)
      source.addEventListener('evlet', event => {
        try { accept(JSON.parse(event.data)); failures = 0 } catch { fallback() }
      })
      source.addEventListener('unavailable', () => {
        close(); window.dispatchEvent(new Event('evlet-session-ended'))
      })
      // Normal bounded-stream reconnects replay Last-Event-ID automatically.
      source.onerror = () => { if (++failures >= 2 || source?.readyState === 2) fallback() }
    } catch { fallback() }
  })()
  return close
}
