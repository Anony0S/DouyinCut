<script setup lang="ts">
import { appearance, setAppearance, type Appearance } from "../appearance";

const themes: { value: Appearance; label: string }[] = [
  { value: "system", label: "跟随系统" },
  { value: "light", label: "浅色" },
  { value: "dark", label: "深色" },
];
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
