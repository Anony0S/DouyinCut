const assert = require('node:assert/strict')
const { modules } = require('./test-modules.cjs')

const saved = new Map()
global.window = {}
global.localStorage = {
  getItem: key => saved.get(key) ?? null,
  setItem: (key, value) => saved.set(key, value),
}

const preferences = modules().load('window-preferences.ts')

async function main() {
  assert.deepEqual(await preferences.loadWindowPreferences(), {
    rememberPosition: true,
    startupSize: 'standard',
  })
  assert.deepEqual(await preferences.saveWindowPreferences({
    rememberPosition: false,
    startupSize: 'qhd',
  }), {
    rememberPosition: false,
    startupSize: 'qhd',
  })
  assert.deepEqual(JSON.parse(saved.get('douyincut.window-preferences')), {
    rememberPosition: false,
    startupSize: 'qhd',
  })

  const calls = []
  window.__TAURI_INTERNALS__ = {
    invoke: async (command, args) => {
      calls.push([command, args])
      return { rememberPosition: true, startupSize: 'standard' }
    },
  }
  assert.deepEqual(await preferences.loadWindowPreferences(), {
    rememberPosition: true,
    startupSize: 'standard',
  })
  assert.deepEqual(await preferences.saveWindowPreferences({
    rememberPosition: true,
    startupSize: 'full-hd',
  }), {
    rememberPosition: true,
    startupSize: 'standard',
  })
  assert.deepEqual(calls, [
    ['get_window_preferences', undefined],
    ['set_window_preferences', { rememberPosition: true, startupSize: 'full-hd' }],
  ])
  console.log('Window preferences: browser persistence and native command bridge passed.')
}

main().catch(error => {
  console.error(error)
  process.exitCode = 1
})
