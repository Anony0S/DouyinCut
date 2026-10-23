const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')

function load(name, imports = {}, transform = source => source) {
  const source = transform(fs.readFileSync(path.join(__dirname, `../src/${name}.ts`), 'utf8'))
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText
  const result = { exports: {} }
  new Function('require', 'module', 'exports', code)(name => imports[name] || require(name), result, result.exports)
  return result.exports
}

function streamResponse(text, splitEveryByte = false) {
  const bytes = new TextEncoder().encode(text)
  return new Response(new ReadableStream({
    start(controller) {
      if (splitEveryByte) for (const byte of bytes) controller.enqueue(Uint8Array.of(byte))
      else controller.enqueue(bytes)
      controller.close()
    },
  }), { headers: { 'Content-Type': 'text/event-stream; charset=utf-8' } })
}

async function main() {
  const originalFetch = global.fetch
  try {
    const calls = []
    global.fetch = async (url, options) => {
      calls.push({ url, options })
      return new Response('{}', { headers: { 'Content-Type': 'application/json' } })
    }
    const api = load('api', {}, source => source.replace("import.meta.env.VITE_SESSION_TOKEN || ''", "'mock-session-token'"))
    const signal = new AbortController().signal
    await api.apiRequest('/events', { signal, headers: { Accept: 'text/event-stream' } })
    assert.equal(calls[0].url, '/api/v1/events')
    assert.equal(calls[0].options.headers.get('X-Session-Token'), 'mock-session-token')
    assert.equal(calls[0].options.headers.get('Accept'), 'text/event-stream')
    assert.equal(calls[0].options.credentials, 'include')
    assert.equal(calls[0].options.signal, signal)
    await api.api('/assets')
    assert.equal(calls[1].options.headers.get('X-Session-Token'), 'mock-session-token')
  } finally { global.fetch = originalFetch }

  for (const status of [401, 403]) {
    let requests = 0, errors = []
    const events = load('events', { './api': { apiRequest: async () => {
      requests++; return new Response('{}', { status })
    } } })
    let stop
    await new Promise(resolve => {
      stop = events.subscribeJobEvents({ onJobs: () => assert.fail('Unauthorized event'), onError: message => { errors.push(message); resolve() } })
    })
    await new Promise(resolve => setTimeout(resolve, 10))
    stop()
    assert.equal(requests, 1, 'Auth rejection must stop automatic reconnect')
    assert.match(errors[0], /本地会话已失效/)
  }

  let requests = 0, jobs = 0, errors = [], stop
  const events = load('events', { './api': { apiRequest: async (url, options) => {
    requests++
    assert.equal(url, '/events')
    assert.equal(options.headers.Accept, 'text/event-stream')
    return streamResponse(': heartbeat\r\nevent: jobs\r\ndata: {"text":"中文"}\r\n\r\nevent: ignored\ndata: {}\n\nevent: jobs\ndata: []\n\n', true)
  } } })
  await new Promise(resolve => {
    stop = events.subscribeJobEvents({
      onJobs: () => { if (++jobs === 2) { stop(); resolve() } },
      onError: message => errors.push(message),
    })
  })
  assert.equal(jobs, 2, 'Fragmented UTF-8/CRLF and multiple events must parse correctly')
  assert.equal(requests, 1)
  assert.deepEqual(errors, [])

  let retries = 0, retryErrors = 0, retryStop, retrySignal
  const reconnect = load('events', { './api': { apiRequest: async (url, options) => {
    retrySignal = options.signal
    if (++retries === 1) throw new TypeError('Network disconnected')
    return streamResponse('event: jobs\ndata: []\n\n')
  } } })
  await new Promise(resolve => {
    retryStop = reconnect.subscribeJobEvents({
      onJobs: () => { retryStop(); resolve() },
      onError: () => { retryErrors++ },
    })
  })
  assert.equal(retries, 2, 'Network failure reconnects')
  assert.equal(retryErrors, 1)
  assert.equal(retrySignal.aborted, true, 'Unmount/stop aborts current connection')

  let cancelRequests = 0, cancelStop
  const cancelled = load('events', { './api': { apiRequest: async () => {
    cancelRequests++; return streamResponse('')
  } } })
  await new Promise(resolve => {
    cancelStop = cancelled.subscribeJobEvents({ onJobs() {}, onError: () => { cancelStop(); resolve() } })
  })
  await new Promise(resolve => setTimeout(resolve, 10))
  assert.equal(cancelRequests, 1, 'Stopping during reconnect delay prevents more requests')
  console.log('Event checks passed: shared session header, auth rejection, chunked SSE, reconnect and cancellation.')
}

main().catch(error => { console.error(error); process.exitCode = 1 })
