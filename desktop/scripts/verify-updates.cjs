const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vue = require('vue')
const ts = require('typescript')
const { parse, compileScript } = require('vue/compiler-sfc')
const { modules } = require('./test-modules.cjs')
const { assertNoRecording, updateCommand } = modules().load('services/updates.ts')

// Test production script + template with a minimal renderer and controlled I/O.
const node = type => ({ type, props: {}, children: [], parent: null, text: '' })
const renderer = vue.createRenderer({
  createElement: node, createComment: node,
  createText: text => Object.assign(node('text'), { text }),
  setText: (el, text) => { el.text = text }, setElementText: (el, text) => { el.text = text },
  patchProp: (el, key, old, value) => { el.props[key] = value },
  insert(el, parent, anchor) { const i = parent.children.indexOf(anchor); parent.children.splice(i < 0 ? parent.children.length : i, 0, el); el.parent = parent },
  remove(el) { if (el.parent) el.parent.children.splice(el.parent.children.indexOf(el), 1); el.parent = null },
  parentNode: el => el.parent, nextSibling: el => el.parent?.children[el.parent.children.indexOf(el) + 1],
})
const all = (root, predicate) => [...(predicate(root) ? [root] : []), ...root.children.flatMap(child => all(child, predicate))]
const text = root => root.text + root.children.map(text).join('')
const button = (root, label) => all(root, el => el.type === 'button' && text(el).trim() === label)[0]
const settle = async () => { for (let i = 0; i < 20; i++) { await Promise.resolve(); await vue.nextTick() } }
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r }); return { promise, resolve } }
const empty = { render: () => null }
let timers = new Map(), timerId = 0
const setTimer = callback => { timers.set(++timerId, callback); return timerId }
const clearTimer = id => timers.delete(id)
global.window = Object.assign(new EventTarget(), { setTimeout: setTimer, clearTimeout: clearTimer })
function compile(relative, mocks, development = false) {
  let source = fs.readFileSync(path.resolve(__dirname, '../src', relative), 'utf8')
  source = compileScript(parse(source).descriptor, { id: relative, inlineTemplate: true }).content
    .replaceAll('import.meta.env.DEV', String(development))
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText
  const module = { exports: {} }
  new Function('require', 'module', 'exports', '__APP_VERSION__', 'setTimeout', 'clearTimeout', code)(
    name => name === 'vue' ? vue : mocks[name] || (() => { throw new Error(`Missing mock ${name}`) })(),
    module, module.exports, '0.2.137', setTimer, clearTimer)
  return module.exports.default
}
async function mount(component) {
  const root = node('root'), app = renderer.createApp(component)
  app.mount(root); await settle()
  return { root, app }
}
async function click(root, label) {
  const el = button(root, label); assert.ok(el, `Expected ${label}`); assert.ok(!el.props.disabled, `${label} enabled`)
  el.props.onClick(); await settle()
}
async function tickStatus() {
  const next = [...timers.entries()][0]; assert.ok(next, 'Status polling scheduled')
  timers.delete(next[0]); await next[1](); await settle()
}

async function verifyUpdateRow() {
  const commands = [], events = []
  let status = { phase: 'idle', currentVersion: '0.2.137', latestVersion: null, hasUpdate: false,
    notes: '', downloadedBytes: 0, totalBytes: 100, message: '' }, pending = null
  const internals = { async invoke(command) {
    assert.equal(this, internals, 'Preserves the native bridge receiver')
    commands.push(command)
    if (command === 'get_update_status') return pending ? pending.promise : { ...status }
    if (command === 'check_for_update') status = { ...status, phase: 'available', latestVersion: '0.3.0', hasUpdate: true, notes: '<script>unsafe</script>', message: '发现新版本' }
    if (command === 'download_update') status = { ...status, phase: 'downloading', downloadedBytes: 25, message: '下载中' }
    if (command === 'cancel_update_download') status = { ...status, phase: 'error', message: '下载已取消' }
  } }
  window.__TAURI_INTERNALS__ = internals
  const service = modules().load('services/updates.ts')
  const component = compile('components/SoftwareUpdate.vue', { '../services/updates': service })
  const handler = () => events.push('install-request')
  window.addEventListener('douyincut-update-request', handler)
  let mounted = await mount(component)
  assert.deepEqual(commands, ['get_update_status'], 'Opening settings does not auto-check GitHub')
  await click(mounted.root, '检查更新'); await tickStatus()
  assert.ok(button(mounted.root, '下载 v0.3.0'))
  assert.equal(all(mounted.root, el => el.type === 'script').length, 0, 'Release notes are text, never executable HTML')
  await click(mounted.root, '下载 v0.3.0'); await tickStatus()
  assert.ok(text(mounted.root).includes('25%'))
  assert.equal(button(mounted.root, '检查更新').props.disabled, true)
  await click(mounted.root, '取消下载'); await tickStatus()
  assert.ok(text(mounted.root).includes('下载已取消'))
  await click(mounted.root, '下载 v0.3.0')
  status = { ...status, phase: 'ready', downloadedBytes: 100, message: '校验完成' }
  await tickStatus(); await click(mounted.root, '退出并安装')
  assert.deepEqual(events, ['install-request'])
  assert.ok(!commands.includes('install_update'), 'The row must use the global exit guard, not install directly')
  mounted.app.unmount(); assert.equal(timers.size, 0)
  mounted = await mount(component)
  assert.ok(button(mounted.root, '退出并安装'), 'Native ready state survives page navigation')
  pending = deferred(); const inFlight = tickStatus(); await settle()
  mounted.app.unmount(); pending.resolve(status); await inFlight
  assert.equal(timers.size, 0, 'Late responses cannot restart polling after unmount')
  pending = null
  mounted = await mount(compile('components/SoftwareUpdate.vue', { '../services/updates': service }, true))
  assert.equal(button(mounted.root, '退出并安装').props.disabled, true, 'Development cannot install')
  mounted.app.unmount(); window.removeEventListener('douyincut-update-request', handler)
  delete window.__TAURI_INTERNALS__
  await assert.rejects(updateCommand('check_for_update'), /Windows 桌面/)
}

async function verifyExitCoordination() {
  let monitors = [], summaryError = false, nativeError = false, monitorReads = 0
  const native = [], reports = []
  const session = vue.reactive({ busy: false, connected: true, activeJobs: 0, error: '', errorRevision: 0, message: '', report: cause => reports.push(String(cause)) })
  const project = vue.reactive({ savedDraft: '', planDirty: false, assetId: null, currentDraft: () => ({}) })
  const mocks = {
    pinia: { storeToRefs: vue.toRefs },
    'vue-router': { RouterView: empty, useRoute: () => ({ name: 'local-settings' }), useRouter: () => ({}) },
    './stores/session': { useSessionStore: () => session }, './stores/project': { useProjectStore: () => project },
    './workspace': { createWorkspace: () => ({ start() {}, stop() {} }), provideWorkspace() {} },
    './composables/useVideoImport': { provideVideoImport: () => ({ fileInput: vue.ref(null), selectFile() {} }) },
    './components/live/LiveIcon.vue': { default: empty },
    './exit-guards': { hasUnsavedChanges: () => false },
    './services/jobs': { jobsApi: { summary: async () => { if (summaryError) throw new Error('summary unavailable'); return { active: 125 } }, list: async () => [] } },
    './api': { api: async () => { monitorReads++; return monitors } },
    './services/updates': { assertNoRecording },
  }
  window.__TAURI_INTERNALS__ = { async invoke(command) { native.push(command); if (nativeError) throw new Error('installer refused') } }
  const mounted = await mount(compile('App.vue', mocks))
  const request = async () => { window.dispatchEvent(new Event('douyincut-update-request')); await settle() }
  const recording = { record_requested: false, sessions: [{ status: 'recording' }] }
  project.planDirty = true
  await request()
  assert.ok(button(mounted.root, '返回保存')); assert.equal(monitorReads, 0)
  await click(mounted.root, '返回保存'); assert.equal(project.planDirty, true)
  await request(); await click(mounted.root, '放弃并继续更新')
  assert.ok(text(mounted.root).includes('125 项后台任务'), 'Uses full summary count rather than first page')
  await click(mounted.root, '继续使用'); assert.deepEqual(native, [])
  project.planDirty = false; monitors = [recording]
  await request(); assert.ok(reports.at(-1).includes('请先')); assert.deepEqual(native, [])
  monitors = [{ record_requested: true, sessions: [] }]
  await request(); assert.ok(reports.at(-1).includes('请先')); assert.deepEqual(native, [])
  monitors = []; summaryError = true
  await request(); assert.ok(reports.at(-1).includes('summary unavailable')); assert.deepEqual(native, [])
  summaryError = false
  await request(); monitors = [recording]
  await click(mounted.root, '退出并安装'); assert.deepEqual(native, [], 'Rechecks recording immediately before install')
  await click(mounted.root, '继续使用'); monitors = []; nativeError = true
  await request(); await click(mounted.root, '退出并安装')
  assert.deepEqual(native, ['install_update']); assert.ok(button(mounted.root, '退出并安装'), 'Failed launch leaves a retry prompt')
  nativeError = false
  await click(mounted.root, '退出并安装')
  assert.deepEqual(native, ['install_update', 'install_update'])
  assert.ok(!native.includes('complete_exit'), 'Update uses the installation handoff')
  mounted.app.unmount(); assert.equal(timers.size, 0)
}

;(async () => {
  assert.doesNotThrow(() => assertNoRecording([{ record_requested: false, sessions: [{ status: 'ended' }] }]))
  await verifyUpdateRow()
  await verifyExitCoordination()
  console.log('Update behavior passed: check/download/cancel/progress, route lifetime, drafts, recording, tasks and confirmed installation.')
})().catch(error => { console.error(error); process.exitCode = 1 })
