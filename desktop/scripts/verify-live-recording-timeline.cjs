const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vue = require('vue')
const ts = require('typescript')
const { parse, compileScript } = require('vue/compiler-sfc')
const { createPinia } = require('pinia')
const checkDst = process.argv.includes('--dst')
const checkMidnightDst = process.argv.includes('--midnight-dst')
process.env.TZ = checkMidnightDst ? 'America/Sao_Paulo' : checkDst ? 'America/New_York' : 'Asia/Shanghai'

// Mount the real page and its children; substitute only network, navigation and time.
const src = path.resolve(__dirname, '../src')
const fixedNow = new Date(checkMidnightDst ? '2018-11-05T15:00:00-02:00' : checkDst ? '2026-11-02T15:00:00-05:00' : '2026-10-23T15:00:00+08:00').getTime()
const now = fixedNow / 1000
const originalNow = Date.now
let clockNow = fixedNow
Date.now = () => clockNow
const originalSetInterval = global.setInterval, originalClearInterval = global.clearInterval
const polls = new Set()
global.setInterval = callback => { polls.add(callback); return callback }
global.clearInterval = callback => { polls.delete(callback) }
const session = (id, started, ended, index) => ({
  id, started, ended, status: ended === null ? 'recording' : 'stopped',
  product_events: [], product_listener: null, product_catalog_error: null,
  current_product: { promotion_id: id, product_name: `讲解-${id}`, event_time: started, received_at: started },
  segments: [{ id: `segment-${id}`, path: `${id}.ts`, segment_index: index, status: 'completed', size_bytes: 100, duration_us: 1e6, asset_id: null, import_job_id: null, clip_job: null }],
  product_clips: [{ id: `clip-${id}`, segment_id: `segment-${id}`, segment_index: index, promotion_id: id, product_name: `片段-${id}`, start: 0, end: 1, path: `${id}.mp4`, status: 'completed', size_bytes: 100, duration_us: 1e6, asset_id: null, import_job_id: null, error: '' }],
})
const monitor = (id, sessions) => ({ id, sessions, name: `直播间-${id}`, url: `https://live.douyin.com/${id}`, auto_record: false, notify_live: false, live_state: 'offline', is_live: false, live_checked: null, error: null })
let response = [monitor('一', [session('历史', now - 18000, now - 10800, 7), session('当前', now - 3600, null, 8)])]
let lastWindow
const api = async url => {
  if (url === '/auth') return {}
  if (url.startsWith('/live/monitors')) {
    lastWindow = new URL(url, 'http://test').searchParams
    return structuredClone(typeof response === 'function' ? response(lastWindow) : response)
  }
  throw new Error(`Unexpected request: ${url}`)
}
const overrides = new Map([
  [path.join(src, 'api.ts'), { api, duration: n => `${n / 1e6} 秒`, seconds: n => n / 1e6 }],
  [path.join(src, 'workspace.ts'), { useWorkspace: () => ({ openProject: async () => {} }) }],
])
const cache = new Map()
function load(file) {
  file = path.resolve(file)
  if (overrides.has(file)) return overrides.get(file)
  if (cache.has(file)) return cache.get(file).exports
  if (file.endsWith('.css')) return {}
  let source = fs.readFileSync(file, 'utf8')
  if (file.endsWith('.vue')) source = compileScript(parse(source).descriptor, { id: file, inlineTemplate: true }).content
  const result = { exports: {} }; cache.set(file, result)
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText
  new Function('require', 'module', 'exports', code)(name => {
    if (name === 'vue') return { ...vue, vModelText: {}, vModelCheckbox: {} }
    if (!name.startsWith('.')) return require(name)
    const dep = path.resolve(path.dirname(file), name)
    return load(path.extname(dep) ? dep : `${dep}.ts`)
  }, result, result.exports)
  return result.exports
}
const node = type => ({ type, props: {}, children: [], parent: null, text: '' })
global.window = { addEventListener() {}, removeEventListener() {} }
const body = node('body')
const renderer = vue.createRenderer({
  createElement: node, createComment: () => node('comment'), createText: text => ({ ...node('text'), text }),
  setText: (el, text) => { el.text = text }, setElementText: (el, text) => { el.text = text },
  patchProp: (el, key, old, value) => { el.props[key] = value },
  insert(el, parent, anchor) { if (el.parent) this.remove(el); const i = parent.children.indexOf(anchor); parent.children.splice(i < 0 ? parent.children.length : i, 0, el); el.parent = parent },
  remove(el) { if (el.parent) el.parent.children.splice(el.parent.children.indexOf(el), 1); el.parent = null },
  parentNode: el => el.parent, nextSibling: el => el.parent?.children[el.parent.children.indexOf(el) + 1],
  querySelector: () => body,
})
const all = (el, predicate) => [...(predicate(el) ? [el] : []), ...el.children.flatMap(child => all(child, predicate))]
const text = el => el.text + el.children.map(text).join('')
const settle = async () => { for (let i = 0; i < 16; i++) { await Promise.resolve(); await vue.nextTick() } }
const click = async el => { assert.ok(el, 'interactive target exists'); await el.props.onClick({ currentTarget: el, target: el }); await settle() }
const selectButton = (root, label) => all(root, el => el.type === 'button' && `${el.props['aria-label'] || ''}`.startsWith(label))[0]
async function mount() {
  const root = node('root')
  const app = renderer.createApp(load(path.join(src, 'views/LiveMonitoringView.vue')).default)
  app.use(createPinia()); app.mount(root); await settle()
  return { root, app }
}
async function verify() {
  const { root, app } = await mount()
  try {
    assert.ok(text(root).includes('最近 24 小时'), 'page shows recording timeline')
    assert.equal(lastWindow.get('window_start'), String(now - 86400), 'request covers the complete recent day')
    assert.equal(lastWindow.get('window_end'), String(now))
    assert.equal(lastWindow.get('include_end_recording'), 'true', 'recent window includes a recording just starting now')
    assert.equal(all(root, el => el.type === 'select').length, 0, 'timeline replaces the dropdown')
    assert.ok(text(root).includes('讲解-当前'), 'recording is selected before finished sessions')
    assert.ok(text(root).includes('片段-当前'))
    const historical = selectButton(root, '选择录制场次：')
    assert.ok(historical)
    historical.props.onMouseenter()
    await settle()
    assert.ok(text(root).includes('讲解-当前'), 'hovering does not change selected explanation')
    const track = all(root, el => el.props.role === 'group' && el.props['aria-label'] === '选择录制场次')[0]
    track.props.onMouseleave?.() // Moving from the time bar to its summary stays inside the timeline.
    await settle()
    const summary = selectButton(root, '查看录制场次：')
    assert.ok(text(summary).includes('2 小时'), 'summary shows the full recording duration')
    await click(summary)
    assert.ok(text(root).includes('讲解-历史'), 'summary card selects the corresponding whole session')
    await click(all(root, el => el.type === 'button' && el.props['aria-pressed'] === false
      && `${el.props['aria-label'] || ''}`.startsWith('选择录制场次：'))[0])
    assert.ok(text(root).includes('讲解-当前'))
    await click(historical)
    assert.ok(text(root).includes('讲解-历史'), 'click switches product explanation')
    assert.match(text(root), /分段\s+07/, 'click switches recording segments')
    assert.ok(text(root).includes('片段-历史'), 'click switches product clips')
    assert.ok(!text(root).includes('片段-当前'), 'previous session content is replaced')
  } finally { app.unmount() }

  response = [monitor('一', [session('跨界', now - 90000, now - 82800, 9), session('较新', now - 7200, now - 3600, 10)]), monitor('二', [])]
  const second = await mount()
  try {
    assert.ok(text(second.root).includes('讲解-较新'), 'latest finished session is the default without an active recording')
    const crossBoundary = selectButton(second.root, '选择录制场次：')
    assert.ok(text(crossBoundary).includes('‹'), 'recording beginning before the window has a continuation marker')
    await click(crossBoundary)
    assert.ok(text(second.root).includes('讲解-跨界'))
    assert.ok(text(selectButton(second.root, '查看录制场次：')).includes('2 小时'), 'clipping does not shorten the full duration')
    const room = all(second.root, el => el.props.role === 'button' && text(el).includes('直播间-二'))[0]
    await click(room)
    assert.ok(text(second.root).includes('最近 24 小时无录制，可选择历史日期'))
    assert.equal(all(second.root, el => el.type === 'h3' && ['商品讲解', '录制分段', '商品视频片段'].includes(text(el))).length, 3, 'empty room retains all three empty cards')
    assert.ok(!text(second.root).includes('讲解-跨界'), 'switching rooms clears old session contents')
    const firstRoom = all(second.root, el => el.props.role === 'button' && text(el).includes('直播间-一'))[0]
    await click(firstRoom)
    assert.ok(text(second.root).includes('讲解-较新'), 'switching back applies default selection again')
  } finally { second.app.unmount() }

  const history = [session('昨晚', 1792677600, 1792681200, 11), session('跨午夜', 1792594800, 1792602000, 12)]
  response = params => [monitor('一', Number(params.get('window_start')) === 1792598400 ? history : [session('今天', now - 7200, now - 3600, 13)])]
  const dated = await mount()
  try {
    const input = all(dated.root, el => el.type === 'input' && el.props['aria-label'] === '录制日期')[0]
    assert.ok(input, 'recording timeline offers a historical date picker')
    assert.equal(input.props.max, '2026-10-22', 'today and future dates cannot be selected')
    await input.props.onChange({ target: { value: '2026-10-22' } })
    await settle()
    assert.equal(lastWindow.get('window_start'), '1792598400', 'historical day starts at local midnight')
    assert.equal(lastWindow.get('window_end'), '1792684800', 'historical day ends at next local midnight')
    assert.equal(lastWindow.get('include_end_recording'), 'false', 'historical day excludes recordings starting next midnight')
    assert.ok(text(dated.root).includes('2026-10-22'))
    assert.ok(text(dated.root).includes('讲解-昨晚'), 'changing date chooses the latest matching session')
    await click(selectButton(dated.root, '回到最近'))
    assert.equal(lastWindow.get('window_start'), String(now - 86400))
    assert.ok(text(dated.root).includes('讲解-今天'))
  } finally { dated.app.unmount() }

  const overlap = session('跨日保留', 1792677600, 1792688400, 14)
  const earlier = session('昨天更早', 1792612800, 1792616400, 15)
  response = params => [{ ...monitor('一', [overlap, ...(Number(params.get('window_start')) === 1792598400 ? [earlier] : [])]), live_state: 'recording' }, monitor('二', [])]
  const retained = await mount()
  try {
    const dateInput = () => all(retained.root, el => el.type === 'input' && el.props['aria-label'] === '录制日期')[0]
    await dateInput().props.onChange({ target: { value: '2026-10-22' } }); await settle()
    assert.ok(text(retained.root).includes('讲解-跨日保留'), 'intersecting selection survives switching to a historical date')
    await click(selectButton(retained.root, '回到最近'))
    assert.ok(text(retained.root).includes('讲解-跨日保留'), 'returning to recent preserves an intersecting selection')
    await dateInput().props.onChange({ target: { value: '2026-10-22' } }); await settle()
    const recordingRoom = all(retained.root, el => el.props.role === 'button' && text(el).includes('直播间-一'))[0]
    assert.ok(text(recordingRoom).includes('录制中'), 'historical sessions do not hide the room’s current recording state')
    const roomTwo = all(retained.root, el => el.props.role === 'button' && text(el).includes('直播间-二'))[0]
    await click(roomTwo)
    assert.equal(dateInput().props.value, '2026-10-22', 'switching rooms preserves the historical date')
    assert.ok(text(retained.root).includes('当天无录制'))
    assert.ok(!text(retained.root).includes('讲解-跨日保留'), 'empty historical room does not fall back to another room')
    await dateInput().props.onChange({ target: { value: '' } }); await settle()
    assert.equal(lastWindow.get('window_start'), String(now - 86400), 'clearing the picker restores the recent day')
    assert.ok(text(retained.root).includes('最近 24 小时无录制，可选择历史日期'))
  } finally { retained.app.unmount() }

  response = [{ ...monitor('持续', [session('持续录制', 1792594800, null, 16)]), live_state: 'recording' }]
  const ongoing = await mount()
  try {
    const input = all(ongoing.root, el => el.type === 'input' && el.props['aria-label'] === '录制日期')[0]
    await input.props.onChange({ target: { value: '2026-10-22' } }); await settle()
    const period = selectButton(ongoing.root, '选择录制场次：')
    assert.ok(text(period).includes('‹') && text(period).includes('›'), 'ongoing session extends beyond both historical boundaries')
    assert.ok(text(selectButton(ongoing.root, '查看录制场次：')).includes('40 小时'), 'ongoing duration is calculated to now, not historical midnight')
    assert.ok(text(ongoing.root).includes('讲解-持续录制'))
  } finally { ongoing.app.unmount() }
}
async function verifyLocalCalendarDay() {
  const date = checkMidnightDst ? '2018-11-04' : '2026-11-01'
  const start = checkMidnightDst ? '1541300400' : '1793505600'
  const end = checkMidnightDst ? '1541383200' : '1793595600'
  response = [monitor('时区', [])]
  const { root, app } = await mount()
  try {
    const input = all(root, el => el.type === 'input' && el.props['aria-label'] === '录制日期')[0]
    assert.equal(input.props.max, date)
    await input.props.onChange({ target: { value: date } }); await settle()
    assert.equal(lastWindow.get('window_start'), start, 'calendar day begins at the first valid local instant')
    assert.equal(lastWindow.get('window_end'), end, 'calendar day ends at next midnight, even when DST skipped the first midnight')
    clockNow += 86400000
    for (const poll of polls) poll()
    await settle()
    assert.equal(lastWindow.get('window_start'), start, 'polling preserves historical calendar boundaries')
    assert.equal(lastWindow.get('window_end'), end)
    assert.equal(input.props.max, checkMidnightDst ? '2018-11-05' : '2026-11-02', 'latest selectable date advances with the local calendar')
    assert.ok(text(root).includes('当天无录制'))
  } finally { app.unmount() }
}
(checkDst || checkMidnightDst ? verifyLocalCalendarDay() : verify()).then(() => {
  console.log(checkDst || checkMidnightDst ? `historical calendar checks passed (${process.env.TZ})` : 'live recording timeline page checks passed')
  if (!checkDst && !checkMidnightDst) {
    for (const option of ['--dst', '--midnight-dst']) require('node:child_process').execFileSync(process.execPath, [__filename, option], { stdio: 'inherit' })
  }
}).catch(error => { console.error(error); process.exitCode = 1 }).finally(() => {
  Date.now = originalNow
  global.setInterval = originalSetInterval; global.clearInterval = originalClearInterval
})
