const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vue = require('vue')
const ts = require('typescript')

let now = 0
let sequence = 0
const timers = new Map()
const requests = []
let delayed = false
const pending = []
const response = text => ({ ok: true, blob: async () => new Blob([text]) })
const nativeSetTimeout = global.setTimeout
const nativeClearTimeout = global.clearTimeout
global.setTimeout = (callback, delay) => { const id = ++sequence; timers.set(id, { callback, at: now + delay }); return id }
global.clearTimeout = id => timers.delete(id)
const flush = async () => { for (let i = 0; i < 12; i++) { await Promise.resolve(); await vue.nextTick() } }
async function advance(ms) {
  const end = now + ms
  while (true) {
    const next = [...timers].filter(([, timer]) => timer.at <= end).sort((a, b) => a[1].at - b[1].at)[0]
    if (!next) break
    now = next[1].at; timers.delete(next[0]); next[1].callback(); await flush()
  }
  now = end; await flush()
}
const file = path.join(__dirname, '../src/components/live/useTimelineThumbnail.ts')
const code = ts.transpileModule(fs.readFileSync(file, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText
const mod = { exports: {} }
new Function('require', 'module', 'exports', code)(name => name === 'vue' ? vue : { apiRequest: async url => {
  requests.push({ at: now, url })
  if (delayed) return new Promise(resolve => pending.push(resolve))
  return response('frame')
} }, mod, mod.exports)
const hover = vue.ref(null)
let state
const renderer = vue.createRenderer({ createComment: () => ({}), insert() {}, remove() {}, parentNode() {}, nextSibling() {} })
const app = renderer.createApp({ setup() { state = mod.exports.useTimelineThumbnail(() => 'session', hover); return () => null } })
app.mount({})
const target = second => ({ periodId: 'period', at: second, key: String(second), message: '', anchor: {} })
;(async () => {
  try {
    hover.value = target(0); await flush()
    assert.equal(requests.length, 1, 'first hover starts a request without waiting for a timer')
    assert.equal(requests[0].at, 0, 'first hover has no artificial delay')
    for (let second = 1; second <= 10; second++) { await advance(80); hover.value = target(second); await flush() }
    console.log(`continuous movement: ${requests.length} requests by ${now}ms`)
    assert.ok(requests.length > 0, 'continuous mouse movement must not indefinitely postpone previews')
    await advance(200)
    const count = requests.length
    hover.value = null; await flush(); hover.value = target(10); await flush()
    assert.ok(state.image.value, 'cached target appears immediately')
    assert.equal(requests.length, count, 'cached target does not decode again')
    delayed = true
    hover.value = null; await flush(); hover.value = target(20); await flush()
    assert.equal(pending.length, 1, 're-entering at an uncached position starts immediately')
    hover.value = target(21); await flush(); await advance(100)
    assert.equal(pending.length, 1)
    const firstRequest = requests.length
    await advance(20)
    hover.value = target(22); await flush(); await advance(100)
    assert.equal(requests.length, firstRequest, 'only one request is in flight while pointer moves')
    pending.shift()(response('old frame'))
    await flush()
    assert.equal(state.image.value, '', 'obsolete response never appears at the new pointer time')
    await advance(0)
    assert.equal(requests.length, firstRequest + 1, 'latest request follows immediately without a polling delay')
    assert.ok(requests.at(-1).url.endsWith('at=22'), 'intermediate pointer positions are skipped')
    pending.shift()(response('latest frame'))
    await flush()
    assert.ok(state.image.value, 'latest completed frame is displayed')
    console.log('timeline thumbnail scheduling checks passed')
  } finally { app.unmount(); global.setTimeout = nativeSetTimeout; global.clearTimeout = nativeClearTimeout }
})().catch(error => { console.error(error); process.exitCode = 1 })
