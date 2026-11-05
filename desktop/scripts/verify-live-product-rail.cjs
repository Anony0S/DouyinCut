const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vue = require('vue')
const ts = require('typescript')
const { parse, compileScript } = require('vue/compiler-sfc')

const src = path.resolve(__dirname, '../src')
const originalSetInterval = global.setInterval
const originalClearInterval = global.clearInterval
global.setInterval = () => 1
global.clearInterval = () => {}
global.window = { addEventListener() {}, removeEventListener() {} }

const session = {
  id: 'session', started: 1000, ended: 1100, status: 'recording',
  product_periods: [{
    id: 'period', session_id: 'session', promotion_id: 'product', product_name: '商品',
    started: 1000, ended: 1100, mapping_error: '', clip_job: null,
    sources: [
      { segment_id: 'sealed', segment_index: 1, state: 'ready', start: 1000, end: 1080 },
      { segment_id: 'recording', segment_index: 2, state: 'recording', start: 1080, end: 1100 },
    ],
  }],
}

const makeNode = (type, text = '') => ({
  type, text, props: {}, children: [], parent: null,
  focus() {}, setPointerCapture() {},
  getBoundingClientRect() { return { left: 200, width: 800, right: 1000, top: 0, height: 34, bottom: 34 } },
})
const renderer = vue.createRenderer({
  createElement: type => makeNode(type),
  createComment: () => makeNode('comment'),
  createText: text => makeNode('text', text),
  setText: (element, text) => { element.text = text },
  setElementText: (element, text) => { element.text = text },
  patchProp: (element, key, _previous, value) => { element.props[key] = value },
  insert(element, parent, anchor) {
    if (element.parent) this.remove(element)
    const index = parent.children.indexOf(anchor)
    parent.children.splice(index < 0 ? parent.children.length : index, 0, element)
    element.parent = parent
  },
  remove(element) {
    if (element.parent) element.parent.children.splice(element.parent.children.indexOf(element), 1)
    element.parent = null
  },
  parentNode: element => element.parent,
  nextSibling: element => element.parent?.children[element.parent.children.indexOf(element) + 1],
  querySelector: () => makeNode('body'),
})
const all = (element, predicate) => [
  ...(predicate(element) ? [element] : []),
  ...element.children.flatMap(child => all(child, predicate)),
]
const settle = async () => { for (let i = 0; i < 12; i++) { await Promise.resolve(); await vue.nextTick() } }
const load = file => {
  let source = fs.readFileSync(file, 'utf8')
  if (file.endsWith('.vue')) {
    source = compileScript(parse(source).descriptor, { id: file, inlineTemplate: true }).content
  }
  const module = { exports: {} }
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText
  new Function('require', 'module', 'exports', code)((name) => {
    if (name === 'vue') return { ...vue, vModelText: {}, vModelCheckbox: {} }
    if (name.endsWith('.css')) return {}
    if (!name.startsWith('.')) return require(name)
    const dependency = path.resolve(path.dirname(file), name)
    const resolved = path.extname(dependency) ? dependency : `${dependency}.ts`
    return load(resolved)
  }, module, module.exports)
  return module.exports
}

async function verify() {
  const { productPeriodColor } = load(path.join(src, 'components/live/presentation.ts'))
  const expectedColor = productPeriodColor(session.product_periods[0].promotion_id)
  assert.equal(productPeriodColor('product'), expectedColor, 'a product keeps the same color across views and renders')
  const distinctColors = new Set(Array.from({ length: 30 }, (_, index) => productPeriodColor(`product-${index}`)))
  assert.ok(distinctColors.size >= 29, 'many product IDs receive distinct colors without a small repeating palette')
  const { default: Component } = load(path.join(src, 'components/live/LiveProductTimeline.vue'))
  const root = makeNode('root')
  const app = renderer.createApp(Component, {
    session, selectedPeriodId: 'period', busy: false,
    onSplit: async () => ({ state: 'noop', message: 'done' }),
  })
  app.mount(root)
  try {
    await settle()
    const periodButton = all(root, element => element.props.class?.includes('live-product-rail-period'))[0]
    assert.equal(periodButton.props.style['--period-color'], expectedColor, 'the rail uses the shared product color mapping')
    const splitButton = all(root, element => element.type === 'button' && element.props.class?.includes('btn-primary'))[0]
    await splitButton.props.onClick()
    await settle()
    const overlay = all(root, element => element.props.class === 'live-product-range-overlay')[0]
    const handles = all(overlay, element => element.props.role === 'slider')
    assert.equal(overlay.props.style.width, '80%', 'selectable overlay ends at the sealed-media boundary')
    assert.equal(handles[0].props.style.left, '0%', 'start thumb center aligns with the selectable interval start')
    assert.equal(handles[1].props.style.left, '100%', 'end thumb center aligns with the selectable interval end')
    assert.equal(handles[1].props['aria-valuemax'], 80)

    handles[1].props.onPointermove({ clientX: 600 })
    await settle()
    assert.equal(handles[1].props['aria-valuenow'], 80, 'hover pointer movement does not move an idle handle')

    handles[1].props.onPointerdown({ currentTarget: handles[1], pointerId: 1, clientX: 1000, preventDefault() {} })
    handles[1].props.onPointermove({ clientX: 1800, pointerId: 1 })
    await settle()
    assert.equal(handles[1].props['aria-valuenow'], 80, 'dragging beyond the track clamps to the sealed boundary')
    assert.equal(handles[1].props.style.left, '100%', 'right thumb stays aligned at the boundary after dragging')
    assert.equal(overlay.props.style.width, '80%', 'dragging does not resize or shift the selectable interval')

    session.product_periods[0].sources.push({ segment_id: 'new-sealed', segment_index: 3, state: 'ready', start: 1080, end: 1090 })
    await settle()
    assert.equal(handles[1].props['aria-valuemax'], 80, 'newly sealed media does not shift handles during the active selection')
    assert.equal(handles[1].props.style.left, '100%')
    handles[1].props.onPointerup({ pointerId: 1 })

    handles[0].props.onPointerdown({ currentTarget: handles[0], pointerId: 2, clientX: 200, preventDefault() {} })
    handles[0].props.onPointermove({ clientX: 300, pointerId: 2 })
    await settle()
    assert.equal(handles[0].props['aria-valuenow'], 10, 'pointer coordinates map to the overlay track without native range insets')
    assert.equal(handles[0].props.style.left, '12.5%')
    assert.ok(Number(handles[1].props['aria-valuenow']) - Number(handles[0].props['aria-valuenow']) >= 1, 'the handles preserve a one-second minimum interval')
    handles[0].props.onPointerup({ pointerId: 2 })
    handles[0].props.onPointermove({ clientX: 700, pointerId: 2 })
    await settle()
    assert.equal(handles[0].props['aria-valuenow'], 10, 'pointer movement after release does not move the handle')
  } finally {
    app.unmount()
  }

  const longSession = {
    id: 'long-session', started: 1000, ended: 5000, status: 'stopped',
    product_periods: [{
      id: 'long-period', session_id: 'long-session', promotion_id: 'long-product', product_name: '长视频商品',
      started: 1000, ended: 4000, mapping_error: '', clip_job: null,
      sources: [{ segment_id: 'long-segment', segment_index: 1, state: 'ready', start: 1000, end: 4000 }],
    }],
  }
  let submittedRange = null
  const longRoot = makeNode('root')
  const longApp = renderer.createApp(Component, {
    session: longSession, selectedPeriodId: 'long-period', busy: false,
    onSplit: async (_id, _confirmed, range) => { submittedRange = range; return { state: 'noop', message: 'done' } },
  })
  longApp.mount(longRoot)
  try {
    await settle()
    const splitButton = all(longRoot, element => element.type === 'button' && element.props.class?.includes('btn-primary'))[0]
    await splitButton.props.onClick()
    await settle()
    const overlay = all(longRoot, element => element.props.class === 'live-product-range-overlay')[0]
    const fill = all(overlay, element => element.props.class === 'live-product-range-fill')[0]
    const handles = all(overlay, element => element.props.role === 'slider')
    assert.equal(handles[0].props['aria-valuenow'], 0)
    assert.equal(handles[1].props['aria-valuenow'], 1200, 'long lectures initially select only 20 minutes')

    fill.props.onPointerdown({ currentTarget: fill, pointerId: 3, clientX: 200, preventDefault() {} })
    fill.props.onPointermove({ pointerId: 3, clientX: 280 })
    await settle()
    assert.equal(handles[0].props['aria-valuenow'], 300, 'dragging the selected range moves its start')
    assert.equal(handles[1].props['aria-valuenow'], 1500, 'dragging the selected range preserves its duration')
    fill.props.onPointerup({ pointerId: 3 })

    handles[1].props.onPointerdown({ currentTarget: handles[1], pointerId: 4, clientX: 600, preventDefault() {} })
    handles[1].props.onPointermove({ pointerId: 4, clientX: 653.333 })
    await settle()
    assert.equal(handles[1].props['aria-valuenow'], 1700, 'a handle may move away from the previous duration grid when outside the snap zone')
    handles[1].props.onPointerup({ pointerId: 4 })

    handles[1].props.onPointerdown({ currentTarget: handles[1], pointerId: 5, clientX: 653.333, preventDefault() {} })
    handles[1].props.onPointermove({ pointerId: 5, clientX: 674.667 })
    await settle()
    assert.equal(handles[1].props['aria-valuenow'], 1800, 'the selected duration snaps to a five-minute multiple from the opposite handle')
    assert.ok(handles[1].props.class.includes('is-snapped'), 'a handle shows a visual state while snapped')
    handles[1].props.onPointerup({ pointerId: 5 })
    await settle()
    assert.equal(handles[1].props.class.includes('is-snapped'), false, 'the visual snap state clears after releasing the handle')

    const confirmButton = all(longRoot, element => element.type === 'button' && element.props.class?.includes('btn-primary'))[0]
    await confirmButton.props.onClick({ currentTarget: confirmButton, target: confirmButton, preventDefault() {}, stopPropagation() {} })
    await settle()
    assert.deepEqual(submittedRange, { start: 1300, end: 2800 }, 'confirmation submits only the selected absolute range')
  } finally {
    longApp.unmount()
  }

  const sessionPanel = fs.readFileSync(path.join(src, 'components/live/LiveSessionPanel.vue'), 'utf8')
  const liveStyles = fs.readFileSync(path.resolve(src, '../src/styles/live-monitoring.css'), 'utf8')
  assert.match(sessionPanel, /:style="productColorStyle\(period\.promotion_id\)"/, 'lecture list entries use the shared product color')
  assert.match(sessionPanel, /<span v-if="clipPeriodTime\(clip\.period_id\)" class="live-clip-period-time" :style="productColorStyle\(clip\.promotion_id\)">/, 'clip lecture labels use the shared product color in the metadata row')
  assert.match(liveStyles, /\.live-event-time time \{ color: var\(--period-color\)/, 'lecture timestamps display their product color')
  assert.match(liveStyles, /\.live-clip-period-time \{ color: var\(--period-color\)/, 'clip lecture labels display their product color')

  session.product_periods[0].sources.pop()
  session.status = 'stopped'
  const endedRoot = makeNode('root')
  const endedApp = renderer.createApp(Component, {
    session, selectedPeriodId: 'period', busy: false,
    onSplit: async () => ({ state: 'noop', message: 'done' }),
  })
  endedApp.mount(endedRoot)
  try {
    await settle()
    const splitButton = all(endedRoot, element => element.type === 'button' && element.props.class?.includes('btn-primary'))[0]
    await splitButton.props.onClick()
    await settle()
    const overlay = all(endedRoot, element => element.props.class === 'live-product-range-overlay')[0]
    const handles = all(overlay, element => element.props.role === 'slider')
    assert.equal(handles[1].props['aria-valuemax'], 100, 'ended sessions allow selection through the lecture end despite a stale recording source state')
    assert.equal(handles[1].props.style.left, '100%')
  } finally {
    endedApp.unmount()
    global.setInterval = originalSetInterval
    global.clearInterval = originalClearInterval
    delete global.window
  }
}

verify().then(() => console.log('live product rail range checks passed')).catch(error => {
  console.error(error)
  process.exitCode = 1
})
