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
  id, started, ended, status: ended === null ? 'recording' : 'stopped', end_time_source: ended === null ? null : 'observed',
  product_events: [], product_listener: null, product_catalog_error: null,
  current_product: { promotion_id: id, product_name: `讲解-${id}`, event_time: started, received_at: started },
  segments: [{ id: `segment-${id}`, path: `${id}.ts`, segment_index: index, status: 'completed', size_bytes: 100, duration_us: 1e6, asset_id: null, import_job_id: null, clip_job: null }],
  product_clips: [{ id: `clip-${id}`, segment_id: `segment-${id}`, segment_index: index, promotion_id: id, product_name: `片段-${id}`, start: 0, end: 1, path: `${id}.mp4`, status: 'completed', size_bytes: 100, duration_us: 1e6, asset_id: null, import_job_id: null, error: '' }],
})
const monitor = (id, sessions) => ({ id, sessions, name: `直播间-${id}`, url: `https://live.douyin.com/${id}`, auto_record: false, notify_live: false, live_state: 'offline', is_live: false, live_checked: null, error: null })
let response = [monitor('一', [session('历史', now - 18000, now - 10800, 7), session('当前', now - 3600, null, 8)])]
let lastWindow
let detailResponse = null
const openedUrls = []
const openedSegments = []
const api = async (url, method, body) => {
  if (url === '/auth') return {}
  if (/^\/live\/segments\/[^/]+\/open$/.test(url)) {
    openedSegments.push({ url, method, body })
    return { opened: true, revealed: Boolean(body?.reveal), path: 'segment.ts' }
  }
  if (/^\/live\/monitors\/[^/]+\/sessions\//.test(url)) {
    return structuredClone(typeof detailResponse === 'function' ? await detailResponse(url) : detailResponse)
  }
  if (url.startsWith('/live/monitors')) {
    lastWindow = new URL(url, 'http://test').searchParams
    return structuredClone(typeof response === 'function' ? await response(lastWindow) : response)
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
  if (file.endsWith('.vue')) source = compileScript(parse(source, { templateParseOptions: { isCustomElement: tag => ['calendar-date', 'calendar-month'].includes(tag) } }).descriptor, { id: file, inlineTemplate: true, templateOptions: { compilerOptions: { isCustomElement: tag => ['calendar-date', 'calendar-month'].includes(tag) } } }).content
  const result = { exports: {} }; cache.set(file, result)
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText
  new Function('require', 'module', 'exports', code)(name => {
    if (name === 'vue') return { ...vue, vModelText: {}, vModelCheckbox: {} }
    if (name === '@tauri-apps/plugin-opener') return { openUrl: async url => openedUrls.push(url) }
    if (!name.startsWith('.')) return require(name)
    const dep = path.resolve(path.dirname(file), name)
    return load(path.extname(dep) ? dep : `${dep}.ts`)
  }, result, result.exports)
  return result.exports
}
const node = type => ({ focus() {}, type, props: {}, children: [], parent: null, text: '' })
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
const timelineSummary = root => all(root, el => el.props.class?.includes('live-recording-timeline-summary'))[0]
const deferred = () => {
  let resolve, reject
  const promise = new Promise((ok, fail) => { resolve = ok; reject = fail })
  return { promise, resolve, reject }
}
async function mount() {
  const root = node('root')
  const app = renderer.createApp(load(path.join(src, 'views/LiveMonitoringView.vue')).default)
  app.use(createPinia()); app.mount(root); await settle()
  return { root, app }
}
async function verify() {
  const workbenchCapability = JSON.parse(fs.readFileSync(path.join(__dirname, '../src-tauri/capabilities/workbench.json'), 'utf8'))
  assert.ok(workbenchCapability.windows.includes('main'), 'the development workbench window can open approved external URLs')
  assert.ok(workbenchCapability.windows.includes('workbench'), 'the production workbench window can open approved external URLs')
  const openerPermission = workbenchCapability.permissions.find(permission => typeof permission === 'object' && permission.identifier === 'opener:allow-open-url')
  assert.ok(openerPermission, 'workbench capability grants the scoped opener permission')
  assert.ok(openerPermission.allow.every(({ url }) => /^(https?):\/\/([^/]*\.)?douyin\.com\/\*$/.test(url)), 'opener only permits Douyin room domains')
  const { root, app } = await mount()
  try {
    assert.ok(text(root).includes('今天 · 2026-10-23 · 00:00–24:00'), 'page shows recording timeline')
    assert.equal(lastWindow.get('window_start'), '1792684800', 'today starts at local midnight')
    assert.equal(lastWindow.get('window_end'), '1792771200', 'today includes its unused evening hours')
    assert.equal(lastWindow.get('include_end_recording'), 'false', 'exclude the following midnight from the selected day')
    assert.equal(all(root, el => el.type === 'select').length, 0, 'timeline replaces the dropdown')
    assert.ok(text(root).includes('讲解-当前'), 'recording is selected before finished sessions')
    assert.ok(text(root).includes('片段-当前'))
    const productClipRow = all(root, el => el.type === 'article' && el.props.class?.includes('live-product-clip-row'))[0]
    const productClipActions = all(productClipRow, el => el.props.class?.includes('live-recording-actions'))[0]
    assert.ok(productClipActions, 'product clip actions reuse the recording segment interaction styles')
    for (const action of ['open-file', 'open-folder', 'import', 'delete']) {
      assert.ok(all(productClipActions, el => el.type === 'button' && el.props.class?.includes(`live-segment-action-${action}`)).length,
        `product clip exposes the shared ${action} action style`)
    }
    const segmentPathButton = selectButton(root, '查看录制分段')
    assert.ok(segmentPathButton, 'recording segment exposes a file path icon')
    assert.equal(segmentPathButton.props['aria-expanded'], false, 'file path tooltip starts closed')
    await click(segmentPathButton)
    assert.match(segmentPathButton.parent.props.class, /tooltip-open/, 'clicking the file path icon opens its tooltip')
    assert.match(segmentPathButton.parent.props.class, /tooltip-center/, 'file path tooltip is centered on its icon')
    assert.ok(text(root).includes('当前.ts'), 'file path tooltip contains the recording path')
    await click(segmentPathButton)
    assert.doesNotMatch(segmentPathButton.parent.props.class, /tooltip-open/, 'clicking the file path icon again closes its tooltip')
    await click(all(root, el => el.type === 'button' && text(el).trim() === '打开文件')[0])
    await click(all(root, el => el.type === 'button' && text(el).trim() === '打开文件夹')[0])
    assert.equal(openedSegments.length, 2, 'recording segment exposes file and folder actions')
    assert.equal(openedSegments[0].method, 'POST')
    assert.equal(openedSegments[0].body.reveal, false, 'file action opens the recording')
    assert.equal(openedSegments[1].body.reveal, true, 'folder action reveals the recording')
    const roomUrl = all(root, el => el.type === 'a' && el.props.class === 'live-room-selected-url')[0]
    assert.ok(roomUrl, 'selected room URL is a link')
    assert.equal(roomUrl.props.href, 'https://live.douyin.com/一')
    assert.equal(typeof roomUrl.props.onClick, 'function', 'selected room link explicitly opens in the system browser')
    let defaultPrevented = false
    await roomUrl.props.onClick({
      currentTarget: roomUrl,
      target: roomUrl,
      preventDefault() { defaultPrevented = true },
    })
    assert.equal(defaultPrevented, true, 'room link does not navigate inside the WebView')
    assert.deepEqual(openedUrls, [new URL(roomUrl.props.href).href], 'room link is passed to the external URL opener')
    const ticks = all(root, el => el.props.class === 'live-recording-timeline-ticks')[0]
    assert.deepEqual(ticks.children.filter(el => el.type === 'span').map(text), ['00:00', '06:00', '12:00', '18:00', '24:00'])
    const recentButton = selectButton(root, '回到今天')
    assert.ok(recentButton, 'return-to-today button stays visible when viewing today')
    assert.equal(recentButton.props.disabled, true, 'return-to-today is disabled when already today')
    const dateTrigger = all(root, el => el.type === 'summary' && el.props['aria-label'] === '录制日期')[0]
    const polling = deferred()
    const originalResponse = response
    response = () => polling.promise
    clockNow += 5000
    for (const poll of polls) poll()
    await settle()
    assert.equal(selectButton(root, '回到今天'), recentButton, 'polling retains the same return button')
    assert.equal(dateTrigger.props['aria-disabled'], false, 'background polling does not disable or dim the date trigger')
    assert.ok(!text(root).includes('正在读取…'), 'background polling does not insert a shifting date status')
    polling.resolve(originalResponse)
    response = originalResponse
    await settle()
    assert.equal(lastWindow.get('window_start'), '1792684800', 'same-day polling does not shift the start')
    assert.equal(lastWindow.get('window_end'), '1792771200', 'same-day polling does not shift the end')
    const recordingPeriod = all(root, el => el.type === 'button' && el.props.class?.includes('live-recording-timeline-period') && el.props.class?.includes('is-recording'))[0]
    assert.equal(parseFloat(recordingPeriod.props.style.left), 14 / 24 * 100)
    assert.ok(Math.abs(parseFloat(recordingPeriod.props.style.width) - 3605 / 86400 * 100) < 1e-8, 'ongoing period extends only to now and leaves future hours empty')
    assert.equal(all(root, el => el.props.role === 'group' && el.props['aria-label'] === '逐场选择录制场次').length, 0, 'timeline does not render duplicate session buttons')
    const historical = selectButton(root, '选择录制场次：')
    assert.ok(historical)
    const historicalStyle = { ...historical.props.style }
    historical.props.onMouseenter()
    await settle()
    assert.ok(text(root).includes('讲解-当前'), 'hovering does not change selected explanation')
    assert.equal(historical.props['aria-pressed'], false, 'hover does not select the period')
    assert.deepEqual(historical.props.style, historicalStyle, 'hover preserves the period position and size')
    const track = all(root, el => el.props.role === 'group' && el.props['aria-label'] === '选择录制场次')[0]
    track.props.onMouseleave?.() // Moving from the time bar to its summary stays inside the timeline.
    await settle()
    const summary = timelineSummary(root)
    assert.equal(summary.type, 'div', 'session summary is informational content, not a button')
    assert.ok(text(summary).includes('2 小时'), 'summary shows the full recording duration')
    assert.ok(all(summary, el => el.props.class?.includes('live-recording-timeline-duration')).length, 'session duration has a distinct emphasis style')
    assert.ok(text(summary).includes('已结束'), 'summary shows the session state')
    await click(historical)
    assert.ok(text(root).includes('讲解-历史'), 'clicking a timeline interval selects its session')
    await click(all(root, el => el.type === 'button' && el.props['aria-pressed'] === false
      && `${el.props['aria-label'] || ''}`.startsWith('选择录制场次：'))[0])
    assert.ok(text(root).includes('讲解-当前'))
    await click(historical)
    assert.ok(text(root).includes('讲解-历史'), 'click switches product explanation')
    assert.match(text(root), /分段\s+07/, 'click switches recording segments')
    assert.ok(text(root).includes('片段-历史'), 'click switches product clips')
    assert.ok(!text(root).includes('片段-当前'), 'previous session content is replaced')
  } finally { app.unmount(); clockNow = fixedNow }

  response = [monitor('一', [session('跨界', now - 59400, now - 52200, 9), session('较新', now - 7200, now - 3600, 10)]), monitor('二', [])]
  const second = await mount()
  try {
    assert.ok(text(second.root).includes('讲解-较新'), 'latest finished session is the default without an active recording')
    const crossBoundary = selectButton(second.root, '选择录制场次：')
    assert.ok(text(crossBoundary).includes('‹'), 'recording beginning before the window has a continuation marker')
    await click(crossBoundary)
    assert.ok(text(second.root).includes('讲解-跨界'))
    assert.ok(text(timelineSummary(second.root)).includes('2 小时'), 'clipping does not shorten the full duration')
    const room = all(second.root, el => el.props.role === 'button' && text(el).includes('直播间-二'))[0]
    await click(room)
    assert.ok(text(second.root).includes('今天无录制，可选择其他日期'))
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
    const input = all(dated.root, el => el.type === 'calendar-date' && el.props['aria-label'] === '录制日期')[0]
    assert.ok(input, 'recording timeline offers a historical date picker')
    assert.equal(input.props.max, '2026-10-23', 'today can be selected and future dates are disabled')
    assert.equal(input.props.value, '2026-10-23', 'calendar initially highlights today')
    assert.equal(all(dated.root, el => el.type === 'input' && el.props.type === 'date').length, 0, 'Cally replaces the native date input')
    const picker = all(dated.root, el => el.type === 'details' && el.props.class?.includes('dropdown'))[0]
    const dateTrigger = picker.children.find(el => el.type === 'summary')
    // daisyUI disables pointer events on a focused dropdown's first [tabindex] child.
    // Native summary is already keyboard-focusable; opting into this selector swallows clicks.
    const dropdownCss = fs.readFileSync(path.resolve(__dirname, '../node_modules/daisyui/components/dropdown.css'), 'utf8')
    if (dropdownCss.includes(':focus-within)>[tabindex]:first-child{pointer-events:none}')) {
      assert.ok(dateTrigger.props.tabindex == null, 'enabled date summary must not match daisyUI focus-within pointer-events:none rule')
    }
    picker.open = true
    picker.props.onToggle()
    await input.props.onChange({ target: { value: '2026-10-24' } }); await settle()
    assert.equal(lastWindow.get('window_start'), '1792684800', 'invalid future date does not change the applied window')
    await input.props.onChange({ target: { value: '2026-10-22' } })
    await settle()
    assert.equal(picker.open, false, 'choosing a historical date closes the calendar')
    assert.equal(input.props.value, '2026-10-22', 'calendar reflects the applied date')
    assert.equal(lastWindow.get('window_start'), '1792598400', 'historical day starts at local midnight')
    assert.equal(lastWindow.get('window_end'), '1792684800', 'historical day ends at next local midnight')
    assert.equal(lastWindow.get('include_end_recording'), 'false', 'historical day excludes recordings starting next midnight')
    assert.ok(text(dated.root).includes('2026-10-22'))
    assert.ok(text(dated.root).includes('讲解-昨晚'), 'changing date chooses the latest matching session')
    assert.equal(selectButton(dated.root, '回到今天').props.disabled, false, 'historical windows enable return-to-today')
    await input.props.onChange({ target: { value: '2026-10-23' } }); await settle()
    assert.equal(lastWindow.get('window_start'), '1792684800', 'selecting today restores the default day window')
    assert.equal(lastWindow.get('window_end'), '1792771200')
    assert.equal(input.props.value, '2026-10-23')
    assert.equal(selectButton(dated.root, '回到今天').props.disabled, true, 'selecting today disables the return button')
    await input.props.onChange({ target: { value: '2026-10-22' } }); await settle()
    const historyPoll = deferred()
    const historicalResponse = response
    response = () => historyPoll.promise
    for (const poll of polls) poll()
    await settle()
    assert.equal(selectButton(dated.root, '回到今天').props.disabled, false, 'background history polling keeps return-to-today usable')
    response = historicalResponse
    await click(selectButton(dated.root, '回到今天'))
    historyPoll.resolve([monitor('一', history)])
    await settle()
    assert.equal(lastWindow.get('window_start'), '1792684800')
    assert.ok(text(dated.root).includes('讲解-今天'))
    assert.ok(!text(dated.root).includes('讲解-昨晚'), 'late history poll cannot replace today')
    assert.equal(selectButton(dated.root, '回到今天').props.disabled, true, 'return button stays mounted and disables after returning')
  } finally { dated.app.unmount() }

  const overlap = session('跨日保留', 1792677600, 1792688400, 14)
  const earlier = session('昨天更早', 1792612800, 1792616400, 15)
  response = params => [{ ...monitor('一', [overlap, ...(Number(params.get('window_start')) === 1792598400 ? [earlier] : [])]), live_state: 'recording' }, monitor('二', [])]
  const retained = await mount()
  try {
    const dateInput = () => all(retained.root, el => el.type === 'calendar-date' && el.props['aria-label'] === '录制日期')[0]
    await dateInput().props.onChange({ target: { value: '2026-10-22' } }); await settle()
    assert.ok(text(retained.root).includes('讲解-跨日保留'), 'intersecting selection survives switching to a historical date')
    await click(selectButton(retained.root, '回到今天'))
    assert.ok(text(retained.root).includes('讲解-跨日保留'), 'returning to today preserves an intersecting selection')
    await dateInput().props.onChange({ target: { value: '2026-10-22' } }); await settle()
    const recordingRoom = all(retained.root, el => el.props.role === 'button' && text(el).includes('直播间-一'))[0]
    assert.ok(text(recordingRoom).includes('录制中'), 'historical sessions do not hide the room’s current recording state')
    const roomTwo = all(retained.root, el => el.props.role === 'button' && text(el).includes('直播间-二'))[0]
    await click(roomTwo)
    assert.equal(dateInput().props.value, '2026-10-22', 'switching rooms preserves the historical date')
    assert.ok(text(retained.root).includes('当天无录制'))
    assert.ok(!text(retained.root).includes('讲解-跨日保留'), 'empty historical room does not fall back to another room')
    await click(selectButton(retained.root, '清空日期'))
    assert.equal(lastWindow.get('window_start'), '1792684800', 'clearing the picker restores today')
    assert.ok(text(retained.root).includes('今天无录制，可选择其他日期'))
  } finally { retained.app.unmount() }

  response = [{ ...monitor('持续', [session('持续录制', 1792594800, null, 16)]), live_state: 'recording' }]
  const ongoing = await mount()
  try {
    const input = all(ongoing.root, el => el.type === 'calendar-date' && el.props['aria-label'] === '录制日期')[0]
    await input.props.onChange({ target: { value: '2026-10-22' } }); await settle()
    const period = selectButton(ongoing.root, '选择录制场次：')
    assert.ok(text(period).includes('‹') && text(period).includes('›'), 'ongoing session extends beyond both historical boundaries')
    assert.ok(text(timelineSummary(ongoing.root)).includes('40 小时'), 'ongoing duration is calculated to now, not historical midnight')
    assert.ok(text(ongoing.root).includes('讲解-持续录制'))
  } finally { ongoing.app.unmount() }
  const drifting = session('移出窗口', now - 52200, now - 45000, 17)
  const recent = session('窗口内', now - 1800, now - 600, 18)
  response = [monitor('一', [drifting, recent])]
  const preserved = await mount()
  try {
    await click(selectButton(preserved.root, '选择录制场次：'))
    response = [monitor('一', [drifting, recent, session('新录制', now, null, 19)])]
    for (const poll of polls) poll()
    await settle()
    assert.ok(text(preserved.root).includes('讲解-移出窗口'), 'a new recording does not take over a manual selection')
    response = [monitor('一', [recent, session('新录制', now, null, 19)])]
    detailResponse = { ...drifting, current_product: { ...drifting.current_product, product_name: '窗口外已更新' } }
    clockNow += 32700000
    for (const poll of polls) poll()
    await settle()
    assert.ok(text(preserved.root).includes('窗口外已更新'), 'polling retains and updates the selected session from the previous day')
    assert.ok(text(preserved.root).includes('片段-移出窗口'), 'all three cards continue showing the retained session')
    assert.match(text(preserved.root), /分段\s+17/)
    assert.ok(text(preserved.root).includes('所选场次已不在当前时间范围内'))
    assert.ok(text(timelineSummary(preserved.root)).includes('2 小时'), 'the retained selection keeps its summary even without a bar in the window')
    assert.equal(lastWindow.get('window_start'), '1792771200', 'today advances at midnight while the selection stays pinned')
    assert.equal(lastWindow.get('window_end'), '1792857600')
    assert.ok(text(preserved.root).includes('今天 · 2026-10-24 · 00:00–24:00'))
    assert.equal(all(preserved.root, el => el.type === 'calendar-date')[0].props.value, '2026-10-24', 'default calendar follows today across midnight')
    assert.equal(selectButton(preserved.root, '回到今天').props.disabled, true)
    response = [monitor('一', [drifting])]
    await click(selectButton(preserved.root, '查看该日期'))
    assert.equal(lastWindow.get('window_start'), '1792684800', 'the retained session opens its local calendar day')
    assert.ok(text(preserved.root).includes('讲解-移出窗口'))
  } finally { preserved.app.unmount(); clockNow = fixedNow; detailResponse = null }

  response = [monitor('一', [drifting, recent])]
  const removed = await mount()
  try {
    await click(selectButton(removed.root, '选择录制场次：'))
    clockNow += 32700000
    response = [monitor('一', [recent, session('继续录制', now, null, 20), session('最新结束', now + 60, now + 120, 21)])]
    detailResponse = () => { throw new Error('详情暂时不可用') }
    for (const poll of polls) poll()
    await settle()
    assert.ok(text(removed.root).includes('讲解-移出窗口'), 'a detail read failure keeps valid selected content')
    assert.ok(text(removed.root).includes('刷新录制失败：详情暂时不可用'), 'read failure is visible instead of an empty state')
    detailResponse = drifting
    for (const poll of polls) poll()
    await settle()
    assert.ok(text(removed.root).includes('所选场次已不在当前时间范围内'))
    assert.ok(!text(removed.root).includes('刷新录制失败'), 'successful retry clears the failure state')
    detailResponse = null
    for (const poll of polls) poll()
    await settle()
    assert.ok(text(removed.root).includes('讲解-最新结束'), 'confirmed deletion falls back to the latest start, even with another recording in progress')
    assert.ok(!text(removed.root).includes('所选场次已不在当前时间范围内'))
    response = [monitor('一', [])]
    for (const poll of polls) poll()
    await settle()
    assert.ok(text(removed.root).includes('今天无录制，可选择其他日期'))
    assert.ok(!text(removed.root).includes('讲解-最新结束'), 'confirmed deletion without candidates clears the old cards')
  } finally { removed.app.unmount(); clockNow = fixedNow; detailResponse = null }

  response = [monitor('一', [drifting, recent]), monitor('二', [session('另一间', now - 300, now - 100, 22)])]
  const racing = await mount()
  try {
    await click(selectButton(racing.root, '选择录制场次：'))
    const detail = deferred()
    detailResponse = () => detail.promise
    response = [monitor('一', [recent]), monitor('二', [session('另一间', now - 300, now - 100, 22)])]
    for (const poll of polls) poll()
    await settle()
    const latestButton = all(racing.root, el => el.type === 'button' && `${el.props['aria-label'] || ''}`.startsWith('选择录制场次：')).at(-1)
    await click(latestButton)
    assert.ok(text(racing.root).includes('讲解-窗口内'))
    detail.resolve(drifting)
    await settle()
    assert.ok(text(racing.root).includes('讲解-窗口内'), 'late detail response cannot overwrite a new manual selection')

    const oldWindow = deferred()
    response = () => oldWindow.promise
    const datePicker = all(racing.root, el => el.type === 'calendar-date')[0]
    datePicker.props.onChange({ target: { value: '2026-10-22' } })
    await settle()
    response = [monitor('一', [drifting]), monitor('二', [session('历史新结果', now - 90000, now - 89900, 23)])]
    await click(all(racing.root, el => el.props.role === 'button' && text(el).includes('直播间-二'))[0])
    assert.ok(text(racing.root).includes('讲解-历史新结果'), 'switching rooms starts a fresh request without waiting for an old window response')
    assert.equal(lastWindow.get('window_start'), '1792598400', 'room switch retains the pending historical date intent')
    oldWindow.resolve([monitor('一', [recent]), monitor('二', [session('过期结果', now - 90000, now - 89900, 24)])])
    await settle()
    assert.ok(text(racing.root).includes('讲解-历史新结果'), 'old window response cannot replace the newer room contents')
    assert.ok(!text(racing.root).includes('讲解-过期结果'))

    response = () => { throw new Error('历史查询失败') }
    await all(racing.root, el => el.type === 'calendar-date')[0].props.onChange({ target: { value: '2026-10-21' } }); await settle()
    assert.equal(all(racing.root, el => el.type === 'calendar-date')[0].props.value, '2026-10-22', 'failed date query restores the last applied date')
    assert.ok(text(racing.root).includes('讲解-历史新结果'))
    assert.ok(text(racing.root).includes('刷新录制失败：历史查询失败'))

    const returning = deferred()
    response = () => returning.promise
    const returnPromise = selectButton(racing.root, '回到今天').props.onClick({})
    await settle()
    assert.equal(selectButton(racing.root, '回到今天').props.disabled, true, 'date changes still lock repeated navigation until loaded')
    assert.equal(all(racing.root, el => el.type === 'summary' && el.props['aria-label'] === '录制日期')[0].props['aria-disabled'], true)
    response = [monitor('一', [drifting, session('新房正在录制', now - 60, null, 25)]), monitor('二', [])]
    await click(all(racing.root, el => el.props.role === 'button' && text(el).includes('直播间-一'))[0])
    assert.ok(text(racing.root).includes('讲解-新房正在录制'), 'switching rooms during a date request applies the default in the target window, not the old historical selection')
    returning.resolve([monitor('二', [])])
    await returnPromise; await settle()
    assert.ok(text(racing.root).includes('讲解-新房正在录制'))
  } finally { racing.app.unmount(); detailResponse = null }
}
async function verifyDenseSessions() {
  const dense = Array.from({ length: 30 }, (_, index) => session(`短场-${index + 1}`, now - 1800 + index, now - 1799 + index, index + 1))
  dense[0] = { ...dense[0], status: 'failed', end_time_source: 'recovery_check' }
  dense[1] = { ...dense[1], status: 'failed' }
  response = [monitor('密集', dense)]
  const { root, app } = await mount()
  const warning = '异常结束，结束时间可能晚于实际停录时间'
  try {
    const choices = () => all(root, el => el.type === 'button' && el.props.class?.includes('live-recording-timeline-period'))
    assert.equal(choices().length, 30, 'every one-second session remains clickable on the timeline')
    assert.equal(timelineSummary(root).type, 'div', 'dense recordings share one informational summary')
    choices()[0].props.onFocus()
    await settle()
    assert.ok(text(timelineSummary(root)).includes(warning), 'recovery-check end time shows its uncertainty')
    assert.ok(text(root).includes('讲解-短场-30'), 'focusing a different session only previews it')
    all(root, el => el.type === 'section' && el.props['aria-label'] === '录制时间线')[0].props.onMouseleave()
    await settle()
    assert.ok(text(timelineSummary(root)).includes(warning), 'leaving with the pointer does not hide a keyboard-focused preview')
    choices()[1].props.onMouseenter()
    await settle()
    assert.ok(!text(timelineSummary(root)).includes(warning), 'moving the pointer to another session can preview it even while a previous button holds focus')
    all(root, el => el.type === 'section' && el.props['aria-label'] === '录制时间线')[0].props.onMouseleave()
    await settle()
    assert.ok(text(timelineSummary(root)).includes(warning), 'pointer exit restores the focused preview')
    choices()[0].props.onBlur()
    await settle()
    assert.ok(!text(timelineSummary(root)).includes(warning), 'leaving focus restores the selected session summary')
    choices()[1].props.onMouseenter()
    await settle()
    assert.ok(text(timelineSummary(root)).includes('异常结束'))
    assert.ok(!text(timelineSummary(root)).includes(warning), 'an observed failure does not claim a recovery time error')
    for (let i = 0; i < choices().length; i++) {
      await click(choices()[i])
      assert.ok(text(root).includes(`讲解-短场-${i + 1}`), 'each adjacent short session selects its own explanation')
      assert.ok(text(root).includes(`片段-短场-${i + 1}`))
      assert.match(text(root), new RegExp(`分段\\s+${String(i + 1).padStart(2, '0')}`))
      assert.equal(choices()[i].props['aria-pressed'], true)
    }
  } finally { app.unmount() }

  response = [{ ...monitor('一', []), url: 'https://example.com/not-a-douyin-room' }]
  const unsafe = await mount()
  try {
    const openedBeforeUnsafeUrl = [...openedUrls]
    const roomUrl = all(unsafe.root, el => el.type === 'a' && el.props.class === 'live-room-selected-url')[0]
    await roomUrl.props.onClick({ preventDefault() {} })
    assert.deepEqual(openedUrls, openedBeforeUnsafeUrl, 'non-Douyin room URLs are rejected before opening')
  } finally { unsafe.app.unmount() }
}
async function verifyLocalCalendarDay() {
  const date = checkMidnightDst ? '2018-11-04' : '2026-11-01'
  const start = checkMidnightDst ? '1541300400' : '1793505600'
  const end = checkMidnightDst ? '1541383200' : '1793595600'
  response = [monitor('时区', [])]
  const { root, app } = await mount()
  try {
    const input = all(root, el => el.type === 'calendar-date' && el.props['aria-label'] === '录制日期')[0]
    assert.equal(input.props.max, checkMidnightDst ? '2018-11-05' : '2026-11-02')
    await input.props.onChange({ target: { value: date } }); await settle()
    assert.equal(lastWindow.get('window_start'), start, 'calendar day begins at the first valid local instant')
    assert.equal(lastWindow.get('window_end'), end, 'calendar day ends at next midnight, even when DST skipped the first midnight')
    clockNow += 86400000
    for (const poll of polls) poll()
    await settle()
    assert.equal(lastWindow.get('window_start'), start, 'polling preserves historical calendar boundaries')
    assert.equal(lastWindow.get('window_end'), end)
    assert.equal(input.props.max, checkMidnightDst ? '2018-11-06' : '2026-11-03', 'latest selectable date advances with the local calendar')
    assert.ok(text(root).includes('当天无录制'))
  } finally { app.unmount() }
}
(checkDst || checkMidnightDst ? verifyLocalCalendarDay() : verify().then(verifyDenseSessions)).then(() => {
  console.log(checkDst || checkMidnightDst ? `historical calendar checks passed (${process.env.TZ})` : 'live recording timeline page checks passed')
  if (!checkDst && !checkMidnightDst) {
    for (const option of ['--dst', '--midnight-dst']) require('node:child_process').execFileSync(process.execPath, [__filename, option], { stdio: 'inherit' })
  }
}).catch(error => { console.error(error); process.exitCode = 1 }).finally(() => {
  Date.now = originalNow
  global.setInterval = originalSetInterval; global.clearInterval = originalClearInterval
})
