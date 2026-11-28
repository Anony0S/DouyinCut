<script setup lang="ts">
import { computed, onActivated, onDeactivated, onMounted, onUnmounted, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { ApiError } from '../../api'
import { productPlatformApi, type SessionAvailabilityStatus } from '../../services/productPlatform'

const props = defineProps<{
  monitorId: string
  sessionId: string
  platform: 'xingdou' | 'xingdongli'
  bindingKey: string
  hasProducts: boolean
  hasChecked: boolean
  busy: boolean
}>()
const emit = defineEmits<{ updated: [] }>()

const submitting = ref(false)
const status = ref<SessionAvailabilityStatus>()
const error = ref('')
const errorCode = ref('')
const running = computed(() => status.value?.state === 'queued' || status.value?.state === 'running')
const effectiveError = computed(() => error.value || status.value?.error || '')
const effectiveErrorCode = computed(() => errorCode.value || status.value?.error_code || '')
const needsAuth = computed(() =>
  effectiveErrorCode.value === 'PRODUCT_PLATFORM_AUTH_REQUIRED'
  || effectiveErrorCode.value === 'XINGDONGLI_AUTH_REQUIRED',
)
const settingsRoute = computed(() => ({
  path: '/settings',
  query: props.platform === 'xingdongli'
    ? { cloudTab: 'product-platform', platform: 'xingdongli' }
    : { cloudTab: 'product-platform' },
}))
const buttonLabel = computed(() => {
  if (submitting.value || running.value) return '检测中…'
  return props.hasChecked ? '刷新库存' : '检测库存'
})

let active = true
let readVersion = 0
let timer: ReturnType<typeof setInterval> | undefined

function stopPolling() {
  clearInterval(timer)
  timer = undefined
}

function ensurePolling() {
  if (!active || timer !== undefined) return
  timer = setInterval(() => { void readStatus() }, 1200)
}

function applyStatus(next: SessionAvailabilityStatus, previous?: SessionAvailabilityStatus) {
  status.value = next
  const wasRunning = previous?.state === 'queued' || previous?.state === 'running'
  const progressed = next.checked > (previous?.checked ?? 0)
  const finished = wasRunning && (next.state === 'idle' || next.state === 'failed')
  if (next.state === 'queued' || next.state === 'running') {
    ensurePolling()
  } else {
    stopPolling()
  }
  if (progressed || finished) emit('updated')
}

async function readStatus() {
  if (!active || submitting.value) return
  const version = ++readVersion
  const prev = status.value
  try {
    const result = await productPlatformApi.sessionAvailabilityStatus(props.monitorId, props.sessionId)
    if (!active || version !== readVersion) return
    applyStatus(result, prev)
  } catch {
    if (active && version === readVersion) stopPolling()
  }
}

async function refreshAvailability() {
  if (submitting.value || running.value || !props.hasProducts) return
  submitting.value = true
  error.value = ''
  errorCode.value = ''
  ++readVersion
  const targetKey = `${props.monitorId}:${props.sessionId}:${props.bindingKey}`
  const prev = status.value
  try {
    const result = await productPlatformApi.refreshSessionAvailability(props.monitorId, props.sessionId)
    if (!active || targetKey !== `${props.monitorId}:${props.sessionId}:${props.bindingKey}`) return
    applyStatus(result, prev)
  } catch (cause) {
    if (!active || targetKey !== `${props.monitorId}:${props.sessionId}:${props.bindingKey}`) return
    error.value = cause instanceof Error ? cause.message : '库存检测启动失败'
    errorCode.value = cause instanceof ApiError ? cause.code || '' : ''
    stopPolling()
  } finally {
    submitting.value = false
  }
}

watch(
  () => `${props.monitorId}:${props.sessionId}:${props.bindingKey}`,
  () => {
    ++readVersion
    stopPolling()
    status.value = undefined
    error.value = ''
    errorCode.value = ''
  },
)

function activate() {
  active = true
  if (running.value) void readStatus()
}

function deactivate() {
  active = false
  readVersion++
  stopPolling()
}

onMounted(activate)
onActivated(activate)
onDeactivated(deactivate)
onUnmounted(deactivate)
</script>

<template>
  <div class="flex flex-wrap items-center gap-x-2 gap-y-1" role="group" aria-label="本场商品库存检测">
    <span v-if="platform === 'xingdou'" class="font-medium text-base-content">商品平台 星斗</span>
    <button
      type="button"
      class="btn btn-xs"
      :disabled="busy || submitting || running || !hasProducts"
      :title="hasProducts ? '批量检测本场讲解商品是否有货及库存' : '当前场次暂无可检测商品'"
      @click="refreshAvailability">
      {{ buttonLabel }}
    </button>
    <span v-if="status?.state === 'queued'" role="status">等待检测</span>
    <span v-else-if="status?.state === 'running'" role="status">
      已检测 {{ status.checked }} / {{ status.total }} 件
    </span>
    <p v-if="effectiveError" class="basis-full flex flex-wrap items-center gap-x-2 gap-y-1 text-error" role="alert">
      <span>{{ effectiveError }}</span>
      <RouterLink v-if="needsAuth" :to="settingsRoute" class="link link-primary">前往设置</RouterLink>
    </p>
  </div>
</template>
