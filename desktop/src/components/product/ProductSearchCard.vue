<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { writeClipboard } from '../../clipboard'
import LiveIcon from '../live/LiveIcon.vue'
import type { PlatformProduct } from '../../services/productPlatform'

const props = defineProps<{ product: PlatformProduct; canAddToShowcase: boolean; paginated: boolean }>()
const imageFailed = ref(false)
const cover = computed(() => {
  if (imageFailed.value || !props.product.cover) return ''
  try {
    const url = new URL(props.product.cover)
    return url.protocol === 'https:' ? url.href : ''
  } catch { return '' }
})
const price = computed(() => typeof props.product.price === 'number' ? `¥${props.product.price.toFixed(2)}` : '价格暂无')
const facts = computed(() => {
  const { product, canAddToShowcase } = props
  return [
    { label: '佣金比例', value: product.commission_ratio == null ? '—' : `${product.commission_ratio}%` },
    { label: '库存', value: product.stock ?? '—' },
    canAddToShowcase
      ? { label: '实时销量', value: product.real_time_sales ?? '—' }
      : { label: '近 1 日销量', value: product.yesterday_sales ?? '—' },
    ...(!canAddToShowcase && product.real_time_sales != null ? [{ label: '今日销量', value: product.real_time_sales }] : []),
    { label: '近 30 日销量', value: product.month_sales ?? '—' },
    { label: '累计销量', value: product.total_sales ?? '—' },
    { label: '直播', value: product.has_live == null ? '—' : product.has_live ? '有直播' : '暂无直播' },
    { label: 'AI 直播', value: product.has_ai_live == null ? '—' : product.has_ai_live ? '有' : '无' },
  ].filter(fact => canAddToShowcase || fact.value !== '—')
})
const countFormat = new Intl.NumberFormat('zh-CN')
const catalogFacts = computed(() => [
  { label: '今日销量', value: props.product.real_time_sales },
  { label: '近 1 日', value: props.product.yesterday_sales },
  { label: '近 30 日', value: props.product.month_sales },
  { label: '库存', value: props.product.stock },
])
function displayCount(value: string | number | null | undefined) {
  return typeof value === 'number' ? countFormat.format(value) : typeof value === 'string' && /^\d{1,15}$/.test(value)
    ? countFormat.format(Number(value)) : value || '—'
}
const copyStatus = ref('')
let clearStatus: ReturnType<typeof setTimeout> | undefined
async function copyProductLink() {
  const url = props.product.promotion_url
  if (!url) return
  clearTimeout(clearStatus)
  try {
    await writeClipboard(url)
    copyStatus.value = '已复制商品链接'
  } catch {
    copyStatus.value = '复制失败，请重试'
  }
  clearStatus = setTimeout(() => { copyStatus.value = '' }, 2200)
}
watch(() => props.product.cover, () => { imageFailed.value = false })
onUnmounted(() => clearTimeout(clearStatus))
</script>

<template>
  <article v-if="paginated" class="group card card-border shrink-0 rounded-2xl border-base-300 bg-base-100 transition-colors hover:border-primary/50 focus-within:border-primary/50 motion-reduce:transition-none">
    <div class="card-body gap-0 p-3.5 sm:p-4 lg:p-5">
      <div class="flex items-start gap-3 sm:gap-4">
        <div class="grid aspect-square w-16 shrink-0 place-items-center overflow-hidden rounded-xl border border-base-300 bg-base-200 sm:w-24 lg:w-28">
          <img v-if="cover" :src="cover" :alt="product.title || '商品封面'" class="h-full w-full object-contain"
            loading="lazy" referrerpolicy="no-referrer" @error="imageFailed = true" />
          <div v-else class="flex flex-col items-center gap-1 text-xs text-base-content/55">
            <LiveIcon name="bag" class="size-5" />暂无图片
          </div>
        </div>
        <div class="min-w-0 flex-1">
          <h3 class="m-0! break-words text-base! leading-snug font-bold group-hover:text-primary group-focus-within:text-primary lg:text-xl!">{{ product.title || '未提供商品名称' }}</h3>
          <div class="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1.5 text-sm text-base-content/75">
            <span v-if="product.shop_name" class="flex items-center gap-1.5 break-words">
              <LiveIcon name="store" class="size-4 shrink-0 text-base-content/50" />{{ product.shop_name }}
            </span>
            <span v-if="product.platform_product_id" class="rounded-lg bg-base-200 px-2 py-1 font-mono text-xs font-medium tracking-tight text-base-content/75">ID: {{ product.platform_product_id }}</span>
          </div>
          <p class="mt-2 flex flex-wrap items-center gap-x-1.5 text-xs text-base-content/60 sm:text-sm">
            <LiveIcon name="clock" class="size-4 shrink-0" /><span>上架时间</span><span class="tabular-nums">{{ product.listing_time || '平台未提供' }}</span>
          </p>
        </div>
      </div>
      <div class="mt-3.5 grid grid-cols-[1fr_auto] items-center gap-x-3 gap-y-3 border-t border-base-300 pt-3.5 sm:mt-4 sm:grid-cols-[auto_minmax(0,1fr)_auto] sm:pt-4 lg:gap-x-6">
        <div class="flex flex-col items-start gap-1.5 sm:min-w-20">
          <span class="text-lg font-bold text-error tabular-nums sm:text-2xl">{{ price }}</span>
          <span v-if="product.commission_ratio != null" class="badge badge-soft badge-warning badge-sm rounded-md font-medium">佣金 {{ product.commission_ratio }}%</span>
        </div>
        <dl class="col-span-2 row-start-2 grid min-w-0 grid-cols-4 gap-1 text-center sm:col-span-1 sm:row-start-auto sm:gap-2">
          <div v-for="fact in catalogFacts" :key="fact.label" class="min-w-0">
            <dt class="text-xs text-base-content/60">{{ fact.label }}</dt>
            <dd class="mt-1 break-words text-sm font-semibold tabular-nums sm:text-base" :class="{ 'text-base-content/50': fact.value === 0 || fact.value === '0' || fact.value == null }">{{ displayCount(fact.value) }}</dd>
          </div>
        </dl>
        <button type="button" class="btn btn-primary btn-soft btn-sm col-start-2 row-start-1 justify-self-end rounded-xl sm:col-start-auto sm:row-start-auto"
          :disabled="!product.promotion_url" @click="copyProductLink">
          <LiveIcon :name="copyStatus === '已复制商品链接' ? 'check' : 'copy'" class="size-4" />
          {{ product.promotion_url ? (copyStatus || '复制商品链接') : '暂无商品链接' }}
        </button>
        <span class="sr-only" role="status">{{ copyStatus }}</span>
      </div>
    </div>
  </article>
  <article v-else class="card card-border bg-base-100">
    <div class="card-body gap-5">
      <div class="flex flex-col gap-5 sm:flex-row">
        <div class="grid aspect-square w-40 shrink-0 place-items-center self-center overflow-hidden rounded-box bg-base-200 sm:self-start">
          <img v-if="cover" :src="cover" :alt="product.title || '商品封面'" class="h-full w-full object-contain"
            referrerpolicy="no-referrer" @error="imageFailed = true" />
          <span v-else class="text-sm text-base-content/50">暂无图片</span>
        </div>
        <div class="min-w-0 flex-1">
          <h3 class="break-words text-lg leading-relaxed font-semibold">{{ product.title || '未提供商品名称' }}</h3>
          <p class="mt-2 text-sm text-base-content/60">{{ product.shop_name || '店铺名称暂无' }}</p>
          <p class="mt-4 text-2xl font-semibold text-primary">{{ price }}</p>
          <p class="mt-3 break-all text-xs text-base-content/60">商品 ID：{{ product.platform_product_id || '—' }}</p>
          <button v-if="product.promotion_url !== undefined" type="button" class="btn btn-primary btn-soft btn-sm mt-3 rounded-xl"
            :disabled="!product.promotion_url" @click="copyProductLink">
            <LiveIcon :name="copyStatus === '已复制商品链接' ? 'check' : 'copy'" class="size-4" />
            {{ product.promotion_url ? (copyStatus || '复制商品链接') : '暂无商品链接' }}
          </button>
          <span class="sr-only" role="status">{{ copyStatus }}</span>
        </div>
      </div>
      <dl v-if="facts.length" class="grid grid-cols-2 gap-x-5 gap-y-4 border-t border-base-300 pt-5 sm:grid-cols-3 lg:grid-cols-4">
        <div v-for="fact in facts" :key="fact.label">
          <dt class="text-xs text-base-content/60">{{ fact.label }}</dt>
          <dd class="mt-1 break-words text-sm font-medium">{{ fact.value }}</dd>
        </div>
      </dl>
      <dl v-if="canAddToShowcase || product.listing_time || product.last_explained_at !== undefined"
        class="grid grid-cols-2 gap-x-5 border-t border-base-300 pt-4 text-sm">
        <div>
          <dt class="text-xs text-base-content/60">上架时间</dt>
          <dd class="mt-1">{{ product.listing_time || '—' }}</dd>
        </div>
        <div v-if="product.last_explained_at !== undefined">
          <dt class="text-xs text-base-content/60">最近讲解</dt>
          <dd class="mt-1">{{ product.last_explained_at || '—' }}</dd>
        </div>
      </dl>
    </div>
  </article>
</template>
