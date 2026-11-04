import { api, type Job } from '../api'

export type LiveClipJob = { id:string; state:string; error:string|null }
export type LiveSegment = { id:string; path:string; segment_index:number; status:string; size_bytes:number; duration_us:number|null; asset_id:string|null; import_job_id:string|null; clip_job:LiveClipJob|null }
export type LiveProductEvent = { id:string; promotion_id:string; product_name:string; explain_type:number|null; event_time:number; received_at:number; source:string }
export type LiveProductClip = { id:string; segment_id:string; segment_index:number; promotion_id:string; product_name:string; start:number; end:number; path:string; status:string; size_bytes:number; duration_us:number|null; asset_id:string|null; import_job_id:string|null; error:string }
export type LiveCurrentProduct = { promotion_id:string; product_name:string; event_time:number; received_at:number }
export type LiveProductListener = { status:string; detail:string; updated:number }
export type LiveProductCatalogError = { detail:Record<string,unknown>; updated:number }
export type LiveSession = { id:string; status:string; started:number; ended:number|null; end_time_source:'observed'|'recovery_check'|null; segments:LiveSegment[]; product_clips:LiveProductClip[]; current_product:LiveCurrentProduct|null; product_events:LiveProductEvent[]; product_listener:LiveProductListener|null; product_catalog_error:LiveProductCatalogError|null }
export type LiveMonitorSettings = { auto_record?:boolean; notify_live?:boolean }
export type LiveMonitor = { id:string; url:string; name:string; auto_record:boolean; notify_live:boolean; record_requested:boolean; record_held:boolean; retry_blocked:boolean; live_state:string; is_live:boolean|null; live_checked:number|null; last_checked:number|null; error:string|null; sessions:LiveSession[] }

export const liveMonitoringApi = {
  session: (monitorId: string, sessionId: string) => api<LiveSession | null>(`/live/monitors/${encodeURIComponent(monitorId)}/sessions/${encodeURIComponent(sessionId)}`),
  list: (window: { start: number; end: number; includeEndRecording: boolean }) => api<LiveMonitor[]>(`/live/monitors?window_start=${window.start}&window_end=${window.end}&include_end_recording=${window.includeEndRecording}`),
  create: (url:string,name:string) => api<LiveMonitor>('/live/monitors','POST',{url,name}),
  updateSettings: (id:string,settings:LiveMonitorSettings) => api<LiveMonitor>(`/live/monitors/${encodeURIComponent(id)}`,'PATCH',settings),
  setRecording: (id:string,action:'start'|'stop') => api<LiveMonitor>(`/live/monitors/${encodeURIComponent(id)}/recording`,'POST',{action}),
  remove: (id:string) => api<{deleted_monitor_id:string;recordings_retained:boolean}>(`/live/monitors/${encodeURIComponent(id)}`,'DELETE'),
  importSegment: (id:string) => api<Job|{asset_id:string;state:'imported'}>(`/live/segments/${encodeURIComponent(id)}/import`,'POST'),
  openSegment: (id:string,reveal=false) => api<{opened:boolean;revealed:boolean;path:string}>(`/live/segments/${encodeURIComponent(id)}/open`,'POST',{reveal}),
  importProductClip: (id:string) => api<Job|{asset_id:string;state:'imported'}>(`/live/product-clips/${encodeURIComponent(id)}/import`,'POST'),
  openProductClip: (id:string,reveal=false) => api<{opened:boolean;revealed:boolean;path:string}>(`/live/product-clips/${encodeURIComponent(id)}/open`,'POST',{reveal}),
  deleteProductClip: (id:string) => api<{deleted_clip_id:string;file_deleted:boolean;project_file_retained:boolean}>(`/live/product-clips/${encodeURIComponent(id)}`,'DELETE'),
  splitSegment: (id:string) => api<Job>(`/live/segments/${encodeURIComponent(id)}/product-clips`,'POST'),
  deleteSegment: (id:string) => api<{deleted_segment_id:string;file_deleted:boolean}>(`/live/segments/${encodeURIComponent(id)}`,'DELETE'),
}
