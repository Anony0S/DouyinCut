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
assert.match(appSource, /await tauri\.invoke\('complete_exit'\)/, 'native invoke must retain its Tauri bridge receiver')
assert.doesNotMatch(appSource, /const invoke=.*__TAURI_INTERNALS__\?\.invoke/, 'do not detach the native invoke method')
assert.match(appSource, /exitPrompt\.value='activity'\s+session\.report\(cause\)/,
  'a failed native exit must restore the activity prompt so the user can retry')
assert.match(appSource, /watch\(errorRevision,\(\)=>\{if\(error\.value\) pushToast\('error',error\.value\)\}\)/,
  'identical consecutive errors must still be announced')
assert.match(appSource, /不等于撤销已到达云端的请求.*直接重发.*产生费用/,
  'cloud exit confirmation must explain that interrupted requests may be sent again and charged')

console.log('Exit guard checks passed: project and settings drafts are visible to the exit coordinator.')
