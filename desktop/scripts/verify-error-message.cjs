const assert = require('node:assert/strict')
const { modules } = require('./test-modules.cjs')
const { ref, nextTick, watch } = require('vue')

const { errorMessage } = modules().load('error-message.ts')
assert.equal(errorMessage(new Error('runtime error')), 'runtime error')
assert.equal(errorMessage('Rust command rejected'), 'Rust command rejected')
assert.equal(errorMessage({ message: 'structured rejection' }), 'structured rejection')
assert.equal(errorMessage({ code: 42 }), '{"code":42}')
assert.equal(errorMessage(null), '桌面退出控制返回了空错误')
assert.equal(errorMessage(undefined), '操作失败')
assert.equal(errorMessage('  '), '操作失败')

const error = ref(''), revision = ref(0)
let notifications = 0
const stop = watch(revision, () => { if (error.value) notifications++ })
async function verifyRepeatedNotifications() {
  error.value = errorMessage({ message: 'complete_exit failed' }); revision.value++
  await nextTick()
  error.value = errorMessage({ message: 'complete_exit failed' }); revision.value++
  await nextTick()
  stop()
  assert.equal(notifications, 2, 'identical consecutive failures must each produce a notification')
  console.log('Error checks passed: native errors retain diagnostics and repeated identical failures notify every time.')
}
verifyRepeatedNotifications().catch(error => { console.error(error); process.exitCode = 1 })
