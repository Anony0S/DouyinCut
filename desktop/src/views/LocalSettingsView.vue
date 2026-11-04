<script setup lang="ts">
import { nextTick, onUnmounted, ref } from "vue";

type TauriInternals = {
  invoke: (command: string, args?: Record<string, unknown>) => Promise<unknown>;
};

function tauri(): TauriInternals {
  const internals = (window as Window & { __TAURI_INTERNALS__?: TauriInternals }).__TAURI_INTERNALS__;
  if (!internals) throw new Error("此操作需要在桌面应用中使用");
  return internals;
}
const appVersion = "0.2.101";
const checkingUpdate = ref(false);
const updateResult = ref<{ hasUpdate: boolean; message: string } | null>(null);
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

async function openLogDirectory() {
  try {
    await tauri().invoke("open_log_directory");
    openDialog({ title: "日志目录", message: "已打开应用日志目录。", kind: "info" });
  } catch (error) {
    openDialog({ title: "无法打开日志目录", message: String(error), kind: "error" });
  }
}

function clearLogs() {
  openDialog({
    title: "清理日志",
    message: "确认删除所有日志文件？此操作不可恢复。",
    kind: "confirm",
    action: async () => {
      await tauri().invoke("clear_logs");
      return { title: "日志已清理", message: "应用日志文件已删除。", kind: "info" };
    },
  });
}

async function checkForUpdate() {
  checkingUpdate.value = true;
  updateResult.value = null;
  try {
    const result = await tauri().invoke("check_for_update");
    updateResult.value = result as { hasUpdate: boolean; message: string };
    openDialog({ title: "版本更新", message: updateResult.value.message, kind: "info" });
  } catch (error) {
    updateResult.value = { hasUpdate: false, message: `检查更新失败: ${error}` };
    openDialog({ title: "检查更新失败", message: String(error), kind: "error" });
  } finally {
    checkingUpdate.value = false;
  }
}
</script>

<template>
  <section class="local-settings-panel">
    <div class="page-heading">
      <div>
        <h1>本地设置</h1>
        <p>管理本机工作目录、日志和应用版本。</p>
      </div>
    </div>

    <ul class="list local-settings-list">
      <li class="list-row">
        <div class="local-setting-copy">
          <h2>工作目录管理</h2>
          <p>重新选择工作数据存储位置，需要重启应用。</p>
        </div>
        <div class="local-setting-actions">
          <button class="btn btn-outline" type="button" @click="changeWorkDirectory">更换目录</button>
        </div>
      </li>

      <li class="list-row">
        <div class="local-setting-copy">
          <h2>日志管理</h2>
          <p>查看或清理应用运行日志。</p>
        </div>
        <div class="local-setting-actions">
          <button class="btn btn-outline" type="button" @click="openLogDirectory">打开目录</button>
          <button class="btn btn-outline btn-error" type="button" @click="clearLogs">清理日志</button>
        </div>
      </li>

      <li class="list-row local-update-row">
        <div class="local-setting-copy">
          <h2>版本更新</h2>
          <p>当前版本 <strong>v{{ appVersion }}</strong>，检查是否有新版本。</p>
        </div>
        <div class="local-setting-actions">
          <button class="btn btn-outline" type="button" :disabled="checkingUpdate" @click="checkForUpdate">
            {{ checkingUpdate ? "检查中..." : "检查更新" }}
          </button>
        </div>
      </li>
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
.local-setting-copy p {
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

.local-settings-list .list-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 20px;
  padding: 20px 24px;
}

.local-setting-copy {
  min-width: 0;
}

.local-setting-copy h2 {
  margin: 0;
  font-size: 17px;
}

.local-setting-actions {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  margin-left: auto;
}

.local-update-result {
  width: fit-content;
  margin-top: 10px;
  padding: 8px 12px;
  font-size: 13px;
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
  .local-settings-list .list-row {
    grid-template-columns: minmax(0, 1fr);
    align-items: stretch;
    gap: 14px;
    padding: 18px;
  }

  .local-setting-actions {
    width: 100%;
    margin-left: 0;
    justify-content: flex-start;
    flex-wrap: wrap;
  }
}
</style>
