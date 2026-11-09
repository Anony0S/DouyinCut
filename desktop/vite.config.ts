import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import tailwindcss from '@tailwindcss/vite'
import vueDevTools from 'vite-plugin-vue-devtools'
import { readFileSync } from 'node:fs'

const spec = readFileSync(new URL('../SPEC.md', import.meta.url), 'utf8')
const appVersion = spec.match(/^- 版本：(\d+\.\d+\.\d+)\s*$/m)?.[1]
if (!appVersion) throw new Error('SPEC.md is missing a valid version')

export default defineConfig(({ mode }) => ({
  // A developer's environment must never bake a credential into shipped JS.
  define: { __APP_VERSION__: JSON.stringify(appVersion), ...(mode === 'production' ? { 'import.meta.env.VITE_SESSION_TOKEN': JSON.stringify('') } : {}) },
  build: { rollupOptions: { input: { workbench: 'index.html', setup: 'setup.html' } } },
  plugins: [vue({ template: { compilerOptions: {
    isCustomElement: tag => tag === 'calendar-date' || tag === 'calendar-month',
  } } }), tailwindcss(), vueDevTools()],
  server: {
    port: 5173, strictPort: true,
    // Cargo locks target/*.exe while building; watching it crashes Vite with EBUSY.
    watch: { ignored: ['**/src-tauri/**'] },
    proxy: { '/api': { target: `http://127.0.0.1:${process.env.DOUYINCUT_PORT || '8765'}` } },
  }
}))
