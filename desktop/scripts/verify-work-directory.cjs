const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const code = ts.transpileModule(fs.readFileSync(path.join(__dirname, '../src/work-directory.ts'), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText
const moduleResult = { exports: {} }
new Function('module', 'exports', code)(moduleResult, moduleResult.exports)

async function main() {
  const calls = []
  const replies = [
    { state: 'selection_required', path: null, message: '请选择目录' },
    { state: 'selection_required', path: null, message: '已取消' },
    { state: 'repair_required', path: 'D:/工作', message: '恢复原盘符' },
    { state: 'ready', path: 'D:/工作', message: '' },
    { state: 'opened', path: 'D:/工作', message: '' },
  ]
  const flow = moduleResult.exports.createWorkDirectoryFlow(async (command, args) => {
    assert.equal(command, 'workspace_action')
    calls.push(args.action)
    return replies.shift()
  })
  await flow.run('inspect')
  assert.equal(flow.view.state, 'selection_required')
  await flow.run('select')
  assert.equal(flow.view.state, 'selection_required', 'Cancellation stays on setup')
  assert.deepEqual(calls, ['inspect', 'select'], 'Cancellation never starts services')
  await flow.run('inspect')
  assert.equal(flow.view.state, 'repair_required')
  assert.equal(flow.view.path, 'D:/工作')
  await flow.run('inspect')
  assert.equal(flow.view.state, 'opened', 'Retry opens only after successful preflight')
  assert.deepEqual(calls, ['inspect', 'select', 'inspect', 'inspect', 'open'])
  const failure = moduleResult.exports.createWorkDirectoryFlow(async () => { throw new Error('权限不足') })
  await failure.run('inspect')
  assert.equal(failure.view.state, 'repair_required')
  assert.match(failure.view.message, /权限/)
  assert.equal(failure.view.busy, false)
  console.log('Work directory flow passed: selection, cancel, repair, retry, open and failure.')
}
main().catch(error => { console.error(error); process.exitCode = 1 })
