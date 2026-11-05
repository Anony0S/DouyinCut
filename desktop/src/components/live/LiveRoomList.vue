<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import type { LiveMonitor, LiveMonitorSettings } from "../../services/liveMonitoring";
import LiveIcon from "./LiveIcon.vue";
import ConfirmPopover from "../ConfirmPopover.vue";
import {
  formatLiveTime,
  isRecording,
  liveness,
  monitorNeedsAttention,
} from "./presentation";

const props = defineProps<{
  monitors: LiveMonitor[];
  selectedId: string;
  loading: boolean;
  busy: boolean;
  onRemove: (monitor: LiveMonitor) => Promise<void>;
}>();
const emit = defineEmits<{
  select: [id: string];
  settings: [monitor: LiveMonitor, changes: LiveMonitorSettings];
  record: [monitor: LiveMonitor, action: "start" | "stop"];
  "open-url": [url: string];
}>();
const search = ref(""),
  filter = ref("all");
const filters = [
  { id: "all", name: "全部" },
  { id: "live", name: "开播中" },
  { id: "recording", name: "录制中" },
  { id: "attention", name: "异常" },
];
const filtered = computed(() =>
  props.monitors
    .filter((item) => {
      const query = search.value.trim().toLocaleLowerCase();
      const matches = {
        live: item.is_live === true,
        recording: isRecording(item),
        attention: monitorNeedsAttention(item),
        all: true,
      }[filter.value];
      return (
        (!query ||
          `${item.name} ${item.url}`.toLocaleLowerCase().includes(query)) &&
        matches
      );
    })
    .sort(
      (a, b) =>
        Number(b.is_live === true) - Number(a.is_live === true) ||
        a.name.localeCompare(b.name, "zh-CN"),
    ),
);
const selected = computed(() =>
  props.monitors.find((item) => item.id === props.selectedId),
);
const now = ref(Date.now());
let clockTimer: ReturnType<typeof setInterval> | undefined;
onMounted(() => {
  now.value = Date.now();
  clockTimer = setInterval(() => { now.value = Date.now(); }, 5000);
});
onUnmounted(() => clearInterval(clockTimer));
const selectedCheckTime = computed(() => {
  const checked = selected.value?.last_checked;
  if (!checked) return "尚未检测";
  return `${formatLiveTime(checked)}`;
});
const selectedRecording = computed(() =>
  Boolean(selected.value && isRecording(selected.value)),
);
function removeSelected() {
  return selected.value ? props.onRemove(selected.value) : undefined;
}
/** Auto-record rooms need no start button: recording begins as soon as they go live.
 *  After a manual stop the start button returns so the same broadcast can be resumed. */
const selectedRecordAction = computed<"start" | "stop" | null>(() => {
  if (!selected.value) return null;
  if (selectedRecording.value) return "stop";
  return selected.value.auto_record && !selected.value.record_held && !selected.value.retry_blocked
    ? null
    : "start";
});
const selectedStartDisabled = computed(
  () => !selected.value?.is_live,
);

function selectMonitor(monitor: LiveMonitor, event: Event) {
  // Only the card itself activates on Enter / Space; inner controls handle their own keys.
  if (event.type === "keydown" && event.target !== event.currentTarget) return;
  if (event.type === "keydown") event.preventDefault();
  if (props.busy) return;
  emit("select", monitor.id);
}

function openSelectedRoom(event: MouseEvent) {
  event.preventDefault();
  if (selected.value) emit("open-url", selected.value.url);
}

// The list scrolls, so the settings popover is teleported to <body> and anchored to its
// trigger; otherwise the scroll container would clip it.
const settingsFor = ref<string | null>(null);
const settingsPanel = ref<HTMLElement>();
const anchor = ref({ top: 0, left: 0 });
const settingsMonitor = computed(
  () => props.monitors.find((item) => item.id === settingsFor.value) ?? null,
);
function toggleSettings(monitor: LiveMonitor, event: MouseEvent) {
  if (settingsFor.value === monitor.id) {
    closeSettings();
    return;
  }
  const rect = (event.currentTarget as HTMLElement).getBoundingClientRect();
  anchor.value = {
    top: Math.min(rect.bottom + 6, window.innerHeight - 190),
    left: Math.max(12, Math.min(rect.right - 232, window.innerWidth - 244)),
  };
  settingsFor.value = monitor.id;
  window.addEventListener("pointerdown", onOutside, true);
  window.addEventListener("keydown", onEscape);
  window.addEventListener("scroll", closeSettings, true);
}
function closeSettings() {
  settingsFor.value = null;
  window.removeEventListener("pointerdown", onOutside, true);
  window.removeEventListener("keydown", onEscape);
  window.removeEventListener("scroll", closeSettings, true);
}
function onOutside(event: PointerEvent) {
  const target = event.target as Node;
  if (settingsPanel.value?.contains(target)) return;
  if ((target as HTMLElement).closest?.(".live-room-settings-trigger")) return;
  closeSettings();
}
function onEscape(event: KeyboardEvent) {
  if (event.key === "Escape") closeSettings();
}
onUnmounted(closeSettings);
</script>

<template>
  <section
    class="card card-border bg-base-100 w-full live-room-list"
    aria-label="直播间列表">
    <div class="live-room-list-heading">
      <h2>直播间</h2>
      <span class="badge badge-ghost badge-sm">{{ monitors.length }}</span>
    </div>
    <div class="live-search">
      <LiveIcon name="search" />
      <input
        v-model="search"
        class="input input-sm w-full"
        type="search"
        aria-label="搜索直播间"
        placeholder="搜索名称或直播间链接" />
    </div>
    <div class="live-room-filters join" aria-label="筛选直播间">
      <button
        v-for="item in filters"
        :key="item.id"
        class="btn btn-xs join-item"
        :class="{ 'btn-active': filter === item.id }"
        :aria-pressed="filter === item.id"
        @click="filter = item.id">
        {{ item.name }}
      </button>
    </div>
    <div class="live-room-items">
      <div
        v-for="monitor in filtered"
        :key="monitor.id"
        class="live-room-item"
        :class="{ selected: selectedId === monitor.id }"
        role="button"
        tabindex="0"
        :aria-pressed="selectedId === monitor.id"
        @click="selectMonitor(monitor, $event)"
        @keydown.enter="selectMonitor(monitor, $event)"
        @keydown.space="selectMonitor(monitor, $event)">
        <div class="live-room-item-top">
          <span class="live-room-initial">
            {{ (monitor.name || "抖音")[0] }}
          </span>
          <span class="live-room-item-title">
            {{ monitor.name || "抖音直播间" }}
          </span>
          <template v-if="monitorNeedsAttention(monitor)">
            <LiveIcon name="alert" class="live-room-warning" />
            <span class="live-sr-only">需要关注</span>
          </template>
          <button
            class="live-room-settings-trigger"
            type="button"
            title="直播间设置"
            :aria-label="`${monitor.name || '抖音直播间'}设置`"
            :aria-expanded="settingsFor === monitor.id"
            @click.stop="toggleSettings(monitor, $event)"
            @keydown.stop>
            <LiveIcon name="settings" />
          </button>
        </div>
        <div class="live-room-item-bottom">
          <span
            :class="['badge', 'badge-sm', liveness(monitor).classes]"
            :title="
              monitor.live_checked
                ? `检测于 ${formatLiveTime(monitor.live_checked, true)}`
                : undefined
            ">
            <span
              :class="['status', 'status-md', liveness(monitor).status]"></span>
            {{ liveness(monitor).label }}
          </span>
          <span
            v-if="isRecording(monitor)"
            class="badge badge-sm badge-soft badge-error">
            <span class="status status-md status-error"></span>
            录制中
          </span>
          <span class="live-room-item-sessions">
            最近 {{ monitor.sessions.length }} 场
          </span>
        </div>
      </div>
      <div v-if="!filtered.length" class="live-room-list-empty">
        <LiveIcon name="search" />
        <p>
          {{
            loading
              ? "正在读取直播间…"
              : monitors.length
                ? "没有匹配的直播间"
                : "还没有添加直播间"
          }}
        </p>
        <button
          v-if="search || filter !== 'all'"
          class="btn btn-ghost btn-xs"
          @click="
            search = '';
            filter = 'all';
          ">
          清除筛选
        </button>
      </div>
    </div>

    <section
      v-if="selected"
      class="live-room-selected"
      aria-label="所选直播间">
      <span class="live-section-kicker">当前选择</span>
      <strong class="live-room-selected-name" :title="selected.name">
        {{ selected.name || "抖音直播间" }}
      </strong>
      <a
        class="live-room-selected-url"
        :href="selected.url"
        :title="selected.url"
        @click="openSelectedRoom">
        <span>{{ selected.url }}</span>
        <LiveIcon name="external" />
      </a>
      <div class="live-room-selected-meta">
        <span>
          <LiveIcon name="clock" />
          最近检测 {{ selectedCheckTime }}
        </span>
        <span>
          <LiveIcon name="video" />
          录制内容保存在本机
        </span>
        <span>
          <LiveIcon name="settings" />
          {{
            selected.auto_record ? "开播自动录制已开启" : "开播不自动录制"
          }} · {{ selected.notify_live ? "开播提醒已开启" : "开播不提醒" }}
        </span>
      </div>
      <div
        v-if="selected.error"
        class="alert alert-error alert-soft live-room-error"
        role="alert">
        <LiveIcon name="alert" />
        <div>
          <strong>直播间检测异常</strong>
          <p>{{ selected.error }}</p>
        </div>
      </div>
      <div class="live-room-selected-actions">
        <button
          v-if="selectedRecordAction"
          class="btn btn-sm"
          :class="
            selectedRecordAction === 'stop'
              ? 'btn-soft btn-error'
              : 'btn-soft btn-primary'
          "
          :disabled="
            busy || (selectedRecordAction === 'start' && selectedStartDisabled)
          "
          :title="
            selectedRecordAction === 'start' && selectedStartDisabled
              ? '直播间当前未开播，开播后才能开始录制'
              : undefined
          "
          @click="$emit('record', selected, selectedRecordAction)">
          <LiveIcon :name="selectedRecordAction === 'stop' ? 'stop' : 'record'" />
          {{ selectedRecordAction === "stop" ? "停止录制" : "开始录制" }}
        </button>
        <span v-else class="live-room-auto-hint">
          <LiveIcon name="radio" />
          开播后自动录制
        </span>
        <ConfirmPopover
          :key="selected.id"
          :title="`移除“${selected.name || '抖音直播间'}”？`"
          message="将移除监控元数据、场次、事件和商品记录；已录制的视频文件与已导入项目会保留。"
          confirm-label="确定移除"
          :confirm-action="removeSelected"
          :disabled="busy || selectedRecording"
          v-slot="{ open, expanded, popoverId }">
          <button
            class="btn btn-soft btn-sm btn-error"
            :disabled="busy || selectedRecording"
            :title="selectedRecording ? '录制中，请先停止录制' : undefined"
            :aria-expanded="expanded"
            :aria-controls="popoverId"
            aria-haspopup="dialog"
            @click="open">
            <LiveIcon name="trash" />
            移除直播间
          </button>
        </ConfirmPopover>
      </div>
    </section>
    <div v-else class="live-room-list-foot">
      <span class="live-sync-dot"></span>
      选择直播间查看录制内容
    </div>

    <Teleport to="body">
      <div
        v-if="settingsMonitor"
        ref="settingsPanel"
        class="live-room-settings-panel"
        role="dialog"
        :aria-label="`${settingsMonitor.name || '抖音直播间'}设置`"
        :style="{ top: `${anchor.top}px`, left: `${anchor.left}px` }">
        <div class="live-room-settings-title">直播间设置</div>
        <label class="live-room-setting">
          <input
            class="toggle toggle-sm"
            type="checkbox"
            :checked="settingsMonitor.auto_record"
            @change="
              $emit('settings', settingsMonitor, {
                auto_record: ($event.target as HTMLInputElement).checked,
              })
            " />
          <span>
            开播自动录制
            <small>开播后自动开始，关闭后需手动开始</small>
          </span>
        </label>
        <label class="live-room-setting">
          <input
            class="toggle toggle-sm"
            type="checkbox"
            :checked="settingsMonitor.notify_live"
            @change="
              $emit('settings', settingsMonitor, {
                notify_live: ($event.target as HTMLInputElement).checked,
              })
            " />
          <span>
            开播提醒
            <small>开播时弹出 Windows 通知</small>
          </span>
        </label>
      </div>
    </Teleport>
  </section>
</template>
