<script setup lang="ts">
import { computed, nextTick, onUnmounted, ref } from "vue";
import type { LivePeriodSource, LiveProductPeriod, LiveSplitResult } from "../../services/liveMonitoring";
import ConfirmPopover from "../ConfirmPopover.vue";
import LiveIcon from "./LiveIcon.vue";
import { activeClipJobStates } from "./presentation";

const props = defineProps<{
  period: LiveProductPeriod;
  busy: boolean;
  onSplit: (id: string, confirmedMissingSegmentIds: string[]) => Promise<LiveSplitResult>;
}>();
const pending = ref(false);
let disposed = false;
onUnmounted(() => { disposed = true; });
const missing = ref<LivePeriodSource[]>([]);
const availableCount = ref(0);
const splitting = computed(() => Boolean(props.period.clip_job && activeClipJobStates.has(props.period.clip_job.state)));
const message = computed(() => `${missing.value.map(source => `分段 ${String(source.segment_index).padStart(2, "0")}（${source.state === "deleted" ? "已删除" : source.state === "missing" ? "文件不存在" : "文件异常"}）`).join("、")}不可用。本次只能处理其余 ${availableCount.value} 个已封存来源，生成结果不完整。是否继续？`);

async function submit(confirmed: string[]) {
  const result = await props.onSplit(props.period.id, confirmed);
  if (disposed) return true;
  if (result.state === "confirmation_required" && "missing_segments" in result) {
    missing.value = result.missing_segments;
    availableCount.value = result.available_count;
    return false;
  }
  return true;
}
async function request(event: MouseEvent, open: (anchor: { currentTarget: EventTarget | null }) => Promise<void>) {
  if (pending.value || splitting.value || props.busy) return;
  const anchor = { currentTarget: event.currentTarget };
  pending.value = true;
  try {
    if (!await submit([])) {
      await nextTick();
      await open(anchor);
    }
  } catch {
    // The view reports request errors through the shared notification flow.
  } finally {
    pending.value = false;
  }
}
async function confirmPartial() {
  if (!await submit(missing.value.map(source => source.segment_id))) {
    // Keep the confirmation open when another source disappeared since the first request.
    throw new Error("来源发生变化，请确认更新后的缺失分段");
  }
}
</script>

<template>
  <ConfirmPopover
    title="部分录制分段不可用"
    :message="message"
    confirm-label="只切可用部分"
    confirm-color="neutral"
    :confirm-action="confirmPartial"
    :disabled="busy || splitting"
    v-slot="{ open, expanded, popoverId }">
    <button
      type="button"
      class="btn btn-soft btn-sm"
      :disabled="busy || pending || splitting"
      :aria-label="`切分商品 ${period.product_name || period.promotion_id}`"
      :aria-expanded="expanded"
      :aria-controls="popoverId"
      aria-haspopup="dialog"
      @click="request($event, open)">
      <LiveIcon :name="splitting || pending ? 'refresh' : 'layers'" :class="{ 'is-spinning': splitting || pending }" />
      {{ splitting ? "切分中…" : pending ? "检查来源…" : "切分商品" }}
    </button>
  </ConfirmPopover>
</template>
