<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import LiveIcon from '../components/live/LiveIcon.vue'
import ProductSearchCard from '../components/product/ProductSearchCard.vue'
import { isXingdongliAuthError, productPlatformApi, type PlatformProduct, type XingdongliProductSort } from '../services/productPlatform'

const props = defineProps<{ monitorId: string }>()
const invoke = (window as Window & {
  __TAURI_INTERNALS__?: { invoke: (command: string, args?: { pinned: boolean }) => Promise<boolean> }
}).__TAURI_INTERNALS__?.invoke
const pinned = ref(false), pinBusy = ref(false), pinError = ref('')
const pinTitle = computed(() => pinned.value ? '取消窗口置顶' : '置顶窗口')
const query = ref(''), scopeName = ref(''), error = ref(''), loading = ref(false)
const page = ref<{ products: PlatformProduct[]; start: number; length: number; total: number }>()
const pageCount = computed(() => page.value ? Math.ceil(page.value.total / page.value.length) : 0)
const pageNumber = computed(() => page.value?.total ? Math.floor(page.value.start / page.value.length) + 1 : 0)
const list = ref<HTMLElement>()
const sort = ref<XingdongliProductSort>({ sort_type: 'time', sort_order: 'desc' })
const sortDirection = computed(() => sort.value.sort_order === 'desc' ? { label: '降序', arrow: '↓' } : { label: '升序', arrow: '↑' })
const sortOptions = [
  { key: 'time', label: '上架时间' },
  { key: 'todaySales', label: '实时销量' },
  { key: 'sales', label: '昨日销量' },
  { key: '7Sales', label: '7 日销量' },
  { key: '30Sales', label: '30 日销量' },
] as const
let submittedKeyword = '', requestedStart = 0

function changeSort(key: XingdongliProductSort['sort_type']) {
  if (loading.value) return
  const ascending = sort.value.sort_type === key && sort.value.sort_order === 'desc'
  sort.value = { sort_type: key, sort_order: ascending ? 'asc' : 'desc' }
  void search(0, submittedKeyword)
}

async function search(start = 0, keyword = query.value.trim()) {
  if (loading.value || !props.monitorId) return
  submittedKeyword = keyword
  requestedStart = start
  loading.value = true
  error.value = ''
  if (list.value) list.value.scrollTop = 0
  try {
    const result = await productPlatformApi.search(keyword, { monitor_id: props.monitorId }, start, sort.value)
    if (!result.products || !result.length || result.start === undefined || result.total === undefined) {
      throw new Error('该直播间未绑定星动力商品平台。')
    }
    scopeName.value = result.scope_name
    page.value = { products: result.products, start: result.start, length: result.length, total: result.total }
  } catch (cause) {
    error.value = isXingdongliAuthError(cause)
      ? '星动力登录已失效。请在主窗口的「云端设置 → 平台 → 星动力」更新登录信息，再点击重试。'
      : cause instanceof Error ? cause.message : '商品查询失败，请重试'
  } finally {
    loading.value = false
  }
}

async function updatePin(value?: boolean) {
  if (!invoke || pinBusy.value) return
  pinBusy.value = true
  pinError.value = ''
  try {
    pinned.value = await invoke('xingdongli_products_always_on_top', value === undefined ? undefined : { pinned: value })
  } catch {
    pinError.value = '无法更新窗口置顶状态，请重试。'
  } finally {
    pinBusy.value = false
  }
}

onMounted(() => { void search(); void updatePin() })
</script>

<template>
  <div role="main" class="mx-auto flex h-dvh max-w-[100rem] flex-col gap-3.5 px-3 py-3.5 sm:gap-4 sm:px-5 sm:py-5 lg:gap-5 lg:px-6 lg:py-6">
    <header class="mb-0! flex shrink-0 flex-wrap items-center justify-between gap-2.5">
      <div class="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1.5">
        <div class="grid size-9 shrink-0 place-items-center rounded-xl bg-primary text-primary-content shadow-sm sm:size-11">
          <LiveIcon name="bolt" class="size-5 sm:size-6" />
        </div>
        <h1 class="text-xl font-bold tracking-tight sm:text-2xl lg:text-3xl">星动力商品列表</h1>
        <span v-if="page" class="badge h-6 rounded-full border-0 bg-base-300 px-2.5 text-xs text-base-content/80" aria-live="polite">共 {{ page.total }} 件商品</span>
      </div>
      <div v-if="scopeName || invoke" class="ml-auto flex min-w-0 max-w-full items-center gap-2">
        <div v-if="scopeName" class="flex min-w-0 max-w-60 items-center gap-2 rounded-full border border-base-300 bg-base-100 px-2.5 py-1.5 sm:pr-3.5">
          <div class="avatar avatar-placeholder shrink-0" aria-hidden="true">
            <div class="w-7 rounded-full bg-warning/30 text-base-content"><span class="text-xs font-bold">{{ scopeName.slice(0, 1) }}</span></div>
          </div>
          <span class="truncate text-sm font-semibold" :title="scopeName">{{ scopeName }}</span>
        </div>
        <button v-if="invoke" type="button" class="btn btn-sm btn-square shrink-0 rounded-xl"
          :class="pinned ? 'btn-primary btn-soft' : 'btn-ghost border-base-300 bg-base-100'"
          aria-label="窗口置顶" :aria-pressed="pinned" :title="pinTitle" :disabled="pinBusy"
          @click="updatePin(!pinned)">
          <LiveIcon name="pin" class="size-4" :class="{ '-rotate-45': !pinned }" />
        </button>
      </div>
    </header>
    <p v-if="pinError" role="alert" class="alert alert-soft alert-error shrink-0 text-sm">{{ pinError }}</p>
    <p v-if="!monitorId" role="alert" class="alert alert-soft alert-error">请在直播监控页选择直播间后打开商品列表。</p>
    <template v-else>
      <div class="card card-border shrink-0 rounded-2xl border-base-300 bg-base-100">
        <div class="card-body gap-3 p-3 sm:gap-4 sm:p-4 lg:p-5">
          <form class="flex items-center gap-2.5 sm:gap-3.5" @submit.prevent="search()">
            <label for="xingdongli-query" class="sr-only m-0 shrink-0 text-sm text-base-content/70 sm:not-sr-only">关键词</label>
            <div class="input input-sm min-w-0 flex-1 gap-2.5 border-base-300 bg-base-200 lg:input-md">
              <LiveIcon name="search" class="size-4 shrink-0 text-base-content/55" />
              <input id="xingdongli-query" v-model="query" type="search" class="min-w-0 border-0 bg-transparent p-0" maxlength="200"
                :disabled="loading" placeholder="搜索商品，留空查看全部" />
            </div>
            <button type="submit" class="btn btn-primary btn-sm shrink-0 lg:btn-md lg:px-6" :disabled="loading">{{ query.trim() ? '搜索' : '查看全部' }}</button>
          </form>
          <div class="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-t border-base-300 pt-3 sm:pt-4">
            <div class="flex flex-wrap items-center gap-1.5 sm:gap-2" role="group" aria-label="商品排序">
              <span class="mr-0.5 hidden text-sm text-base-content/60 sm:inline">排序方式</span>
              <button v-for="option in sortOptions" :key="option.key" type="button" class="btn btn-xs rounded-full border-transparent px-2.5 sm:btn-sm sm:px-3.5"
                :class="sort.sort_type === option.key ? 'btn-primary' : 'btn-ghost bg-base-200 text-base-content/80 hover:bg-base-300'"
                :aria-pressed="sort.sort_type === option.key"
                :aria-label="sort.sort_type === option.key ? `${option.label}，${sortDirection.label}` : option.label"
                :disabled="loading" @click="changeSort(option.key)">
                {{ option.label }}<span v-if="sort.sort_type === option.key" aria-hidden="true">{{ sortDirection.arrow }}</span>
              </button>
            </div>
          </div>
        </div>
      </div>
      <div ref="list" class="flex min-h-0 flex-1 flex-col gap-2.5 overflow-y-auto overscroll-contain [scrollbar-gutter:stable] [scrollbar-width:thin] sm:gap-3"
        role="region" aria-label="商品列表" :aria-busy="loading" tabindex="0">
        <div v-if="loading" role="status" class="py-12 text-center text-sm text-base-content/70">
          <span class="loading loading-spinner loading-sm" aria-hidden="true"></span>
          <p class="mt-2">正在查询商品…</p>
        </div>
        <div v-else-if="error" role="alert" class="alert alert-soft alert-error text-sm">
          <span>{{ error }}</span><button type="button" class="btn btn-sm" @click="search(requestedStart, submittedKeyword)">重试</button>
        </div>
        <template v-else-if="page?.products.length">
          <ProductSearchCard v-for="(item, index) in page.products" :key="item.id ?? index" :product="item"
            :can-add-to-showcase="false" paginated />
        </template>
        <p v-else role="status" class="py-12 text-center text-base-content/70">未查询到商品</p>
      </div>
      <nav v-if="page && page.total" aria-label="商品分页"
        class="flex shrink-0 flex-wrap items-center justify-between gap-2 border-t border-base-300 pt-3 sm:pt-4">
        <button type="button" class="btn btn-sm rounded-xl border-base-300 bg-base-100 sm:px-4" aria-label="上一页" :disabled="loading || !!error || !page.start"
          @click="search(Math.max(0, page.start - page.length), submittedKeyword)"><LiveIcon name="sidebar-collapse" class="size-4" />上一页</button>
        <span class="badge h-7 rounded-full border-base-300 bg-base-100 px-2.5 text-xs text-base-content/75 tabular-nums sm:h-8 sm:px-3.5 sm:text-sm">第 {{ pageNumber }} / {{ pageCount }} 页 · 共 {{ page.total }} 件</span>
        <button type="button" class="btn btn-sm rounded-xl border-base-300 bg-base-100 sm:px-4" aria-label="下一页" :disabled="loading || !!error || page.start + page.length >= page.total"
          @click="search(page.start + page.length, submittedKeyword)">下一页<LiveIcon name="sidebar-expand" class="size-4" /></button>
      </nav>
    </template>
  </div>
</template>
