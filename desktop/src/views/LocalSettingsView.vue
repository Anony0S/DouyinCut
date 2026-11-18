<script setup lang="ts">
import { nextTick, onUnmounted, ref } from "vue";
import { api } from "../api";
import SoftwareUpdate from "../components/SoftwareUpdate.vue";

type TauriInternals = {
  invoke: (command: string, args?: Record<string, unknown>) => Promise<unknown>;
};

function tauri(): TauriInternals {
  const internals = (window as Window & { __TAURI_INTERNALS__?: TauriInternals }).__TAURI_INTERNALS__;
  if (!internals) throw new Error("此操作需要在桌面应用中使用");
  return internals;
}
type DialogState = { title: string; message: string; kind: "confirm" | "info" | "error"; action?: () => Promise<void | DialogState> };
const dialog = ref<DialogState | null>(null);
const dialogBusy = ref(false);

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
    dialog.value = { title: "操作失败", message: String(error), kind: "error" };
  } finally {
    dialogBusy.value = false;
  }
}

function onKeydown(event: KeyboardEvent) {
  if (event.key === "Escape") closeDialog();
}
window.addEventListener("keydown", onKeydown);
onUnmounted(() => window.removeEventListener("keydown", onKeydown));

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

type CleanupResult = {
  deleted_files: number;
  deleted_directories: number;
  retained_files: number;
  bytes_reclaimed: number;
};

function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

function confirmCleanup() {
  openDialog({
    title: "清理七天前未使用的文件",
    message: "将清理应用生成目录中超过七天且已无项目或任务引用的文件，以及超期的未完成临时文件。原始视频、直播录制、仍被引用的导出和封面、日志、数据库与设置都会保留。此操作无法撤销。",
    kind: "confirm",
    action: async () => {
      const result = await api<CleanupResult>("/settings/cleanup-unused-files", "POST");
      return {
        title: "清理完成",
        message: `已删除 ${result.deleted_files} 个文件和 ${result.deleted_directories} 个目录，释放 ${formatBytes(result.bytes_reclaimed)}。${result.retained_files ? `另有 ${result.retained_files} 个文件因占用或权限原因保留。` : ""}`,
        kind: "info",
      };
    },
  });
}
</script>

<template>
  <section class="local-settings-panel">
    <div class="page-heading">
      <div>
        <h1>本地设置</h1>
        <p>管理本机工作目录、清理未使用文件和应用版本。</p>
      </div>
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
          <p>清理应用生成目录中超过七天且没有项目或任务引用的文件，释放本机空间。</p>
        </div>
        <div class="local-setting-actions">
          <button class="btn btn-outline" type="button" @click="confirmCleanup">清理文件</button>
        </div>
      </li>

      <SoftwareUpdate />
    </ul>

    <div v-if="dialog" class="modal modal-open local-settings-dialog" role="presentation">
      <section
        class="modal-box"
        role="dialog"
        aria-modal="true"
        aria-labelledby="local-settings-dialog-title"
        aria-describedby="local-settings-dialog-message">
        <h2 id="local-settings-dialog-title">{{ dialog.title }}</h2>
        <p id="local-settings-dialog-message">{{ dialog.message }}</p>
        <div class="modal-action">
          <button v-if="dialog.kind === 'confirm'" class="btn btn-ghost" type="button" :disabled="dialogBusy" @click="closeDialog">取消</button>
          <button
            v-if="dialog.kind === 'confirm'"
            class="btn btn-error"
            type="button"
            data-autofocus
            :disabled="dialogBusy"
            @click="runDialogAction">{{ dialogBusy ? "处理中…" : "确认" }}</button>
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

.page-heading p,
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
