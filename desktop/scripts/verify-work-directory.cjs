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
  // Check the generated command ACL against both actual desktop window labels.
  // Frontend mocks alone cannot catch Tauri rejecting a command before its handler.
  const tauriRoot = path.join(__dirname, '../src-tauri')
  const session = fs.readFileSync(path.join(tauriRoot, 'src/session.rs'), 'utf8')
  const setupWindowStart = session.indexOf('config.url = tauri::WebviewUrl::App("setup.html".into());')
  const setupWindowEnd = session.indexOf('\n    Ok(())', setupWindowStart)
  assert.ok(setupWindowStart >= 0 && setupWindowEnd > setupWindowStart, 'Could not locate production setup window creation')
  const setupWindow = session.slice(setupWindowStart, setupWindowEnd)
  assert.match(setupWindow, /config\.visible = false;/, 'Setup window must start hidden')
  assert.doesNotMatch(setupWindow, /window\.show\(\)/, 'Setup window must not be shown unconditionally during startup')
  const acl = JSON.parse(fs.readFileSync(path.join(tauriRoot, 'gen/schemas/acl-manifests.json'), 'utf8'))['__app-acl__']
  const capabilities = Object.values(JSON.parse(fs.readFileSync(path.join(tauriRoot, 'gen/schemas/capabilities.json'), 'utf8')))
  const localSettings = fs.readFileSync(path.join(__dirname, '../src/views/LocalSettingsView.vue'), 'utf8')
  const commands = [...localSettings.matchAll(/\.invoke\("([^"]+)"\)/g)].map(match => match[1])
  assert.ok(commands.includes('delete_workspace_config'), 'Exercise the reported directory command')
  const config = JSON.parse(fs.readFileSync(path.join(tauriRoot, 'tauri.conf.json'), 'utf8'))
  for (const window of [config.app.windows[0].label || 'main', 'workbench']) {
    for (const command of commands) {
      const grants = capabilities.filter(capability => capability.windows?.includes(window) &&
        capability.permissions.some(permission => acl.permissions[permission]?.commands.allow.includes(command)))
      assert.ok(grants.some(capability => capability.local), `${command} not allowed on window "${window}", URL: local`)
      assert.ok(grants.some(capability => capability.remote?.urls.includes('http://127.0.0.1:*')),
        `${command} not allowed on window "${window}", URL: http://127.0.0.1:*`)
    }
  }
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
  console.log('Local maintenance ACL passed for main/workbench windows and local/loopback pages.')
  console.log('Work directory flow passed: selection, cancel, repair, retry, open and failure.')
}
main().catch(error => { console.error(error); process.exitCode = 1 })
