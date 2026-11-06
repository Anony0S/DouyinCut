<script setup lang="ts">
import { computed, nextTick, onUnmounted, ref, watch } from "vue";
import type { LivePeriodSource, LiveSession, LiveProductPeriod, LiveSplitResult } from "../../services/liveMonitoring";
import ConfirmPopover from "../ConfirmPopover.vue";
import LiveIcon from "./LiveIcon.vue";
import { formatOffset, productPeriodColor } from "./presentation";
import { activeClipJobStates } from "./presentation";

const props = defineProps<{
  session: LiveSession;
  selectedPeriodId: string;
  busy: boolean;
  onSplit: (id: string, confirmedMissingSegmentIds: string[], range?: { start: number; end: number }) => Promise<LiveSplitResult>;
}>();
const emit = defineEmits<{ "update:selectedPeriodId": [id: string]; select: [id: string] }>();
const periods = computed(() => [...props.session.product_periods].sort((a, b) => a.started - b.started || a.id.localeCompare(b.id)));
const selected = computed(() => periods.value.find(item => item.id === props.selectedPeriodId));
const now = ref(Date.now() / 1000);
const timelineEnd = computed(() => Math.max(props.session.ended ?? now.value, ...periods.value.map(period => period.ended ?? now.value), props.session.started + 1));
const frozenTimelineEnd = ref<number | null>(null);
const displayEnd = computed(() => frozenTimelineEnd.value ?? timelineEnd.value);
const timelineDuration = computed(() => Math.max(1, displayEnd.value - props.session.started));
const selectedColor = computed(() => selected.value ? colorFor(selected.value) : "var(--color-primary)");
const editing = ref(false);
const rangeStart = ref(0);
const rangeEnd = ref(0);
const rangeOverlay = ref<HTMLElement | null>(null);
const frozenCuttableSeconds = ref<number | null>(null);
const activeDrag = ref<{
  edge: "start" | "end" | "range";
  pointerId: number;
  snapped?: boolean;
  startX?: number;
  initialStart?: number;
  initialEnd?: number;
} | null>(null);
const pending = ref(false);
const missing = ref<LivePeriodSource[]>([]);
const availableCount = ref(0);
const selectedDuration = computed(() => Math.max(0, rangeEnd.value - rangeStart.value));
const cuttableSeconds = computed(() => {
  if (!selected.value) return 0;
  const sealedEnd = Math.max(selected.value.started, ...selected.value.sources.filter(source => source.state === "ready").map(source => source.end));
  const lectureEnd = selected.value.ended ?? now.value;
  const selectionEnd = props.session.status === "recording" ? Math.min(sealedEnd, lectureEnd) : lectureEnd;
  return Math.max(0, selectionEnd - selected.value.started);
});
const selectionLimit = computed(() => frozenCuttableSeconds.value ?? cuttableSeconds.value);
const splitting = computed(() => Boolean(selected.value?.clip_job && activeClipJobStates.has(selected.value.clip_job.state)));
const splitMessage = computed(() => missing.value.map(source => "录制文件 " + String(source.segment_index).padStart(2, "0") + "（" + (source.state === "deleted" ? "已删除" : source.state === "missing" ? "文件不存在" : "文件异常") + "）").join("、") + "不可用。本次只能处理其余 " + availableCount.value + " 个已封存来源，生成结果不完整。是否继续？");
let disposed = false;
const clock = setInterval(() => { now.value = Date.now() / 1000; }, 1000);
onUnmounted(() => { disposed = true; clearInterval(clock); });
watch(() => props.session.id, () => { editing.value = false; frozenTimelineEnd.value = null; frozenCuttableSeconds.value = null; emit("update:selectedPeriodId", ""); });
watch(() => props.selectedPeriodId, () => { editing.value = false; frozenTimelineEnd.value = null; frozenCuttableSeconds.value = null; });

function colorFor(period: LiveProductPeriod) {
  return productPeriodColor(period.promotion_id);
}
function periodStyle(period: LiveProductPeriod) {
  const left = Math.max(0, (period.started - props.session.started) / timelineDuration.value * 100);
  const width = Math.max(0.3, ((period.ended ?? now.value) - period.started) / timelineDuration.value * 100);
  return { left: left + "%", width: Math.min(width, 100 - left) + "%", "--period-color": colorFor(period) };
}
function rangeStyle() {
  if (!selected.value || cuttableSeconds.value <= 0) return {};
  const left = Math.max(0, (selected.value.started - props.session.started) / timelineDuration.value * 100);
  return {
    left: left + "%",
    width: Math.min(Math.max(0.3, cuttableSeconds.value / timelineDuration.value * 100), 100 - left) + "%",
    "--period-color": selectedColor.value,
  };
}
function choose(period: LiveProductPeriod) {
  const id = props.selectedPeriodId === period.id ? "" : period.id;
  emit("update:selectedPeriodId", id);
  if (id) emit("select", id);
}
function beginSplit() {
  if (!selected.value || cuttableSeconds.value < 1 || splitting.value || props.busy) return;
  rangeStart.value = 0;
  rangeEnd.value = Math.min(cuttableSeconds.value, 20 * 60);
  frozenTimelineEnd.value = timelineEnd.value;
  frozenCuttableSeconds.value = cuttableSeconds.value;
  editing.value = true;
}
function setStart(value: number) { rangeStart.value = Math.max(0, Math.min(value, rangeEnd.value - 1)); }
function setEnd(value: number) { rangeEnd.value = Math.min(selectionLimit.value, Math.max(value, rangeStart.value + 1)); }
function handleStyle(edge: "start" | "end") {
  const value = edge === "start" ? rangeStart.value : rangeEnd.value;
  return { left: `${selectionLimit.value ? value / selectionLimit.value * 100 : 0}%` };
}
function updateFromPointer(event: PointerEvent, edge: "start" | "end") {
  if (activeDrag.value?.edge !== edge || activeDrag.value.pointerId !== event.pointerId) return;
  const rect = rangeOverlay.value?.getBoundingClientRect();
  if (!rect || rect.width <= 0) return;
  const ratio = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
  let value = Math.round(ratio * selectionLimit.value * 10) / 10;
  const snapSeconds = Math.max(1, Math.min(45, selectionLimit.value / rect.width * 16));
  const otherEdge = edge === "start" ? rangeEnd.value : rangeStart.value;
  const distance = edge === "start" ? otherEdge - value : value - otherEdge;
  const snappedDuration = Math.round(distance / 300) * 300;
  const snapValue = edge === "start" ? otherEdge - snappedDuration : otherEdge + snappedDuration;
  const snapped = snappedDuration >= 300
    && snapValue >= 0
    && snapValue <= selectionLimit.value
    && Math.abs(value - snapValue) <= snapSeconds;
  activeDrag.value.snapped = snapped;
  if (snapped) value = snapValue;
  if (edge === "start") setStart(value);
  else setEnd(value);
}
function updateRangeFromPointer(event: PointerEvent) {
  const drag = activeDrag.value;
  if (drag?.edge !== "range" || drag.pointerId !== event.pointerId) return;
  const rect = rangeOverlay.value?.getBoundingClientRect();
  if (!rect || rect.width <= 0) return;
  const delta = (event.clientX - (drag.startX ?? event.clientX)) / rect.width * selectionLimit.value;
  const duration = (drag.initialEnd ?? rangeEnd.value) - (drag.initialStart ?? rangeStart.value);
  const start = Math.max(0, Math.min(selectionLimit.value - duration, (drag.initialStart ?? rangeStart.value) + delta));
  rangeStart.value = Math.round(start * 10) / 10;
  rangeEnd.value = Math.round((start + duration) * 10) / 10;
}
function startDrag(event: PointerEvent, edge: "start" | "end") {
  activeDrag.value = {
    edge,
    pointerId: event.pointerId,
  };
  (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
  updateFromPointer(event, edge);
}
function startRangeDrag(event: PointerEvent) {
  activeDrag.value = {
    edge: "range",
    pointerId: event.pointerId,
    startX: event.clientX,
    initialStart: rangeStart.value,
    initialEnd: rangeEnd.value,
  };
  (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
}
function endDrag(event: PointerEvent, edge: "start" | "end" | "range") {
  if (activeDrag.value?.edge === edge && activeDrag.value.pointerId === event.pointerId) activeDrag.value = null;
}
function keyHandle(event: KeyboardEvent, edge: "start" | "end") {
  const current = edge === "start" ? rangeStart.value : rangeEnd.value;
  const change = event.shiftKey ? 10 : 1;
  let value = current;
  if (event.key === "ArrowLeft" || event.key === "ArrowDown") value -= change;
  else if (event.key === "ArrowRight" || event.key === "ArrowUp") value += change;
  else if (event.key === "Home") value = edge === "start" ? 0 : rangeStart.value + 1;
  else if (event.key === "End") value = edge === "start" ? rangeEnd.value - 1 : selectionLimit.value;
  else return;
  event.preventDefault();
  if (edge === "start") setStart(value);
  else setEnd(value);
}
function cancelSplit() { editing.value = false; frozenTimelineEnd.value = null; frozenCuttableSeconds.value = null; }
function range() {
  return { start: selected.value!.started + rangeStart.value, end: selected.value!.started + rangeEnd.value };
}
async function submit(confirmed: string[], open?: (anchor: { currentTarget: EventTarget | null }) => Promise<void>, event?: MouseEvent) {
  if (!selected.value || pending.value || splitting.value || props.busy) return;
  pending.value = true;
  try {
    const result = await props.onSplit(selected.value.id, confirmed, range());
    if (disposed) return;
    if (result.state === "confirmation_required" && "missing_segments" in result) {
      missing.value = result.missing_segments;
      availableCount.value = result.available_count;
      if (open && event) { await nextTick(); await open({ currentTarget: event.currentTarget }); }
      return;
    }
    editing.value = false;
    frozenTimelineEnd.value = null;
    frozenCuttableSeconds.value = null;
  } finally {
    pending.value = false;
  }
}
async function confirmPartial() {
  if (!selected.value) return;
  const result = await props.onSplit(selected.value.id, missing.value.map(source => source.segment_id), range());
  if (disposed) return;
  if (result.state === "confirmation_required") throw new Error("来源发生变化，请确认更新后的缺失媒体块");
  editing.value = false;
  frozenTimelineEnd.value = null;
  frozenCuttableSeconds.value = null;
}
</script>

<template>
  <section class="live-product-rail" aria-label="商品讲解轨">
    <div class="live-product-rail-heading">
      <div class="live-product-rail-label"><LiveIcon name="clock" /><strong>商品讲解</strong><span>{{ periods.length }} 段</span></div>
      <div v-if="selected" class="live-product-rail-actions">
        <span class="live-product-rail-selected-name">{{ selected.product_name || ("商品 " + selected.promotion_id) }}</span>
        <button v-if="!editing" type="button" class="btn btn-primary btn-sm" :disabled="busy || splitting || cuttableSeconds < 1" :title="cuttableSeconds < 1 ? '该讲解尚无可切分的封存内容' : '为选中的商品讲解选择切分范围'" @click="beginSplit">
          <LiveIcon name="layers" />{{ splitting ? "切分中…" : "切分商品" }}
        </button>
        <template v-else>
          <span class="live-product-rail-range-label">{{ formatOffset(selected.started + rangeStart, session.started) }}–{{ formatOffset(selected.started + rangeEnd, session.started) }} · {{ Math.floor(selectedDuration / 60) }}分{{ Math.floor(selectedDuration % 60) }}秒</span>
          <ConfirmPopover title="部分录制文件不可用" :message="splitMessage" confirm-label="只切可用部分" confirm-color="neutral" :confirm-action="confirmPartial" :disabled="busy || splitting" v-slot="{ open, expanded, popoverId }">
            <button type="button" class="btn btn-primary btn-sm" :disabled="busy || pending || splitting" :aria-expanded="expanded" :aria-controls="popoverId" aria-haspopup="dialog" @click="submit([], open, $event)">
              <LiveIcon :name="pending ? 'refresh' : 'check'" :class="{ 'is-spinning': pending }" />{{ pending ? "检查来源…" : "确认切分" }}
            </button>
          </ConfirmPopover>
          <button type="button" class="btn btn-ghost btn-sm" :disabled="pending" @click="cancelSplit">取消</button>
        </template>
      </div>
      <span v-else class="live-product-rail-hint">选择区间后可切分商品</span>
    </div>
    <div class="live-product-rail-track" role="group" aria-label="选择商品讲解区间">
      <button v-for="period in periods" :key="period.id" type="button" class="live-product-rail-period" :class="{ 'is-selected': period.id === selectedPeriodId }" :style="periodStyle(period)" :title="(period.product_name || ('商品 ' + period.promotion_id)) + ' · ' + formatOffset(period.started, session.started) + '–' + (period.ended === null ? '至今' : formatOffset(period.ended, session.started))" :aria-label="'选择商品讲解 ' + (period.product_name || period.promotion_id) + '，' + formatOffset(period.started, session.started) + ' 至 ' + (period.ended === null ? '至今' : formatOffset(period.ended, session.started))" :aria-pressed="period.id === selectedPeriodId" @click="choose(period)" />
      <div v-if="editing && selected && cuttableSeconds > 0" ref="rangeOverlay" class="live-product-range-overlay" :style="rangeStyle()">
        <div class="live-product-range-fill" :style="{ left: (rangeStart / selectionLimit * 100) + '%', width: ((rangeEnd - rangeStart) / selectionLimit * 100) + '%' }" @pointerdown.prevent="startRangeDrag" @pointermove="updateRangeFromPointer" @pointerup="endDrag($event, 'range')" @pointercancel="endDrag($event, 'range')" @lostpointercapture="endDrag($event, 'range')" />
        <button type="button" class="live-product-range-handle" :class="{ 'is-snapped': activeDrag?.edge === 'start' && activeDrag.snapped }" :style="handleStyle('start')" role="slider" tabindex="0" aria-label="切分起点" :aria-valuemin="0" :aria-valuemax="Math.max(0, rangeEnd - 1)" :aria-valuenow="rangeStart" :aria-valuetext="formatOffset(selected.started + rangeStart, session.started)" @pointerdown.prevent="startDrag($event, 'start')" @pointermove="updateFromPointer($event, 'start')" @pointerup="endDrag($event, 'start')" @pointercancel="endDrag($event, 'start')" @lostpointercapture="endDrag($event, 'start')" @keydown="keyHandle($event, 'start')" />
        <button type="button" class="live-product-range-handle" :class="{ 'is-snapped': activeDrag?.edge === 'end' && activeDrag.snapped }" :style="handleStyle('end')" role="slider" tabindex="0" aria-label="切分终点" :aria-valuemin="rangeStart + 1" :aria-valuemax="selectionLimit" :aria-valuenow="rangeEnd" :aria-valuetext="formatOffset(selected.started + rangeEnd, session.started)" @pointerdown.prevent="startDrag($event, 'end')" @pointermove="updateFromPointer($event, 'end')" @pointerup="endDrag($event, 'end')" @pointercancel="endDrag($event, 'end')" @lostpointercapture="endDrag($event, 'end')" @keydown="keyHandle($event, 'end')" />
      </div>
    </div>
    <div class="live-product-rail-scale"><span>00:00</span><span>{{ formatOffset(displayEnd, session.started) }}</span></div>
    <p v-if="editing && selected" class="live-product-rail-note">拖动两端选择范围；范围限制在已封存内容内，最短 1 秒。</p>
  </section>
</template>
