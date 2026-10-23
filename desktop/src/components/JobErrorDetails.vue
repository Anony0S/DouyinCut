<script setup lang="ts">
import { computed, onUnmounted, ref } from 'vue'
import { api, type JobErrorDetail } from '../api'

const props = defineProps<{ jobId: string }>()
const trigger = ref<HTMLButtonElement | null>(null)
const opened = ref(false), pinned = ref(false), loading = ref(false), failure = ref('')
const detail = ref<JobErrorDetail | null>(null)
const position = ref<Record<string, string>>({})
const hasBody = computed(() => detail.value?.response_body !== null && detail.value?.response_body !== undefined)
const source = computed(() => detail.value?.error_source || 'unknown')
const sourceLabel = computed(() => ({
  transport: 'HTTP 客户端连接异常', http_response: '已收到云端 HTTP 响应',
  response_validation: '已收到云端结果，应用解析失败', unknown: '应用任务错误',
}[source.value] || ''))
const noResponse = computed(() => {
  if (detail.value?.stage === 'remote_disconnected' || detail.value?.reason?.includes('RemoteDisconnected:'))
    return '对端在返回 HTTP 状态和正文前关闭了连接。没有收到云端错误正文，断开原因未由对端提供；服务、网关或中间代理均可能断开连接。'
  if (source.value === 'transport') return '连接异常，未取得云端响应正文。下方 reason 是 HTTP 客户端捕获的原始异常，不是云端返回内容。'
  return '未收到或未保存云端响应正文。'
})
const metadata = computed(() => {
  if (!detail.value) return ''
  const { response_body, request_body, request_headers, request_method, summary, reason, ...values } = detail.value
  return JSON.stringify(values, null, 2)
})
let closeTimer: ReturnType<typeof setTimeout> | undefined
let disposed = false

function cancelClose() { if (closeTimer) clearTimeout(closeTimer) }
function close() { cancelClose(); opened.value = false; pinned.value = false }
function scheduleClose() {
  cancelClose()
  if (!pinned.value) closeTimer = setTimeout(close, 180)
}
async function show() {
  cancelClose()
  const rect = trigger.value?.getBoundingClientRect()
  if (rect) {
    const width = Math.min(680, window.innerWidth - 24)
    const below = window.innerHeight - rect.bottom - 20
    position.value = {
      width: `${width}px`, left: `${Math.max(12, Math.min(rect.left, window.innerWidth - width - 12))}px`,
      ...(below >= 180 ? { top: `${rect.bottom + 8}px`, maxHeight: `${below}px` }
        : { bottom: `${window.innerHeight - rect.top + 8}px`, maxHeight: `${Math.max(80, rect.top - 20)}px` }),
    }
  }
  opened.value = true
  if (detail.value || loading.value) return
  loading.value = true; failure.value = ''
  try {
    const result = await api<JobErrorDetail>(`/jobs/${props.jobId}/error-detail`)
    if (!disposed) detail.value = result
  } catch (error) {
    if (!disposed) failure.value = error instanceof Error ? error.message : '错误详情加载失败'
  } finally { if (!disposed) loading.value = false }
}
function toggle() {
  if (pinned.value) close()
  else { pinned.value = true; void show() }
}
onUnmounted(() => { disposed = true; cancelClose() })
</script>

<template>
  <span class="job-error-details" @keydown.esc="close">
    <button ref="trigger" type="button" class="job-error-trigger" aria-label="查看完整错误信息"
      :aria-expanded="opened" :aria-controls="`job-error-${jobId}`" aria-haspopup="dialog"
      @mouseenter="show" @mouseleave="scheduleClose" @focus="show" @blur="scheduleClose" @click="toggle">
      <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" stroke-width="1.7"/><path d="M12 7v6" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/><circle cx="12" cy="16.5" r="1" fill="currentColor"/></svg>
    </button>
    <div v-if="opened" :id="`job-error-${jobId}`" class="job-error-popover" :style="position"
      role="dialog" aria-label="云端错误详情" :aria-modal="false" tabindex="0"
      @mouseenter="cancelClose" @mouseleave="scheduleClose" @focusin="cancelClose" @focusout="scheduleClose">
      <div class="job-error-heading"><strong>完整错误信息</strong><button type="button" class="secondary" aria-label="关闭错误详情" @click="close">关闭</button></div>
      <p v-if="loading" role="status">正在加载错误详情…</p>
      <p v-else-if="failure" role="alert">{{ failure }} <button type="button" class="secondary" @click="show">重试加载</button></p>
      <template v-else-if="detail">
        <h4>错误来源：{{ sourceLabel }}</h4>
        <p v-if="!hasBody">{{ noResponse }}</p>
        <h4 v-if="detail.summary">应用说明</h4><p v-if="detail.summary">{{ detail.summary }}</p>
        <h4 v-if="hasBody">{{ source === 'response_validation' ? '云端返回 JSON（完整内容，已格式化与脱敏）' : detail.stage === 'incomplete_response' ? '已收到的部分云端响应（已脱敏）' : '云端响应正文（原文，已脱敏）' }}</h4>
        <pre v-if="hasBody">{{ detail.response_body || '（响应正文为空）' }}</pre>
        <h4 v-if="detail.reason">{{ source === 'transport' ? 'HTTP 客户端原始异常（reason）' : '应用解析说明（reason）' }}</h4>
        <pre v-if="detail.reason">{{ detail.reason }}</pre>
        <h4 v-if="detail.request_body || detail.request_headers">实际请求信息（Authorization 已脱敏，input_audio 地址保留）</h4>
        <pre v-if="detail.request_method">{{ JSON.stringify({ method: detail.request_method, headers: detail.request_headers }, null, 2) }}</pre>
        <pre v-if="detail.request_body">{{ detail.request_body }}</pre>
        <h4>请求诊断信息（应用记录）</h4><pre>{{ metadata }}</pre>
      </template>
    </div>
  </span>
</template>
