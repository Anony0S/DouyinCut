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
const router = { useRoute: () => route, useRouter: () => ({ push: async target => navigation.push(target) }),
  RouterLink: { setup: (_props, { slots }) => () => vue.h('a', slots.default?.()) } }
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
  getBoundingClientRect() { return { bottom: 44, right: 300, height: 340 } },
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
const click = async el => { assert.ok(el); assert.ok(!el.props.disabled); const result = el.props.onClick?.({ currentTarget: el, target: el, stopPropagation() {}, preventDefault() {} }); await settle(); return result }
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
    const tab = button(mounted.root, '平台')
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
    selectedPeriodId: '', mergePeriodIds: [],
    onDeleteProductClip: async () => {}, onSplit: async () => {}, onRetryProductListener: async () => {}, })
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
    assert.equal(requests.at(-1).body.monitor_id, 'monitor')
    await click(button(body, '关闭商品查询'))
    assert.equal(all(body, el => el.type === 'dialog').length, 0)
  } finally { mounted.app.unmount() }
}

async function verifyDaduoduoControls() {
  const room = { id: 'monitor-a', name: '直播间 A', state: 'idle', count: 0, synced_at: null,
    downloaded: 0, total: null, error: '' }
  respond = (url, options) => {
    if (url.endsWith('/settings/cloud')) return { config: {}, secrets: {}, secret_previews: {} }
    if (url.endsWith('/status')) return status
    if (url.endsWith('/daduoduo')) return { rooms: [room] }
    if (url.endsWith('/monitor-b/product-platform') && options.method === 'PATCH') {
      return { id: 'monitor-b', product_platform: options.body.binding }
    }
    if (url.endsWith('/product-catalog')) return options.method === 'POST'
      ? { ...room, state: 'running', downloaded: 50, total: 295 }
      : room
    throw new Error(`Unexpected request ${url}`)
  }
  let mounted = await mount('views/SettingsView.vue')
  try {
    await click(button(mounted.root, '达多多'))
    assert.ok(text(mounted.root).includes('前往直播监控'))
    assert.ok(text(mounted.root).includes('直播间 A'))
    assert.ok(!button(mounted.root, '保存凭据'))
  } finally { mounted.app.unmount() }

  let saved = 0
  mounted = await mount('components/live/LiveRoomList.vue', {
    monitors: [{ id: 'monitor-b', name: '直播间 B', url: 'https://live.douyin.com/b', sessions: [], product_platform: null }],
    selectedId: 'monitor-a', loading: false, busy: false, onRemove: async () => {},
    onPlatformSaved: () => saved++,
  })
  try {
    await click(button(mounted.root, '直播间 B设置'))
    await click(button(body, '绑定平台'))
    const [dialog] = all(body, el => el.type === 'dialog')
    assert.ok(dialog.open)
    assert.ok(text(dialog).includes('直播间 B'))
    const [select] = all(dialog, el => el.type === 'select')
    select.props.onChange({ target: { value: 'daduoduo' } }); await settle()
    assert.ok(button(dialog, '保存绑定').props.disabled)
    const fields = all(dialog, el => el.type === 'input')
    assert.equal(fields.length, 1)
    fields[0].props['onUpdate:modelValue']('test-ip')
    await settle()
    assert.ok(!button(dialog, '保存绑定').props.disabled)
    const [form] = all(dialog, el => el.type === 'form')
    await form.props.onSubmit({ preventDefault() {} }); await settle()
    assert.deepEqual(calls.find(call => call.url.endsWith('/monitor-b/product-platform')).body,
      { binding: { platform: 'daduoduo', ip_id: 'test-ip' } })
    assert.equal(dialog.open, false)
    assert.equal(saved, 1)
  } finally { mounted.app.unmount() }

  mounted = await mount('components/live/LiveRoomPlatform.vue', {
    monitor: { id: 'monitor-a', product_platform: { platform: 'daduoduo', ip_id: 'i' } },
    busy: false,
  })
  try {
    await click(button(mounted.root, '更新商品'))
    assert.ok(text(mounted.root).includes('已拉取 50 / 295 条'))
    assert.ok(button(mounted.root, '更新中…').props.disabled)
  } finally { mounted.app.unmount() }
}

async function verifySearch() {
  const firstProduct = { title: '测试商品', price: 0, stock: '0', commission_ratio: 0, platform_product_id: 'p1', has_live: false, has_ai_live: false,
    listing_time: '2026-11-16 15:27:24',
    cover: 'javascript:alert(1)' }
  const xingdouResult = { product: firstProduct, scope_name: '店铺 A', can_add_to_showcase: true }
  respond = () => xingdouResult
  const mountSearch = keyword => mount('components/product/ProductSearchDialog.vue', { keyword, monitorId: 'monitor' })
  let mounted = await mountSearch(' 测试商品 ')
  try {
    assert.ok(text(body).includes('测试商品'))
    assert.ok(text(body).includes('上架时间'))
    assert.ok(text(body).includes('2026-11-16 15:27:24'), 'Listing timestamp retains seconds')
    assert.ok(!text(body).includes('最近讲解'))
    assert.ok(!text(body).includes('活动时间'))
    assert.ok(text(body).includes('¥0.00'), 'Zero prices are displayed without treating them as missing')
    assert.ok(text(body).includes('0%'))
    assert.equal(all(body, el => el.type === 'img').length, 0, 'Unsafe image scheme is rejected')
    assert.ok(text(body).includes('仅展示平台返回的第一条'))
    assert.ok(button(body, '添加橱窗'))
    await click(button(body, '添加橱窗'))
    assert.ok(text(body).includes('已添加到当前合作范围的橱窗。'))
    assert.ok(button(body, '已添加橱窗').props.disabled)
    assert.ok(calls.some(call => call.url.endsWith('/products/add-to-showcase')
      && call.body.platform_product_id === 'p1' && call.body.monitor_id === 'monitor'))
  } finally { mounted.app.unmount() }

  respond = () => ({ product: { ...firstProduct, yesterday_sales: '31', listing_time: '2026-11-20', last_explained_at: '11/21 21:15' },
    scope_name: '', can_add_to_showcase: false })
  mounted = await mountSearch('测试商品')
  assert.ok(text(body).includes('近 1 日销量'))
  assert.ok(text(body).includes('上架时间2026-11-20'))
  assert.ok(all(body, el => el.type === 'dl' && text(el).includes('上架时间2026-11-20最近讲解11/21 21:15')).length)
  assert.ok(!button(body, '添加橱窗'))
  mounted.app.unmount()

  respond = () => ({ product: null, scope_name: '店铺 A' })
  mounted = await mountSearch('不存在')
  assert.ok(text(body).includes('未查询到商品')); mounted.app.unmount()

  const authRequired = { status: 401, body: { detail: { code: 'PRODUCT_PLATFORM_AUTH_REQUIRED', message: '未登录' } } }
  respond = () => authRequired
  mounted = await mountSearch('测试商品')
  assert.deepEqual(navigation.at(-1), { path: '/settings', query: { cloudTab: 'product-platform' } })
  mounted.app.unmount()

  const before = navigation.length
  const pending = deferred(); respond = () => pending.promise
  mounted = await mountSearch('测试商品')
  mounted.app.unmount(); pending.resolve(authRequired); await settle()
  assert.equal(navigation.length, before, 'Late authentication failures after close cannot navigate')

  const searchRequest = deferred(); respond = () => searchRequest.promise
  mounted = await mountSearch('重试商品')
  searchRequest.resolve({ status: 503, body: { detail: '网络暂时不可用' } }); await settle()
  assert.ok(text(body).includes('网络暂时不可用'))
  respond = () => xingdouResult
  await click(button(body, '重试'))
  assert.ok(text(body).includes('测试商品')); mounted.app.unmount()

  const initialKeyword = '独束大码女装【轻羽小蛮腰】修身显瘦羽绒服26DS84096（国补）'
  let customSearch = false
  respond = (_url, options) => {
    if (options.body.keyword === initialKeyword && !customSearch) return { product: null, scope_name: '店铺 A' }
    return xingdouResult
  }
  mounted = await mountSearch(initialKeyword)
  try {
    const queryField = all(body, el => el.type === 'input' && el.props['aria-label'] === '商品查询词')[0]
    assert.ok(queryField)
    assert.equal(calls.filter(call => call.url.endsWith('/products/search')).at(-1).body.keyword, initialKeyword,
      'the full lecture product title is searched first')
    queryField.props['onUpdate:modelValue']('独束大码女装【轻羽小蛮腰】修身显瘦羽绒服26DS84096')
    customSearch = true
    const searchForm = all(body, el => el.type === 'form' && el.props.onSubmit)[0]
    assert.ok(searchForm)
    searchForm.props.onSubmit({ preventDefault() {} }); await settle()
    assert.equal(calls.filter(call => call.url.endsWith('/products/search')).at(-1).body.keyword,
      '独束大码女装【轻羽小蛮腰】修身显瘦羽绒服26DS84096', 'edited query is sent to product search')
    assert.ok(text(body).includes('测试商品'))
  } finally { mounted.app.unmount() }
}

async function verifyXingdongli() {
  const account = { ...loggedIn, platform: 'xingdongli', scopes: [{ id: '42', name: '签约达人 A' }] }
  const first = { id: 'one', title: '商品 A', real_time_sales: 0 }
  const second = { id: 'two', title: '商品 B', real_time_sales: 2 }
  respond = (url, options) => {
    if (url.endsWith('/settings/cloud')) return { config: {}, secrets: {}, secret_previews: {} }
    if (url.endsWith('/status')) return status
    if (url.endsWith('/xingdongli')) return { ...status, platform: 'xingdongli' }
    if (url.endsWith('/xingdongli/connect')) return account
    if (url.endsWith('/products/search')) {
      const start = JSON.parse(options.body).start || 0
      const products = start ? [second] : [first, second]
      return { product: products[0], products, start, length: 20, total: 21,
        scope_name: '签约达人 A', can_add_to_showcase: false }
    }
    throw new Error(`Unexpected request ${url}`)
  }
  route.query.platform = 'xingdongli'
  let mounted = await mount('views/SettingsView.vue')
  try {
    input(mounted.root, 'xingdongli-member-token', 'fixture-token')
    const form = all(mounted.root, el => el.type === 'form' && button(el, '验证并保存'))[0]
    await form.props.onSubmit({ preventDefault() {} }); await settle()
    assert.ok(text(mounted.root).includes('签约达人 A'))
    assert.deepEqual(calls.find(call => call.url.endsWith('/xingdongli/connect')).body, { member_token: 'fixture-token' })
  } finally {
    mounted.app.unmount()
    delete route.query.platform
  }
  respond = (url, options) => {
    if (url.endsWith('/xingdongli')) return account
    if (url.endsWith('/monitor-x/product-platform')) return { id: 'monitor-x', product_platform: JSON.parse(options.body).binding }
    throw new Error(`Unexpected request ${url}`)
  }
  mounted = await mount('components/live/LiveRoomList.vue', {
    monitors: [{ id: 'monitor-x', name: '星动力直播间', url: 'https://live.douyin.com/x', sessions: [], product_platform: null }],
    selectedId: 'monitor-x', loading: false, busy: false, onRemove: async () => {},
  })
  try {
    await click(button(mounted.root, '星动力直播间设置'))
    await click(button(body, '绑定平台'))
    const [dialog] = all(body, el => el.type === 'dialog')
    const [platform] = all(dialog, el => el.type === 'select')
    platform.props.onChange({ target: { value: 'xingdongli' } }); await settle()
    const [, scope] = all(dialog, el => el.type === 'select')
    assert.ok(text(scope).includes('签约达人 A'))
    assert.ok(button(dialog, '保存绑定').props.disabled)
    scope.props['onUpdate:modelValue']('42'); await settle()
    assert.ok(!button(dialog, '保存绑定').props.disabled)
    const [form] = all(dialog, el => el.type === 'form')
    await form.props.onSubmit({ preventDefault() {} }); await settle()
    assert.deepEqual(calls.at(-1).body, { binding: { platform: 'xingdongli', ip_id: '42' } })
  } finally { mounted.app.unmount() }
  respond = (url, options) => {
    assert.ok(url.endsWith('/products/search'))
    const start = JSON.parse(options.body).start || 0
    const products = start ? [second] : [first, second]
    return { product: products[0], products, start, length: 20, total: 21,
      scope_name: '签约达人 A', can_add_to_showcase: false }
  }
  mounted = await mount('components/product/ProductSearchDialog.vue', { keyword: '', monitorId: 'monitor', browse: true })
  try {
    const select = all(body, el => el.type === 'select')[0]
    assert.ok(select)
    select.props.onChange({ target: { value: '1' } }); await settle()
    assert.ok(text(body).includes('今日销量2'))
    await click(button(body, '下一页'))
    assert.deepEqual(calls.at(-1).body, { keyword: '', monitor_id: 'monitor', start: 20 })
    assert.ok(button(body, '下一页').props.disabled)
    assert.ok(!button(body, '添加橱窗'))
  } finally { mounted.app.unmount() }
  respond = () => ({ status: 401, body: { detail: { code: 'XINGDONGLI_AUTH_REQUIRED', message: '已失效' } } })
  mounted = await mount('components/product/ProductSearchDialog.vue', { keyword: '商品', monitorId: 'monitor' })
  assert.deepEqual(navigation.at(-1), { path: '/settings', query: { cloudTab: 'product-platform', platform: 'xingdongli' } })
  mounted.app.unmount()
}

async function verifyDouyinScan() {
  route.query = { cloudTab: 'douyin' }
  respond = url => {
    assert.ok(url.endsWith('/settings/cloud'))
    return {
      config: { base_url: '', planner_model: '', oss_endpoint: '', oss_bucket: '' },
      secrets: {}, secret_previews: {},
    }
  }
  let scan = deferred()
  let scanCalls = 0
  window.__TAURI_INTERNALS__ = { invoke(command, args) {
    assert.equal(command, 'scan_douyin_login')
    assert.deepEqual(args, { sessionToken: 'isolated-local-session' })
    scanCalls++
    return scan.promise
  } }
  const mounted = await mount('views/SettingsView.vue')
  try {
    mounted.session.connected = true
    await settle()
    const settings = all(mounted.root, el => el.props.id === 'settings-panel-douyin')[0]
    assert.ok(button(settings, '保存 Cookie'))
    const cancelled = button(settings, '扫码登录').props.onClick()
    await settle()
    assert.equal(button(settings, '等待扫码…').props.disabled, true)
    await button(settings, '等待扫码…').props.onClick()
    assert.equal(scanCalls, 1)
    scan.resolve({ state: 'cancelled' })
    await cancelled
    await settle()
    assert.match(text(settings), /已取消扫码登录/)
    assert.match(text(settings), /未配置/)
    scan = deferred()
    const saved = button(settings, '扫码登录').props.onClick()
    scan.resolve({ state: 'saved', preview: 'sess*****test' })
    await saved
    await settle()
    assert.ok(button(settings, '重新扫码登录'))
    assert.match(text(settings), /抖音登录状态已保存/)
  } finally {
    mounted.app.unmount()
    delete window.__TAURI_INTERNALS__
  }
}

async function main() {
  const originalFetch = global.fetch
  const originalWindow = global.window
  global.window = { innerHeight: 768, innerWidth: 1024, addEventListener() {}, removeEventListener() {} }
  global.fetch = async (url, options) => {
    calls.push({ url, body: options.body ? JSON.parse(options.body) : undefined })
    const result = await respond(url, options)
    return new Response(JSON.stringify(result?.body ?? result), { status: result?.status ?? 200, headers: { 'Content-Type': 'application/json' } })
  }
  try {
    await verifyLogin(); await verifySearch(); await verifyLectureButton(); await verifyDaduoduoControls(); await verifyXingdongli()
    assert.ok(calls.every(call => call.url.startsWith('/api/v1/')), 'The frontend only calls its authenticated local API')
    await verifyDouyinScan()
    console.log('Product platform UI checks passed: settings, SMS and QR login, Xingdongli token and pagination, product cards and auth redirects.')
  } finally { global.fetch = originalFetch; global.window = originalWindow }
}
main().catch(error => { console.error(error); process.exitCode = 1 })
