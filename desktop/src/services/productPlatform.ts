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

export type XingdouProduct = {
  id: string | null
  platform_product_id: string | null
  title: string | null
  price: number | null
  stock: string | null
  cover: string | null
  total_sales: string | null
  real_time_sales: string | null
  month_sales: string | null
  commission_ratio: number | null
  shop_name: string | null
  has_live: boolean | null
  has_ai_live: boolean | null
  promotion_start: string | null
  promotion_end: string | null
}

export const productPlatformApi = {
  status: () => api<ProductPlatformStatus>('/product-platform/status'),
  sendCode: (mobile: string) => api<{sent: boolean; message: string; retry_after_seconds: number}>('/product-platform/send-code', 'POST', {mobile}),
  login: (mobile: string, smsCode: string) => api<ProductPlatformStatus>('/product-platform/login', 'POST', {mobile, sms_code: smsCode}),
  logout: () => api<ProductPlatformStatus>('/product-platform/logout', 'POST'),
  selectScope: (mainIpId: string) => api<ProductPlatformStatus>('/product-platform/scope', 'POST', {main_ip_id: mainIpId}),
  search: (keyword: string) => api<{product: XingdouProduct | null; scope_name: string}>('/product-platform/products/search', 'POST', {keyword}),
}

export function isProductPlatformAuthError(error: unknown): boolean {
  return error instanceof ApiError && error.code === 'PRODUCT_PLATFORM_AUTH_REQUIRED'
}
