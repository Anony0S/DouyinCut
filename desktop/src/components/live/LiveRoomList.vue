<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, useId, watch } from "vue";
import { liveMonitoringApi, type LiveMonitor, type LiveMonitorSettings } from "../../services/liveMonitoring";
import { needsPlatformIp, type RoomPlatformBinding } from "../../services/productPlatform";
import LiveIcon from "./LiveIcon.vue";
import ConfirmPopover from "../ConfirmPopover.vue";
import RoomPlatformFields from "../product/RoomPlatformFields.vue";
import { readRoomOrder, roomOrderKey, sortRooms } from "./roomOrder";
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
  "platform-saved": [];
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
const roomOrder = ref<string[]>(readRoomOrder());
function saveOrder() {
  try { localStorage.setItem(roomOrderKey, JSON.stringify(roomOrder.value)); } catch { }
}
const ordered = computed(() => sortRooms(props.monitors, roomOrder.value));
watch(() => props.monitors, (monitors) => {
  if (!monitors.length) return;
  const ids = new Set(monitors.map((item) => item.id));
  const existing = roomOrder.value.filter((id) => ids.has(id));
  const known = new Set(existing);
  const added = ordered.value.filter((item) => !known.has(item.id)).map((item) => item.id);
  roomOrder.value = [...existing, ...added];
  saveOrder();
}, { immediate: true });
const filtered = computed(() =>
  ordered.value
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
    }),
);
const roomItems = ref<HTMLElement>();
const draggingId = ref<string | null>(null);
const dropSlot = ref<number | null>(null);
let activePointer: number | null = null;

function moveRoom(id: string, slot: number) {
  const visible = filtered.value.map((item) => item.id);
  const source = visible.indexOf(id);
  if (source < 0) return;
  visible.splice(source, 1);
  visible.splice(slot > source ? slot - 1 : slot, 0, id);
  const visibleIds = new Set(visible);
  let index = 0;
  roomOrder.value = ordered.value.map((item) => visibleIds.has(item.id) ? visible[index++] : item.id);
  saveOrder();
}
function startDrag(id: string, event: PointerEvent) {
  if (event.button !== 0 || props.busy || filtered.value.length < 2) return;
  event.preventDefault();
  activePointer = event.pointerId;
  draggingId.value = id;
  dropSlot.value = null;
  (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
}
function updateDrag(event: PointerEvent) {
  if (event.pointerId !== activePointer || !roomItems.value) return;
  const bounds = roomItems.value.getBoundingClientRect();
  if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) {
    dropSlot.value = null;
    return;
  }
  const horizontal = window.matchMedia("(max-width: 850px)").matches;
  const coordinate = horizontal ? event.clientX : event.clientY;
  const cards = Array.from(roomItems.value.querySelectorAll<HTMLElement>(".live-room-item"));
  const slot = cards.findIndex((card) => {
    const rect = card.getBoundingClientRect();
    return coordinate < (horizontal ? (rect.left + rect.right) / 2 : (rect.top + rect.bottom) / 2);
  });
  dropSlot.value = slot < 0 ? cards.length : slot;
}
function endDrag(event: PointerEvent, canceled = false) {
  if (event.pointerId !== activePointer) return;
  if (!canceled && draggingId.value && dropSlot.value !== null) moveRoom(draggingId.value, dropSlot.value);
  activePointer = null;
  draggingId.value = null;
  dropSlot.value = null;
}
function moveRoomByKey(id: string, event: KeyboardEvent) {
  const direction = event.key === "ArrowUp" || event.key === "ArrowLeft" ? -1 : event.key === "ArrowDown" || event.key === "ArrowRight" ? 1 : 0;
  if (!direction || props.busy) return;
  event.preventDefault();
  const index = filtered.value.findIndex((item) => item.id === id);
  if (index < 0 || index + direction < 0 || index + direction >= filtered.value.length) return;
  moveRoom(id, direction < 0 ? index - 1 : index + 2);
}
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
let settingsTrigger: HTMLElement | undefined;
const anchor = ref({ top: 0, left: 0 });
const settingsMonitor = computed(
  () => props.monitors.find((item) => item.id === settingsFor.value) ?? null,
);
async function toggleSettings(monitor: LiveMonitor, event: MouseEvent) {
  if (settingsFor.value === monitor.id) {
    closeSettings();
    return;
  }
  settingsTrigger = event.currentTarget as HTMLElement;
  const rect = settingsTrigger.getBoundingClientRect();
  anchor.value = {
    top: rect.bottom + 6,
    left: Math.max(12, Math.min(rect.right - 232, window.innerWidth - 244)),
  };
  settingsFor.value = monitor.id;
  window.addEventListener("pointerdown", onOutside, true);
  window.addEventListener("keydown", onEscape);
  window.addEventListener("scroll", onSettingsScroll, true);
  await nextTick();
  if (settingsFor.value !== monitor.id || !settingsPanel.value) return;
  const { height } = settingsPanel.value.getBoundingClientRect();
  anchor.value.top = Math.max(12, Math.min(rect.bottom + 6, window.innerHeight - height - 12));
}
function closeSettings() {
  settingsFor.value = null;
  window.removeEventListener("pointerdown", onOutside, true);
  window.removeEventListener("keydown", onEscape);
  window.removeEventListener("scroll", onSettingsScroll, true);
}
function onOutside(event: PointerEvent) {
  const target = event.target as Node;
  if (settingsPanel.value?.contains(target)) return;
  if ((target as HTMLElement).closest?.(".live-room-settings-trigger")) return;
  closeSettings();
}
function onSettingsScroll(event: Event) {
  if (event.target !== settingsPanel.value) closeSettings();
}
function onEscape(event: KeyboardEvent) {
  if (event.key === "Escape") closeSettings();
}
const settingToggles: { key: keyof LiveMonitorSettings; name: string; hint: string }[] = [
  { key: "auto_record", name: "开播自动录制", hint: "开播后自动开始，关闭后需手动开始" },
  { key: "notify_live", name: "开播提醒", hint: "开播时弹出 Windows 通知" },
  { key: "notify_offline", name: "下播提醒", hint: "检测到直播结束时弹出 Windows 通知" },
  { key: "notify_split_complete", name: "切分完成提醒", hint: "商品视频片段全部生成后弹出 Windows 通知" },
];
function changeSetting(monitor: LiveMonitor, key: keyof LiveMonitorSettings, event: Event) {
  emit("settings", monitor, { [key]: (event.target as HTMLInputElement).checked });
}

const bindingDialog = ref<HTMLDialogElement>();
const bindingTitleId = useId();
const bindingMonitor = ref<LiveMonitor | null>(null);
const bindingDraft = ref<RoomPlatformBinding | null>(null);
const savingBinding = ref(false);
const bindingError = ref("");

async function openBinding(monitor: LiveMonitor) {
  bindingMonitor.value = monitor;
  bindingDraft.value = monitor.product_platform ? { ...monitor.product_platform } : null;
  bindingError.value = "";
  closeSettings();
  await nextTick();
  bindingDialog.value?.showModal();
}

function closeBinding() {
  if (!savingBinding.value) bindingDialog.value?.close();
}

function resetBinding() {
  bindingMonitor.value = null;
  settingsTrigger?.focus();
}

async function saveBinding() {
  const monitor = bindingMonitor.value;
  const binding = bindingDraft.value;
  if (!monitor || savingBinding.value || needsPlatformIp(binding)) return;
  savingBinding.value = true;
  bindingError.value = "";
  try {
    await liveMonitoringApi.setProductPlatform(monitor.id, binding);
    bindingDialog.value?.close();
    emit("platform-saved");
  } catch (cause) {
    bindingError.value = cause instanceof Error ? cause.message : "保存商品平台失败";
  } finally {
    savingBinding.value = false;
  }
}
// A checkbox flips in the DOM before the request settles. Once the action ends, put every
// toggle back on the saved value so a failed save does not leave a setting shown as changed.
watch(() => props.busy, async (busy) => {
  if (busy) return;
  await nextTick();
  const monitor = settingsMonitor.value;
  if (!monitor) return;
  settingsPanel.value?.querySelectorAll<HTMLInputElement>("input[data-setting]").forEach((input) => {
    input.checked = monitor[input.dataset.setting as keyof LiveMonitorSettings];
  });
});
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
    <div ref="roomItems" class="live-room-items">
      <div
        v-for="(monitor, index) in filtered"
        :key="monitor.id"
        class="live-room-item"
        :class="{ selected: selectedId === monitor.id, 'is-dragging': draggingId === monitor.id, 'drop-before': dropSlot === index, 'drop-after': dropSlot === filtered.length && index === filtered.length - 1 }"
        role="button"
        tabindex="0"
        :aria-pressed="selectedId === monitor.id"
        @click="selectMonitor(monitor, $event)"
        @keydown.enter="selectMonitor(monitor, $event)"
        @keydown.space="selectMonitor(monitor, $event)">
        <div class="live-room-item-top">
          <button
            class="live-room-drag-handle"
            type="button"
            :disabled="busy || filtered.length < 2"
            :aria-label="`排序 ${monitor.name || '抖音直播间'}`"
            title="拖动排序，或使用方向键移动"
            @click.stop
            @keydown.stop="moveRoomByKey(monitor.id, $event)"
            @pointerdown.stop="startDrag(monitor.id, $event)"
            @pointermove="updateDrag"
            @pointerup="endDrag($event)"
            @pointercancel="endDrag($event, true)"
            @lostpointercapture="endDrag($event, true)">
            <LiveIcon name="grip" />
          </button>
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
          <LiveIcon name="settings" />
          {{
            selected.auto_record ? "开播自动录制已开启" : "开播不自动录制"
          }} · {{ selected.notify_live ? "开播提醒" : "不开播提醒" }} · {{ selected.notify_offline ? "下播提醒" : "不提醒下播" }} · {{ selected.notify_split_complete ? "切分完成提醒" : "不提醒切分" }}
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
        <label v-for="item in settingToggles" :key="item.key" class="live-room-setting">
          <input
            class="toggle toggle-sm"
            type="checkbox"
            :data-setting="item.key"
            :checked="settingsMonitor[item.key]"
            :disabled="busy"
            @change="changeSetting(settingsMonitor, item.key, $event)" />
          <span>
            {{ item.name }}
            <small>{{ item.hint }}</small>
          </span>
        </label>
        <div class="mt-2 border-t border-base-300 px-1.5 pt-2">
          <button type="button" class="btn btn-sm w-full justify-start" aria-haspopup="dialog"
            :disabled="busy" @click="openBinding(settingsMonitor)">
            {{ settingsMonitor.product_platform ? "修改绑定" : "绑定平台" }}
          </button>
        </div>
      </div>
      <dialog v-if="bindingMonitor" ref="bindingDialog" class="modal" :aria-labelledby="bindingTitleId"
        @cancel.prevent="closeBinding" @close="resetBinding">
        <div class="modal-box max-w-sm">
          <h2 :id="bindingTitleId" class="text-lg font-semibold">
            {{ bindingMonitor.product_platform ? "修改绑定" : "绑定平台" }} · {{ bindingMonitor.name || "抖音直播间" }}
          </h2>
          <form class="pt-4" @submit.prevent="saveBinding">
            <fieldset :disabled="savingBinding">
              <RoomPlatformFields v-model="bindingDraft" />
            </fieldset>
            <p v-if="bindingError" class="mt-3 text-sm text-error" role="alert">{{ bindingError }}</p>
            <div class="modal-action">
              <button type="button" class="btn btn-sm" :disabled="savingBinding" @click="closeBinding">取消</button>
              <button class="btn btn-primary btn-sm"
                :disabled="savingBinding || needsPlatformIp(bindingDraft)">保存绑定</button>
            </div>
          </form>
        </div>
        <form method="dialog" class="modal-backdrop" @submit.prevent="closeBinding">
          <button :disabled="savingBinding" aria-label="关闭平台绑定弹窗">关闭</button>
        </form>
      </dialog>
    </Teleport>
  </section>
</template>
