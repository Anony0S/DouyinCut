const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vue = require('vue')
const ts = require('typescript')
const { parse, compileScript } = require('vue/compiler-sfc')
const { createPinia, setActivePinia } = require('pinia')
const src = path.resolve(__dirname, '../src')
const cache = new Map(), navigation = [], calls = []
const route = vue.reactive({ query: { cloudTab: 'product-platform' } })
const router = { useRoute: () => route, useRouter: () => ({ push: async target => navigation.push(target) }) }
const vShow = { beforeMount(el, { value }) { el.hidden = !value }, updated(el, { value }) { el.hidden = !value } }
function load(file) {
  file = path.resolve(src, file)
  if (cache.has(file)) return cache.get(file).exports
  let source = fs.readFileSync(file, 'utf8')
  if (file.endsWith('.vue')) source = compileScript(parse(source).descriptor, { id: file, inlineTemplate: true }).content
  source = source.replace("import.meta.env.VITE_SESSION_TOKEN || ''", "'isolated-local-session'")
  const module = { exports: {} }; cache.set(file, module)
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText
  new Function('require', 'module', 'exports', code)(name => {
    if (name === 'vue') return { ...vue, vModelText: {}, vModelCheckbox: {}, vModelSelect: {}, vShow }
    if (name === 'vue-router') return router
    if (!name.startsWith('.')) return require(name)
    const dep = path.resolve(path.dirname(file), name)
    return load(path.extname(dep) ? dep : dep + '.ts')
  }, module, module.exports)
  return module.exports
}
const node = type => ({ type, props: {}, children: [], parent: null, text: '', open: false,
  showModal() { this.open = true }, close() { this.open = false }, focus() {},
})
const body = node('body')
const renderer = vue.createRenderer({ createElement: node, createComment: node,
  createText: text => Object.assign(node('text'), { text }), setText: (el, text) => { el.text = text },
  setElementText: (el, text) => { el.text = text }, patchProp: (el, key, old, value) => { el.props[key] = value },
  insert(el, parent, anchor) { if (el.parent) this.remove(el); const i = parent.children.indexOf(anchor); parent.children.splice(i < 0 ? parent.children.length : i, 0, el); el.parent = parent },
  remove(el) { if (el.parent) el.parent.children.splice(el.parent.children.indexOf(el), 1); el.parent = null },
  parentNode: el => el.parent, nextSibling: el => el.parent?.children[el.parent.children.indexOf(el) + 1], querySelector: () => body,
})
const all = (root, predicate) => [...(predicate(root) ? [root] : []), ...root.children.flatMap(child => all(child, predicate))]
const text = root => root.text + root.children.map(text).join('')
const button = (root, label) => all(root, el => el.type === 'button' && (text(el).trim() === label || el.props['aria-label'] === label))[0]
const settle = async () => { for (let i = 0; i < 30; i++) { await Promise.resolve(); await vue.nextTick() } }
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r }); return { promise, resolve } }
const click = async el => { assert.ok(el); assert.ok(!el.props.disabled); const result = el.props.onClick?.({ currentTarget: el, target: el }); await settle(); return result }
const input = (root, suffix, value) => {
  const field = all(root, el => el.type === 'input' && el.props.id?.endsWith(suffix))[0]
  assert.ok(field); field.props['onUpdate:modelValue'](value)
}
const status = { platform: 'xingdou', logged_in: false, mobile: '', member_name: '', member_id: null,
  main_ip_id: null, main_ip_name: '', scopes: [], last_validated_at: null }
const loggedIn = { ...status, logged_in: true, mobile: '138****0000', member_name: '测试账号', member_id: '123',
  main_ip_id: '11', main_ip_name: '店铺 A', scopes: [{ id: '11', name: '店铺 A' }], last_validated_at: 12345 }
let respond
async function mount(file, props = {}) {
  const pinia = createPinia(); setActivePinia(pinia)
  const root = node('root'), app = renderer.createApp(load(file).default, props)
  app.use(pinia); app.mount(root); await settle()
  return { root, app, session: load('stores/session.ts').useSessionStore(pinia) }
}

async function verifyLogin() {
  let loginResponse = loggedIn
  respond = url => {
    if (url.endsWith('/settings/cloud')) return { config: {}, secrets: {}, secret_previews: {} }
    if (url.endsWith('/status')) return status
    if (url.endsWith('/send-code')) return { sent: true, retry_after_seconds: 60 }
    if (url.endsWith('/login')) return loginResponse
    throw new Error(`Unexpected request ${url}`)
  }
  const mounted = await mount('views/SettingsView.vue')
  try {
    const tab = button(mounted.root, '星斗平台')
    assert.equal(tab.props['aria-selected'], true, 'Deep link selects the product platform tab')
    assert.ok(button(mounted.root, '点击登录'))
    await click(button(mounted.root, '点击登录'))
    assert.ok(all(body, el => el.type === 'dialog')[0].open)
    input(body, '-mobile', '13800000000'); input(body, '-code', '123456')
    const agreement = all(body, el => el.type === 'input' && el.props.type === 'checkbox')[0]
    assert.equal(button(body, '获取验证码').props.disabled, true)
    agreement.props['onUpdate:modelValue'](true); await settle()
    const sentBefore = calls.filter(call => call.url.endsWith('/send-code')).length
    await click(button(body, '获取验证码'))
    assert.equal(calls.filter(call => call.url.endsWith('/send-code')).length, sentBefore + 1)
    assert.ok(text(body).includes('60 秒后重发'))
    assert.equal(button(body, '60 秒后重发').props.disabled, true)
    const form = all(body, el => el.type === 'form' && el.props.onSubmit)[0]
    const pendingLogin = deferred(); loginResponse = pendingLogin.promise
    const loginBefore = calls.filter(call => call.url.endsWith('/login')).length
    const inFlight = form.props.onSubmit({ preventDefault() {} }); await settle()
    await form.props.onSubmit({ preventDefault() {} }); await settle()
    assert.equal(calls.filter(call => call.url.endsWith('/login')).length, loginBefore + 1, 'Duplicate login submits are ignored')
    pendingLogin.resolve(loggedIn); await inFlight; await settle()
    assert.ok(text(mounted.root).includes('138****0000'))
    assert.ok(text(mounted.root).includes('店铺 A'))
    assert.equal(all(body, el => el.type === 'dialog').length, 0, 'Successful login closes the modal')
    assert.ok(mounted.session.message.includes('登录成功'))
  } finally { mounted.app.unmount() }
}

async function verifyLectureButton() {
  respond = () => ({ product: null, scope_name: '店铺 A' })
  const period = { id: 'lecture-a', session_id: 'session', promotion_id: 'product-a', product_name: '完整商品名称',
    started: 1000, ended: 1020, sources: [], mapping_error: '', clip_job: null }
  const session = { id: 'session', status: 'ended', started: 1000, ended: 1040, end_time_source: 'observed', segments: [],
    product_clips: [], product_periods: [period, { ...period, id: 'lecture-b', product_name: '   ' }],
    current_product: null, product_events: [], product_listener: null, product_catalog_error: null }
  const mounted = await mount('components/live/LiveSessionPanel.vue', { session, monitorId: 'monitor', busy: false,
    onDelete: async () => {}, onDeleteProductClip: async () => {}, onSplit: async () => {}, onRetryProductListener: async () => {}, })
  try {
    const actions = all(mounted.root, el => el.type === 'button' && text(el).trim() === '查询商品')
    assert.equal(actions.length, 2)
    const enabled = actions.find(action => !action.props.disabled), disabled = actions.find(action => action.props.disabled)
    assert.ok(disabled.props.title.includes('暂无商品名称'))
    const before = calls.filter(call => call.url.endsWith('/products/search')).length
    await click(enabled)
    const requests = calls.filter(call => call.url.endsWith('/products/search'))
    assert.equal(requests.length, before + 1)
    assert.equal(requests.at(-1).body.keyword, '完整商品名称')
    await click(button(body, '关闭商品查询'))
    assert.equal(all(body, el => el.type === 'dialog').length, 0)
  } finally { mounted.app.unmount() }
}

async function verifySearch() {
  const firstProduct = { title: '测试商品', price: 0, stock: '0', commission_ratio: 0, platform_product_id: 'p1', has_live: false, has_ai_live: false,
    cover: 'javascript:alert(1)' }
  respond = () => ({ product: firstProduct, scope_name: '店铺 A' })
  let mounted = await mount('components/product/ProductSearchDialog.vue', { keyword: ' 测试商品 ' })
  try {
    assert.ok(text(body).includes('测试商品'))
    assert.ok(text(body).includes('¥0.00'), 'Zero prices are displayed without treating them as missing')
    assert.ok(text(body).includes('0%'))
    assert.equal(all(body, el => el.type === 'img').length, 0, 'Unsafe image scheme is rejected')
    assert.ok(text(body).includes('仅展示平台返回的第一条'))
  } finally { mounted.app.unmount() }

  respond = () => ({ product: null, scope_name: '店铺 A' })
  mounted = await mount('components/product/ProductSearchDialog.vue', { keyword: '不存在' })
  assert.ok(text(body).includes('未查询到商品')); mounted.app.unmount()

  const authRequired = { status: 401, body: { detail: { code: 'PRODUCT_PLATFORM_AUTH_REQUIRED', message: '未登录' } } }
  respond = () => authRequired
  mounted = await mount('components/product/ProductSearchDialog.vue', { keyword: '测试商品' })
  assert.deepEqual(navigation.at(-1), { path: '/settings', query: { cloudTab: 'product-platform' } })
  mounted.app.unmount()

  const before = navigation.length
  const pending = deferred(); respond = () => pending.promise
  mounted = await mount('components/product/ProductSearchDialog.vue', { keyword: '测试商品' })
  mounted.app.unmount(); pending.resolve(authRequired); await settle()
  assert.equal(navigation.length, before, 'Late authentication failures after close cannot navigate')

  const searchRequest = deferred(); respond = () => searchRequest.promise
  mounted = await mount('components/product/ProductSearchDialog.vue', { keyword: '重试商品' })
  searchRequest.resolve({ status: 503, body: { detail: '网络暂时不可用' } }); await settle()
  assert.ok(text(body).includes('网络暂时不可用'))
  respond = () => ({ product: firstProduct, scope_name: '店铺 A' })
  await click(button(body, '重试'))
  assert.ok(text(body).includes('测试商品')); mounted.app.unmount()
}

async function main() {
  const originalFetch = global.fetch
  const originalWindow = global.window
  global.window = { addEventListener() {}, removeEventListener() {} }
  global.fetch = async (url, options) => {
    calls.push({ url, body: options.body ? JSON.parse(options.body) : undefined })
    const result = await respond(url, options)
    return new Response(JSON.stringify(result?.body ?? result), { status: result?.status ?? 200, headers: { 'Content-Type': 'application/json' } })
  }
  try {
    await verifyLogin(); await verifySearch(); await verifyLectureButton()
    assert.ok(calls.every(call => call.url.startsWith('/api/v1/')), 'The frontend only calls its authenticated local API')
    console.log('Product platform UI checks passed: settings deep link, SMS cooldown, login, product card, empty/error results, auth redirect and stale response guard.')
  } finally { global.fetch = originalFetch; global.window = originalWindow }
}
main().catch(error => { console.error(error); process.exitCode = 1 })
