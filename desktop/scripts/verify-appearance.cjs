const assert = require('node:assert/strict')
// Initialize Vue before providing the minimal document used by this non-DOM test.
require('vue')
const { modules } = require('./test-modules.cjs')

const saved = new Map()
const mediaListeners = new Set()
const storageListeners = new Set()
const media = {
  matches: false,
  addEventListener: (_, listener) => mediaListeners.add(listener),
  removeEventListener: (_, listener) => mediaListeners.delete(listener),
}
global.document = { documentElement: { dataset: {} } }
global.window = {
  matchMedia: () => media,
  addEventListener: (_, listener) => storageListeners.add(listener),
  removeEventListener: (_, listener) => storageListeners.delete(listener),
}
global.localStorage = {
  getItem: key => saved.get(key) ?? null,
  setItem: (key, value) => saved.set(key, value),
}
const theme = () => document.documentElement.dataset.theme
const changeSystem = dark => {
  media.matches = dark
  for (const listener of mediaListeners) listener()
}
const { appearance, setAppearance, startAppearance } = modules().load('appearance.ts')
let stop = startAppearance()
assert.equal(appearance.value, 'system')
assert.equal(theme(), 'light')
changeSystem(true)
assert.equal(theme(), 'dark')
setAppearance('light')
changeSystem(false)
changeSystem(true)
assert.equal(theme(), 'light')
stop()
stop = startAppearance()
assert.equal(appearance.value, 'light')
assert.equal(theme(), 'light')
setAppearance('dark')
changeSystem(false)
assert.equal(theme(), 'dark')
setAppearance('system')
assert.equal(theme(), 'light')
for (const listener of storageListeners) listener({ key: 'douyincut.appearance', newValue: 'dark' })
assert.equal(theme(), 'dark')
for (const listener of storageListeners) listener({ key: null, newValue: null })
assert.equal(appearance.value, 'system')
assert.equal(theme(), 'light')
stop()
assert.equal(mediaListeners.size, 0)
assert.equal(storageListeners.size, 0)
localStorage.getItem = () => { throw new Error('Storage unavailable') }
localStorage.setItem = () => { throw new Error('Storage unavailable') }
stop = startAppearance()
assert.equal(appearance.value, 'system')
setAppearance('dark')
assert.equal(theme(), 'dark')
stop()

// The packaged workbench is served from a random localhost port on every launch.
// Its theme must therefore use the native app configuration store rather than
// port-scoped WebView localStorage.
;(async () => {
  let nativeValue = 'dark'
  const nativeCalls = []
  global.window.__TAURI_INTERNALS__ = {
    invoke: async (command, args) => {
      nativeCalls.push({ command, args })
      if (command === 'get_appearance_preference') return nativeValue
      if (command === 'set_appearance_preference') {
        nativeValue = args.appearance
        return nativeValue
      }
      throw new Error(`Unexpected native command: ${command}`)
    },
  }
  stop = startAppearance()
  await Promise.resolve()
  assert.equal(appearance.value, 'dark')
  setAppearance('light')
  await Promise.resolve()
  assert.equal(nativeValue, 'light')
  assert.ok(nativeCalls.some(call => call.command === 'get_appearance_preference'))
  assert.ok(nativeCalls.some(call => call.command === 'set_appearance_preference'))
  stop()
  console.log('Appearance: system changes, explicit overrides, persistence, native packaged persistence, storage sync, unavailable storage and cleanup passed.')
})().catch(error => {
  console.error(error)
  process.exitCode = 1
})
