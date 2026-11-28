import type { LiveClipProjectStatus, LiveMonitor, LiveProductClip } from '../../services/liveMonitoring'

export function productPeriodColor(promotionId: string) {
  let hash = 2166136261
  for (const char of promotionId) hash = Math.imul(hash ^ char.charCodeAt(0), 16777619) >>> 0
  const hue = hash / 0xffffffff * 360
  return `hsl(${hue.toFixed(4)} 88% 42%)`
}

export const listenerNames: Record<string, string> = { preparing: '准备连接', connecting: '正在连接', listening: '商品消息连接已建立', retrying: '正在重试', error: '商品连接中断', stopped: '已停止' }
export const listenerBadgeClasses: Record<string, string> = { preparing: 'badge-soft badge-warning', connecting: 'badge-soft badge-info', listening: 'badge-soft badge-success', retrying: 'badge-soft badge-warning', error: 'badge-soft badge-error', stopped: 'badge-soft badge-neutral' }
type ClipBadge = { label: string; color: string }
const clipBadges: Record<string, ClipBadge> = {
  processing: { label: '生成中', color: 'badge-neutral' },
  completed: { label: '待导入', color: 'badge-info' },
  failed: { label: '生成失败', color: 'badge-error' },
  importing: { label: '导入中', color: 'badge-warning' },
}

const clipProjectBadges: Record<LiveClipProjectStatus, ClipBadge> = {
  roughcut_pending: { label: '已导入 · 待粗剪', color: 'badge-neutral' },
  roughcut_queued: { label: '粗剪排队中', color: 'badge-info' },
  roughcut_running: { label: '粗剪中', color: 'badge-info' },
  roughcut_pause_requested: { label: '粗剪暂停中', color: 'badge-warning' },
  roughcut_paused: { label: '粗剪暂停', color: 'badge-warning' },
  roughcut_cancel_requested: { label: '粗剪取消中', color: 'badge-warning' },
  roughcut_cancelled: { label: '粗剪已取消', color: 'badge-neutral' },
  roughcut_failed: { label: '粗剪失败', color: 'badge-error' },
  roughcut_completed: { label: '粗剪已完成', color: 'badge-success' },
  draft_queued: { label: '剪映草稿排队中', color: 'badge-info' },
  draft_running: { label: '剪映草稿生成中', color: 'badge-info' },
  draft_pause_requested: { label: '剪映草稿暂停中', color: 'badge-warning' },
  draft_paused: { label: '剪映草稿已暂停', color: 'badge-warning' },
  draft_cancel_requested: { label: '剪映草稿取消中', color: 'badge-warning' },
  draft_failed: { label: '剪映草稿生成失败', color: 'badge-error' },
  draft_succeeded: { label: '已生成剪映草稿', color: 'badge-success' },
  project_missing: { label: '项目已删除', color: 'badge-neutral' },
}

export function productClipBadge(clip: LiveProductClip): ClipBadge {
  if (clip.project_status) return clipProjectBadges[clip.project_status]
  return clipBadges[clip.status] || { label: clip.status, color: 'badge-neutral' }
}

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
