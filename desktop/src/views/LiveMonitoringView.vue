<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import { storeToRefs } from "pinia";
import { openUrl } from "@tauri-apps/plugin-opener";
import { useSessionStore } from "../stores/session";
import { useWorkspace } from "../workspace";
import {
  liveMonitoringApi,
  type LiveMonitor,
  type LiveMonitorSettings,
  type LiveSession,
  type LiveSessionSummary,
  type LiveSplitResult,
} from "../services/liveMonitoring";
import LiveIcon from "../components/live/LiveIcon.vue";
import LiveRoomList from "../components/live/LiveRoomList.vue";
import LiveSessionPanel from "../components/live/LiveSessionPanel.vue";
import LiveRecordingTimeline from "../components/live/LiveRecordingTimeline.vue";
import LiveProductTimeline from "../components/live/LiveProductTimeline.vue";
import {
  formatLiveTime,
  formatOffset,
  monitorNeedsAttention,
} from "../components/live/presentation";
import "../styles/live-monitoring.css";

const session = useSessionStore(),
  workspace = useWorkspace();
const { busy, connected } = storeToRefs(session);
const monitors = ref<LiveMonitor[]>([]);
const url = ref(""),
  name = ref(""),
  loading = ref(false),
  showCreate = ref(false);
const selectedId = ref(""),
  updatedAt = ref<number | null>(null);
const selectedSession = ref<LiveSession>();
const selectedPeriodId = ref("");
const refreshError = ref("");
let refreshVersion = 0;
let disposed = false;
let chooseRoomDefault = false;
let pendingRecordingMonitorId = "";
// A clicked timeline session is applied by the next refresh, which reads its detail.
let requestedSessionId = "";
const urlInput = ref<HTMLInputElement>(),
  createDialog = ref<HTMLDialogElement>();
let timer: ReturnType<typeof setInterval> | undefined;
const currentTime = ref(Date.now() / 1000);
const selectedDate = ref("");
const recordingWindow = ref(dateWindow("", currentTime.value));
const dateLoading = computed(() => loading.value
  && (updatedAt.value === null || selectedDate.value !== recordingWindow.value.date));
const latestSelectableDate = computed(() => localDate(new Date(currentTime.value * 1000)));
const windowLabel = computed(() => `${recordingWindow.value.date || `今天 · ${localDate(new Date(recordingWindow.value.start * 1000))}`} · 00:00–24:00`);

function localDate(value: Date) {
  return `${String(value.getFullYear()).padStart(4, "0")}-${String(value.getMonth() + 1).padStart(2, "0")}-${String(value.getDate()).padStart(2, "0")}`;
}
function dateWindow(date: string, now: number) {
  const day = date || localDate(new Date(now * 1000));
  const start = new Date(`${day}T00:00:00`);
  const end = new Date(start);
  end.setDate(end.getDate() + 1);
  end.setHours(0, 0, 0, 0);
  return { start: start.getTime() / 1000, end: end.getTime() / 1000, date, includeEndRecording: false };
}
async function changeDate(date: string) {
  if (date === latestSelectableDate.value) date = "";
  if (dateLoading.value || date === selectedDate.value) return;
  if (date && (!/^\d{4}-\d{2}-\d{2}$/.test(date) || date > latestSelectableDate.value
    || localDate(new Date(`${date}T00:00:00`)) !== date)) return;
  pendingRecordingMonitorId = "";
  selectedDate.value = date;
  await refresh();
}

const selectedMonitor = computed(
  () =>
    monitors.value.find((item) => item.id === selectedId.value) ??
    monitors.value.find((item) => item.live_state === "recording") ??
    monitors.value[0],
);
const sessions = computed(() =>
  [...(selectedMonitor.value?.sessions ?? [])].sort(recordingFirst),
);
function recordingFirst(a: LiveSessionSummary, b: LiveSessionSummary) {
  return Number(b.status === "recording") - Number(a.status === "recording") || b.started - a.started;
}
// The window lists sessions before the selected one's detail has been read.
const readingSession = computed(() => !selectedSession.value && sessions.value.length > 0);
const emptyMessage = computed(() => readingSession.value ? "正在读取场次…"
  : recordingWindow.value.date ? "当天无录制" : "今天无录制，可选择其他日期");
const cardEmptyMessage = computed(() => readingSession.value ? "正在读取场次…"
  : recordingWindow.value.date ? "当天无录制" : "今天无录制");
const selectionOutsideWindow = computed(() => selectedSession.value
  && !sessions.value.some(item => item.id === selectedSession.value?.id));
function defaultSession(items: LiveSessionSummary[]) {
  return [...items].sort(recordingFirst)[0];
}
function latestSession(items: LiveSessionSummary[]) {
  return [...items].sort((a, b) => b.started - a.started)[0];
}
function selectMonitor(id: string) {
  if (id === selectedMonitor.value?.id) return;
  pendingRecordingMonitorId = "";
  requestedSessionId = "";
  selectedId.value = id;
  selectedSession.value = undefined;
  selectedPeriodId.value = "";
  chooseRoomDefault = true;
  void refresh();
}
function selectSession(id: string) {
  if (!sessions.value.some(item => item.id === id)) return;
  pendingRecordingMonitorId = "";
  chooseRoomDefault = false;
  requestedSessionId = id === selectedSession.value?.id ? "" : id;
  if (requestedSessionId || loading.value) void refresh();
}
function focusPeriod(id: string) {
  document.getElementById(`live-period-${id}`)?.scrollIntoView({ behavior: "smooth", block: "nearest" });
}
function viewSelectedDate() {
  if (selectedSession.value) void changeDate(localDate(new Date(selectedSession.value.started * 1000)));
}
const recordingCount = computed(
  () => monitors.value.filter((item) => item.live_state === "recording").length,
);
const liveCount = computed(
  () => monitors.value.filter((item) => item.is_live === true).length,
);
const attentionCount = computed(
  () => monitors.value.filter(monitorNeedsAttention).length,
);

watch(showCreate, async (value) => {
  await nextTick();
  if (value) {
    if (!createDialog.value?.open) createDialog.value?.showModal();
    urlInput.value?.focus();
  } else if (createDialog.value?.open) {
    createDialog.value.close();
  }
});

function closeCreate() {
  showCreate.value = false;
}

async function refresh() {
  const version = ++refreshVersion;
  loading.value = true;
  const previousMonitor = selectedMonitor.value?.id;
  const dateChanged = selectedDate.value !== recordingWindow.value.date;
  try {
    await session.connect();
    if (disposed || version !== refreshVersion) return;
    const now = Date.now() / 1000;
    const range = dateWindow(selectedDate.value, now);
    const result = await liveMonitoringApi.list(range);
    if (disposed || version !== refreshVersion) return;
    const nextMonitor = result.find(item => item.id === selectedId.value)
      ?? result.find(item => item.live_state === "recording") ?? result[0];
    const items = nextMonitor?.sessions ?? [];
    const current = !chooseRoomDefault && previousMonitor === nextMonitor?.id ? selectedSession.value : undefined;
    // A start request is acknowledged before the recording worker creates its
    // session. Keep the one-time jump pending across refreshes until it appears.
    const startedSession = pendingRecordingMonitorId === nextMonitor?.id
      ? defaultSession(items.filter(item => item.status === "recording")) : undefined;
    const wantedId = requestedSessionId || current?.id;
    let target = startedSession ?? items.find(item => item.id === wantedId);
    let nextSession: LiveSession | null | undefined;
    if (!target && current && nextMonitor) {
      if (dateChanged) {
        target = latestSession(items);
      } else {
        // Absence from a time window does not establish that the session was deleted.
        nextSession = await liveMonitoringApi.session(nextMonitor.id, current.id);
        if (disposed || version !== refreshVersion) return;
        if (!nextSession) target = latestSession(items);
      }
    } else if (!target && !current) {
      target = !chooseRoomDefault && dateChanged && previousMonitor === nextMonitor?.id
        ? latestSession(items) : defaultSession(items);
    }
    if (!nextSession && target && nextMonitor) {
      nextSession = await liveMonitoringApi.session(nextMonitor.id, target.id);
      if (disposed || version !== refreshVersion) return;
    }
    currentTime.value = now;
    recordingWindow.value = range;
    monitors.value = result;
    selectedId.value = nextMonitor?.id ?? "";
    if (nextSession?.id !== selectedSession.value?.id) selectedPeriodId.value = "";
    selectedSession.value = nextSession ?? undefined;
    requestedSessionId = "";
    if (startedSession) pendingRecordingMonitorId = "";
    chooseRoomDefault = false;
    if (session.error === refreshError.value) session.error = "";
    refreshError.value = "";
    updatedAt.value = Date.now() / 1000;
  } catch (cause) {
    if (disposed || version !== refreshVersion) return;
    requestedSessionId = "";
    selectedDate.value = pendingRecordingMonitorId ? "" : recordingWindow.value.date;
    refreshError.value = cause instanceof Error ? cause.message : "操作失败";
    session.report(cause);
  } finally {
    if (version === refreshVersion) loading.value = false;
  }
}
function openExternalUrl(url: string) {
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    session.report(new Error("直播间链接无效"));
    return;
  }
  const host = parsed.hostname.toLowerCase();
  if (
    !["http:", "https:"].includes(parsed.protocol) ||
    !(host === "douyin.com" || host.endsWith(".douyin.com")) ||
    parsed.username ||
    parsed.password ||
    parsed.port
  ) {
    session.report(new Error("仅支持打开抖音直播间链接"));
    return;
  }
  void openUrl(parsed.href).catch((cause) => session.report(cause));
}
async function addMonitor() {
  await session.act(async () => {
    const created = await liveMonitoringApi.create(
      url.value.trim(),
      name.value.trim(),
    );
    selectedId.value = created.id;
    url.value = "";
    name.value = "";
    showCreate.value = false;
    session.message = "直播间已加入监控，开播后按设置自动录制或提醒";
    await refresh();
  });
}
async function updateSettings(
  monitor: LiveMonitor,
  changes: LiveMonitorSettings,
) {
  await session.act(async () => {
    await liveMonitoringApi.updateSettings(monitor.id, changes);
    if ("auto_record" in changes) {
      session.message = changes.auto_record
        ? "已开启开播自动录制"
        : "已关闭开播自动录制；开播后需手动开始录制";
    } else {
      session.message = changes.notify_live
        ? "已开启开播提醒"
        : "已关闭开播提醒";
    }
    await refresh();
  });
}
async function record(monitor: LiveMonitor, action: "start" | "stop") {
  await session.act(async () => {
    await liveMonitoringApi.setRecording(monitor.id, action);
    if (disposed) return;
    if (action === "start") {
      selectedId.value = monitor.id;
      selectedDate.value = "";
      pendingRecordingMonitorId = monitor.id;
    } else if (pendingRecordingMonitorId === monitor.id) {
      pendingRecordingMonitorId = "";
    }
    session.message =
      action === "start"
        ? "已开始录制"
      : "已停止录制；正在完成场次收尾";
    await refresh();
  });
}
async function remove(monitor: LiveMonitor) {
  await session.act(async () => {
    await liveMonitoringApi.remove(monitor.id);
    session.message = "监控项已移除，录制文件保留";
    await refresh();
  });
}
async function importProductClip(clipId: string) {
  await session.act(async () => {
    const result = await liveMonitoringApi.importProductClip(clipId);
    session.message = "商品视频片段已提交导入；完成后会打开对应视频项目";
    await refresh();
    if ("asset_id" in result) await workspace.openProject(result.asset_id);
  });
}
async function openClipFile(clipId: string, reveal: boolean) {
  await session.act(async () => {
    await liveMonitoringApi.openProductClip(clipId, reveal);
    if (reveal) session.message = "已在文件夹中显示商品片段";
  });
}
async function viewSource(segmentId: string, offset: number) {
  await session.act(async () => {
    await liveMonitoringApi.openSegment(segmentId);
    session.message = `已用系统播放器打开录制片段；鼠标位置对应文件内 ${formatOffset(offset, 0)}，可手动跳转查看`;
  });
}
async function splitPeriod(periodId: string, confirmedMissingSegmentIds: string[], range?: {start:number; end:number}): Promise<LiveSplitResult> {
  try {
    session.error = "";
    const result = await liveMonitoringApi.splitPeriod(periodId, confirmedMissingSegmentIds, range);
    if (result.state === "confirmation_required") return result;
    session.message = "message" in result ? result.message : "已提交该次讲解的切分任务；完成后在“商品视频片段”中查看";
    await refresh();
    return result;
  } catch (cause) {
    session.report(cause);
    throw cause;
  }
}
async function deleteProductClip(clipId: string) {
  await session.act(async () => {
    const result = await liveMonitoringApi.deleteProductClip(clipId);
    session.message = result.project_file_retained
      ? "商品片段记录已删除；已导入项目及文件保留"
      : result.file_deleted ? "商品片段及本地文件已删除" : "商品片段记录已删除；本地文件原已不存在";
    await refresh();
  });
}
async function deleteSession(sessionId: string) {
  const monitorId = selectedMonitor.value?.id;
  if (!monitorId) return;
  await session.act(async () => {
    const result = await liveMonitoringApi.deleteSession(monitorId, sessionId);
    session.message = result.deleted_files
      ? `场次已删除，清理 ${result.deleted_files} 个未导入文件`
      : "场次已删除；已导入项目及其素材保留";
    await refresh();
  });
}
async function retryProductListener(monitorId: string, sessionId: string) {
  await session.act(async () => {
    await liveMonitoringApi.retryProductListener(monitorId, sessionId);
    session.message = "已提交商品连接重试；录制不会中断";
    await refresh();
  });
}
// Recording and detection run in the backend; a hidden window only needs to catch up once shown.
function syncWhenVisible() {
  if (!document.hidden && !loading.value) void refresh();
}
onMounted(() => {
  void refresh();
  timer = setInterval(syncWhenVisible, 5000);
  document.addEventListener("visibilitychange", syncWhenVisible);
});
onUnmounted(() => {
  disposed = true;
  refreshVersion++;
  if (timer) clearInterval(timer);
  document.removeEventListener("visibilitychange", syncWhenVisible);
});
</script>

<template>
  <section class="live-page" aria-label="直播监控工作台">
    <div
      class="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4 live-overview">
      <div class="card card-border bg-base-100 live-metric">
        <div class="card-body flex-row items-center gap-6 p-4">
          <LiveIcon name="rooms" class="live-metric-icon shrink-0 text-info" />
          <div class="live-metric-copy">
            <span class="text-lg text-base-content">直播间总数</span>
          </div>
          <strong class="text-2xl font-bold leading-none">
            {{ monitors.length }}
          </strong>
        </div>
      </div>
      <div class="card card-border bg-base-100 live-metric">
        <div class="card-body flex-row items-center gap-6 p-4">
          <LiveIcon name="record" class="live-metric-icon shrink-0 text-error" />
          <div class="live-metric-copy">
            <span class="text-lg text-base-content">正在录制</span>
          </div>
          <strong class="text-2xl font-bold leading-none">
            {{ recordingCount }}
          </strong>
        </div>
      </div>
      <div class="card card-border bg-base-100 live-metric">
        <div class="card-body flex-row items-center gap-6 p-4">
          <LiveIcon name="radio" class="live-metric-icon shrink-0 text-success" />
          <div class="live-metric-copy">
            <span class="text-lg text-base-content">正在直播</span>
          </div>
          <strong class="text-2xl font-bold leading-none">
            {{ liveCount }}
          </strong>
        </div>
      </div>
      <div class="card card-border bg-base-100 live-metric">
        <div class="card-body flex-row items-center gap-6 p-4">
          <LiveIcon
            :name="attentionCount ? 'alert' : 'check'"
            class="live-metric-icon shrink-0"
            :class="attentionCount ? 'text-warning' : 'text-success'" />
          <div class="live-metric-copy">
            <span class="text-lg text-base-content">需要关注</span>
          </div>
          <strong class="text-2xl font-bold leading-none">
            {{ attentionCount }}
          </strong>
        </div>
      </div>
    </div>

    <div class="live-toolbar">
      <p>
        <span
          :class="['badge', 'badge-soft', 'badge-sm', connected ? 'badge-success' : 'badge-warning']">
          <span :class="['status', 'status-xs', connected ? 'status-success' : 'status-warning']"></span>
          {{ connected ? "监控工作台已连接" : "等待本地服务连接" }}
        </span>
        <span class="live-toolbar-note">
          列表每 5 秒同步 · 开播状态每 20 秒检测
        </span>
      </p>
      <div class="live-toolbar-actions">
        <span v-if="updatedAt" class="live-updated">
          更新于 {{ formatLiveTime(updatedAt, true) }}
        </span>
        <button
          class="btn btn-ghost btn-sm"
          :disabled="busy || loading"
          @click="refresh">
          <LiveIcon name="refresh" :class="{ 'is-spinning': loading }" />
          刷新
        </button>
        <button
          class="btn btn-primary btn-sm"
          @click="showCreate = true">
          <LiveIcon name="plus" />
          添加直播间
        </button>
      </div>
    </div>

    <dialog
      ref="createDialog"
      class="modal live-add-dialog"
      aria-labelledby="live-create-title"
      @close="showCreate = false">
      <div class="modal-box">
        <div class="live-add-heading">
          <span class="live-section-icon"><LiveIcon name="plus" /></span>
          <div>
            <h2 id="live-create-title">添加抖音直播间</h2>
            <p>支持直播间链接或分享短链，备注留空时自动获取主播名称。</p>
          </div>
          <button
            class="btn btn-ghost btn-square btn-sm live-add-close"
            type="button"
            aria-label="关闭"
            @click="closeCreate">
            <LiveIcon name="close" />
          </button>
        </div>
        <form @submit.prevent="addMonitor">
          <div class="live-add-fields">
            <label>
              直播间链接
              <input
                ref="urlInput"
                v-model="url"
                class="input input-sm w-full"
                type="text"
                required
                maxlength="2000"
                placeholder="https://live.douyin.com/…" />
            </label>
            <label>
              备注名称
              <span>选填</span>
              <input
                v-model="name"
                class="input input-sm w-full"
                maxlength="200"
                placeholder="例如：独束官方旗舰店" />
            </label>
          </div>
          <div class="modal-action">
            <button class="btn btn-ghost btn-sm" type="button" @click="closeCreate">
              取消
            </button>
            <button
              class="btn btn-primary btn-sm"
              :disabled="busy || !connected || !url.trim()">
              <LiveIcon name="plus" />
              开始监控
            </button>
          </div>
        </form>
      </div>
      <form method="dialog" class="modal-backdrop">
        <button aria-label="关闭添加直播间弹窗">关闭</button>
      </form>
    </dialog>

    <div class="live-workbench">
      <LiveRoomList
        :monitors="monitors"
        :selected-id="selectedMonitor?.id ?? ''"
        :loading="loading && updatedAt === null"
        :busy="busy"
        @select="selectMonitor"
        @open-url="openExternalUrl"
        @settings="updateSettings"
        @record="record"
        :on-remove="remove" />

      <div v-if="selectedMonitor" class="live-detail">
        <div v-if="refreshError" role="alert" class="alert alert-error mb-3 flex-none text-sm">
          刷新录制失败：{{ refreshError }}。已保留上次成功读取的内容。
        </div>
        <LiveRecordingTimeline
          :key="selectedMonitor.id"
          :sessions="sessions"
          :selected-id="selectedSession?.id ?? ''"
          :selected-session="selectedSession"
          :start="recordingWindow.start"
          :end="recordingWindow.end"
          :now="currentTime"
          :busy="busy"
          :date="selectedDate"
          :max-date="latestSelectableDate"
          :loading="dateLoading"
          :label="windowLabel"
          :empty-message="emptyMessage"
          :on-delete-session="deleteSession"
          @date-change="changeDate"
          @select="selectSession">
          <LiveProductTimeline
            v-if="selectedSession"
            :key="selectedSession.id"
            v-model:selected-period-id="selectedPeriodId"
            :session="selectedSession"
            :busy="busy"
            :on-split="splitPeriod"
            @view-source="viewSource"
            @select="focusPeriod" />
          <section v-else class="live-product-rail live-product-rail-empty" aria-label="商品讲解轨">
            <div class="live-product-rail-heading">
              <div class="live-product-rail-label"><LiveIcon name="clock" /><strong>商品讲解</strong><span>0 段</span></div>
              <span class="live-product-rail-hint">选择录制场次后显示讲解区间</span>
            </div>
          </section>
        </LiveRecordingTimeline>
        <div v-if="selectionOutsideWindow" role="status"
          class="alert alert-info alert-vertical sm:alert-horizontal mb-3 flex-none text-sm">
          <span>所选场次已不在当前时间范围内</span>
          <button type="button" class="btn btn-sm" :disabled="dateLoading"
            aria-label="查看该日期" @click="viewSelectedDate">查看该日期</button>
        </div>
        <LiveSessionPanel
          v-if="selectedSession"
          :key="selectedSession.id"
          :session="selectedSession"
          :selected-period-id="selectedPeriodId"
          :monitor-id="selectedMonitor.id"
          :busy="busy"
          @import-product-clip="importProductClip"
          @open="workspace.openProject"
          :on-delete-product-clip="deleteProductClip"
          :on-retry-product-listener="retryProductListener"
          @select-period="selectedPeriodId = $event"
          @open-file="openClipFile" />
        <div v-else class="live-content-columns">
          <section v-for="title in ['商品讲解', '商品视频片段']" :key="title"
            class="card card-border bg-base-100 live-content-card">
            <div class="live-content-card-heading"><h3>{{ title }}</h3></div>
            <div class="live-card-empty"><strong>{{ cardEmptyMessage }}</strong></div>
          </section>
        </div>
      </div>

      <div v-else class="live-detail live-welcome">
        <span class="live-welcome-illustration">
          <LiveIcon name="radio" />
          <i></i>
        </span>
        <span class="live-section-kicker">从一场直播开始</span>
        <h2>把直播素材，留给下一条好视频</h2>
        <p>
          添加抖音直播间，自动监控开播并将录制内容保存在本机。
          <br />
          商品讲解与录制内容集中管理，随时导入剪辑。
        </p>
        <button class="btn btn-soft btn-primary" @click="showCreate = true">
          <LiveIcon name="plus" />
          添加第一个直播间
        </button>
        <div class="live-welcome-steps">
          <span>
            <LiveIcon name="radio" />
            监控开播
          </span>
          <LiveIcon name="arrow" />
          <span>
            <LiveIcon name="video" />
            本地录制
          </span>
          <LiveIcon name="arrow" />
          <span>
            <LiveIcon name="layers" />
            导入剪辑
          </span>
        </div>
      </div>
    </div>
  </section>
</template>
