export async function writeClipboard(value: string): Promise<void> {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(value)
      return
    } catch {
      // Some desktop webviews only support the legacy clipboard operation.
    }
  }
  const active = document.activeElement as HTMLElement | null
  const selection = active instanceof HTMLInputElement || active instanceof HTMLTextAreaElement
    ? { start: active.selectionStart, end: active.selectionEnd, direction: active.selectionDirection }
    : null
  const input = document.createElement('textarea')
  input.value = value
  input.readOnly = true
  input.style.position = 'fixed'
  input.style.left = '-9999px'
  input.setAttribute('aria-hidden', 'true')
  try {
    document.body.appendChild(input)
    input.select()
    if (!document.execCommand('copy')) throw new Error('Clipboard unavailable')
  } finally {
    input.value = ''
    input.remove()
    active?.focus({ preventScroll: true })
    if (selection && selection.start !== null && selection.end !== null) {
      (active as HTMLInputElement | HTMLTextAreaElement).setSelectionRange(selection.start, selection.end, selection.direction || undefined)
    }
  }
}
