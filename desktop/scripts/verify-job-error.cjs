const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vue = require('vue')
const { parse, compileScript } = require('vue/compiler-sfc')
const ts = require('typescript')
const source = fs.readFileSync(path.join(__dirname, '../src/components/JobErrorDetails.vue'), 'utf8')
const compiled = compileScript(parse(source).descriptor, { id: 'job-error-check', inlineTemplate: true })
const code = ts.transpileModule(compiled.content, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText
const componentModule = { exports: {} }
let requests = 0, pending, rejectPending
const mockApi = (url) => {
  assert.equal(url, '/jobs/job-test/error-detail')
  requests++
  return new Promise((resolve, reject) => { pending = resolve; rejectPending = reject })
}
new Function('require', 'module', 'exports', code)(
  name => name === 'vue' ? vue : name === '../api' ? { api: mockApi } : require(name),
  componentModule, componentModule.exports,
)
const component = componentModule.exports.default
const node = type => ({ type, props: {}, children: [], parent: null, getBoundingClientRect: () => ({left: 50, top: 80, bottom: 100}) })
const renderer = vue.createRenderer({
  createElement: node,
  createText: text => ({ ...node('text'), text }),
  createComment: text => ({ ...node('comment'), text }),
  setText: (element, text) => { element.text = text },
  setElementText: (element, text) => { element.text = text },
  patchProp: (element, key, previous, value) => { element.props[key] = value },
  insert(element, parent, anchor) {
    if (element.parent) this.remove(element)
    const index = parent.children.indexOf(anchor)
    parent.children.splice(index < 0 ? parent.children.length : index, 0, element)
    element.parent = parent
  },
  remove(element) {
    const siblings = element.parent?.children
    if (siblings) siblings.splice(siblings.indexOf(element), 1)
    element.parent = null
  },
  parentNode: element => element.parent,
  nextSibling: element => element.parent?.children[element.parent.children.indexOf(element) + 1] || null,
})

const find = (element, predicate) => predicate(element) ? element : element.children.map(child => find(child, predicate)).find(Boolean)
const textOf = element => [element.text || '', ...element.children.map(textOf)].join('')
const settle = async () => { for (let i = 0; i < 8; i++) { await Promise.resolve(); await vue.nextTick() } }
async function verify() {
  const originalWindow = global.window
  global.window = { innerWidth: 1200, innerHeight: 900 }
  const root = node('root')
  const app = renderer.createApp({ render: () => vue.h(component, { jobId: 'job-test' }) })
  app.mount(root)
  try {
    const trigger = find(root, element => element.props.class === 'job-error-trigger')
    assert.equal(requests, 0, 'No response details fetched until hover or focus')
    const hover = trigger.props.onMouseenter()
    await settle()
    assert.equal(trigger.props['aria-expanded'], true)
    assert.ok(textOf(root).includes('正在加载'))
    const body = '<script>alert("unsafe")</script>\n' + '完整正文' .repeat(3000)
    pending({ error_source: 'http_response', stage: 'json_decode', http_status: 200, request_id: 'test-request', response_body: body })
    await hover; await settle()
    const panel = find(root, element => element.props.role === 'dialog')
    const pre = find(panel, element => element.type === 'pre' && element.text === body)
    assert.ok(pre, 'Entire raw response is rendered as text without truncation')
    assert.equal(pre.props.innerHTML, undefined, 'Cloud HTML must never be executed')
    assert.ok(textOf(panel).includes('test-request'))
    assert.ok(textOf(panel).includes('已收到云端 HTTP 响应'))
    assert.ok(textOf(panel).includes('云端响应正文（原文，已脱敏）'))
    assert.equal(panel.props.style.position, undefined) // Positioning comes from the CSS class.
    trigger.props.onClick(); await settle()
    trigger.props.onMouseleave()
    await new Promise(resolve => setTimeout(resolve, 220)); await settle()
    assert.ok(find(root, element => element.props.role === 'dialog'), 'Click pins details for selection and scrolling')
    find(panel, element => element.props['aria-label'] === '关闭错误详情').props.onClick(); await settle()
    assert.equal(trigger.props['aria-expanded'], false)
    await trigger.props.onFocus(); await settle()
    assert.equal(requests, 1, 'Repeat hover/focus reuses loaded result')
    const escape = find(root, element => element.props.class === 'job-error-details')
    escape.props.onKeydown({ key: 'Escape' }); await settle()
    assert.equal(trigger.props['aria-expanded'], false)
  } finally { app.unmount(); global.window = originalWindow }

  global.window = { innerWidth: 320, innerHeight: 500 }
  const root2 = node('root')
  const app2 = renderer.createApp({ render: () => vue.h(component, { jobId: 'job-test' }) })
  app2.mount(root2)
  try {
    const trigger = find(root2, element => element.props.class === 'job-error-trigger')
    const focus = trigger.props.onFocus()
    rejectPending(new Error('本地会话失效'))
    await focus; await settle()
    assert.ok(textOf(root2).includes('本地会话失效'))
    const retry = find(root2, element => element.type === 'button' && textOf(element) === '重试加载')
    const reload = retry.props.onClick()
    pending({ error_source: 'transport', summary: '云端连接失败', stage: 'connection_error', reason: 'RemoteDisconnected: Remote end closed connection without response', response_body: null })
    await reload; await settle()
    assert.ok(textOf(root2).includes('HTTP 客户端连接异常'))
    assert.ok(textOf(root2).includes('没有收到云端错误正文'))
    assert.ok(textOf(root2).includes('HTTP 客户端原始异常（reason）'))
    assert.ok(textOf(root2).includes('RemoteDisconnected: Remote end closed connection without response'))
    assert.ok(textOf(root2).includes('应用说明'))
    assert.ok(!find(root2, element => element.type === 'h4' && textOf(element).startsWith('云端响应正文')))
    const panel = find(root2, element => element.props.role === 'dialog')
    assert.equal(panel.props.style.width, '296px', 'Popover fits narrow windows')
  } finally { app2.unmount(); global.window = originalWindow }
  console.log('Cloud error popover checks passed: hover/focus, full safe text, pin/close, cached loading, failure/retry and no response.')
}
verify().catch(error => { console.error(error); process.exitCode = 1 })
