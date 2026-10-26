const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')

function loadApi() {
  const source = fs.readFileSync(path.join(__dirname, '../src/api.ts'), 'utf8')
    .replace("import.meta.env.VITE_SESSION_TOKEN || ''", "'development-fixture-token'")
  const code = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText
  const module = { exports: {} }
  new Function('module', 'exports', code)(module, module.exports)
  return module.exports
}

async function main() {
  const originalFetch = global.fetch
  const calls = []
  try {
    global.fetch = async (url, options) => {
      calls.push({ url, options })
      return new Response('{}', { headers: { 'Content-Type': 'application/json' } })
    }
    global.__DOUYINCUT_SESSION_TOKEN__ = 'private-runtime-token'
    const production = loadApi()
    await production.apiRequest('/projects')
    assert.equal(calls[0].options.headers.get('X-Session-Token'), 'private-runtime-token')
    assert.equal(calls[0].url, '/api/v1/projects')
    assert.equal(calls[0].options.credentials, 'include')
    assert.equal(global.__DOUYINCUT_SESSION_TOKEN__, undefined, 'Consume the native injection after capturing it in module memory')
    const development = loadApi()
    await development.apiRequest('/projects')
    assert.equal(calls[1].options.headers.get('X-Session-Token'), 'development-fixture-token')
  } finally {
    global.fetch = originalFetch
    delete global.__DOUYINCUT_SESSION_TOKEN__
  }
  console.log('Runtime session checks passed: private memory injection, same-origin requests and development fallback.')
}

main().catch(error => { console.error(error); process.exitCode = 1 })
