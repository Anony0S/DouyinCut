<script setup lang="ts">
import { computed, ref } from 'vue'
import type { LiveSession } from '../../services/liveMonitoring'
import ConfirmPopover from '../ConfirmPopover.vue'
import LiveIcon from './LiveIcon.vue'
import { formatBytes, formatLiveTime } from './presentation'
import RecordingDatePicker from './RecordingDatePicker.vue'

const props = defineProps<{
  sessions: LiveSession[]
  selectedId: string
  selectedSession: LiveSession | undefined
  start: number
  end: number
  now: number
  date: string
  maxDate: string
  loading: boolean
  busy: boolean
  label: string
  emptyMessage: string
  onDeleteSession: (id: string) => Promise<void>
}>()
const emit = defineEmits<{ select: [id: string]; 'date-change': [date: string] }>()
const hoveredId = ref('')
const focusedId = ref('')
const chronological = computed(() => [...props.sessions].sort((a, b) => a.started - b.started || a.id.localeCompare(b.id)))
const preview = computed(() => props.sessions.find(item => item.id === hoveredId.value)
  ?? props.sessions.find(item => item.id === focusedId.value)
  ?? props.selectedSession)
function previewFocus(id: string) {
  focusedId.value = id
  hoveredId.value = ''
}
const ticks = computed(() => Array.from({ length: 5 }, (_, index) => {
  const instant = new Date(props.start * 1000)
  instant.setHours(index * 6, 0, 0, 0)
  const timestamp = index === 4 ? props.end : instant.getTime() / 1000
  return {
    position: (timestamp - props.start) / (props.end - props.start) * 100,
    label: `${String(index * 6).padStart(2, '0')}:00`,
  }
}))
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
  if (item.status === 'recording') return '录制中'
  if (item.status !== 'failed') return '已结束'
  return item.end_time_source === 'recovery_check' ? '异常结束，结束时间可能晚于实际停录时间' : '异常结束'
}
function summary(item: LiveSession) {
  return `${formatLiveTime(item.started)} — ${item.status === 'recording' ? '至今' : formatLiveTime(item.ended)} · ${duration(item)} · ${state(item)}`
}
function deleteCounts(item: LiveSession) {
  const segments = item.segments.filter(segment => segment.status !== 'imported')
  const clips = item.product_clips.filter(clip => clip.status !== 'imported')
  return {
    items: segments.length + clips.length,
    bytes: [...segments, ...clips].reduce((total, file) => total + file.size_bytes, 0),
  }
}
function deleteMessage(item: LiveSession) {
  const counts = deleteCounts(item)
  const labels = [segmentsLabel(item), clipsLabel(item)].filter((_, index) => {
    const source = index === 0 ? item.segments : item.product_clips
    return source.some(file => file.status !== 'imported')
  })
  const files = counts.items
    ? `将删除 ${labels.join('、')}（共 ${counts.items} 项，约 ${formatBytes(counts.bytes)}）`
    : '将删除本场的讲解记录；没有待清理的未导入视频文件'
  return `${files}。已导入的视频项目及其素材会保留。此操作无法撤销。`
}
function deleteSession(item: LiveSession | undefined) {
  return item ? props.onDeleteSession(item.id) : Promise.resolve()
}
function segmentsLabel(item: LiveSession) {
  return `${item.segments.filter(segment => segment.status !== 'imported').length} 个未导入录制分段`
}
function clipsLabel(item: LiveSession) {
  return `${item.product_clips.filter(clip => clip.status !== 'imported').length} 个未导入商品片段`
}
</script>

<template>
  <section class="card card-border bg-base-100 live-recording-timeline" aria-label="录制时间线" @mouseleave="hoveredId = ''">
    <div class="live-recording-timeline-heading">
      <h3>录制场次</h3>
      <span class="text-base-content/60">{{ label }} · {{ sessions.length }} 场</span>
      <div class="live-recording-timeline-controls">
        <RecordingDatePicker :value="date" :max="maxDate" :disabled="loading"
          @change="emit('date-change', $event)" />
        <button type="button" class="btn btn-ghost btn-sm" :disabled="loading || !date"
          aria-label="回到今天" @click="$emit('date-change', '')">回到今天</button>
        <span v-if="loading" role="status" class="live-sr-only">正在读取…</span>
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
            @mouseenter="hoveredId = item.id" @focus="previewFocus(item.id)" @blur="focusedId = ''"
            @click="$emit('select', item.id)">
            <span v-if="item.started < start" class="live-recording-timeline-continuation" aria-label="开始于窗口之前">‹</span>
            <span class="live-sr-only">{{ state(item) }}</span>
            <span v-if="(item.status === 'recording' ? now : item.ended ?? item.started) > end" class="live-recording-timeline-continuation" aria-label="结束于窗口之后">›</span>
          </button>
        </div>
      </div>
    </div>
    <div v-if="preview" class="live-recording-timeline-summary">
      <div class="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
        <span>{{ formatLiveTime(preview.started) }} — {{ preview.status === 'recording' ? '至今' : formatLiveTime(preview.ended) }}</span>
        <span class="live-recording-timeline-duration">{{ duration(preview) }}</span>
        <span class="text-base-content/60">· {{ state(preview) }}</span>
      </div>
      <ConfirmPopover
        v-if="preview.id === selectedId"
        :key="preview.id"
        :title="`删除录制场次「${formatLiveTime(preview.started)}」？`"
        :message="deleteMessage(preview)"
        confirm-label="确定删除场次"
        :confirm-action="() => deleteSession(preview)"
        :disabled="busy || loading || preview.status === 'recording'"
        v-slot="{ open, expanded, popoverId }">
        <button type="button" class="btn btn-ghost btn-sm text-error"
          :disabled="busy || loading || preview.status === 'recording'"
          :title="preview.status === 'recording' ? '录制中的场次不能删除' : '删除当前录制场次'"
          :aria-label="`删除录制场次：${summary(preview)}`"
          :aria-expanded="expanded" :aria-controls="popoverId" aria-haspopup="dialog"
          @click="open">
          <LiveIcon name="trash" />
          删除场次
        </button>
      </ConfirmPopover>
    </div>
    <p v-else class="live-recording-timeline-empty text-base-content/60">{{ emptyMessage }}</p>
  </section>
</template>
