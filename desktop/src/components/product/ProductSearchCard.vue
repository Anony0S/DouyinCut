<script setup lang="ts">
import { computed, ref } from 'vue'
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
  const { product, canAddToShowcase, paginated } = props
  return [
    { label: '佣金比例', value: product.commission_ratio == null ? '—' : `${product.commission_ratio}%` },
    { label: '库存', value: product.stock ?? '—' },
    canAddToShowcase
      ? { label: '实时销量', value: product.real_time_sales ?? '—' }
      : { label: '近 1 日销量', value: product.yesterday_sales ?? '—' },
    { label: '今日销量', value: paginated ? product.real_time_sales ?? '—' : '—' },
    { label: '近 30 日销量', value: product.month_sales ?? '—' },
    { label: '累计销量', value: product.total_sales ?? '—' },
    { label: '直播', value: product.has_live == null ? '—' : product.has_live ? '有直播' : '暂无直播' },
    { label: 'AI 直播', value: product.has_ai_live == null ? '—' : product.has_ai_live ? '有' : '无' },
  ].filter(fact => canAddToShowcase || fact.value !== '—')
})
const compactFacts = computed(() => [
  { label: '上架时间', value: props.product.listing_time || '平台未提供' },
  ...facts.value,
])
</script>

<template>
  <article class="card card-border bg-base-100" :class="{ 'card-sm shadow-xs shadow-base-content/10': paginated }">
    <div class="card-body" :class="paginated ? 'gap-1.5 p-2 sm:p-2.5' : 'gap-5'">
      <div class="flex" :class="paginated ? 'items-start gap-2 sm:gap-3' : 'flex-col gap-5 sm:flex-row'">
        <div class="grid aspect-square shrink-0 place-items-center overflow-hidden rounded-box bg-base-200"
          :class="paginated ? 'w-[56px]' : 'w-40 self-center sm:self-start'">
          <img v-if="cover" :src="cover" :alt="product.title || '商品封面'" class="h-full w-full object-contain"
            :loading="paginated ? 'lazy' : undefined" referrerpolicy="no-referrer" @error="imageFailed = true" />
          <span v-else class="text-base-content/50" :class="paginated ? 'text-xs' : 'text-sm'">暂无图片</span>
        </div>
        <div class="min-w-0 flex-1">
          <h3 class="break-words font-semibold" :class="paginated ? 'text-sm leading-snug' : 'text-lg leading-relaxed'">
            {{ product.title || '未提供商品名称' }}
          </h3>
          <div :class="{ 'mt-1 flex flex-wrap items-baseline gap-x-3 gap-y-1': paginated }">
            <p class="text-base-content/60" :class="paginated ? 'text-xs' : 'mt-2 text-sm'">{{ product.shop_name || '店铺名称暂无' }}</p>
            <p class="font-semibold text-primary" :class="paginated ? 'text-sm' : 'mt-4 text-2xl'">{{ price }}</p>
            <p class="break-all text-xs text-base-content/60" :class="{ 'mt-3': !paginated }">
              商品 ID：{{ product.platform_product_id || '—' }}
            </p>
          </div>
          <dl v-if="paginated" class="mt-1.5 flex flex-wrap gap-x-3 gap-y-1 text-xs">
            <div v-for="fact in compactFacts" :key="fact.label" class="flex min-w-0 items-baseline gap-1">
              <dt class="shrink-0 text-base-content/60">{{ fact.label }}</dt>
              <dd class="break-words font-medium">{{ fact.value }}</dd>
            </div>
          </dl>
        </div>
      </div>
      <dl v-if="!paginated && facts.length" class="grid grid-cols-2 gap-x-5 gap-y-4 border-t border-base-300 pt-5 sm:grid-cols-3 lg:grid-cols-4">
        <div v-for="fact in facts" :key="fact.label">
          <dt class="text-xs text-base-content/60">{{ fact.label }}</dt>
          <dd class="mt-1 break-words text-sm font-medium">{{ fact.value }}</dd>
        </div>
      </dl>
      <dl v-if="!paginated && (canAddToShowcase || product.listing_time || product.last_explained_at !== undefined)"
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
