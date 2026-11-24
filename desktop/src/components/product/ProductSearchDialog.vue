<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, useId } from 'vue'
import { useRouter } from 'vue-router'
import { isProductPlatformAuthError, isXingdongliAuthError, productPlatformApi, type PlatformProduct, type XingdongliProductSort } from '../../services/productPlatform'
import { useSessionStore } from '../../stores/session'
import ProductSearchCard from './ProductSearchCard.vue'

const props = defineProps<{ keyword: string; monitorId: string; browse?: boolean }>()
const emit = defineEmits<{ close: [] }>()
const dialog = ref<HTMLDialogElement>()
const id = useId(), router = useRouter(), session = useSessionStore()
const query = ref(props.keyword.trim())
const loading = ref(false), adding = ref(false), added = ref(false)
const error = ref(''), addError = ref(''), scopeName = ref('')
const product = ref<PlatformProduct | null>(null)
const page = ref<{ products: PlatformProduct[]; start: number; length: number; total: number }>()
const products = computed(() => page.value?.products ?? (product.value ? [product.value] : []))
const canAddToShowcase = ref(false)
const canBrowse = ref(props.browse)
const sort = ref<XingdongliProductSort>({ sort_type: 'time', sort_order: 'desc' })
const sortDirection = computed(() => sort.value.sort_order === 'desc' ? { label: '降序', arrow: '↓' } : { label: '升序', arrow: '↑' })
const sortOptions = [
  { key: 'time', label: '上架时间' },
  { key: 'todaySales', label: '实时销量' },
  { key: 'sales', label: '昨日销量' },
  { key: '7Sales', label: '7 日销量' },
  { key: '30Sales', label: '30 日销量' },
] as const
let submittedKeyword = query.value, requestedStart = 0
let disposed = false
function close() { disposed = true; emit('close') }
function changeSort(key: XingdongliProductSort['sort_type']) {
  if (loading.value || adding.value) return
  const ascending = sort.value.sort_type === key && sort.value.sort_order === 'desc'
  sort.value = { sort_type: key, sort_order: ascending ? 'asc' : 'desc' }
  void search(0, submittedKeyword)
}
async function addToShowcase() {
  const platformProductId = product.value?.platform_product_id
  if (!platformProductId || !canAddToShowcase.value || adding.value || added.value) return
  adding.value = true; addError.value = ''
  try {
    await productPlatformApi.addToShowcase(platformProductId, props.monitorId)
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
async function search(start = 0, keyword = query.value.trim()) {
  if (loading.value || adding.value) return
  if (!keyword && !canBrowse.value) { error.value = '请输入查询词'; return }
  submittedKeyword = keyword; requestedStart = start
  loading.value = true; error.value = ''; addError.value = ''; added.value = false
  product.value = null; canAddToShowcase.value = false
  try {
    const result = await productPlatformApi.search(keyword, {monitor_id: props.monitorId}, start, canBrowse.value ? sort.value : undefined)
    if (disposed) return
    product.value = result.product; scopeName.value = result.scope_name
    canAddToShowcase.value = result.can_add_to_showcase
    if (result.products && result.length && result.start !== undefined && result.total !== undefined) {
      canBrowse.value = true
      page.value = {products: result.products, start: result.start, length: result.length, total: result.total}
    } else page.value = undefined
  } catch (cause) {
    if (disposed) return
    if (isXingdongliAuthError(cause)) {
      close()
      session.message = '请在云端设置中填写有效的星动力 member-token，再查询商品'
      await router.push({ path: '/settings', query: { cloudTab: 'product-platform', platform: 'xingdongli' } })
    } else if (isProductPlatformAuthError(cause)) {
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
      <div class="modal-box w-[calc(100vw-2rem)] max-w-3xl"
        :class="{ 'flex h-[min(76dvh,680px)] flex-col overflow-hidden p-4 sm:p-5': canBrowse }">
        <div class="flex shrink-0 items-start justify-between gap-4">
          <div class="min-w-0">
            <h2 :id="`${id}-title`" class="text-lg font-semibold">商品查询</h2>
          </div>
          <button class="btn btn-ghost btn-sm btn-square shrink-0" aria-label="关闭商品查询" autofocus @click="close">✕</button>
        </div>
        <form class="mt-3 flex shrink-0 items-center gap-2" @submit.prevent="search()">
          <span class="label-text shrink-0 whitespace-nowrap text-xs text-base-content/60">查询词</span>
          <input v-model="query" class="input input-bordered input-sm min-w-0 flex-1" maxlength="200"
            :disabled="loading || adding" aria-label="商品查询词" />
          <button type="submit" class="btn btn-primary btn-sm shrink-0" :disabled="loading || adding || (!canBrowse && !query.trim())">
            <span v-if="loading" class="loading loading-spinner loading-xs" aria-hidden="true"></span>
            {{ loading ? '查询中…' : canBrowse && !query.trim() ? '查看全部' : '搜索' }}
          </button>
        </form>
        <div v-if="canBrowse" class="mt-3 flex shrink-0 flex-wrap gap-1.5" role="group" aria-label="商品排序">
          <button v-for="option in sortOptions" :key="option.key" type="button" class="btn btn-xs rounded-full"
            :class="sort.sort_type === option.key ? 'btn-primary' : 'btn-soft'"
            :aria-pressed="sort.sort_type === option.key"
            :aria-label="sort.sort_type === option.key ? `${option.label}，${sortDirection.label}` : option.label"
            :disabled="loading" @click="changeSort(option.key)">
            {{ option.label }}<span v-if="sort.sort_type === option.key" aria-hidden="true">{{ sortDirection.arrow }}</span>
          </button>
        </div>
        <div v-if="loading" role="status" class="py-16 text-center text-sm text-base-content/60">
          <span class="loading loading-spinner loading-md mb-3" aria-hidden="true"></span>
          <p>正在查询商品…</p>
        </div>
        <div v-else-if="error" role="alert" class="alert alert-soft alert-error mt-6 text-sm">
          <span>{{ error }}</span><button class="btn btn-sm" @click="search(requestedStart, submittedKeyword)">重试</button>
        </div>
        <template v-else-if="products.length">
          <div v-if="page" class="mt-3 flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto overscroll-contain py-0.5 pr-2 pl-0.5"
            role="region" aria-label="商品列表" tabindex="0">
            <ProductSearchCard v-for="(item, index) in products" :key="item.id ?? index"
              :product="item" :can-add-to-showcase="canAddToShowcase" paginated class="shrink-0" />
          </div>
          <ProductSearchCard v-else-if="product" :product="product" :can-add-to-showcase="canAddToShowcase"
            :paginated="false" class="mt-6" />
          <div v-if="canAddToShowcase && product" class="mt-4 flex flex-wrap items-center justify-between gap-3">
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
          <p v-if="scopeName" class="mt-2 text-sm text-base-content/60">当前范围“{{ scopeName }}”内暂无匹配结果</p>
        </div>
        <div class="modal-action shrink-0" :class="{ 'mt-auto pt-3 items-center justify-between gap-2': canBrowse }">
          <div v-if="canBrowse" class="flex min-w-0 flex-wrap items-center gap-2">
            <template v-if="page && (page.start > 0 || page.total > page.length)">
              <button type="button" class="btn btn-sm" :disabled="loading || !!error || !page.start"
                @click="search(Math.max(0, page.start - page.length), submittedKeyword)">上一页</button>
              <span class="text-sm text-base-content/70">第 {{ Math.floor(page.start / page.length) + 1 }} 页 · 共 {{ page.total }} 件</span>
              <button type="button" class="btn btn-sm" :disabled="loading || !!error || page.start + page.length >= page.total"
                @click="search(page.start + page.length, submittedKeyword)">下一页</button>
            </template>
          </div>
          <button class="btn shrink-0" :class="{ 'btn-sm': canBrowse }" @click="close">关闭</button>
        </div>
      </div>
      <form method="dialog" class="modal-backdrop" @submit.prevent="close"><button>关闭</button></form>
    </dialog>
  </Teleport>
</template>
