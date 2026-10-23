import { api, type CloudConfig } from '../api'

export const cloudApi = {
  load: () => api<{config:CloudConfig;secrets:Record<string,boolean>;secret_previews:Record<string,string>}>('/settings/cloud'),
  save: (config: CloudConfig) => api<CloudConfig>('/settings/cloud', 'PATCH', config),
  saveSecret: (name: string, value: string) => api<{saved:boolean;preview:string}>('/settings/secrets', 'POST', {name,value}),
}
