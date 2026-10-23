export type Asset = { id: string; name: string; path: string; duration_us: number; width: number; height: number; has_audio?: boolean; video_start_us?: number; audio_start_us?: number }
export type Product = { id: string; name: string; variant: string; confirmed: boolean }
export type Candidate = { id: string; source_asset_id: string; product_id: string; start_us: number; end_us: number; tags: string[]; reason: string; evidence: string; quality: number }
export type Clip = { source_asset_id: string; source_start_us: number; source_end_us: number; timeline_start_us: number; candidate_id: string; audio_gain_db: number }
export type Plan = { id: string; revision: number; product_id: string; target_duration_us: number; clips: Clip[]; subtitles: {start_us: number; end_us: number; text: string}[]; canvas: {width: number; height: number; fps: number}; status: string; instructions: string; warnings: string[]; schema_version: '1.0' }
export type Job = { id: string; kind: string; state: string; progress: number; error: string | null; result: Record<string, unknown> | null; payload?: Record<string, unknown>; attempts?: number; has_error_detail?: boolean }
export type JobErrorDetail = { summary?: string | null; error_source?: 'transport' | 'http_response' | 'response_validation'; exception_type?: string; stage?: string; endpoint?: string; model?: string | null; http_status?: number | null; request_id?: string | null; content_type?: string | null; request_method?: string | null; request_headers?: Record<string,string> | null; request_body?: string | null; reason?: string | null; response_body?: string | null }
export type Artifact = { id: string; plan_id: string; revision: number; format: string; path: string; video_path: string; duration_us: number }
export type CloudConfig = { region: 'cn'; base_url: string; planner_model: string; asr_model: string; oss_endpoint: string; oss_bucket: string; chat_timeout_seconds: number }

export type ProjectDraft = { product_name: string; product_info: string; instructions: string; target_duration_us: number }
export type VideoProject = { id: string; name: string; product_name: string; duration_us: number; width: number; height: number; status: string; updated_at: number; plan_id: string | null; output_duration_us: number | null }
export type ProjectPage = { items: VideoProject[]; total: number; page: number; page_size: number; pages: number }
export type ProjectDetail = { asset: Asset; draft: ProjectDraft; plans: Plan[]; candidates: Candidate[]; artifacts: Artifact[]; jobs: Job[]; latest_analysis: Job | null }

const sessionToken = import.meta.env.VITE_SESSION_TOKEN || ''

export function apiRequest(path: string, options: RequestInit = {}): Promise<Response> {
  const headers = new Headers(options.headers)
  if (sessionToken) headers.set('X-Session-Token', sessionToken)
  return fetch(`/api/v1${path}`, { ...options, headers, credentials: 'include' })
}

export async function api<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const response = await apiRequest(path, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  const data = await response.json()
  if (!response.ok) {
    const detail = typeof data.detail === 'string' ? data.detail : '参数无效，请检查输入'
    throw new Error(detail)
  }
  return data as T
}

export async function uploadVideo(file: File): Promise<Job> {
  const response = await apiRequest(`/assets/upload?filename=${encodeURIComponent(file.name)}`, {
    method: 'POST', headers: { 'Content-Type': 'application/octet-stream' }, body: file,
  })
  const result = await response.json()
  if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : '视频导入失败')
  return result as Job
}

export const seconds = (us: number) => (us / 1_000_000).toFixed(1)
export const duration = (us: number) => `${Math.floor(us / 60_000_000)}:${String(Math.floor(us / 1_000_000) % 60).padStart(2, '0')}`
