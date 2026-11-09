<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, useId } from 'vue'
import { useRouter } from 'vue-router'
import { isProductPlatformAuthError, productPlatformApi, type XingdouProduct } from '../../services/productPlatform'
import { useSessionStore } from '../../stores/session'

const props = defineProps<{ keyword: string }>()
const emit = defineEmits<{ close: [] }>()
const dialog = ref<HTMLDialogElement>()
const id = useId(), router = useRouter(), session = useSessionStore()
const query = ref(props.keyword.trim())
const loading = ref(false), adding = ref(false), added = ref(false)
const error = ref(''), addError = ref(''), scopeName = ref(''), imageFailed = ref(false)
const product = ref<XingdouProduct | null>(null)
let disposed = false
const cover = computed(() => {
  if (imageFailed.value || !product.value?.cover) return ''
  try {
    const url = new URL(product.value.cover)
    return url.protocol === 'https:' ? url.href : ''
  } catch { return '' }
})
const price = computed(() => typeof product.value?.price === 'number' ? `¥${product.value.price.toFixed(2)}` : '价格暂无')
const facts = computed(() => product.value ? [
  { label: '佣金比例', value: product.value.commission_ratio == null ? '—' : `${product.value.commission_ratio}%` },
  { label: '库存', value: product.value.stock ?? '—' },
  { label: '实时销量', value: product.value.real_time_sales ?? '—' },
  { label: '近 30 日销量', value: product.value.month_sales ?? '—' },
  { label: '累计销量', value: product.value.total_sales ?? '—' },
  { label: '直播', value: product.value.has_live == null ? '—' : product.value.has_live ? '有直播' : '暂无直播' },
  { label: 'AI 直播', value: product.value.has_ai_live == null ? '—' : product.value.has_ai_live ? '有' : '无' },
] : [])
function close() { disposed = true; emit('close') }
async function addToShowcase() {
  const platformProductId = product.value?.platform_product_id
  if (!platformProductId || adding.value || added.value) return
  adding.value = true; addError.value = ''
  try {
    await productPlatformApi.addToShowcase(platformProductId)
    if (!disposed) added.value = true
  } catch (cause) {
    if (disposed) return
    if (isProductPlatformAuthError(cause)) {
      close()
      session.message = '星斗商品平台未登录或登录已失效，请手动登录后重新添加'
      await router.push({ path: '/settings', query: { cloudTab: 'product-platform' } })
    } else addError.value = cause instanceof Error ? cause.message : '添加橱窗失败，请重试'
  } finally { if (!disposed) adding.value = false }
}
async function search() {
  if (loading.value || adding.value) return
  const keyword = query.value.trim()
  if (!keyword) { error.value = '请输入查询词'; return }
  loading.value = true; error.value = ''; addError.value = ''; added.value = false
  product.value = null; imageFailed.value = false
  try {
    const result = await productPlatformApi.search(keyword)
    if (disposed) return
    product.value = result.product; scopeName.value = result.scope_name
  } catch (cause) {
    if (disposed) return
    if (isProductPlatformAuthError(cause)) {
      // Close before navigating so no modal backdrop survives a route change.
      close()
      session.message = '星斗商品平台未登录或登录已失效，请手动登录后重新查询'
      await router.push({ path: '/settings', query: { cloudTab: 'product-platform' } })
    } else error.value = cause instanceof Error ? cause.message : '商品查询失败，请重试'
  } finally { if (!disposed) loading.value = false }
}
onMounted(() => { dialog.value?.showModal(); void search() })
onUnmounted(() => { disposed = true; dialog.value?.close() })
</script>

<template>
  <Teleport to="body">
    <dialog ref="dialog" class="modal" :aria-labelledby="`${id}-title`" @cancel.prevent="close">
      <div class="modal-box w-[calc(100vw-2rem)] max-w-3xl">
        <div class="flex items-start justify-between gap-4">
          <div class="min-w-0">
            <h2 :id="`${id}-title`" class="text-lg font-semibold">商品查询</h2>
          </div>
          <button class="btn btn-ghost btn-sm btn-square shrink-0" aria-label="关闭商品查询" autofocus @click="close">✕</button>
        </div>
        <form class="mt-3 flex items-center gap-2" @submit.prevent="search">
          <span class="label-text shrink-0 whitespace-nowrap text-xs text-base-content/60">查询词</span>
          <input v-model="query" class="input input-bordered input-sm min-w-0 flex-1" maxlength="200"
            :disabled="loading || adding" aria-label="商品查询词" />
          <button type="submit" class="btn btn-primary btn-sm shrink-0" :disabled="loading || adding || !query.trim()">
            <span v-if="loading" class="loading loading-spinner loading-xs" aria-hidden="true"></span>
            {{ loading ? '查询中…' : '搜索' }}
          </button>
        </form>
        <div v-if="loading" role="status" class="py-16 text-center text-sm text-base-content/60">
          <span class="loading loading-spinner loading-md mb-3" aria-hidden="true"></span>
          <p>正在查询商品…</p>
        </div>
        <div v-else-if="error" role="alert" class="alert alert-soft alert-error mt-6 text-sm">
          <span>{{ error }}</span><button class="btn btn-sm" @click="search">重试</button>
        </div>
        <template v-else-if="product">
          <article class="card card-border bg-base-100 mt-6">
            <div class="card-body gap-5">
              <div class="flex flex-col gap-5 sm:flex-row">
                <div class="grid aspect-square w-40 shrink-0 place-items-center self-center overflow-hidden rounded-box bg-base-200 sm:self-start">
                  <img v-if="cover" :src="cover" :alt="product.title || '商品封面'" class="h-full w-full object-contain"
                    referrerpolicy="no-referrer" @error="imageFailed = true" />
                  <span v-else class="text-sm text-base-content/50">暂无图片</span>
                </div>
                <div class="min-w-0 flex-1">
                  <h3 class="break-words text-lg font-semibold leading-relaxed">{{ product.title || '未提供商品名称' }}</h3>
                  <p class="mt-2 text-sm text-base-content/60">{{ product.shop_name || '店铺名称暂无' }}</p>
                  <p class="mt-4 text-2xl font-semibold text-primary">{{ price }}</p>
                  <p class="mt-3 break-all text-xs text-base-content/60">商品 ID：{{ product.platform_product_id || '—' }}</p>
                </div>
              </div>
              <dl class="grid grid-cols-2 gap-x-5 gap-y-4 border-t border-base-300 pt-5 sm:grid-cols-3 lg:grid-cols-4">
                <div v-for="fact in facts" :key="fact.label">
                  <dt class="text-xs text-base-content/60">{{ fact.label }}</dt>
                  <dd class="mt-1 break-words text-sm font-medium">{{ fact.value }}</dd>
                </div>
              </dl>
              <div class="border-t border-base-300 pt-4 text-sm">
                <p class="text-xs text-base-content/60">活动时间</p>
                <p class="mt-1">{{ product.promotion_start || '—' }} 至 {{ product.promotion_end || '—' }}</p>
              </div>
            </div>
          </article>
          <div class="mt-4 flex flex-wrap items-center justify-between gap-3">
            <p class="text-xs text-base-content/60">仅展示平台返回的第一条商品，请核对名称与规格。查询范围：{{ scopeName || '当前合作范围' }}。</p>
            <button type="button" class="btn btn-primary btn-sm" :disabled="adding || added || !product.platform_product_id" @click="addToShowcase">
              <span v-if="adding" class="loading loading-spinner loading-xs" aria-hidden="true"></span>
              {{ added ? '已添加橱窗' : adding ? '添加中…' : '添加橱窗' }}
            </button>
          </div>
          <p v-if="added" role="status" class="alert alert-soft alert-success mt-3 text-sm">已添加到当前合作范围的橱窗。</p>
          <p v-if="addError" role="alert" class="alert alert-soft alert-error mt-3 text-sm">{{ addError }}</p>
        </template>
        <div v-else role="status" class="py-16 text-center">
          <p class="font-medium">未查询到商品</p>
          <p class="mt-2 text-sm text-base-content/60">当前范围“{{ scopeName || '合作商品' }}”内暂无匹配结果</p>
        </div>
        <div class="modal-action"><button class="btn" @click="close">关闭</button></div>
      </div>
      <form method="dialog" class="modal-backdrop" @submit.prevent="close"><button>关闭</button></form>
    </dialog>
  </Teleport>
</template>
