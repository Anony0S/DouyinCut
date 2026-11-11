import type { LiveMonitor } from '../../services/liveMonitoring'

export function productPeriodColor(promotionId: string) {
  let hash = 2166136261
  for (const char of promotionId) hash = Math.imul(hash ^ char.charCodeAt(0), 16777619) >>> 0
  const hue = hash / 0xffffffff * 360
  return `hsl(${hue.toFixed(4)} 88% 42%)`
}

export const listenerNames: Record<string, string> = { preparing: '准备连接', connecting: '正在连接', listening: '商品消息连接已建立', retrying: '正在重试', error: '商品连接中断', stopped: '已停止' }
export const listenerBadgeClasses: Record<string, string> = { preparing: 'badge-soft badge-warning', connecting: 'badge-soft badge-info', listening: 'badge-soft badge-success', retrying: 'badge-soft badge-warning', error: 'badge-soft badge-error', stopped: 'badge-soft badge-neutral' }
export const clipNames: Record<string, string> = { processing: '生成中', completed: '待导入', failed: '生成失败', importing: '导入中', imported: '已导入' }
export const clipBadgeClasses: Record<string, string> = { processing: 'badge-soft badge-neutral', completed: 'badge-soft badge-info', failed: 'badge-soft badge-error', importing: 'badge-soft badge-warning', imported: 'badge-soft badge-success' }

export function formatLiveTime(value: number | null, timeOnly = false) {
  if (!value) return '尚未检测'
  return new Date(value * 1000).toLocaleString('zh-CN', timeOnly
    ? { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }
    : { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })
}
export function formatOffset(value: number, started: number) {
  const offset = Math.max(0, Math.floor(value - started))
  const hours = Math.floor(offset / 3600)
  return `${hours ? `${String(hours).padStart(2, '0')}:` : ''}${String(Math.floor(offset / 60) % 60).padStart(2, '0')}:${String(offset % 60).padStart(2, '0')}`
}
export function formatBytes(value: number) {
  if (value >= 1024 ** 3) return `${(value / 1024 ** 3).toFixed(2)} GB`
  return value < 1024 ** 2 ? `${(value / 1024).toFixed(0)} KB` : `${(value / 1024 ** 2).toFixed(1)} MB`
}
export const activeClipJobStates = new Set(['queued', 'running', 'pause_requested', 'paused', 'cancel_requested'])
export function liveness(monitor: LiveMonitor) {
  if (monitor.is_live === null) {
    return monitor.live_checked
      ? { label: '检测异常', classes: 'badge-soft badge-warning', status: 'status-warning' }
      : { label: '等待检测', classes: 'badge-ghost', status: 'status-neutral' }
  }
  return monitor.is_live
    ? { label: '已开播', classes: 'badge-soft badge-success', status: 'status-success' }
    : { label: '未开播', classes: 'badge-soft badge-neutral', status: 'status-neutral' }
}
export function isRecording(monitor: LiveMonitor) {
  return monitor.live_state === 'recording'
}
export function monitorNeedsAttention(monitor: LiveMonitor) {
  const latest = [...monitor.sessions].sort((a, b) => b.started - a.started)[0]
  // Product errors can only be retried while recording, so an ended session must not keep the warning alive.
  const recording = latest?.status === 'recording' ? latest : undefined
  return Boolean(monitor.error || monitor.live_state === 'error' || recording?.product_error)
}
