import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import tailwindcss from '@tailwindcss/vite'
import vueDevTools from 'vite-plugin-vue-devtools'

export default defineConfig({
  plugins: [vue(), tailwindcss(), vueDevTools()],
  server: {
    port: 5173, strictPort: true,
    proxy: { '/api': { target: `http://127.0.0.1:${process.env.DOUYINCUT_PORT || '8765'}` } },
  }
})
