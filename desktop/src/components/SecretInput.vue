<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { api } from '../api'
import { writeClipboard } from '../clipboard'

const props = defineProps<{
  id: string
  modelValue: string
  saved: boolean
  secretName: string
  preview: string
  placeholder?: string
  disabled?: boolean
}>()
const emit = defineEmits<{ 'update:modelValue': [value: string] }>()
const editing = ref(Boolean(props.modelValue))
const focused = ref(false)
const revealed = ref(''), error = ref(''), loading = ref(false)
const copying = ref(false), copied = ref(false)
let copiedTimer: ReturnType<typeof setTimeout> | undefined
let generation = 0
// Saved display values never become replacement drafts.
const displayValue = computed(() => editing.value ? props.modelValue : revealed.value || props.preview)

function hide() {
  generation++
  revealed.value = ''; error.value = ''; loading.value = false
  copying.value = false; copied.value = false
  if (copiedTimer) clearTimeout(copiedTimer)
}

function finishEdit() {
  focused.value = false
  if (!props.modelValue) editing.value = false
}

function update(event: Event) {
  const input = event.target as HTMLInputElement
  const value = input.value
  // A partially edited hidden preview is not a valid replacement credential.
  if (!editing.value && !revealed.value && props.preview && value.includes('*****')) {
    input.value = displayValue.value
    error.value = '请全选后输入新密钥，或点击小眼睛查看完整值再编辑'
    return
  }
  hide()
  editing.value = true
  emit('update:modelValue', value)
}

async function toggleReveal() {
  if (revealed.value) { hide(); return }
  if (!props.saved || props.disabled || loading.value || copying.value || props.modelValue) return
  editing.value = false
  error.value = ''
  loading.value = true
  const requestGeneration = ++generation
  try {
    const result = await api<{value: string}>(`/settings/secrets/${props.secretName}/reveal`, 'POST')
    if (requestGeneration === generation) revealed.value = result.value
  } catch (e) {
    if (requestGeneration === generation) error.value = e instanceof Error ? e.message : '密钥读取失败'
  } finally {
    if (requestGeneration === generation) loading.value = false
  }
}

async function copySecret() {
  if (props.disabled || loading.value || copying.value || (!props.saved && !props.modelValue)) return
  error.value = ''; copied.value = false; copying.value = true
  if (copiedTimer) clearTimeout(copiedTimer)
  const requestGeneration = generation
  try {
    // Copy a new draft or the actual saved value; never copy the hidden preview.
    let value = props.modelValue || revealed.value
    if (!value) {
      const result = await api<{value: string}>(`/settings/secrets/${props.secretName}/reveal`, 'POST')
      value = result.value
    }
    if (requestGeneration !== generation || props.disabled) return
    await writeClipboard(value)
    if (requestGeneration === generation) {
      copied.value = true
      copiedTimer = setTimeout(() => { copied.value = false }, 2000)
    }
  } catch {
    if (requestGeneration === generation) error.value = '复制失败，请重试或查看完整值后手动复制'
  } finally {
    if (requestGeneration === generation) copying.value = false
  }
}

watch(() => props.modelValue, (value, previous) => {
  if (value) { hide(); editing.value = true }
  else if (previous) { hide(); editing.value = focused.value && !props.disabled }
})
watch(() => [props.secretName, props.preview, props.saved], () => {
  hide()
  if (!props.modelValue) editing.value = false
})
onUnmounted(hide)
</script>

<template>
  <div class="secret-input">
    <div class="secret-input-box">
      <input
        :id="id"
        :value="displayValue"
        type="text"
        autocomplete="off"
        autocapitalize="off"
        :spellcheck="false"
        :disabled="disabled"
        :placeholder="placeholder || (saved ? '输入新密钥替换，留空保留原值' : '请输入密钥')"
        @focus="focused = true"
        @blur="finishEdit"
        @input="update"
      />
      <button
        class="secret-eye"
        type="button"
        :disabled="disabled || loading || copying || !saved || Boolean(modelValue)"
        :aria-label="revealed ? '隐藏完整密钥' : '查看完整密钥'"
        :aria-pressed="Boolean(revealed)"
        :aria-controls="id"
        :title="loading ? '正在读取密钥' : revealed ? '隐藏完整密钥' : '查看完整密钥'"
        @mousedown.prevent
        @click="toggleReveal"
      >
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z" />
          <circle cx="12" cy="12" r="3" />
          <path v-if="revealed" d="m3 3 18 18" />
        </svg>
      </button>
      <button
        class="secret-copy"
        type="button"
        :disabled="disabled || loading || copying || (!saved && !modelValue)"
        :aria-label="copied ? '已复制完整密钥' : '复制完整密钥'"
        :title="copying ? '正在复制' : copied ? '已复制' : '复制完整密钥'"
        @mousedown.prevent
        @click="copySecret"
      >
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path v-if="copied" d="m5 12 4 4L19 6" />
          <template v-else>
            <rect x="8" y="8" width="12" height="12" rx="2" />
            <path d="M16 8V4a1 1 0 0 0-1-1H4a1 1 0 0 0-1 1v11a1 1 0 0 0 1 1h4" />
          </template>
        </svg>
      </button>
    </div>
    <p v-if="error" class="secret-input-error" role="alert">{{ error }}</p>
    <span class="secret-copy-status" role="status" aria-live="polite">{{ copied ? '已复制完整密钥' : '' }}</span>
  </div>
</template>
