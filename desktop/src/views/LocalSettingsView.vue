<script setup lang="ts">
import { computed, nextTick, onUnmounted, ref } from "vue";
import { api, ApiError } from "../api";
import AppearanceSettings from "../components/AppearanceSettings.vue";
import SoftwareUpdate from "../components/SoftwareUpdate.vue";
import { useProjectStore } from "../stores/project";
import { useSessionStore } from "../stores/session";

type TauriInternals = {
  invoke: (command: string, args?: Record<string, unknown>) => Promise<unknown>;
};

function tauri(): TauriInternals {
  const internals = (window as Window & { __TAURI_INTERNALS__?: TauriInternals }).__TAURI_INTERNALS__;
  if (!internals) throw new Error("此操作需要在桌面应用中使用");
  return internals;
}
type DialogState = { title: string; message: string; kind: "confirm" | "cleanup" | "info" | "error"; action?: () => Promise<void | DialogState> };
const dialog = ref<DialogState | null>(null);
const dialogBusy = ref(false);
const DEFAULT_RETENTION_DAYS = 7;
const retentionDays = ref(DEFAULT_RETENTION_DAYS);
const validRetentionDays = computed(() => Number.isSafeInteger(retentionDays.value) && retentionDays.value >= 1);
const clearExpiredData = ref(false);
const cleanupPreview = ref<CleanupPreview | null>(null);
const canConfirmCleanup = computed(() => {
  const preview = cleanupPreview.value;
  return !!(preview && (preview.file_count || preview.directory_count || preview.record_count));
});
const cleanupPreviewLoading = ref(false);
const cleanupPreviewError = ref("");
const project = useProjectStore();
const session = useSessionStore();
let cleanupPreviewRequest = 0;

function openDialog(state: DialogState) {
  dialog.value = state;
  void nextTick(() => document.querySelector<HTMLButtonElement>(".local-settings-dialog [data-autofocus]")?.focus());
}

function closeDialog() {
  if (!dialogBusy.value) dialog.value = null;
}

async function runDialogAction() {
  if (!dialog.value?.action || dialogBusy.value) return;
  dialogBusy.value = true;
  try {
    dialog.value = await dialog.value.action() ?? null;
  } catch (error) {
    if (dialog.value?.kind === "cleanup" && error instanceof ApiError && error.status === 409) {
      await refreshCleanupPreview("清理范围已变化，请核对重新统计的结果。");
    } else if (dialog.value?.kind === "cleanup" && error instanceof ApiError && error.status === 423) {
      cleanupPreview.value = null;
      cleanupPreviewError.value = error.message;
    } else {
      dialog.value = { title: "操作失败", message: String(error), kind: "error" };
    }
  } finally {
    dialogBusy.value = false;
  }
}

function onKeydown(event: KeyboardEvent) {
  if (event.key === "Escape") closeDialog();
}
window.addEventListener("keydown", onKeydown);
onUnmounted(() => {
  cleanupPreviewRequest++;
  window.removeEventListener("keydown", onKeydown);
});

function changeWorkDirectory() {
  openDialog({
    title: "更换工作目录",
    message: "更换工作目录需要重启应用，未保存的更改将丢失。确认继续？",
    kind: "confirm",
    action: async () => {
      await tauri().invoke("delete_workspace_config");
      await tauri().invoke("relaunch");
    },
  });
}

async function openWorkDirectory() {
  try {
    await api<{ opened: boolean }>("/settings/work-directory/open", "POST");
  } catch (error) {
    openDialog({ title: "无法打开工作目录", message: String(error), kind: "error" });
  }
}

type CleanupPreview = {
  preview_id: string;
  file_count: number;
  directory_count: number;
  record_count: number;
  original_videos: string[];
  estimated_bytes: number;
};

type CleanupResult = {
  deleted_files: number;
  deleted_directories: number;
  retained_files: number;
  retained_directories: number;
  deleted_records: number;
  retained_records: number;
  bytes_reclaimed: number;
};

function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

async function refreshCleanupPreview(message = "") {
  const request = ++cleanupPreviewRequest;
  cleanupPreview.value = null;
  cleanupPreviewError.value = message;
  const days = retentionDays.value;
  if (!validRetentionDays.value) {
    cleanupPreviewLoading.value = false;
    return;
  }
  cleanupPreviewLoading.value = true;
  try {
    const query = new URLSearchParams({
      clear_expired_data: String(clearExpiredData.value),
      retention_days: String(days),
    });
    const preview = await api<CleanupPreview>(`/settings/cleanup-unused-files?${query}`);
    if (request === cleanupPreviewRequest) cleanupPreview.value = preview;
  } catch (error) {
    if (request === cleanupPreviewRequest) cleanupPreviewError.value = error instanceof Error ? error.message : String(error);
  } finally {
    if (request === cleanupPreviewRequest) cleanupPreviewLoading.value = false;
  }
}

function confirmCleanup() {
  retentionDays.value = DEFAULT_RETENTION_DAYS;
  clearExpiredData.value = false;
  openDialog({
    title: "清理应用生成文件",
    message: "请核对预计清理范围。删除后无法撤销。",
    kind: "cleanup",
    action: async () => {
      const preview = cleanupPreview.value;
      if (!preview || !canConfirmCleanup.value) return dialog.value ?? undefined;
      const result = await api<CleanupResult>("/settings/cleanup-unused-files", "POST", {
        clear_expired_data: clearExpiredData.value,
        retention_days: retentionDays.value,
        preview_id: preview.preview_id,
      });
      const pending = result.retained_files > 0 || result.retained_directories > 0;
      if (result.deleted_records) void project.refreshAfterCleanup().catch(session.report);
      const records = clearExpiredData.value ? `已删除 ${result.deleted_records} 条本地业务记录。` : "";
      const remaining = pending
        ? `另有 ${result.retained_files} 个文件和 ${result.retained_directories} 个目录未能删除。${result.retained_records ? `${result.retained_records} 条业务记录已保留，可重新统计后再清理。` : ""}`
        : "";
      return {
        title: pending ? "部分清理完成" : "清理完成",
        message: `已删除 ${result.deleted_files} 个文件和 ${result.deleted_directories} 个目录，释放 ${formatBytes(result.bytes_reclaimed)}。${records}${remaining}`,
        kind: "info",
      };
    },
  });
  void refreshCleanupPreview();
}
</script>

<template>
  <section class="local-settings-panel">
    <div class="page-heading">
      <h1>本地设置</h1>
    </div>

    <ul class="list local-settings-list">
      <li class="list-row">
        <div class="local-setting-copy">
          <h2>工作目录管理</h2>
          <p>打开当前工作数据目录，或重新选择存储位置（需要重启应用）。</p>
        </div>
        <div class="local-setting-actions">
          <button class="btn btn-outline" type="button" @click="openWorkDirectory">打开目录</button>
          <button class="btn btn-outline" type="button" @click="changeWorkDirectory">更换目录</button>
        </div>
      </li>

      <li class="list-row">
        <div class="local-setting-copy">
          <h2>清理未使用文件</h2>
          <p>按文件存放天数清理应用生成文件，默认清理超过 7 天且未被引用的文件。</p>
        </div>
        <div class="local-setting-actions">
          <button class="btn btn-outline" type="button" @click="confirmCleanup">清理文件</button>
        </div>
      </li>

      <SoftwareUpdate />
    </ul>

    <AppearanceSettings />

    <div v-if="dialog" class="modal modal-open local-settings-dialog" role="presentation">
      <section
        class="modal-box"
        role="dialog"
        aria-modal="true"
        aria-labelledby="local-settings-dialog-title"
        aria-describedby="local-settings-dialog-message">
        <h2 id="local-settings-dialog-title">{{ dialog.title }}</h2>
        <p id="local-settings-dialog-message">{{ dialog.message }}</p>
        <template v-if="dialog.kind === 'cleanup'">
          <label class="label mt-4 flex w-fit whitespace-normal text-base-content">
            清理超过
            <input
              class="input input-sm w-20"
              type="number"
              min="1"
              step="1"
              :aria-invalid="!validRetentionDays"
              :value="Number.isNaN(retentionDays) ? '' : retentionDays"
              :disabled="dialogBusy"
              @input="retentionDays = ($event.target as HTMLInputElement).valueAsNumber; refreshCleanupPreview()" />
            天
          </label>
          <label class="label mt-4 flex w-fit whitespace-normal text-base-content">
            <input
              type="checkbox"
              class="checkbox checkbox-sm"
              :checked="clearExpiredData"
              :disabled="dialogBusy"
              @change="clearExpiredData = ($event.target as HTMLInputElement).checked; refreshCleanupPreview()" />
            同时清理到期记录和原始视频
          </label>
          <p v-if="clearExpiredData" class="text-error">
            清理超过所选天数的项目、任务、直播记录、录制和产物，包括下方列出的原始视频。
            期内记录依赖的资料继续保留。
          </p>
          <p v-if="cleanupPreviewError" role="status">{{ cleanupPreviewError }}</p>
          <div class="mt-4 rounded-box bg-base-200 p-3 text-sm text-base-content" role="status" aria-live="polite">
            <span v-if="!validRetentionDays" class="text-error">请输入大于或等于 1 的整数天数。</span>
            <span v-else-if="cleanupPreviewLoading">正在统计可清理文件…</span>
            <span v-else-if="cleanupPreview">
              预计清理 <strong>{{ cleanupPreview.file_count }}</strong> 个文件、{{ cleanupPreview.directory_count }} 个目录，
              <template v-if="clearExpiredData">删除 <strong>{{ cleanupPreview.record_count }}</strong> 条本地业务记录，</template>
              释放 <strong>{{ formatBytes(cleanupPreview.estimated_bytes) }}</strong>
            </span>
            <button v-else class="btn btn-sm" type="button" @click="refreshCleanupPreview()">重新统计</button>
          </div>
          <div v-if="cleanupPreview?.original_videos.length" class="mt-3 text-sm text-error">
            <p>将删除 {{ cleanupPreview.original_videos.length }} 个原始视频</p>
            <ul class="mt-1 max-h-32 overflow-y-auto break-all">
              <li v-for="path in cleanupPreview.original_videos" :key="path">{{ path }}</li>
            </ul>
          </div>
        </template>
        <div class="modal-action">
          <button v-if="dialog.action" class="btn btn-ghost" type="button" :data-autofocus="dialog.kind === 'cleanup' || undefined" :disabled="dialogBusy" @click="closeDialog">取消</button>
          <button
            v-if="dialog.action"
            class="btn btn-error"
            type="button"
            :data-autofocus="dialog.kind === 'confirm' || undefined"
            :disabled="dialogBusy || (dialog.kind === 'cleanup' && !canConfirmCleanup)"
            @click="runDialogAction">{{ dialogBusy ? "处理中…" : dialog.kind === "cleanup" ? (clearExpiredData ? "确认删除到期数据" : "确认清理") : "确认" }}</button>
          <button v-else class="btn btn-primary" type="button" data-autofocus @click="closeDialog">知道了</button>
        </div>
      </section>
      <button class="modal-backdrop" type="button" aria-label="关闭提示" @click="closeDialog"></button>
    </div>
  </section>
</template>

<style scoped>
.local-settings-panel {
  width: 100%;
}

.page-heading {
  margin-bottom: 22px;
}

.page-heading h1 {
  margin: 0;
  font-size: 24px;
}

:deep(.local-setting-copy p) {
  margin: 5px 0 0;
  color: var(--text-secondary);
  font-size: 14px;
  line-height: 1.6;
}

.local-settings-list {
  overflow: hidden;
  border: 1px solid var(--app-border);
  border-radius: 8px;
  background: var(--color-base-100);
}

.local-settings-list :deep(.list-row) {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 20px;
  padding: 20px 24px;
}

:deep(.local-setting-copy) {
  min-width: 0;
}

:deep(.local-setting-copy h2) {
  margin: 0;
  font-size: 17px;
}

:deep(.local-setting-actions) {
  display: flex;
  flex-wrap: wrap;
  flex: 0 0 auto;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  margin-left: auto;
}

.local-settings-dialog .modal-box {
  max-width: 480px;
}

.local-settings-dialog .modal-box h2 {
  margin: 0;
  font-size: 20px;
}

.local-settings-dialog .modal-box p {
  margin: 14px 0 0;
  color: var(--text-secondary);
  line-height: 1.7;
  overflow-wrap: anywhere;
}

@media (max-width: 620px) {
  .local-settings-list :deep(.list-row) {
    grid-template-columns: minmax(0, 1fr);
    align-items: stretch;
    gap: 14px;
    padding: 18px;
  }

  :deep(.local-setting-actions) {
    width: 100%;
    margin-left: 0;
    justify-content: flex-start;
    flex-wrap: wrap;
  }
}
</style>
