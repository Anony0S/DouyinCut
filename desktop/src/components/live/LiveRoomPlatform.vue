<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { productPlatformApi, type CatalogStatus } from '../../services/productPlatform'
import type { LiveMonitor } from '../../services/liveMonitoring'

const props = defineProps<{ monitor: LiveMonitor; busy: boolean }>()
const updating = ref(false)
const status = ref<CatalogStatus>(), error = ref('')
const syncing = computed(() => status.value?.state === 'running' || status.value?.state === 'queued')
const bindingKey = computed(() => JSON.stringify(props.monitor.product_platform))
let active = true
let readVersion = 0
let timer: ReturnType<typeof setInterval>

async function readStatus() {
  if (updating.value) return
  const version = ++readVersion
  try {
    const result = await productPlatformApi.catalogStatus(props.monitor.id)
    if (active && version === readVersion) status.value = result
  } catch (cause) {
    if (active && version === readVersion && !status.value) {
      error.value = cause instanceof Error ? cause.message : '读取商品目录状态失败'
    }
  }
}

async function update() {
  updating.value = true
  error.value = ''
  ++readVersion
  const key = bindingKey.value
  try {
    const result = await productPlatformApi.updateCatalog(props.monitor.id)
    if (active && key === bindingKey.value) status.value = result
  } catch (cause) {
    if (active && key === bindingKey.value) error.value = cause instanceof Error ? cause.message : '更新商品失败'
  } finally { updating.value = false }
}

watch(bindingKey, () => {
  ++readVersion
  status.value = undefined
  error.value = ''
  void readStatus()
})
onMounted(() => {
  void readStatus()
  timer = setInterval(readStatus, 2500)
})
onUnmounted(() => {
  active = false
  clearInterval(timer)
})
</script>

<template>
  <div class="flex flex-wrap items-center gap-x-2 gap-y-1" role="group" aria-label="直播间商品平台">
    <span class="font-medium text-base-content">商品平台 达多多</span>
    <button type="button" class="btn btn-xs" :disabled="busy || updating || syncing" @click="update">
      {{ syncing || updating ? '更新中…' : '更新商品' }}
    </button>
    <span v-if="status?.state === 'queued'" role="status">等待更新</span>
    <span v-else-if="status?.state === 'running'" role="status">
      已拉取 {{ status.downloaded }} / {{ status.total ?? '—' }} 条
    </span>
    <span v-if="status?.synced_at">
      {{ status.count }} 条 · {{ new Date(status.synced_at * 1000).toLocaleString() }}
    </span>
    <span v-else-if="status && !syncing">商品列表尚未同步</span>
    <p v-if="error || status?.error" class="basis-full text-error" role="alert">{{ error || status?.error }}</p>
  </div>
</template>
