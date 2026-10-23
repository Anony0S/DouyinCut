const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vue = require('vue')
const { parse, compileScript } = require('vue/compiler-sfc')
const ts = require('typescript')

const source = fs.readFileSync(path.join(__dirname, '../src/components/ConfirmPopover.vue'), 'utf8')
const compiled = compileScript(parse(source).descriptor, { id: 'confirm-popover-check', inlineTemplate: true })
const code = ts.transpileModule(compiled.content, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText
const componentModule = { exports: {} }
new Function('require', 'module', 'exports', code)(name => name === 'vue' ? vue : require(name), componentModule, componentModule.exports)
const component = componentModule.exports.default

const node = type => {
  const element = { type, props: {}, children: [], parent: null, focused: false,
    getBoundingClientRect: () => ({ left: 40, top: 80, right: 140, bottom: 110, width: 100, height: 30 }),
    focus() { element.focused = true },
    contains(target) { return target === element || element.children.some(child => child.contains?.(target)) },
  }
  return element
}
const body = node('body')
const windowListeners = new Map()
global.window = {
  innerWidth: 900, innerHeight: 700,
  addEventListener(type, handler) { windowListeners.set(type, handler) },
  removeEventListener(type, handler) { if (windowListeners.get(type) === handler) windowListeners.delete(type) },
}
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
  querySelector: selector => selector === 'body' ? body : null,
})
const find = (element, predicate) => predicate(element) ? element : element.children.map(child => find(child, predicate)).find(Boolean)
const all = (element, predicate) => [...(predicate(element) ? [element] : []), ...element.children.flatMap(child => all(child, predicate))]
const settle = async () => { for (let i = 0; i < 8; i++) { await Promise.resolve(); await vue.nextTick() } }

async function verify() {
  let resolveConfirm
  let confirmed = 0
  const root = node('root')
  const app = renderer.createApp({ render: () => vue.h(component, {
    title: '删除项目？', message: '项目文件会被清理。',
    confirmAction: () => { confirmed++; return new Promise(resolve => { resolveConfirm = resolve }) },
  }, { default: ({ open, expanded, popoverId }) => vue.h('button', {
    class: 'trigger', 'aria-expanded': expanded, 'aria-controls': popoverId, onClick: open,
  }, '删除') }) })
  app.mount(root)
  const secondRoot = node('root')
  const secondApp = renderer.createApp({ render: () => vue.h(component, { title: '删除另一个项目？', message: '另一项目', confirmAction: () => {} }, { default: ({ open, expanded, popoverId }) => vue.h('button', { class: 'other-trigger', 'aria-expanded': expanded, 'aria-controls': popoverId, onClick: open }, '删除') }) })
  secondApp.mount(secondRoot)
  try {
    const trigger = find(root, element => element.props.class === 'trigger')
    trigger.props.onClick({ currentTarget: trigger, target: trigger })
    await settle()
    const otherTrigger = find(secondRoot, element => element.props.class === 'other-trigger')
    assert.notEqual(trigger.props['aria-controls'], otherTrigger.props['aria-controls'], 'each trigger has a unique dialog id')
    otherTrigger.props.onClick({ currentTarget: otherTrigger, target: otherTrigger })
    await settle()
    assert.equal(all(body, element => element.props.role === 'dialog').length, 1, 'only one instance can be open')
    trigger.props.onClick({ currentTarget: trigger, target: trigger })
    await settle()
    const dialog = find(body, element => element.props.role === 'dialog')
    assert.ok(dialog, 'confirmation popover opens from the trigger')
    assert.ok(dialog.children.some(child => `${child.text || ''}`.includes('项目文件会被清理。')))
    const cancel = find(dialog, element => element.props['aria-label'] === '取消')
    cancel.props.onClick()
    await settle()
    assert.equal(find(body, element => element.props.role === 'dialog'), undefined)
    assert.equal(trigger.focused, true, 'focus returns to the trigger after cancel')

    trigger.props.onClick({ currentTarget: trigger, target: trigger })
    await settle()
    const openDialog = find(body, element => element.props.role === 'dialog')
    const confirm = find(openDialog, element => element.props['aria-label'] === '确定删除')
    confirm.props.onClick()
    await settle()
    assert.equal(confirmed, 1)
    assert.equal(confirm.props.disabled, true, 'confirm is disabled while the action is pending')
    confirm.props.onClick()
    assert.equal(confirmed, 1, 'pending confirmation cannot be submitted twice')
    windowListeners.get('keydown')({ key: 'Escape' })
    await settle()
    trigger.props.onClick({ currentTarget: trigger, target: trigger })
    await settle()
    assert.equal(find(body, element => element.props.role === 'dialog'), undefined, 'dismissing a pending action cannot unlock a second submission')
    resolveConfirm()
    await settle()
    assert.equal(find(body, element => element.props.role === 'dialog'), undefined)

    trigger.props.onClick({ currentTarget: trigger, target: trigger })
    await settle()
    windowListeners.get('pointerdown')({ target: node('outside') })
    await settle()
    assert.equal(find(body, element => element.props.role === 'dialog'), undefined, 'outside click cancels')

    trigger.props.onClick({ currentTarget: trigger, target: trigger })
    await settle()
    windowListeners.get('keydown')({ key: 'Escape' })
    await settle()
    assert.equal(find(body, element => element.props.role === 'dialog'), undefined, 'Escape cancels')

    window.innerWidth = 240; window.innerHeight = 500
    trigger.getBoundingClientRect = () => ({ left: 210, top: 450, right: 230, bottom: 480 })
    trigger.props.onClick({ currentTarget: trigger, target: trigger })
    await settle()
    const narrow = find(body, element => element.props.role === 'dialog')
    assert.equal(narrow.props.style.width, '216px', 'popover fits a very narrow viewport')
    assert.equal(narrow.props.style.left, '12px', 'right-edge trigger keeps popover in viewport')
    assert.equal(narrow.props.style.bottom, '58px', 'above placement stays anchored eight pixels from trigger')
    assert.ok(find(narrow, element => element.props['aria-label'] === '取消').focused, 'cancel receives opening focus')
  } finally { app.unmount(); secondApp.unmount(); body.children.splice(0); global.window = undefined }
  console.log('Confirmation popover checks passed: open, cancel, confirm, focus, outside click, Escape and pending lock.')
}

verify().catch(error => { console.error(error); process.exitCode = 1 })
