<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'

const props = defineProps<{ value: string; max: string; disabled: boolean }>()
const emit = defineEmits<{ change: [date: string] }>()
const dropdown = ref<HTMLDetailsElement>()
const trigger = ref<HTMLElement>()
const open = ref(false)

function close(restoreFocus = false) {
  if (dropdown.value) dropdown.value.open = false
  open.value = false
  if (restoreFocus) trigger.value?.focus()
}
function changeDate(event: Event) {
  const calendar = event.target as HTMLElement & { value: string }
  const date = calendar.value
  // Keep the applied value until the page successfully loads the new window.
  calendar.value = props.value
  if (props.disabled || !date || date > props.max) return
  close(true)
  emit('change', date)
}
function clear() {
  if (props.disabled) return
  close(true)
  emit('change', '')
}
function toggle() {
  if (props.disabled && dropdown.value?.open) close()
  open.value = dropdown.value?.open ?? false
}
function outside(event: PointerEvent) {
  if (open.value && !dropdown.value?.contains(event.target as Node)) close()
}
function escape(event: KeyboardEvent) {
  if (event.key === 'Escape' && open.value) {
    event.preventDefault()
    close(true)
  }
}
onMounted(() => {
  window.addEventListener('pointerdown', outside)
  window.addEventListener('keydown', escape)
})
onUnmounted(() => {
  window.removeEventListener('pointerdown', outside)
  window.removeEventListener('keydown', escape)
})
</script>

<template>
  <details ref="dropdown" class="dropdown dropdown-end dropdown-bottom" @toggle="toggle">
    <summary ref="trigger" class="btn btn-sm" aria-label="录制日期"
      :aria-expanded="open" :aria-disabled="disabled" :tabindex="disabled ? -1 : undefined"
      @click="disabled && $event.preventDefault()">
      {{ value || '选择日期' }}
      <span aria-hidden="true">▾</span>
    </summary>
    <div class="dropdown-content z-50 mt-2 w-max max-w-[calc(100vw-2rem)] rounded-box border border-base-300 bg-base-100 p-2 shadow-lg"
      :inert="disabled || undefined" :aria-busy="disabled">
      <calendar-date class="cally" aria-label="录制日期" locale="zh-CN"
        :value="value" :max="max" :focused-date="value || max" :first-day-of-week="1"
        @change="changeDate">
        <span slot="previous" aria-label="上个月">‹</span>
        <span slot="next" aria-label="下个月">›</span>
        <calendar-month />
      </calendar-date>
      <div class="border-t border-base-300 pt-2 text-right">
        <button type="button" class="btn btn-ghost btn-sm" :disabled="disabled || !value"
          aria-label="清空日期" @click="clear">清空日期</button>
      </div>
    </div>
  </details>
</template>
