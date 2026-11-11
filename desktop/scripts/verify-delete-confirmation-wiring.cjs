const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vue = require('vue')
const ts = require('typescript')
const { parse, compileScript } = require('vue/compiler-sfc')
const { createPinia, setActivePinia } = require('pinia')
const src = path.resolve(__dirname, '../src')
const overrides = new Map()
const stub = { __esModule: true, default: { render: () => null } }
const workspace = { refreshProjects: async () => {}, goProjects: async () => {}, openProject: async () => {} }
overrides.set(path.join(src, 'workspace.ts'), { useWorkspace: () => workspace })
overrides.set(path.join(src, 'composables/useVideoImport.ts'), { useVideoImport: () => ({ chooseNewVideo() {} }) })
overrides.set(path.join(src, 'components/WorkspaceSplit.vue'), { __esModule: true, default: { setup(_, { slots }) { return () => vue.h('div', [slots.left?.(), slots.right?.()]) } } })
for (const file of ['components/JobErrorDetails.vue', 'components/live/LiveIcon.vue']) overrides.set(path.join(src, file), stub)
const router = { useRoute: () => ({ query: {}, params: { id: 'video' } }) }
const calls = []
let fail = false
const clip = id => ({ candidate_id: id, source_asset_id: 'video', source_start_us: 0, source_end_us: 2e6, timeline_start_us: 0 })
const plan = id => ({ id, revision: 1, clips: [clip('a'), clip('b')], warnings: [], subtitles: [] })
const api = async (url, method) => {
  if (method === 'DELETE') {
    calls.push(url)
    if (fail) throw new Error('活动任务禁止删除')
    return { cleanup_pending: [], file_deleted: true, project_file_retained: false }
  }
  if (url.startsWith('/projects?')) return { items: [], total: 0, page: 1, pages: 1, page_size: 20 }
  if (url === '/auth') return {}
  throw new Error(`Unexpected API: ${url}`)
}
overrides.set(path.join(src, 'api.ts'), { api, duration: () => '2 秒', seconds: n => n / 1e6 })
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
    if (name === 'vue') return { ...vue, vModelText: {}, vModelSelect: {}, vModelCheckbox: {} }
    if (name === 'vue-router') return router
    if (!name.startsWith('.')) return require(name)
    const dep = path.resolve(path.dirname(file), name)
    return load(path.extname(dep) ? dep : `${dep}.ts`)
  }, result, result.exports)
  return result.exports
}
const listeners = new Map()
global.window = { innerWidth: 800, innerHeight: 600,
  addEventListener(type, fn) { if (!listeners.has(type)) listeners.set(type, new Set()); listeners.get(type).add(fn) },
  removeEventListener(type, fn) { listeners.get(type)?.delete(fn) },
}
const node = type => { const el = { type, props: {}, children: [], parent: null, text: '',
  getBoundingClientRect: () => ({ left: 40, right: 90, top: 120, bottom: 150 }),
  focus() {}, contains(target) { return target === el || el.children.some(c => c.contains(target)) },
}; return el }
const body = node('body')
const renderer = vue.createRenderer({ createElement: node, createText: text => Object.assign(node('text'), { text }), createComment: node,
  setText: (el, text) => { el.text = text }, setElementText: (el, text) => { el.text = text },
  patchProp: (el, key, old, value) => { el.props[key] = value },
  insert(el, parent, anchor) { if (el.parent) this.remove(el); const i = parent.children.indexOf(anchor); parent.children.splice(i < 0 ? parent.children.length : i, 0, el); el.parent = parent },
  remove(el) { if (el.parent) el.parent.children.splice(el.parent.children.indexOf(el), 1); el.parent = null },
  parentNode: el => el.parent, nextSibling: el => el.parent?.children[el.parent.children.indexOf(el) + 1], querySelector: () => body,
})
const all = (el, pred) => [...(pred(el) ? [el] : []), ...el.children.flatMap(c => all(c, pred))]
const text = el => el.text + el.children.map(text).join('')
const settle = async () => { for (let i = 0; i < 12; i++) { await Promise.resolve(); await vue.nextTick() } }
const click = async el => { assert.ok(el, 'button exists'); assert.ok(!el.props.disabled, 'button is enabled'); await el.props.onClick({ currentTarget: el, target: el }); await settle() }
const button = (root, label) => all(root, el => el.type === 'button' && (el.props['aria-label'] === label || text(el).trim() === label))[0]
const dialog = () => all(body, el => el.props.role === 'dialog')[0]
async function mount(file, props = {}) {
  const pinia = createPinia(); setActivePinia(pinia)
  const root = node('root')
  const app = renderer.createApp(load(path.join(src, file)).default, props); app.use(pinia); app.mount(root); await settle()
  return { root, app, pinia }
}
async function checkConfirmation(root, label, expectedCall, message) {
  const before = calls.length
  await click(button(root, label)); assert.ok(text(dialog()).includes(message)); assert.equal(calls.length, before)
  await click(button(dialog(), '取消')); assert.equal(calls.length, before)
  await click(button(root, label)); await click(button(dialog(), '确定删除'))
  assert.equal(calls.at(-1), expectedCall)
}
async function verify() {
  const projects = await mount('views/ProjectsView.vue')
  const list = load(path.join(src, 'stores/projects.ts')).useProjectsStore(projects.pinia)
  const item = { id: 'video', name: '测试项目', status: 'pending', output_duration_us: null }
  list.projectPage.items = [item]; await settle()
  await checkConfirmation(projects.root, '删除', '/projects/video', '原始视频文件')
  list.projectPage.items = [item]; fail = true; await settle()
  await click(button(projects.root, '删除')); await click(button(dialog(), '确定删除'))
  const session = load(path.join(src, 'stores/session.ts')).useSessionStore(projects.pinia)
  assert.equal(session.error, '活动任务禁止删除'); assert.equal(list.projectPage.items.length, 1)
  fail = false; projects.app.unmount()

  const editor = await mount('views/WorkspaceView.vue')
  const project = load(path.join(src, 'stores/project.ts')).useProjectStore(editor.pinia)
  project.assetId = 'video'; project.assets = [{ id: 'video', name: '测试项目' }]; project.plans = [plan('one'), plan('two')]; project.selectedPlan = plan('one'); await settle()
  await click(button(editor.root, '移除片段 1')); assert.equal(project.selectedPlan.clips.length, 2)
  await click(button(dialog(), '取消')); assert.equal(project.selectedPlan.clips.length, 2)
  await click(button(editor.root, '移除片段 1')); await click(button(dialog(), '确定移除'))
  assert.equal(project.selectedPlan.clips.length, 1); assert.equal(button(editor.root, '移除片段 1').props.disabled, true)
  project.selectedPlan = plan('one'); await settle()
  await click(button(editor.root, '移除片段 1')); project.selectedPlan = plan('two'); await settle()
  assert.equal(dialog(), undefined, 'switching plans cancels the previous clip confirmation')
  await checkConfirmation(editor.root, '删除方案 1', '/plans/one', '源视频、ASR 对话')
  editor.app.unmount()

  const liveSession = { id: 's', started: 1, status: 'completed', product_events: [], product_periods: [], product_clips: [
    { id: 'c', status: 'completed', product_name: '商品一', promotion_id: 'p' },
    { id: 'i', status: 'imported', product_name: '商品二', promotion_id: 'q' },
    { id: 'busy', status: 'processing', product_name: '生成中', promotion_id: 'r' },
  ], segments: [{ id: 'seg', status: 'completed', segment_index: 1 }, { id: 'split', status: 'completed', segment_index: 2, splitting: true }] }
  const panel = await mount('components/live/LiveSessionPanel.vue', { session: liveSession, monitorId: 'monitor', busy: false, selectedPeriodId: '',
    onDeleteProductClip: async id => calls.push(`/clips/${id}`),
    onSplit: async () => ({state: 'noop', message: ''}), onRetryProductListener: async () => {},
  })
  assert.equal(all(panel.root, el => el.type === 'h3' && text(el) === '录制分段').length, 0, 'internal media chunks are not shown as a recording segment card')
  await checkConfirmation(panel.root, '删除商品片段 商品一', '/clips/c', '本地文件')
  await checkConfirmation(panel.root, '删除商品片段 商品二', '/clips/i', '已导入项目和文件会保留')
  assert.equal(button(panel.root, '删除商品片段 生成中'), undefined)
  panel.app.unmount()

  const retryCalls = []
  const retryPanel = await mount('components/live/LiveSessionPanel.vue', {
    session: {...liveSession, status: 'recording', product_listener: {status: 'error', detail: '连接失败', updated: 1},
      product_catalog_error: {detail: {summary: '商品列表获取失败'}, updated: 1}},
    monitorId: 'monitor', busy: false, selectedPeriodId: '', onDeleteProductClip: async () => {},
    onSplit: async () => ({state: 'noop', message: ''}),
    onRetryProductListener: async (monitorId, sessionId) => retryCalls.push([monitorId, sessionId]),
  })
  assert.ok(text(retryPanel.root).includes('商品消息连接已建立') === false, 'error state does not show the connected label')
  await click(button(retryPanel.root, '重试商品连接'))
  assert.deepEqual(retryCalls, [['monitor', liveSession.id]])
  const noRetry = await mount('components/live/LiveSessionPanel.vue', {
    session: {...liveSession, status: 'recording', product_listener: {status: 'listening', detail: '商品消息连接已建立', updated: 1}, product_catalog_error: null},
    monitorId: 'monitor', busy: false, selectedPeriodId: '', onDeleteProductClip: async () => {},
    onSplit: async () => ({state: 'noop', message: ''}), onRetryProductListener: async () => {},
  })
  assert.equal(button(noRetry.root, '重试商品连接'), undefined, 'healthy product listener has no manual retry')
  assert.equal(all(noRetry.root, el => el.props.class?.includes('live-listener-note')).length, 0,
    'the connected detail is not repeated below the product listener badge')
  assert.equal((text(noRetry.root).match(/商品消息连接已建立/g) || []).length, 1,
    'a healthy product listener shows its connected state once')
  retryPanel.app.unmount(); noRetry.app.unmount()

  const timelineSessions = [
    { ...liveSession, id: 'finished', started: 1, ended: 11, status: 'stopped', end_time_source: 'observed',
      segments: [{ id: 'timeline-segment', status: 'completed', segment_index: 1, size_bytes: 2048 }],
      product_clips: [{ id: 'timeline-clip', status: 'completed', product_name: '商品一', size_bytes: 1024 }] },
    { ...liveSession, id: 'recording', started: 21, ended: null, status: 'recording', end_time_source: null },
  ]
  const timeline = await mount('components/live/LiveRecordingTimeline.vue', {
    sessions: timelineSessions, selectedId: 'finished', selectedSession: timelineSessions[0],
    start: 0, end: 86400, now: 30, date: '', maxDate: '2026-11-05', loading: false, busy: false,
    label: '今天 · 2026-11-05 · 00:00–24:00', emptyMessage: '今天无录制',
    onDeleteSession: async id => calls.push(`/sessions/${id}`),
  })
  const deleteSessionButton = all(timeline.root, el => el.type === 'button' && el.props['aria-label']?.startsWith('删除录制场次：'))[0]
  assert.ok(deleteSessionButton, 'selected finished session exposes delete action')
  await click(deleteSessionButton)
  assert.ok(text(dialog()).includes('未导入录制视频文件'), 'session confirmation explains recording cleanup')
  await click(button(dialog(), '取消'))
  assert.equal(calls.at(-1), '/clips/i', 'cancelling session deletion performs no action')
  await click(deleteSessionButton); await click(button(dialog(), '确定删除场次'))
  assert.equal(calls.at(-1), '/sessions/finished')
  timeline.app.unmount()
  const activeTimeline = await mount('components/live/LiveRecordingTimeline.vue', {
    sessions: [timelineSessions[1]], selectedId: 'recording', selectedSession: timelineSessions[1],
    start: 0, end: 86400, now: 30, date: '', maxDate: '2026-11-05', loading: false, busy: false,
    label: '今天 · 2026-11-05 · 00:00–24:00', emptyMessage: '今天无录制',
    onDeleteSession: async id => calls.push(`/sessions/${id}`),
  })
  const activeDelete = all(activeTimeline.root, el => el.type === 'button' && el.props['aria-label']?.startsWith('删除录制场次：'))[0]
  assert.equal(activeDelete.props.disabled, true, 'recording session delete action is disabled')
  activeTimeline.app.unmount()

  const monitors = ['m1', 'm2'].map(id => ({ id, name: id, url: 'https://live.douyin.com/1', sessions: [], live_state: 'idle' }))
  const selectedId = vue.ref('m1'), recording = vue.ref(false)
  const root = node('root')
  const app = renderer.createApp({ render: () => vue.h(load(path.join(src, 'components/live/LiveRoomList.vue')).default, {
    monitors: recording.value ? [{ ...monitors[0], live_state: 'recording', sessions: [{ status: 'recording' }] }, monitors[1]] : monitors,
    selectedId: selectedId.value, loading: false, busy: false, onRemove: async m => calls.push(`/monitors/${m.id}`),
  }) }); app.mount(root); await settle()
  await click(button(root, '移除直播间')); selectedId.value = 'm2'; await settle()
  assert.equal(dialog(), undefined, 'changing selected monitor cancels the previous confirmation')
  await click(button(root, '移除直播间')); assert.ok(text(dialog()).includes('m2'))
  await click(button(dialog(), '确定移除')); assert.equal(calls.at(-1), '/monitors/m2')
  selectedId.value = 'm1'; recording.value = true; await settle()
  assert.equal(button(root, '移除直播间').props.disabled, true)
  app.unmount()
console.log('Mounted delete integrations passed: project, plan, session, product clips, protected states and error reporting.')
}
verify().catch(error => { console.error(error); process.exitCode = 1 })
