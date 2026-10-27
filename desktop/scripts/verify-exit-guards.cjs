const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const { modules } = require('./test-modules.cjs')

const { load } = modules()
const { registerExitGuard, hasUnsavedChanges } = load('exit-guards.ts')

let projectDirty = false
let settingsDirty = false
const stopProject = registerExitGuard(() => projectDirty)
const stopSettings = registerExitGuard(() => settingsDirty)

assert.equal(hasUnsavedChanges(), false)
projectDirty = true
assert.equal(hasUnsavedChanges(), true, 'unsaved clip-plan edits must block immediate exit')
projectDirty = false
settingsDirty = true
assert.equal(hasUnsavedChanges(), true, 'unsaved settings and secret drafts must block immediate exit')
stopSettings()
assert.equal(hasUnsavedChanges(), false, 'unmounted settings must no longer guard exit')
stopProject()

const appSource = fs.readFileSync(path.resolve(__dirname, '../src/App.vue'), 'utf8')
assert.match(appSource, /@click="returnToSave">返回保存/, 'return-to-save must leave the window open')
assert.match(appSource, /@click="continueExit">放弃并退出/, 'only explicit discard continues exit')

console.log('Exit guard checks passed: project and settings drafts are visible to the exit coordinator.')
