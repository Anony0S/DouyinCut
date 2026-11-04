<script lang="ts">
let activeClose: ((restoreFocus?: boolean) => void) | null = null
let nextId = 0
</script>

<script setup lang="ts">
import { nextTick, onUnmounted, ref } from "vue"

type ConfirmAction = () => void | Promise<void>

const props = withDefaults(defineProps<{
  title: string
  message: string
  confirmAction: ConfirmAction
  confirmLabel?: string
  cancelLabel?: string
  disabled?: boolean
  confirmColor?: "error" | "neutral"
}>(), {
  confirmLabel: "确定删除",
  cancelLabel: "取消",
  disabled: false,
  confirmColor: "error",
})

const opened = ref(false)
const pending = ref(false)
const trigger = ref<HTMLElement | null>(null)
const popover = ref<HTMLElement | null>(null)
const position = ref<Record<string, string>>({})
const popoverId = `confirm-popover-${++nextId}`
const cancelButton = ref<HTMLButtonElement | null>(null)

function clamp(value: number, min: number, max: number) {
  return Math.min(Math.max(value, min), Math.max(min, max))
}

function updatePosition() {
  const anchor = trigger.value
  if (!anchor) return
  const rect = anchor.getBoundingClientRect()
  const viewportWidth = window.innerWidth
  const viewportHeight = window.innerHeight
  const width = Math.min(360, Math.max(0, viewportWidth - 24))
  const horizontal = clamp(rect.left, 12, viewportWidth - width - 12)
  const spaceBelow = viewportHeight - rect.bottom - 20
  const spaceAbove = rect.top - 20
  const below = spaceBelow >= 180 || spaceBelow >= spaceAbove
  const maxHeight = Math.max(0, Math.min(420, below ? spaceBelow : spaceAbove))
  position.value = {
    width: `${width}px`,
    left: `${horizontal}px`,
    ...(below ? { top: `${Math.max(12, rect.bottom + 8)}px` }
      : { bottom: `${Math.max(12, viewportHeight - rect.top + 8)}px` }),
    maxHeight: `${maxHeight}px`,
  }
}

function removeListeners() {
  window.removeEventListener("pointerdown", onOutside, true)
  window.removeEventListener("keydown", onEscape)
  window.removeEventListener("resize", updatePosition)
  window.removeEventListener("scroll", updatePosition, true)
}

function close(restoreFocus = true) {
  if (!opened.value) return
  opened.value = false
  removeListeners()
  if (activeClose === close) activeClose = null
  const target = trigger.value
  if (restoreFocus) void nextTick(() => target?.focus())
}

async function open(event?: { currentTarget?: EventTarget | null; target?: EventTarget | null }) {
  if (props.disabled || pending.value) return
  activeClose?.(false)
  const current = event?.currentTarget
  const currentElement = current && typeof current === "object" && "getBoundingClientRect" in current
    ? current as HTMLElement
    : null
  const target = event?.target
  const targetElement = target && typeof target === "object" && "closest" in target
    ? target as HTMLElement
    : null
  trigger.value = currentElement ?? targetElement?.closest("button") ?? null
  if (!trigger.value) return
  activeClose = close
  updatePosition()
  opened.value = true
  window.addEventListener("pointerdown", onOutside, true)
  window.addEventListener("keydown", onEscape)
  window.addEventListener("resize", updatePosition)
  window.addEventListener("scroll", updatePosition, true)
  await nextTick()
  updatePosition()
  cancelButton.value?.focus()
}

function onOutside(event: PointerEvent) {
  const target = event.target as Node | null
  if (target && (popover.value?.contains(target) || trigger.value?.contains(target))) return
  close()
}

function onEscape(event: KeyboardEvent) {
  if (event.key === "Escape") close()
}

async function confirm() {
  if (pending.value || props.disabled) return
  pending.value = true
  try {
    await props.confirmAction()
    close()
  } catch {
    // Callers report action failures through the existing Toast flow.
  } finally {
    pending.value = false
  }
}

onUnmounted(() => {
  if (activeClose === close) activeClose = null
  removeListeners()
})
</script>

<template>
  <span class="confirm-popover-anchor">
    <slot :open="open" :expanded="opened" :popover-id="popoverId" />
  </span>
  <Teleport to="body">
    <div
      v-if="opened"
      :id="popoverId"
      ref="popover"
      class="confirm-popover"
      :style="position"
      role="dialog"
      aria-modal="false"
      :aria-labelledby="`${popoverId}-title`"
      :aria-describedby="`${popoverId}-message`"
      @keydown.esc="close()">
      <strong :id="`${popoverId}-title`" class="confirm-popover-title">{{ title }}</strong>
      <p :id="`${popoverId}-message`" class="confirm-popover-message">{{ message }}</p>
      <div class="confirm-popover-actions">
        <button ref="cancelButton" type="button" class="btn btn-ghost btn-sm" :disabled="pending" :aria-label="cancelLabel" @click="close()">
          {{ cancelLabel }}
        </button>
        <button type="button" class="btn btn-sm" :class="confirmColor === 'error' ? 'btn-error' : 'btn-neutral'" :disabled="pending || disabled" :aria-label="confirmLabel" @click="confirm">
          {{ pending ? "处理中…" : confirmLabel }}
        </button>
      </div>
    </div>
  </Teleport>
</template>
