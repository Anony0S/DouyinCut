const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vue = require('vue')
const ts = require('typescript')
const { parse, compileScript } = require('vue/compiler-sfc')
const { createPinia, setActivePinia } = require('pinia')

// Mount the real log page; substitute only the network, the clipboard, timers and document visibility.
const src = path.resolve(__dirname, '../src')
const polls = new Set()
const originalSetInterval = global.setInterval, originalClearInterval = global.clearInterval
global.setInterval = callback => { polls.add(callback); return callback }
global.clearInterval = callback => { polls.delete(callback) }

const job = `job_${'a'.repeat(32)}`, clipId = `product_clip_${'b'.repeat(32)}`
const file = (category, name, extra = {}) => ({ category, name, size: 2048, modified: 1792700000, group: null, group_type: null, job_kind: null, product_name: null, ...extra })
let files, contents, requests, clearResult, failList, opened, copied
function reset() {
  files = [
    file('runtime', 'desktop-runtime.log'),
    file('task', `${job}-10.log`, { group: job, group_type: 'job', job_kind: 'export' }),
    file('task', `${job}-2.log`, { group: job, group_type: 'job', job_kind: 'export' }),
    file('task', `${job}-concat.log`, { group: job, group_type: 'job', job_kind: 'export' }),
    file('task', `product-clip-${clipId}-h264_amf.log`, { group: clipId, group_type: 'product_clip', product_name: '针织开衫' }),
  ]
  contents = {
    'runtime/desktop-runtime.log': [
      '2026-11-11 10:00:00 INFO     [worker] douyincut.worker: 任务开始',
      '2026-11-11 10:00:01 ERROR    [worker] douyincut.worker: job failed: ProviderError',
      'Traceback (most recent call last):',
      '  File "worker.py", line 1, in run',
      '2026-11-11 10:00:02 WARNING  [live_monitor] app.live_monitoring.notifications: 无法发送提醒',
      '2026-11-11 10:00:03 INFO     [api] douyincut.runtime: api 已启动',
    ].join('\n') + '\n',
    [`task/${job}-2.log`]: '[h264 @ 000001] Missing reference picture\n',
  }
  requests = []; clearResult = { runtime_cleared: true, deleted_files: 3, retained_files: 1, bytes_reclaimed: 4096 }
  failList = false; opened = []; copied = []
}
const api = async (url, method = 'GET', body) => {
  requests.push(`${method} ${url}`)
  if (url === '/logs' && method === 'GET') {
    if (failList) throw new Error('本地服务不可用')
    return { files: structuredClone(files), runtime_available: true }
  }
  if (url === '/logs' && method === 'DELETE') { files = files.slice(1, 2); return clearResult }
  if (url === '/logs/open') { opened.push(body); return { opened: true } }
  const read = /^\/logs\/(runtime|task)\/([^?]+)(?:\?after=(\d+))?$/.exec(url)
  if (read) {
    const text = contents[`${read[1]}/${decodeURIComponent(read[2])}`]
    if (text === undefined) throw new Error('找不到对应记录')
    if (read[3] === undefined) return { text, start: 0, next: text.length, size: text.length, truncated: false, reset: false }
    const after = Number(read[3])
    if (after > text.length) return { text, start: 0, next: text.length, size: text.length, truncated: false, reset: true }
    return { text: text.slice(after), start: after, next: text.length, size: text.length, truncated: false, reset: false }
  }
  throw new Error(`Unexpected request: ${method} ${url}`)
}
const overrides = new Map([
  [path.join(src, 'api.ts'), { api }],
  [path.join(src, 'clipboard.ts'), { writeClipboard: async value => { copied.push(value) } }],
  [path.join(src, 'components/live/LiveIcon.vue'), { __esModule: true, default: { render: () => null } }],
])
const cache = new Map()
function load(target) {
  target = path.resolve(target)
  if (overrides.has(target)) return overrides.get(target)
  if (cache.has(target)) return cache.get(target).exports
  if (target.endsWith('.css')) return {}
  let source = fs.readFileSync(target, 'utf8')
  if (target.endsWith('.vue')) source = compileScript(parse(source).descriptor, { id: target, inlineTemplate: true }).content
  const result = { exports: {} }; cache.set(target, result)
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText
  new Function('require', 'module', 'exports', code)(name => {
    if (name === 'vue') return { ...vue, vModelText: {}, vModelCheckbox: {} }
    if (!name.startsWith('.')) return require(name)
    const dep = path.resolve(path.dirname(target), name)
    return load(path.extname(dep) ? dep : `${dep}.ts`)
  }, result, result.exports)
  return result.exports
}
global.window = { innerWidth: 1200, innerHeight: 800, addEventListener() {}, removeEventListener() {} }
const visibilityListeners = new Set()
global.document = {
  hidden: false,
  addEventListener(type, listener) { if (type === 'visibilitychange') visibilityListeners.add(listener) },
  removeEventListener(type, listener) { if (type === 'visibilitychange') visibilityListeners.delete(listener) },
}
const node = type => { const el = { type, props: {}, children: [], parent: null, text: '', scrollTop: 0, scrollHeight: 1000, clientHeight: 400,
  getBoundingClientRect: () => ({ left: 40, right: 90, top: 120, bottom: 150 }),
  focus() {}, contains(target) { return target === el || el.children.some(child => child.contains(target)) } }; return el }
const body = node('body')
const renderer = vue.createRenderer({
  createElement: node, createComment: () => node('comment'), createText: text => Object.assign(node('text'), { text }),
  setText: (el, text) => { el.text = text }, setElementText: (el, text) => { el.text = text },
  patchProp: (el, key, old, value) => { el.props[key] = value },
  insert(el, parent, anchor) { if (el.parent) this.remove(el); const i = parent.children.indexOf(anchor); parent.children.splice(i < 0 ? parent.children.length : i, 0, el); el.parent = parent },
  remove(el) { if (el.parent) el.parent.children.splice(el.parent.children.indexOf(el), 1); el.parent = null },
  parentNode: el => el.parent, nextSibling: el => el.parent?.children[el.parent.children.indexOf(el) + 1], querySelector: () => body,
})
const all = (el, predicate) => [...(predicate(el) ? [el] : []), ...el.children.flatMap(child => all(child, predicate))]
const text = el => el.text + el.children.map(text).join('')
const settle = async () => { for (let i = 0; i < 16; i++) { await Promise.resolve(); await vue.nextTick() } }
const click = async el => { assert.ok(el, 'button exists'); assert.ok(!el.props.disabled, 'button is enabled'); await el.props.onClick({ currentTarget: el, target: el }); await settle() }
const button = (root, label) => all(root, el => el.type === 'button' && text(el).trim().startsWith(label))[0]
const lines = root => all(root, el => el.props.class?.includes('logs-line')).map(text)
const fileButtons = root => all(root, el => el.type === 'button' && el.props.class?.includes('logs-file'))
const toggle = (root, label) => all(root, el => el.type === 'label' && text(el).includes(label))[0]?.children.find(el => el.type === 'input')
const setModel = async (input, value) => { await input.props['onUpdate:modelValue'](value); await settle() }
const viewer = root => all(root, el => el.props.class === 'logs-content')[0]
async function mount() {
  const pinia = createPinia(); setActivePinia(pinia)
  const root = node('root')
  const app = renderer.createApp(load(path.join(src, 'views/LogsView.vue')).default); app.use(pinia); app.mount(root); await settle()
  return { root, app, session: load(path.join(src, 'stores/session.ts')).useSessionStore(pinia) }
}

function verifyPresentation() {
  const { classifyLines, appendChunk, groupTaskLogs, fileLabel } = load(path.join(src, 'logPresentation.ts'))
  assert.deepEqual(classifyLines('ERROR:    Exception in ASGI application\n  + frame\nTraceback (most recent call last):\n').map(line => line.level),
    ['error', 'error', 'error'], 'lines written before the shared format are still recognised as errors')
  assert.deepEqual(classifyLines('2026-11-11 10:00:00 INFO     [api] a: ok\nplain\n').map(line => line.level), ['info', 'info'])
  assert.deepEqual(classifyLines(''), [], 'an empty file has no lines')
  const grown = appendChunk('one\ntwo\n', false, { text: 'three\n', reset: false, truncated: false }, 10)
  assert.deepEqual(grown, { text: 'two\nthree\n', truncated: true }, 'the oldest whole lines are dropped once the limit is exceeded')
  assert.deepEqual(appendChunk('old\n', true, { text: 'new\n', reset: true, truncated: false }), { text: 'new\n', truncated: false },
    'a cleared file replaces the shown content')
  reset()
  const groups = groupTaskLogs(files)
  assert.deepEqual(groups.map(group => group.title), ['成片导出', '商品片段编码 · 针织开衫'])
  assert.deepEqual(groups[0].files.map(fileLabel), ['片段 3', '片段 11', '拼接'], 'clip logs sort numerically and use 1-based labels')
  assert.equal(groupTaskLogs([file('task', `${job}-0.log`, { group: job, group_type: 'job' })])[0].title, '已删除的任务')
  assert.equal(fileLabel(file('task', `${job}-jianying-0.log`, { group: job, group_type: 'job' })), '剪映片段 1')
}

async function verify() {
  verifyPresentation()
  reset()
  const { root, app, session } = await mount()
  try {
    assert.deepEqual(requests, ['GET /logs', 'GET /logs/runtime/desktop-runtime.log'], 'the application log is opened by default')
    assert.equal(lines(root).length, 6)
    assert.ok(text(root).includes('成片导出') && text(root).includes('商品片段编码 · 针织开衫'), 'task logs are grouped by their job')
    assert.equal(fileButtons(root)[0].props['aria-pressed'], true)
    const errorLines = all(root, el => el.props.class?.includes('logs-line') && el.props.class.includes('is-error'))
    assert.equal(errorLines.length, 3, 'a traceback is shown with the level of its record')

    await setModel(toggle(root, '只看错误'), true)
    assert.equal(lines(root).length, 3)
    assert.ok(text(root).includes('显示 3 / 6 行'))
    const searchInput = all(root, el => el.type === 'input' && el.props.type === 'search')[0]
    await setModel(searchInput, 'TRACEBACK')
    assert.deepEqual(lines(root), ['Traceback (most recent call last):'], 'search is case-insensitive and combines with the error filter')
    await click(button(root, '复制'))
    assert.deepEqual(copied, ['Traceback (most recent call last):'], 'copy takes the filtered lines')
    assert.equal(session.message, '已复制筛选后的 1 行')
    await setModel(searchInput, '没有这样的内容')
    assert.ok(text(root).includes('没有匹配的内容'))
    await setModel(searchInput, ''); await setModel(toggle(root, '只看错误'), false)

    // Following reads only what was appended, and keeps a reader who scrolled up in place.
    const box = viewer(root)
    box.scrollTop = 600; box.scrollHeight = 1000
    contents['runtime/desktop-runtime.log'] += '2026-11-11 10:00:04 INFO     [api] douyincut.runtime: 新增一行\n'
    const before = requests.length
    for (const poll of polls) poll()
    await settle()
    assert.match(requests.at(-1), /^GET \/logs\/runtime\/desktop-runtime\.log\?after=\d+$/)
    assert.equal(requests.length, before + 1)
    assert.equal(lines(root).length, 7, 'appended content is added to the view')
    box.scrollTop = 100; box.scrollHeight = 2000
    contents['runtime/desktop-runtime.log'] += '2026-11-11 10:00:05 INFO     [api] douyincut.runtime: 又一行\n'
    for (const poll of polls) poll()
    await settle()
    assert.equal(box.scrollTop, 100, 'new lines do not move a reader who scrolled away from the end')

    const hiddenBefore = requests.length
    document.hidden = true
    for (const poll of polls) poll()
    await settle()
    assert.equal(requests.length, hiddenBefore, 'a hidden window does not poll')
    document.hidden = false
    await setModel(toggle(root, '自动跟随'), false)
    for (const poll of polls) poll()
    await settle()
    assert.equal(requests.length, hiddenBefore, 'following can be switched off')
    await setModel(toggle(root, '自动跟随'), true)

    const taskFile = fileButtons(root).find(el => text(el).includes('片段 3'))
    await click(taskFile)
    assert.deepEqual(lines(root), ['[h264 @ 000001] Missing reference picture'])
    assert.equal(toggle(root, '只看错误'), undefined, 'FFmpeg output has no levels to filter by')
    await click(button(root, '在文件夹中显示'))
    assert.deepEqual(opened, [{ category: 'task', name: `${job}-2.log` }])

    await click(fileButtons(root).find(el => text(el).includes('拼接')))
    assert.ok(text(root).includes('读取日志失败：找不到对应记录'), 'a failed read is reported in place')
    const failedAt = requests.length
    for (const poll of polls) poll()
    await settle()
    assert.equal(requests.length, failedAt, 'a file that failed to load is not polled')

    await click(button(root, '清理日志'))
    const dialog = all(body, el => el.props.role === 'dialog')[0]
    assert.ok(text(dialog).includes('进行中任务的日志会保留'))
    assert.ok(!requests.includes('DELETE /logs'), 'clearing waits for confirmation')
    await click(button(dialog, '确定清理'))
    assert.ok(requests.includes('DELETE /logs'))
    assert.equal(session.message, '已清空运行日志，删除 3 个日志文件；保留 1 个进行中任务或被占用的日志')
    assert.equal(fileButtons(root).length, 1, 'the list is reloaded after clearing')
    assert.equal(fileButtons(root)[0].props['aria-pressed'], true, 'the first remaining file is selected')

    failList = true
    await click(button(root, '刷新'))
    assert.ok(text(root).includes('读取日志列表失败：本地服务不可用'))
    assert.equal(fileButtons(root).length, 1, 'a failed refresh keeps the last list')
  } finally { app.unmount() }
  assert.equal(polls.size, 0, 'leaving the page stops the follow timer')
  assert.equal(visibilityListeners.size, 0, 'leaving the page removes its visibility listener')

  reset(); files = []
  const empty = await mount()
  try {
    assert.ok(text(empty.root).includes('暂无日志'))
    assert.equal(button(empty.root, '清理日志').props.disabled, true, 'nothing to clear')
  } finally { empty.app.unmount() }

  const localSettings = fs.readFileSync(path.join(src, 'views/LocalSettingsView.vue'), 'utf8')
  assert.ok(!localSettings.includes('日志'), 'local settings no longer carries log management')
  for (const native of ['src-tauri/src/main.rs', 'src-tauri/src/workspace.rs', 'src-tauri/permissions/default.toml']) {
    assert.doesNotMatch(fs.readFileSync(path.join(__dirname, '..', native), 'utf8'), /open_log_directory|clear_logs/,
      `${native} no longer exposes the native log commands`)
  }
  console.log('Runtime log page checks passed: listing, grouping, filters, following, visibility, reveal, clear and failures.')
}
verify().catch(error => { console.error(error); process.exitCode = 1 }).finally(() => {
  global.setInterval = originalSetInterval; global.clearInterval = originalClearInterval
})
