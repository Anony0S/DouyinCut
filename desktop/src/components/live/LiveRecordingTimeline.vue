<script setup lang="ts">
import { computed, ref } from 'vue'
import type { LiveSession } from '../../services/liveMonitoring'
import { formatLiveTime } from './presentation'

const props = defineProps<{
  sessions: LiveSession[]
  selectedId: string
  start: number
  end: number
  now: number
  date: string
  maxDate: string
  loading: boolean
  label: string
  emptyMessage: string
}>()
const emit = defineEmits<{ select: [id: string]; 'date-change': [date: string] }>()
function changeDate(event: Event) {
  const input = event.target as HTMLInputElement
  const date = input.value
  input.value = props.date
  emit('date-change', date)
}
const previewId = ref('')
const chronological = computed(() => [...props.sessions].sort((a, b) => a.started - b.started || a.id.localeCompare(b.id)))
const preview = computed(() => props.sessions.find(item => item.id === previewId.value)
  ?? props.sessions.find(item => item.id === props.selectedId))
const ticks = computed(() => Array.from({ length: 5 }, (_, index) => ({
  position: index * 25,
  label: new Date((props.start + (props.end - props.start) * index / 4) * 1000).toLocaleString('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
  }),
})))
function position(item: LiveSession) {
  const start = Math.max(props.start, item.started)
  const end = Math.min(props.end, item.status === 'recording' ? props.now : item.ended ?? item.started)
  return { left: `${(start - props.start) / (props.end - props.start) * 100}%`, width: `${Math.max(0, end - start) / (props.end - props.start) * 100}%` }
}
function duration(item: LiveSession) {
  const seconds = Math.max(0, Math.floor((item.status === 'recording' ? props.now : item.ended ?? item.started) - item.started))
  const hours = Math.floor(seconds / 3600), minutes = Math.floor(seconds / 60) % 60
  return [hours ? `${hours} 小时` : '', minutes ? `${minutes} 分钟` : '', seconds % 60 ? `${seconds % 60} 秒` : ''].filter(Boolean).join(' ') || '不足 1 秒'
}
function state(item: LiveSession) {
  return item.status === 'recording' ? '录制中' : item.status === 'failed' ? '异常结束' : '已结束'
}
function summary(item: LiveSession) {
  return `${formatLiveTime(item.started)} — ${item.status === 'recording' ? '至今' : formatLiveTime(item.ended)} · ${duration(item)} · ${state(item)}`
}
</script>

<template>
  <section class="card card-border bg-base-100 live-recording-timeline" aria-label="录制时间线" @mouseleave="previewId = ''">
    <div class="live-recording-timeline-heading">
      <h3>录制场次</h3>
      <span class="text-base-content/60">{{ label }} · {{ sessions.length }} 场</span>
      <div class="live-recording-timeline-controls">
        <label class="live-recording-date">
          <span class="live-sr-only">录制日期</span>
          <input class="input input-sm" type="date" aria-label="录制日期" :max="maxDate"
            :value="date" :disabled="loading"
            @change="changeDate" />
        </label>
        <button v-if="date" type="button" class="btn btn-ghost btn-sm" :disabled="loading"
          aria-label="回到最近" @click="$emit('date-change', '')">回到最近</button>
        <span v-if="loading" role="status" class="text-base-content/60">正在读取…</span>
      </div>
    </div>
    <div class="live-recording-timeline-scroll">
      <div class="live-recording-timeline-chart">
        <div class="live-recording-timeline-ticks" aria-hidden="true">
          <span v-for="tick in ticks" :key="tick.position" :style="{ left: `${tick.position}%` }">{{ tick.label }}</span>
        </div>
        <div class="live-recording-timeline-track" role="group" aria-label="选择录制场次">
          <button v-for="item in chronological" :key="item.id" type="button"
            class="btn btn-sm live-recording-timeline-period"
            :class="{ 'is-selected': item.id === selectedId, 'is-recording': item.status === 'recording' }"
            :style="position(item)" :aria-label="`选择录制场次：${summary(item)}`"
            :aria-pressed="item.id === selectedId"
            @mouseenter="previewId = item.id" @focus="previewId = item.id" @blur="previewId = ''"
            @click="$emit('select', item.id)">
            <span v-if="item.started < start" class="live-recording-timeline-continuation" aria-label="开始于窗口之前">‹</span>
            <span class="live-sr-only">{{ state(item) }}</span>
            <span v-if="(item.status === 'recording' ? now : item.ended ?? item.started) > end" class="live-recording-timeline-continuation" aria-label="结束于窗口之后">›</span>
          </button>
        </div>
      </div>
    </div>
    <button v-if="preview" type="button" class="btn btn-ghost btn-sm live-recording-timeline-summary"
      :aria-label="`查看录制场次：${summary(preview)}`" @click="$emit('select', preview.id)">
      <span>{{ formatLiveTime(preview.started) }} — {{ preview.status === 'recording' ? '至今' : formatLiveTime(preview.ended) }}</span>
      <span class="text-base-content/60">{{ duration(preview) }} · {{ state(preview) }}</span>
    </button>
    <p v-else class="live-recording-timeline-empty text-base-content/60">{{ emptyMessage }}</p>
  </section>
</template>
