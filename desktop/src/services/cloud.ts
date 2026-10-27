import { api, type CloudConfig } from '../api'

export type ModelConnectionTestResult = { ok: boolean; model: string; elapsed_ms: number; message: string }

export const cloudApi = {
  load: () => api<{config:CloudConfig;secrets:Record<string,boolean>;secret_previews:Record<string,string>}>('/settings/cloud'),
  save: (config: CloudConfig) => api<CloudConfig>('/settings/cloud', 'PATCH', config),
  testModel: (config: Pick<CloudConfig, 'base_url' | 'planner_model' | 'chat_timeout_seconds'>, apiKey: string) =>
    api<ModelConnectionTestResult>('/settings/cloud/test-model', 'POST', {config, api_key:apiKey}),
  saveSecret: (name: string, value: string) => api<{saved:boolean;preview:string}>('/settings/secrets', 'POST', {name,value}),
}
