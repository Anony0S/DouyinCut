<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { desktopUpdatesAvailable, updateCommand, type UpdateStatus } from '../services/updates'

declare const __APP_VERSION__: string
const appVersion = __APP_VERSION__
const desktop = desktopUpdatesAvailable()
const development = import.meta.env.DEV
const status = ref<UpdateStatus | null>(null), error = ref(''), commandBusy = ref(false)
const busy = computed(() => commandBusy.value || ['checking', 'downloading', 'installing'].includes(status.value?.phase || ''))
const percent = computed(() => status.value?.totalBytes ? Math.min(100, Math.floor(status.value.downloadedBytes / status.value.totalBytes * 100)) : 0)
const canDownload = computed(() => status.value?.hasUpdate && !busy.value && status.value.phase !== 'ready')
let stopped = false, timer: ReturnType<typeof setTimeout> | undefined

async function refresh() {
  try {
    const result = await updateCommand<UpdateStatus>('get_update_status')
    if (!stopped) status.value = result
  } catch (cause) {
    if (!stopped) error.value = String(cause)
  } finally {
    if (!stopped) timer = setTimeout(refresh, 750)
  }
}

async function perform(command: string) {
  if (commandBusy.value) return
  commandBusy.value = true
  error.value = ''
  try { await updateCommand(command) }
  catch (cause) { if (!stopped) error.value = String(cause) }
  finally { commandBusy.value = false }
}

function install() {
  window.dispatchEvent(new Event('douyincut-update-request'))
}
const megabytes = (bytes: number) => (bytes / 1024 / 1024).toFixed(1)
onMounted(() => { if (desktop) void refresh() })
onUnmounted(() => { stopped = true; if (timer !== undefined) clearTimeout(timer) })
</script>

<template>
  <li class="list-row">
    <div class="local-setting-copy list-col-grow">
      <h2>版本更新</h2>
      <p>当前版本 <strong>v{{ appVersion }}</strong>，从 GitHub Releases 获取正式版本。</p>
      <p v-if="!desktop">请在 Windows 桌面应用中检查和安装更新。</p>
      <p v-else-if="development">开发模式可以检查和下载更新，安装请使用正式安装版。</p>
    </div>
    <div class="local-setting-actions">
      <button class="btn btn-outline" type="button" :disabled="!desktop || busy" @click="perform('check_for_update')">
        {{ status?.phase === 'checking' ? '检查中…' : '检查更新' }}
      </button>
      <button v-if="canDownload" class="btn" type="button" @click="perform('download_update')">下载 v{{ status?.latestVersion }}</button>
      <button v-if="status?.phase === 'downloading'" class="btn" type="button" :disabled="commandBusy" @click="perform('cancel_update_download')">取消下载</button>
      <button v-if="status?.phase === 'ready'" class="btn btn-primary" type="button" :disabled="development || busy" @click="install">退出并安装</button>
    </div>
    <div v-if="status?.message || error" class="list-col-wrap min-w-0" aria-live="polite">
      <p :class="{ 'text-error': error || status?.phase === 'error' }" role="status">{{ error || status?.message }}</p>
      <template v-if="status?.phase === 'downloading'">
        <progress class="progress mt-2 w-full" :value="status.downloadedBytes" :max="status.totalBytes || 1" aria-label="安装包下载进度"></progress>
        <p>{{ percent }}% · {{ megabytes(status.downloadedBytes) }} / {{ megabytes(status.totalBytes) }} MB</p>
      </template>
      <details v-if="status?.hasUpdate && status.notes" class="mt-3">
        <summary class="cursor-pointer">v{{ status.latestVersion }} 更新说明</summary>
        <p class="mt-2 max-h-64 overflow-auto whitespace-pre-wrap break-words">{{ status.notes }}</p>
      </details>
    </div>
  </li>
</template>
