<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, useId } from 'vue'

const storageKey = 'douyincut.workspace-left-width'
const minimumLeft = 230, minimumRight = 360, separatorWidth = 16
const root = ref<HTMLDivElement | null>(null), left = ref<HTMLDivElement | null>(null)
const width = ref<number | null>(null), available = ref(0), stacked = ref(false), dragging = ref(false)
const id = useId()
const maximumLeft = computed(() => Math.max(minimumLeft, available.value - minimumRight - separatorWidth))
const boundedWidth = computed(() => Math.min(maximumLeft.value, Math.max(minimumLeft, width.value ?? defaultWidth())))
const paneStyle = computed(() => width.value === null ? {} : { '--workspace-left-width': `${boundedWidth.value}px` })
let observer: ResizeObserver | undefined
let pointer: number | null = null, startX = 0, startWidth = 0
let handle: HTMLElement | null = null

function defaultWidth() {
  return root.value ? Number.parseFloat(getComputedStyle(root.value).getPropertyValue('--workspace-default-left')) || 280 : 280
}
function measure() {
  available.value = root.value?.clientWidth ?? 0
  stacked.value = window.matchMedia('(max-width: 850px)').matches
  if (stacked.value) finishDrag()
}
function remember() {
  try {
    if (width.value === null) localStorage.removeItem(storageKey)
    else localStorage.setItem(storageKey, String(boundedWidth.value))
  } catch { /* Layout remains adjustable when browser storage is unavailable. */ }
}
function startDrag(event: PointerEvent) {
  if (event.button !== 0 || stacked.value || pointer !== null) return
  measure()
  pointer = event.pointerId; startX = event.clientX
  startWidth = left.value?.getBoundingClientRect().width ?? boundedWidth.value
  handle = event.currentTarget as HTMLElement
  handle.setPointerCapture(pointer)
  dragging.value = true
  event.preventDefault()
}
function moveDrag(event: PointerEvent) {
  if (event.pointerId !== pointer) return
  width.value = Math.min(maximumLeft.value, Math.max(minimumLeft, startWidth + event.clientX - startX))
}
function finishDrag() {
  const previous = pointer
  pointer = null; dragging.value = false
  if (previous !== null && handle?.hasPointerCapture(previous)) handle.releasePointerCapture(previous)
  handle = null
  if (previous !== null) remember()
}
function endDrag(event: PointerEvent) {
  if (event.pointerId === pointer) finishDrag()
}
function resetWidth() { finishDrag(); width.value = null; remember() }
function resizeWithKey(event: KeyboardEvent) {
  if (stacked.value) return
  const step = event.shiftKey ? 50 : 20
  const actions: Record<string, number> = {
    ArrowLeft: boundedWidth.value - step, ArrowRight: boundedWidth.value + step,
    Home: minimumLeft, End: maximumLeft.value,
  }
  if (event.key === 'Enter') { event.preventDefault(); resetWidth(); return }
  if (!(event.key in actions)) return
  event.preventDefault()
  width.value = Math.min(maximumLeft.value, Math.max(minimumLeft, actions[event.key]))
  remember()
}
onMounted(() => {
  try {
    const stored = localStorage.getItem(storageKey)
    const value = stored === null ? NaN : Number(stored)
    if (Number.isFinite(value) && value >= minimumLeft) width.value = value
  } catch { /* Use the default width. */ }
  measure()
  observer = new ResizeObserver(measure)
  if (root.value) observer.observe(root.value)
  window.addEventListener('resize', measure)
})
onUnmounted(() => { finishDrag(); observer?.disconnect(); window.removeEventListener('resize', measure) })
</script>

<template>
  <div ref="root" class="roughcut-grid" :class="{'is-resizing':dragging}" :style="paneStyle">
    <div :id="`${id}-left`" ref="left" class="workspace-pane"><slot name="left"/></div>
    <div class="workspace-splitter" role="separator" aria-orientation="vertical"
      aria-label="调整素材与预览卡片宽度" :aria-controls="`${id}-left ${id}-right`"
      :aria-valuemin="minimumLeft" :aria-valuemax="Math.round(maximumLeft)" :aria-valuenow="Math.round(boundedWidth)"
      :aria-valuetext="`左侧宽度 ${Math.round(boundedWidth)} 像素`" :tabindex="stacked ? -1 : 0"
      title="拖动调整左右宽度；双击恢复默认；方向键微调"
      @pointerdown="startDrag" @pointermove="moveDrag" @pointerup="endDrag" @pointercancel="endDrag"
      @lostpointercapture="finishDrag" @dblclick="resetWidth" @keydown="resizeWithKey"><span/></div>
    <div :id="`${id}-right`" class="workspace-pane"><slot name="right"/></div>
  </div>
</template>
