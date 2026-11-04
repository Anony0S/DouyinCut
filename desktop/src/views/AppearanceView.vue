<script setup lang="ts">
import { onMounted, ref } from "vue";
import { appearance, setAppearance, type Appearance } from "../appearance";
import {
  defaultWindowPreferences,
  loadWindowPreferences,
  saveWindowPreferences,
  type WindowPreferences,
  type StartupWindowSize,
} from "../window-preferences";

const themes: { value: Appearance; label: string }[] = [
  { value: "system", label: "跟随系统" },
  { value: "light", label: "浅色" },
  { value: "dark", label: "深色" },
];

const windowPreferences = ref<WindowPreferences>({ ...defaultWindowPreferences });
const loadingWindowPreferences = ref(true);
const savingWindowPreferences = ref(false);
const windowPreferencesError = ref("");

onMounted(async () => {
  try {
    windowPreferences.value = await loadWindowPreferences();
  } catch (error) {
    windowPreferencesError.value = error instanceof Error ? error.message : "读取窗口设置失败。";
  } finally {
    loadingWindowPreferences.value = false;
  }
});

async function updateWindowPreferences(next: WindowPreferences) {
  if (savingWindowPreferences.value || loadingWindowPreferences.value) return;
  savingWindowPreferences.value = true;
  windowPreferencesError.value = "";
  try {
    windowPreferences.value = await saveWindowPreferences(next);
  } catch (error) {
    windowPreferencesError.value = error instanceof Error ? error.message : "保存窗口设置失败。";
  } finally {
    savingWindowPreferences.value = false;
  }
}

function rememberPositionChanged(event: Event) {
  const rememberPosition = (event.currentTarget as HTMLInputElement).checked;
  void updateWindowPreferences({ ...windowPreferences.value, rememberPosition });
}

function startupSizeChanged(event: Event) {
  const startupSize = (event.currentTarget as HTMLSelectElement).value as StartupWindowSize;
  void updateWindowPreferences({ ...windowPreferences.value, startupSize });
}
</script>

<template>
  <section class="appearance-page">
    <h1>外观</h1>
    <section class="card card-border bg-base-100 appearance-card" aria-labelledby="theme-title">
      <div class="appearance-card-heading">
        <h2 id="theme-title">主题</h2>
        <p>选择应用的配色方案</p>
      </div>
      <fieldset class="theme-options" aria-labelledby="theme-title">
        <legend class="sr-only">选择主题</legend>
        <label
          v-for="theme in themes"
          :key="theme.value"
          class="appearance-option"
          :class="{ selected: appearance === theme.value }">
          <div class="theme-preview" :class="`theme-preview-${theme.value}`" aria-hidden="true">
            <div class="preview-window">
              <div class="preview-sidebar">
                <i></i><i></i><i></i>
              </div>
              <div class="preview-content">
                <b></b><span></span><span></span><span></span>
              </div>
            </div>
          </div>
          <span class="theme-choice">
            <input
              type="radio"
              class="radio radio-primary"
              name="app-theme"
              :value="theme.value"
              :checked="appearance === theme.value"
              @change="setAppearance(theme.value)" />
            <span>{{ theme.label }}</span>
          </span>
        </label>
      </fieldset>
    </section>

    <section class="card card-border bg-base-100 appearance-card" aria-labelledby="window-title">
      <div class="appearance-card-heading">
        <h2 id="window-title">窗口</h2>
        <p>设置应用打开时的位置和大小</p>
      </div>
      <div class="window-preferences">
        <div class="window-preference-row">
          <div class="window-preference-copy">
            <strong>记住窗口位置</strong>
            <p>下次启动时恢复上次关闭前的位置</p>
          </div>
          <input
            type="checkbox"
            class="toggle"
            aria-label="记住窗口位置"
            :checked="windowPreferences.rememberPosition"
            :disabled="loadingWindowPreferences || savingWindowPreferences"
            @change="rememberPositionChanged" />
        </div>
        <label class="window-preference-field">
          <span>启动窗口大小</span>
          <select
            class="select"
            :value="windowPreferences.startupSize"
            :disabled="loadingWindowPreferences || savingWindowPreferences"
            @change="startupSizeChanged">
            <option value="standard">1440 × 810</option>
            <option value="full-hd">1920 × 1080</option>
            <option value="qhd">2560 × 1440</option>
          </select>
          <small>更改后立即生效，并用于下次启动。</small>
        </label>
        <p v-if="windowPreferencesError" class="window-preferences-error" role="alert">
          {{ windowPreferencesError }}
        </p>
      </div>
    </section>
  </section>
</template>

<style scoped>
.appearance-page {
  width: 100%;
}

.appearance-page > h1 {
  margin: 0 0 22px;
}

.appearance-card {
  padding: 24px;
}

.appearance-card-heading h2 {
  font-size: 17px;
}

.appearance-card-heading p {
  margin: 5px 0 0;
  color: var(--text-muted);
}

.window-preferences {
  display: grid;
  gap: 20px;
  margin-top: 24px;
}

.window-preference-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
}

.window-preference-copy {
  min-width: 0;
}

.window-preference-copy strong,
.window-preference-field > span {
  font-size: 14px;
  font-weight: 600;
}

.window-preference-copy p,
.window-preference-field small {
  display: block;
  margin-top: 5px;
  color: var(--text-muted);
  font-size: 12px;
  line-height: 1.6;
}

.window-preference-field {
  display: grid;
  justify-items: start;
}

.window-preference-field .select {
  width: min(100%, 280px);
  margin-top: 9px;
}

.window-preferences-error {
  margin: 0;
  color: var(--color-error);
  font-size: 12px;
}

.theme-options {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 18px;
  margin: 22px 0 0;
  padding: 0;
  border: 0;
}

.appearance-option {
  min-width: 0;
  margin: 0;
  padding: 8px;
  border: 1px solid var(--app-border);
  border-radius: 8px;
  color: var(--text);
  cursor: pointer;
  transition: border-color 120ms ease, background 120ms ease;
}

.appearance-option:hover {
  background: var(--surface-soft);
}

.appearance-option.selected {
  border-color: var(--color-primary);
}

.theme-preview {
  aspect-ratio: 16 / 9;
  overflow: hidden;
  padding: 9px;
  border-radius: 5px;
  background: #dfe3ea;
}

.preview-window {
  display: flex;
  width: 100%;
  height: 100%;
  overflow: hidden;
  border: 1px solid rgb(0 0 0 / 8%);
  border-radius: 4px;
  background: #fff;
}

.preview-sidebar {
  display: flex;
  flex: 0 0 23%;
  flex-direction: column;
  gap: 7px;
  padding: 10px 6px;
  background: #fff;
}

.preview-sidebar i,
.preview-content b,
.preview-content span {
  display: block;
  height: 5px;
  border-radius: 3px;
  background: #d6dbe4;
}

.preview-sidebar i:first-child {
  background: #4164d9;
}

.preview-content {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 7px;
  padding: 10px;
  background: #f7f9fc;
}

.preview-content b {
  width: 42%;
  height: 7px;
  margin-bottom: 3px;
  background: #aeb7c8;
}

.preview-content span:nth-child(2) {
  width: 90%;
  height: 15px;
  background: #fff;
}

.preview-content span:nth-child(3) {
  width: 74%;
}

.preview-content span:nth-child(4) {
  width: 82%;
}

.theme-preview-dark {
  background: #25262a;
}

.theme-preview-dark .preview-window,
.theme-preview-dark .preview-sidebar {
  border-color: #34353a;
  background: #111214;
}

.theme-preview-dark .preview-content {
  background: #202127;
}

.theme-preview-dark .preview-sidebar i,
.theme-preview-dark .preview-content b,
.theme-preview-dark .preview-content span {
  background: #44464e;
}

.theme-preview-dark .preview-sidebar i:first-child {
  background: #7891f2;
}

.theme-preview-dark .preview-content span:nth-child(2) {
  background: #303239;
}

.theme-preview-system {
  background: linear-gradient(90deg, #e7e9ee 50%, #24252a 50%);
}

.theme-preview-system .preview-content {
  background: linear-gradient(90deg, #f7f9fc 50%, #202127 50%);
}

.theme-preview-system .preview-content span:nth-child(2) {
  background: linear-gradient(90deg, #fff 50%, #303239 50%);
}

.theme-choice {
  display: flex;
  align-items: center;
  gap: 9px;
  margin: 10px 2px 2px;
  font-size: 14px;
}

.theme-choice .radio {
  flex: none;
}

@media (max-width: 850px) {
  .theme-options {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
