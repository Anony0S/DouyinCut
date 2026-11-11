<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from "vue";
import { storeToRefs } from "pinia";
import { useSessionStore } from "../stores/session";
import { logsApi, type LogFile } from "../services/logs";
import { appendChunk, classifyLines, fileKey, fileLabel, groupTaskLogs } from "../logPresentation";
import { updatedTime } from "../presentation";
import { formatBytes } from "../components/live/presentation";
import { errorMessage } from "../error-message";
import { writeClipboard } from "../clipboard";
import ConfirmPopover from "../components/ConfirmPopover.vue";
import LiveIcon from "../components/live/LiveIcon.vue";

const session = useSessionStore();
const { busy } = storeToRefs(session);
const files = ref<LogFile[]>([]);
const listLoaded = ref(false), listError = ref("");
const selectedKey = ref("");
const text = ref(""), truncated = ref(false), nextOffset = ref(0);
const reading = ref(false), readError = ref("");
const search = ref(""), errorsOnly = ref(false), follow = ref(true);
const viewer = ref<HTMLElement>();
let disposed = false, readVersion = 0;
let timer: ReturnType<typeof setInterval> | undefined;

const runtimeFiles = computed(() => files.value.filter((file) => file.category === "runtime"));
const taskGroups = computed(() => groupTaskLogs(files.value));
const selected = computed(() => files.value.find((file) => fileKey(file) === selectedKey.value));
const lines = computed(() => classifyLines(text.value));
// FFmpeg output has no levels, so the error filter applies to the application log only.
const canFilterErrors = computed(() => selected.value?.category === "runtime");
const visibleLines = computed(() => {
  const query = search.value.trim().toLocaleLowerCase();
  const onlyErrors = errorsOnly.value && canFilterErrors.value;
  return lines.value.filter((line) => (!onlyErrors || line.level === "error")
    && (!query || line.text.toLocaleLowerCase().includes(query)));
});
const filtered = computed(() => visibleLines.value.length !== lines.value.length);

async function loadFiles() {
  try {
    const result = await logsApi.list();
    if (disposed) return;
    files.value = result.files;
    listError.value = "";
    listLoaded.value = true;
    if (!selected.value) await select(result.files[0]);
  } catch (cause) {
    if (!disposed) listError.value = errorMessage(cause);
  }
}

async function select(file: LogFile | undefined) {
  const version = ++readVersion;
  selectedKey.value = file ? fileKey(file) : "";
  text.value = "";
  truncated.value = false;
  nextOffset.value = 0;
  readError.value = "";
  if (!file) return;
  reading.value = true;
  try {
    const chunk = await logsApi.read(file);
    if (disposed || version !== readVersion) return;
    text.value = chunk.text;
    truncated.value = chunk.truncated;
    nextOffset.value = chunk.next;
    await scrollToEnd();
  } catch (cause) {
    if (!disposed && version === readVersion) readError.value = errorMessage(cause);
  } finally {
    if (version === readVersion) reading.value = false;
  }
}

async function scrollToEnd() {
  await nextTick();
  if (viewer.value) viewer.value.scrollTop = viewer.value.scrollHeight;
}

async function readAppended() {
  const file = selected.value;
  if (!file || reading.value || readError.value) return;
  const version = readVersion;
  try {
    const chunk = await logsApi.read(file, nextOffset.value);
    if (disposed || version !== readVersion) return;
    nextOffset.value = chunk.next;
    if (!chunk.text && !chunk.reset) return;
    const element = viewer.value;
    // Keep the reader's place unless they were already at the end.
    const atEnd = !element || element.scrollHeight - element.scrollTop - element.clientHeight < 48;
    ({ text: text.value, truncated: truncated.value } = appendChunk(text.value, truncated.value, chunk));
    if (atEnd) await scrollToEnd();
  } catch (cause) {
    if (!disposed && version === readVersion) readError.value = errorMessage(cause);
  }
}

function tick() {
  if (follow.value && !document.hidden) void readAppended();
}

async function refresh() {
  await loadFiles();
  const file = selected.value;
  // The selected file may have been removed, or failed to load: read it afresh.
  if (readError.value || (selectedKey.value && !file)) await select(file ?? files.value[0]);
  else await readAppended();
}

async function copyVisible() {
  try {
    await writeClipboard(visibleLines.value.map((line) => line.text).join("\n"));
    session.message = filtered.value ? `已复制筛选后的 ${visibleLines.value.length} 行` : `已复制 ${visibleLines.value.length} 行`;
  } catch (cause) {
    session.report(cause);
  }
}

async function reveal() {
  const file = selected.value;
  if (!file) return;
  try {
    await logsApi.open(file.category, file.name);
  } catch (cause) {
    session.report(cause);
  }
}

async function clearLogs() {
  await session.act(async () => {
    const result = await logsApi.clear();
    const parts = [result.runtime_cleared ? "已清空运行日志" : "", result.deleted_files ? `删除 ${result.deleted_files} 个日志文件` : ""].filter(Boolean);
    session.message = `${parts.join("，") || "没有可清理的日志"}${result.retained_files ? `；保留 ${result.retained_files} 个进行中任务或被占用的日志` : ""}`;
    selectedKey.value = "";
    await loadFiles();
  });
}

onMounted(() => {
  void loadFiles();
  timer = setInterval(tick, 3000);
  document.addEventListener("visibilitychange", tick);
});
onUnmounted(() => {
  disposed = true;
  readVersion++;
  if (timer) clearInterval(timer);
  document.removeEventListener("visibilitychange", tick);
});
</script>

<template>
  <section class="card card-border bg-base-100 w-full logs-panel" aria-label="运行日志">
    <div class="logs-heading">
      <div>
        <h1>运行日志</h1>
        <p>应用运行日志与各任务的 FFmpeg 输出；密钥不会写入日志。</p>
      </div>
      <div class="logs-heading-actions">
        <button class="btn btn-ghost btn-sm" type="button" @click="refresh">
          <LiveIcon name="refresh" />刷新
        </button>
        <ConfirmPopover
          title="清理日志？"
          message="将清空应用运行日志，并删除已结束任务的日志文件；进行中任务的日志会保留。此操作无法撤销。"
          confirm-label="确定清理"
          :confirm-action="clearLogs"
          :disabled="busy || !files.length"
          v-slot="{ open, expanded, popoverId }">
          <button class="btn btn-soft btn-error btn-sm" type="button" :disabled="busy || !files.length"
            :aria-expanded="expanded" :aria-controls="popoverId" aria-haspopup="dialog" @click="open">
            <LiveIcon name="trash" />清理日志
          </button>
        </ConfirmPopover>
      </div>
    </div>

    <div class="logs-body">
      <nav class="logs-files" aria-label="日志文件">
        <p v-if="listError" class="logs-note is-error" role="alert">读取日志列表失败：{{ listError }}</p>
        <p v-else-if="!files.length" class="logs-note">{{ listLoaded ? "暂无日志" : "正在读取日志列表…" }}</p>
        <template v-if="runtimeFiles.length">
          <h2>应用运行</h2>
          <button v-for="file in runtimeFiles" :key="fileKey(file)" type="button" class="logs-file"
            :class="{ selected: fileKey(file) === selectedKey }" :aria-pressed="fileKey(file) === selectedKey"
            @click="select(file)">
            <span class="logs-file-name">{{ fileLabel(file) }}</span>
            <span class="logs-file-meta">{{ formatBytes(file.size) }} · {{ updatedTime(file.modified) }}</span>
          </button>
        </template>
        <template v-if="taskGroups.length">
          <h2>任务日志</h2>
          <div v-for="group in taskGroups" :key="group.key" class="logs-group">
            <div class="logs-group-title" :title="group.key">
              <strong>{{ group.title }}</strong>
              <span>{{ group.subtitle }}</span>
            </div>
            <button v-for="file in group.files" :key="fileKey(file)" type="button" class="logs-file"
              :class="{ selected: fileKey(file) === selectedKey }" :aria-pressed="fileKey(file) === selectedKey"
              :title="file.name" @click="select(file)">
              <span class="logs-file-name">{{ fileLabel(file) }}</span>
              <span class="logs-file-meta">{{ formatBytes(file.size) }} · {{ updatedTime(file.modified) }}</span>
            </button>
          </div>
        </template>
      </nav>

      <div class="logs-viewer">
        <div class="logs-toolbar">
          <input v-model="search" class="input input-sm logs-search" type="search" aria-label="搜索日志内容"
            placeholder="搜索当前日志" :disabled="!selected" />
          <label v-if="canFilterErrors" class="logs-toggle">
            <input v-model="errorsOnly" class="toggle toggle-sm" type="checkbox" />只看错误
          </label>
          <label class="logs-toggle" title="窗口可见时每 3 秒读取新增内容">
            <input v-model="follow" class="toggle toggle-sm" type="checkbox" />自动跟随
          </label>
          <span class="logs-count" role="status">
            {{ selected ? (filtered ? `显示 ${visibleLines.length} / ${lines.length} 行` : `${lines.length} 行`) : "" }}
          </span>
          <button class="btn btn-ghost btn-sm" type="button" :disabled="!visibleLines.length" @click="copyVisible">复制</button>
          <button class="btn btn-ghost btn-sm" type="button" :disabled="!selected" @click="reveal">
            <LiveIcon name="folder" />在文件夹中显示
          </button>
        </div>
        <p v-if="truncated" class="logs-note">文件较大，仅显示末尾部分；完整内容请在文件夹中打开。</p>
        <p v-if="readError" class="logs-note is-error" role="alert">读取日志失败：{{ readError }}</p>
        <div ref="viewer" class="logs-content" tabindex="0" aria-label="日志内容" :aria-busy="reading">
          <p v-if="!selected" class="logs-empty">{{ files.length ? "选择左侧的日志文件" : "" }}</p>
          <p v-else-if="reading" class="logs-empty">正在读取…</p>
          <p v-else-if="!visibleLines.length && !readError" class="logs-empty">
            {{ lines.length ? "没有匹配的内容" : "日志为空" }}
          </p>
          <div v-for="line in visibleLines" :key="line.index" class="logs-line" :class="line.level && `is-${line.level}`">{{ line.text || " " }}</div>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.logs-panel { display: flex; flex: 1; flex-direction: column; min-height: 0; overflow: hidden; }
.logs-heading { display: flex; flex: none; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 12px 16px; padding: 20px 24px; border-bottom: 1px solid var(--app-border); }
.logs-heading h1 { margin: 0; font-size: 21px; font-weight: 600; }
.logs-heading p { margin: 6px 0 0; color: var(--text-muted); font-size: 13px; }
.logs-heading-actions { display: flex; align-items: center; gap: 8px; }
.logs-heading-actions .live-icon, .logs-toolbar .live-icon { width: 15px; height: 15px; }
.logs-body { display: grid; flex: 1; grid-template-columns: clamp(220px, 22vw, 320px) minmax(0, 1fr); min-height: 0; }
.logs-files { min-height: 0; padding: 12px; overflow-y: auto; overscroll-behavior: contain; border-right: 1px solid var(--app-border); }
.logs-files h2 { margin: 10px 8px 6px; color: var(--text-muted); font-size: 12px; font-weight: 500; }
.logs-files h2:first-child { margin-top: 2px; }
.logs-group { margin-bottom: 8px; }
.logs-group-title { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; padding: 6px 8px 2px; font-size: 13px; }
.logs-group-title strong { overflow: hidden; font-weight: 600; text-overflow: ellipsis; white-space: nowrap; }
.logs-group-title span { flex: none; color: var(--text-muted); font: 11px/1.4 Consolas, monospace; }
.logs-file { display: flex; align-items: baseline; justify-content: space-between; gap: 10px; width: 100%; padding: 7px 8px; border: 0; border-radius: 6px; background: transparent; color: inherit; font-size: 13px; text-align: left; cursor: pointer; }
.logs-file:hover { background: var(--surface-soft); }
.logs-file.selected { background: var(--accent-soft); color: var(--accent); font-weight: 600; }
.logs-file:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
.logs-file-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.logs-file-meta { flex: none; color: var(--text-muted); font-size: 11px; font-weight: 400; font-variant-numeric: tabular-nums; }
.logs-viewer { display: flex; flex-direction: column; min-width: 0; min-height: 0; }
.logs-toolbar { display: flex; flex: none; align-items: center; flex-wrap: wrap; gap: 8px 14px; padding: 10px 16px; border-bottom: 1px solid var(--app-border); }
.logs-search { flex: 1 1 200px; max-width: 320px; }
.logs-toggle { display: inline-flex; align-items: center; gap: 6px; font-size: 13px; white-space: nowrap; cursor: pointer; }
.logs-count { margin-left: auto; color: var(--text-muted); font-size: 12px; font-variant-numeric: tabular-nums; }
.logs-note { flex: none; margin: 0; padding: 8px 16px; color: var(--text-muted); font-size: 12px; }
.logs-files .logs-note { padding: 8px; }
.logs-note.is-error { color: var(--color-error); overflow-wrap: anywhere; }
.logs-content { flex: 1; min-height: 0; padding: 10px 16px 16px; overflow: auto; overscroll-behavior: contain; font: 12px/1.6 Consolas, "Cascadia Mono", monospace; }
.logs-line { white-space: pre-wrap; overflow-wrap: anywhere; }
.logs-line.is-error { color: var(--color-error); }
.logs-line.is-warning { color: color-mix(in oklab, var(--color-warning) 70%, var(--color-base-content)); }
.logs-empty { margin: 0; padding: 40px 0; color: var(--text-muted); font-family: inherit; text-align: center; }
@media (max-width: 850px) {
  .logs-body { grid-template-columns: minmax(0, 1fr); grid-template-rows: minmax(110px, 30%) minmax(0, 1fr); }
  .logs-files { border-right: 0; border-bottom: 1px solid var(--app-border); }
  .logs-count { margin-left: 0; }
}
</style>
