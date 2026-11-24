import { api, ApiError } from '../api'

export type ProductPlatformStatus = {
  platform: 'xingdou'
  logged_in: boolean
  mobile: string
  member_name: string
  member_id: string | null
  main_ip_id: string | null
  main_ip_name: string
  scopes: {id: string; name: string}[]
  last_validated_at: number | null
}

export type PlatformProduct = {
  id: string | null
  platform_product_id: string | null
  title: string | null
  price: number | null
  stock: string | number | null
  cover: string | null
  total_sales: string | null
  real_time_sales: string | number | null
  month_sales: string | number | null
  commission_ratio: number | null
  shop_name: string | null
  has_live: boolean | null
  has_ai_live: boolean | null
  listing_time: string | null
  last_explained_at?: string | null
  yesterday_sales?: string | number | null
}

export type RoomPlatformBinding = { platform: 'xingdou' | 'daduoduo' | 'xingdongli'; ip_id: string }
export type XingdongliStatus = Pick<ProductPlatformStatus, 'logged_in' | 'member_name' | 'mobile' | 'scopes' | 'last_validated_at'>
type ProductSearchResult = {
  product: PlatformProduct | null
  scope_name: string
  can_add_to_showcase: boolean
  products?: PlatformProduct[]
  total?: number
  start?: number
  length?: number
}
export type CatalogStatus = {
  state: 'idle' | 'queued' | 'running' | 'failed'; downloaded: number; total: number | null
  count: number; synced_at: number | null; error: string
}
export type DaduoduoSettings = {
  rooms: (CatalogStatus & {id: string; name: string})[]
}

const roomCatalogPath = (id: string) => `/live/monitors/${encodeURIComponent(id)}/product-catalog`

export const productPlatformApi = {
  status: () => api<ProductPlatformStatus>('/product-platform/status'),
  sendCode: (mobile: string) => api<{sent: boolean; message: string; retry_after_seconds: number}>('/product-platform/send-code', 'POST', {mobile}),
  login: (mobile: string, smsCode: string) => api<ProductPlatformStatus>('/product-platform/login', 'POST', {mobile, sms_code: smsCode}),
  logout: () => api<ProductPlatformStatus>('/product-platform/logout', 'POST'),
  selectScope: (mainIpId: string) => api<ProductPlatformStatus>('/product-platform/scope', 'POST', {main_ip_id: mainIpId}),
  search: (keyword: string, context: {monitor_id: string} | {asset_id: string}, start = 0) =>
    api<ProductSearchResult>('/product-platform/products/search', 'POST', {keyword, ...context, ...(start ? {start} : {})}),
  images: (assetId:string, productId: string, refresh=false) => api<{images:{id:string;url:string}[];cached:boolean}>('/product-platform/products/images', 'POST', {asset_id:assetId,product_id:productId,refresh}),
  addToShowcase: (platformProductId: string, monitorId: string) =>
    api<{added: boolean}>('/product-platform/products/add-to-showcase', 'POST',
      {platform_product_id: platformProductId, monitor_id: monitorId}),
  daduoduoSettings: () => api<DaduoduoSettings>('/product-platform/daduoduo'),
  catalogStatus: (id: string) => api<CatalogStatus>(roomCatalogPath(id)),
  updateCatalog: (id: string) => api<CatalogStatus>(roomCatalogPath(id), 'POST'),
  xingdongliStatus: () => api<XingdongliStatus>('/product-platform/xingdongli'),
  connectXingdongli: (member_token: string) =>
    api<XingdongliStatus>('/product-platform/xingdongli/connect', 'POST', {member_token}),
  refreshXingdongli: () => api<XingdongliStatus>('/product-platform/xingdongli/refresh', 'POST'),
  logoutXingdongli: () => api<XingdongliStatus>('/product-platform/xingdongli/logout', 'POST'),
}

export function isProductPlatformAuthError(error: unknown): boolean {
  return error instanceof ApiError && error.code === 'PRODUCT_PLATFORM_AUTH_REQUIRED'
}

export function isXingdongliAuthError(error: unknown): boolean {
  return error instanceof ApiError && error.code === 'XINGDONGLI_AUTH_REQUIRED'
}

export function needsPlatformIp(binding: RoomPlatformBinding | null) {
  return !!binding && binding.platform !== 'xingdou' && !binding.ip_id.trim()
}
