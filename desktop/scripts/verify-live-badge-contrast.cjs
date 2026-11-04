const assert = require('node:assert/strict')
const { execFileSync } = require('node:child_process')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')

const dist = path.join(__dirname, '../dist')
const index = fs.readFileSync(path.join(dist, 'index.html'), 'utf8')
const stylesheets = [...index.matchAll(/<link[^>]+rel="stylesheet"[^>]+href="([^"]+\.css)"/g)]
  .map(([, href]) => path.join(dist, href.replace(/^\//, '')))
assert.ok(stylesheets.length, 'built application stylesheets exist')

const chrome = process.env.CHROME_BIN || [
  process.env.PROGRAMFILES && path.join(process.env.PROGRAMFILES, 'Google/Chrome/Application/chrome.exe'),
  process.env['PROGRAMFILES(X86)'] && path.join(process.env['PROGRAMFILES(X86)'], 'Microsoft/Edge/Application/msedge.exe'),
].filter(Boolean).find(file => fs.existsSync(file))
assert.ok(chrome, 'set CHROME_BIN to a Chromium browser executable')

const cases = [
  ['neutral', '未开播'],
  ['info', '待导入'],
  ['success', '已导入'],
  ['warning', '正在导入'],
  ['error', '文件异常'],
]
const chips = cases.map(([color, label]) => `<span class="badge badge-soft badge-${color}">${label}</span>`).join('')
const css = stylesheets.map(file => fs.readFileSync(file, 'utf8')).join('\n')
const surfaces = ['light', 'dark'].map(theme => `<main class="live-page" data-theme="${theme}">${chips}<span class="badge badge-ghost">数量</span></main>`).join('')
const html = `<!doctype html><html data-theme="light"><head><meta charset="utf-8"><style>${css}</style></head><body>${surfaces}<script>
const luminance = value => {
  const canvas = document.createElement('canvas')
  canvas.width = canvas.height = 1
  const context = canvas.getContext('2d')
  context.fillStyle = '#000'
  context.fillStyle = value
  context.fillRect(0, 0, 1, 1)
  const channels = [...context.getImageData(0, 0, 1, 1).data.slice(0, 3)].map(channel => {
    const normalized = channel / 255
    return normalized <= .04045 ? normalized / 12.92 : ((normalized + .055) / 1.055) ** 2.4
  })
  return channels[0] * .2126 + channels[1] * .7152 + channels[2] * .0722
}
const contrast = (foreground, background) => {
  const [light, dark] = [luminance(foreground), luminance(background)].sort((a, b) => b - a)
  return (light + .05) / (dark + .05)
}
const results = [...document.querySelectorAll('.live-page .badge')].map(element => {
  const style = getComputedStyle(element)
  return { theme: element.closest('.live-page').dataset.theme, label: element.textContent, foreground: style.color, background: style.backgroundColor, ratio: contrast(style.color, style.backgroundColor) }
})
document.body.dataset.contrastResults = JSON.stringify(results)
</script></body></html>`

const fixture = path.join(os.tmpdir(), `live-badge-contrast-${process.pid}.html`)
try {
  fs.writeFileSync(fixture, html)
  const output = execFileSync(chrome, [
    '--headless', '--disable-gpu', '--no-sandbox', '--disable-dev-shm-usage', '--dump-dom',
    `file:///${fixture.replace(/\\/g, '/')}`,
  ], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] })
  const match = output.match(/data-contrast-results="([^\"]+)"/)
  assert.ok(match, 'Chromium computed badge colors')
  const results = JSON.parse(match[1].replaceAll('&quot;', '"').replaceAll('&amp;', '&'))
  const failures = []
  for (const item of results) {
    console.log(`${item.theme} ${item.label}: ${item.ratio.toFixed(2)}:1 (${item.foreground} on ${item.background})`)
    if (item.ratio < 4.5) failures.push(`${item.theme} ${item.label}: ${item.ratio.toFixed(2)}:1`)
  }
  assert.deepEqual(failures, [], `live badge contrast is below 4.5:1: ${failures.join(', ')}`)
} finally {
  fs.rmSync(fixture, { force: true })
}
