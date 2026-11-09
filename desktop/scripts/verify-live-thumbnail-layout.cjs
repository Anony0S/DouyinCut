const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const { execFileSync } = require('node:child_process')
const ts = require('typescript')
const layoutModule = { exports: {} }
const layoutCode = ts.transpileModule(fs.readFileSync(path.join(__dirname, '../src/components/live/timelineThumbnailLayout.ts'), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText
new Function('exports', layoutCode)(layoutModule.exports)
const { timelineThumbnailLayout } = layoutModule.exports

const chrome = process.env.CHROME_BIN || [
  process.env.PROGRAMFILES && path.join(process.env.PROGRAMFILES, 'Google/Chrome/Application/chrome.exe'),
  process.env['PROGRAMFILES(X86)'] && path.join(process.env['PROGRAMFILES(X86)'], 'Microsoft/Edge/Application/msedge.exe'),
].filter(Boolean).find(file => fs.existsSync(file))
assert.ok(chrome, 'Chromium is required for real image layout verification')
const dist = path.join(__dirname, '../dist')
const index = fs.readFileSync(path.join(dist, 'index.html'), 'utf8')
const builtCss = [...index.matchAll(/<link[^>]+rel="stylesheet"[^>]+href="([^"]+\.css)"/g)]
  .map(([, href]) => fs.readFileSync(path.join(dist, href.replace(/^\//, '')), 'utf8')).join('\n')
const sourceCss = fs.readFileSync(path.join(__dirname, '../src/styles/live-monitoring.css'), 'utf8')
const cases = [
  [180, 320, 140, 360], [320, 180, 400, 360], [180, 180, 660, 360], [80, 320, 920, 360],
  [180, 320, 8, 30], [320, 180, 1195, 580], [180, 320, 500, 200],
]
const surfaces = cases.map(([width, height, x, top]) => {
  const layout = timelineThumbnailLayout({ x, top, bottom: top + 28, viewportWidth: 1200, viewportHeight: 600 }, width, height)
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}"><rect width="100%" height="50%" fill="red"/><rect y="50%" width="100%" height="50%" fill="blue"/></svg>`
  return `<div class="card border border-base-300 bg-base-100 text-base-content shadow-lg live-product-hover-preview" style="left:${layout.left}px;top:${layout.top}px;width:${layout.width}px;--preview-image-height:${layout.imageHeight}px" role="tooltip"><div class="live-product-hover-image bg-neutral"><img alt="frame" src="data:image/svg+xml;base64,${Buffer.from(svg).toString('base64')}"/></div><time class="px-2 py-1 text-xs tabular-nums">00:50</time></div>`
}).join('')
const html = `<!doctype html><html><head><meta charset="utf-8"><style>${builtCss}\n${sourceCss}</style></head><body>${surfaces}<script>
Promise.all([...document.images].map(img => img.decode())).then(() => {
  const results = [...document.images].map(img => {
    const image = img.getBoundingClientRect();
    const box = img.parentElement.getBoundingClientRect();
    const popup = img.closest('[role=tooltip]').getBoundingClientRect();
    return { natural: [img.naturalWidth, img.naturalHeight], image: {width:image.width,height:image.height}, box: {width:box.width,height:box.height}, fullFrame: image.top >= box.top - 1 && image.bottom <= box.bottom + 1 && image.left >= box.left - 1 && image.right <= box.right + 1, popupVisible: popup.top >= 0 && popup.bottom <= 600 && popup.left >= 0 && popup.right <= 1200, proportional: Math.abs(image.width / image.height - img.naturalWidth / img.naturalHeight) < .02, fit: getComputedStyle(img).objectFit };
  });
  document.body.dataset.results = JSON.stringify(results);
});
</script></body></html>`
const fixture = path.join(os.tmpdir(), `live-thumbnail-layout-${process.pid}.html`)
try {
  fs.writeFileSync(fixture, html)
  const output = execFileSync(chrome, ['--headless', '--disable-gpu', '--no-sandbox', '--disable-dev-shm-usage', '--window-size=1200,800', '--virtual-time-budget=1000', '--dump-dom', `file:///${fixture.replace(/\\/g, '/')}`], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] })
  const match = output.match(/data-results="([^"]+)"/)
  assert.ok(match, 'Chromium measured decoded images')
  const results = JSON.parse(match[1].replaceAll('&quot;', '"').replaceAll('&amp;', '&'))
  console.log(JSON.stringify(results, null, 2))
  for (const item of results) {
    assert.ok(item.fullFrame, `${item.natural.join('x')} frame extends outside its visible image container`)
    assert.ok(item.popupVisible, 'preview remains inside the viewport')
    assert.ok(item.proportional, 'preview follows the source orientation and aspect ratio')
    assert.equal(item.fit, 'contain', 'complete image is fitted without cropping')
  }
} finally {
  fs.rmSync(fixture, { force: true })
}
