// Verify the real Vue component with mocked credentials and an in-memory renderer.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vue = require('vue')
const { parse, compileScript } = require('vue/compiler-sfc')
const ts = require('typescript')
const {modules}=require('./test-modules.cjs')
const {createPinia,setActivePinia}=require('pinia')

const source = fs.readFileSync(path.join(__dirname, '../src/components/SecretInput.vue'), 'utf8')
const { descriptor } = parse(source)
const compiled = compileScript(descriptor, { id: 'secret-input-check', inlineTemplate: true })
const code = ts.transpileModule(compiled.content, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText
let reads = 0, pending
const clipboardWrites = []
let clipboardFails = false
const mockClipboard = async value => {
  if (clipboardFails) throw new Error('Clipboard permission denied')
  clipboardWrites.push(value)
}
const fakeApi = async (url, method) => {
  reads++
  assert.equal(url, '/settings/secrets/llm_api_key/reveal')
  assert.equal(method, 'POST')
  return new Promise(resolve => { pending = resolve })
}
const moduleResult = { exports: {} }
new Function('require', 'module', 'exports', code)(
  name => name === 'vue' ? vue : name === '../api' ? { api: fakeApi } : name === '../clipboard' ? { writeClipboard: mockClipboard } : require(name),
  moduleResult, moduleResult.exports,
)
const component = moduleResult.exports.default
const node = type => ({ type, props: {}, children: [], parent: null })
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

async function verify() {
  const state = vue.reactive({ modelValue: '', saved: true, preview: 'sk-exa*****1234', disabled: false })
  const emitted = []
  const app = renderer.createApp({
    render: () => vue.h(component, {
      id: 'test-key', secretName: 'llm_api_key', ...state,
      'onUpdate:modelValue': value => { emitted.push(value); state.modelValue = value },
    }),
  })
  const root = node('root')
  app.mount(root)
  const input = root.children[0].children[0].children.find(child => child.type === 'input')
  const eye = root.children[0].children[0].children.find(child => child.type === 'button')
  const copy = root.children[0].children[0].children.find(child => child.props.class === 'secret-copy')
  const settle = async () => { await Promise.resolve(); await vue.nextTick() }
  const type = async value => { input.props.onInput({ target: { value } }); await settle() }

  assert.equal(input.props.type, 'text')
  assert.equal(input.props.value, state.preview)
  assert.equal(reads, 0, 'Mounting must not read full credentials')
  input.props.onFocus(); input.props.onClick?.(); await settle()
  assert.equal(input.props.value, state.preview, 'Clicking and focusing must preserve preview')
  assert.equal(emitted.length, 0, 'Focusing must not create a replacement draft')
  const hiddenCopy = copy.props.onClick()
  pending({ value: 'sk-example-private-1234' })
  await hiddenCopy; await settle()
  assert.equal(clipboardWrites.at(-1), 'sk-example-private-1234', 'Hidden copy must use full saved credential')
  assert.equal(input.props.value, state.preview, 'Copy must preserve hidden display')
  assert.equal(emitted.length, 0, 'Copy must not create a draft')
  assert.equal(copy.props.title, '已复制')
  const reveal = eye.props.onClick()
  pending({ value: 'sk-example-private-1234' })
  await reveal; await settle()
  assert.equal(input.props.value, 'sk-example-private-1234')
  input.props.onFocus(); input.props.onClick?.(); await settle()
  assert.equal(input.props.value, 'sk-example-private-1234', 'Focusing must preserve revealed credential')
  assert.equal(state.modelValue, '', 'Viewing must not change replacement draft')
  const readsBeforeCopy = reads
  await copy.props.onClick(); await settle()
  assert.equal(reads, readsBeforeCopy, 'Revealed copy reuses full value')
  assert.equal(clipboardWrites.at(-1), 'sk-example-private-1234')
  clipboardFails = true
  await copy.props.onClick(); await settle()
  assert.notEqual(copy.props.title, '已复制', 'Clipboard failure must not report success')
  assert.equal(root.children[0].children.find(child => child.type === 'p').text, '复制失败，请重试或查看完整值后手动复制')
  clipboardFails = false
  await eye.props.onClick(); await settle()
  assert.equal(input.props.value, state.preview)

  input.props.onFocus(); await settle()
  assert.equal(input.props.value, state.preview)
  await type('sk-exa*****1234x')
  assert.equal(state.modelValue, '', 'Partially edited preview must not become a credential')
  assert.equal(input.props.value, state.preview)
  await type('replacement')
  input.props.onClick?.(); await settle()
  assert.equal(input.props.value, 'replacement', 'Clicking draft must preserve entered text')
  assert.equal(eye.props.disabled, true)
  await copy.props.onClick(); await settle()
  assert.equal(clipboardWrites.at(-1), 'replacement', 'Copy new draft instead of old saved value')
  await type('')
  assert.equal(input.props.value, '', 'Deleting draft stays empty until blur')
  input.props.onBlur(); await settle()
  assert.equal(input.props.value, state.preview)

  const lateReveal = eye.props.onClick()
  input.props.onFocus(); await settle()
  await type('new-key-5678')
  pending({ value: 'sk-example-private-1234' })
  await lateReveal; await settle()
  assert.equal(input.props.value, 'new-key-5678', 'Late response must not overwrite editing')
  state.disabled = true; await settle()
  state.modelValue = ''; state.preview = 'new-*****5678'; await settle()
  state.disabled = false; await settle()
  assert.equal(input.props.value, 'new-*****5678', 'Saving restores updated preview')
  assert.ok(!emitted.includes('sk-example-private-1234'))

  const writesBeforeStaleCopy = clipboardWrites.length
  const staleCopy = copy.props.onClick()
  await type('updated-key-9012')
  pending({ value: 'sk-example-private-1234' })
  await staleCopy; await settle()
  assert.equal(clipboardWrites.length, writesBeforeStaleCopy, 'Editing during read cancels copying outdated value')
  state.disabled = true; state.modelValue = ''; await settle()
  state.disabled = false; await settle()

  const editRevealed = eye.props.onClick()
  pending({ value: 'sk-example-private-1234' })
  await editRevealed; await settle()
  await type('sk-example-private-9999')
  assert.equal(state.modelValue, 'sk-example-private-9999', 'Explicitly editing full value creates a draft')
  state.disabled = true; state.modelValue = ''; await settle()
  state.disabled = false; await settle()

  const unmountedReveal = eye.props.onClick()
  app.unmount()
  pending({ value: 'sk-example-private-1234' })
  await unmountedReveal; await settle()
  console.log('SecretInput interaction checks passed (mock credentials only).')
}

async function verifySettings() {
  const secretPreview = value => `${value.slice(0, value.startsWith('sk-') ? 6 : 4)}*****${value.slice(-4)}`
  const values = {}, submissions = []
  let bindings, savedConfig
  const settingsApi = async (url, method = 'GET', body) => {
    if (url === '/auth') return {}
    if (url === '/jobs-summary') return {active:0}
    if (url.startsWith('/projects?')) return {items:[],total:0,page:1,page_size:20,pages:1}
    if (['/assets', '/products', '/candidates', '/plans', '/jobs', '/exports'].includes(url)) return []
    if (url === '/settings/cloud') {
      if (method === 'PATCH') { savedConfig = structuredClone(body); return savedConfig }
      savedConfig ||= JSON.parse(JSON.stringify(bindings.config.value))
      return {
        config: { ...savedConfig },
        secrets: Object.fromEntries(Object.keys(values).map(name => [name, true])),
        secret_previews: Object.fromEntries(Object.entries(values).map(([name, value]) => [name, secretPreview(value)])),
      }
    }
    if (url === '/settings/secrets') {
      values[body.name] = body.value
      submissions.push({ ...body })
      return { saved: true, preview: secretPreview(body.value) }
    }
    throw new Error(`Unexpected API route: ${url}`)
  }
  const appSource = fs.readFileSync(path.join(__dirname, '../src/views/SettingsView.vue'), 'utf8')
  assert.ok(!appSource.includes('<h2>百炼 ASR</h2>'), 'Separate ASR card must be removed')
  assert.ok(appSource.includes('qwen-audio-3.1-asr-flash'))
  const appScript = compileScript(parse(appSource).descriptor, { id: 'settings-save-check' })
  const appCode = ts.transpileModule(appScript.content, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText
  const pinia=createPinia();setActivePinia(pinia)
  const loaded=modules({[path.resolve(__dirname,'../src/api.ts')]:{api:settingsApi}})
  loaded.load('stores/session.ts').useSessionStore(pinia).connected=true
  const appModule = { exports: {} }
  new Function('require', 'module', 'exports', appCode)(name => {
    if (name === 'vue') return vue
    if (name === '../services/cloud') return loaded.load('services/cloud.ts')
    if (name === '../stores/session') return loaded.load('stores/session.ts')
    if (name === './events') return { subscribeJobEvents: () => () => {} }
    if (name === '../components/JobErrorDetails.vue') return { __esModule: true, default: {} }
    if (name === '../components/WorkspaceSplit.vue') return { __esModule: true, default: {} }
    if (name === '../components/SecretInput.vue') return { __esModule: true, default: component }
    return require(name)
  }, appModule, appModule.exports)
  const fields = ['llm_api_key', 'oss_access_key_id', 'oss_access_key_secret']
  const root = node('root')
  const originalEventSource = global.EventSource
  global.EventSource = class { addEventListener() {} close() {} }
  const app = renderer.createApp({
    setup() {
      // Exercise SettingsView's actual loading/save methods and shared input components together.
      bindings = appModule.exports.default.setup({}, { expose() {} })
      return () => vue.h('section', fields.map(name => vue.h(component, {
        id: name, secretName: name, disabled: bindings.busy.value,
        saved: name === 'llm_api_key' ? bindings.modelKeyConfigured.value : Boolean(bindings.secrets.value[name]),
        preview: name === 'llm_api_key' ? bindings.modelKeyPreview.value : bindings.secretPreviews.value[name] || '',
        modelValue: name === 'llm_api_key' ? bindings.modelKey.value : bindings.secretDrafts.value[name],
        'onUpdate:modelValue': value => {
          if (name === 'llm_api_key') bindings.modelKey.value = value
          else bindings.secretDrafts.value[name] = value
        },
      })))
    },
  })
  const settle = async () => { for (let i = 0; i < 12; i++) { await Promise.resolve(); await vue.nextTick() } }
  try {
    app.use(pinia);app.mount(root)
    await settle()
    assert.equal(bindings.error.value, '')
    const inputs = root.children[0].children.filter(child => child.type === 'div').map(child => child.children[0].children[0])
    const keys = ['sk-model-example-1234', 'LTAI-example-9012', 'oss-example-3456']
    for (let i = 0; i < fields.length; i++) {
      inputs[i].props.onFocus()
      inputs[i].props.onInput({ target: { value: keys[i] } })
      await settle()
      if (i === 0) await bindings.saveModelConnection()
      else await bindings.saveSecret(fields[i])
      await settle()
      assert.equal(bindings.error.value, '', 'Saving should not fail')
      assert.equal(inputs[i].props.value, secretPreview(keys[i]), `${fields[i]} must display preview immediately after save`)
    }
    const count = submissions.length
    await bindings.saveModelConnection(); await settle()
    assert.equal(submissions.length, count, 'Saving settings without a new key must preserve all credentials')
    assert.deepEqual(fields.map((name, i) => inputs[i].props.value), keys.map(secretPreview), 'Settings reload preserves every preview')
    bindings.secretPreviews.value = {}
    await bindings.loadSettings(); await settle()
    assert.deepEqual(inputs.map(input => input.props.value), keys.map(secretPreview), 'Saved keys reload from backend metadata')
    assert.equal(submissions.length, 3)
    bindings.config.value.chat_timeout_seconds = 600
    bindings.config.value.asr_model = 'custom-qwen-audio-v1'
    bindings.config.value.oss_bucket = 'unsaved-oss-draft'
    bindings.secretDrafts.value.oss_access_key_id = 'unsaved-oss-id'
    const bucketBefore = savedConfig.oss_bucket
    await bindings.saveModelConnection(); await settle()
    assert.equal(savedConfig.chat_timeout_seconds, 600, 'Model connection saves chat timeout')
    assert.equal(savedConfig.asr_model, 'custom-qwen-audio-v1', 'Model connection saves manual speech model')
    assert.equal(savedConfig.oss_bucket, bucketBefore, 'Model save must not persist OSS draft')
    assert.equal(bindings.config.value.oss_bucket, 'unsaved-oss-draft', 'Model save preserves OSS draft locally')
    assert.equal(bindings.secretDrafts.value.oss_access_key_id, 'unsaved-oss-id')
    bindings.config.value.asr_model = 'another-unsaved-model'
    await bindings.saveSettings(); await settle()
    assert.equal(savedConfig.oss_bucket, 'unsaved-oss-draft', 'OSS save persists its own fields')
    assert.equal(savedConfig.asr_model, 'custom-qwen-audio-v1', 'OSS save must not persist model draft')
    assert.equal(bindings.config.value.asr_model, 'another-unsaved-model', 'OSS save preserves model draft locally')
    const configBeforeKeySave = structuredClone(savedConfig)
    await bindings.saveSecret('oss_access_key_id'); await settle()
    assert.deepEqual(savedConfig, configBeforeKeySave, 'OSS key save must not change model/OSS settings')
    console.log('Settings save/reload checks passed (three mock credentials).')
  } finally {
    app.unmount()
    global.EventSource = originalEventSource
  }
}

async function main() {
  await verify()
  await verifySettings()
}
main().catch(error => { console.error(error); process.exitCode = 1 })
