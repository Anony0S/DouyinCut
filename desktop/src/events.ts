import { apiRequest } from './api'

type JobEventHandlers = {
  onJobs: () => void
  onOpen?: () => void
  onError: (message: string) => void
}

async function readJobEvents(response: Response, signal: AbortSignal, onJobs: () => void) {
  if (!response.body) throw new Error('Event stream unavailable')
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = '', event = '', hasData = false
  // Parse lines independently of transport chunks, including CRLF and split UTF-8.
  const line = (value: string) => {
    if (!value) {
      if (hasData && event === 'jobs' && !signal.aborted) onJobs()
      event = ''; hasData = false
      return
    }
    if (value.startsWith(':')) return
    const separator = value.indexOf(':')
    const field = separator < 0 ? value : value.slice(0, separator)
    let data = separator < 0 ? '' : value.slice(separator + 1)
    if (data.startsWith(' ')) data = data.slice(1)
    if (field === 'event') event = data
    if (field === 'data') hasData = true
  }
  try {
    while (!signal.aborted) {
      const { value, done } = await reader.read()
      if (signal.aborted) return
      buffer += decoder.decode(value, { stream: !done })
      let end: number
      while ((end = buffer.indexOf('\n')) >= 0) {
        line(buffer.slice(0, end).replace(/\r$/, ''))
        buffer = buffer.slice(end + 1)
      }
      if (done) return
    }
  } finally {
    try { await reader.cancel() } finally { reader.releaseLock() }
  }
}

function retryDelay(signal: AbortSignal, milliseconds: number): Promise<void> {
  return new Promise(resolve => {
    if (signal.aborted) { resolve(); return }
    const finish = () => {
      clearTimeout(timer)
      signal.removeEventListener('abort', finish)
      resolve()
    }
    const timer = setTimeout(finish, milliseconds)
    signal.addEventListener('abort', finish, { once: true })
  })
}

export function subscribeJobEvents(handlers: JobEventHandlers): () => void {
  const controller = new AbortController()
  const { signal } = controller
  void (async () => {
    let delay = 2000
    while (!signal.aborted) {
      try {
        const response = await apiRequest('/events', {
          headers: { Accept: 'text/event-stream' }, signal,
        })
        if (signal.aborted) return
        if (response.status === 401 || response.status === 403) {
          handlers.onError('本地会话已失效，请刷新页面；服务重启后请通过统一启动器重新打开')
          return
        }
        if (!response.ok || !response.headers.get('content-type')?.includes('text/event-stream')) {
          throw new Error('Event connection failed')
        }
        handlers.onOpen?.()
        await readJobEvents(response, signal, handlers.onJobs)
      } catch {
        if (signal.aborted) return
      }
      if (signal.aborted) return
      handlers.onError('任务连接中断，正在尝试重连')
      await retryDelay(signal, delay)
      delay = Math.min(delay * 2, 15000)
    }
  })()
  return () => controller.abort()
}
