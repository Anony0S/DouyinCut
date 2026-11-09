import { onUnmounted, ref, watch, type Ref } from 'vue'
import { apiRequest } from '../../api'
import type { ThumbnailAnchor } from './timelineThumbnailLayout'

export type HoverFrame = { periodId: string; at: number; key: string; message: string; anchor: ThumbnailAnchor }

export function useTimelineThumbnail(sessionId: () => string, hover: Ref<HoverFrame | null>) {
  const image = ref('')
  const message = ref('')
  const cache = new Map<string, string>()
  let timer: ReturnType<typeof setTimeout> | undefined
  let controller: AbortController | undefined
  let inFlight = false
  let generation = 0
  let disposed = false

  function clear() {
    generation++
    clearTimeout(timer)
    timer = undefined
    controller?.abort()
    image.value = ''
    for (const url of cache.values()) URL.revokeObjectURL(url)
    cache.clear()
  }
  async function load() {
    timer = undefined
    const target = hover.value
    if (!target || target.message || disposed) return
    if (inFlight) return
    const cached = cache.get(target.key)
    if (cached) { image.value = cached; message.value = ''; return }
    const version = generation
    inFlight = true
    controller = new AbortController()
    const requestController = controller
    const timeout = setTimeout(() => requestController.abort(), 10000)
    try {
      const response = await apiRequest(`/live/product-periods/${encodeURIComponent(target.periodId)}/thumbnail?at=${target.at}`, { signal: requestController.signal })
      if (!response.ok) throw new Error('preview unavailable')
      const blob = await response.blob()
      if (disposed || version !== generation) return
      const url = URL.createObjectURL(blob)
      cache.set(target.key, url)
      while (cache.size > 32) {
        const key = cache.keys().next().value!
        URL.revokeObjectURL(cache.get(key)!)
        cache.delete(key)
      }
      if (hover.value?.key === target.key) { image.value = url; message.value = '' }
    } catch {
      if (!disposed && version === generation && hover.value?.key === target.key) message.value = '该位置暂时无法预览，移动鼠标后重试'
    } finally {
      clearTimeout(timeout)
      inFlight = false
      // Follow the newest pointer position immediately after an obsolete request.
      if (!disposed && hover.value && !hover.value.message && (version !== generation || hover.value.key !== target.key)) {
        clearTimeout(timer)
        timer = setTimeout(load, 0)
      }
    }
  }
  watch(() => hover.value?.key, (_key, previousKey) => {
    image.value = ''
    const target = hover.value
    message.value = target?.message || '正在读取画面…'
    if (!target || target.message) { clearTimeout(timer); timer = undefined; return }
    const cached = cache.get(target.key)
    if (cached) { clearTimeout(timer); timer = undefined; image.value = cached; message.value = ''; return }
    // Enter immediately; coalesce later movement without resetting its deadline.
    if (!inFlight && timer === undefined) {
      if (previousKey === undefined) void load()
      else timer = setTimeout(load, 100)
    }
  })
  watch(sessionId, clear)
  onUnmounted(() => { disposed = true; clear() })
  return { image, message }
}
