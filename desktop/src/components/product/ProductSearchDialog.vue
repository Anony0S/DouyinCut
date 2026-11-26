<script setup lang="ts">
import { onDeactivated, onMounted, onUnmounted, ref, useId } from 'vue'
import { useRouter } from 'vue-router'
import { isProductPlatformAuthError, isXingdongliAuthError, productPlatformApi, type PlatformProduct } from '../../services/productPlatform'
import { useSessionStore } from '../../stores/session'
import ProductSearchCard from './ProductSearchCard.vue'

const props = defineProps<{ keyword: string; monitorId: string }>()
const emit = defineEmits<{ close: [] }>()
const dialog = ref<HTMLDialogElement>()
const id = useId(), router = useRouter(), session = useSessionStore()
const query = ref(props.keyword.trim())
const loading = ref(false), adding = ref(false), added = ref(false)
const error = ref(''), addError = ref(''), scopeName = ref('')
const product = ref<PlatformProduct | null>(null)
const canAddToShowcase = ref(false)
let submittedKeyword = query.value
let disposed = false
function close() { disposed = true; emit('close') }
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
async function search(keyword = query.value.trim()) {
  if (loading.value || adding.value) return
  if (!keyword) { error.value = '请输入查询词'; return }
  submittedKeyword = keyword
  loading.value = true; error.value = ''; addError.value = ''; added.value = false
  product.value = null; canAddToShowcase.value = false
  try {
    const result = await productPlatformApi.search(keyword, { monitor_id: props.monitorId })
    if (disposed) return
    product.value = result.product; scopeName.value = result.scope_name
    canAddToShowcase.value = result.can_add_to_showcase
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
onDeactivated(close)
onUnmounted(() => { disposed = true; dialog.value?.close() })
</script>

<template>
  <Teleport to="body">
    <dialog ref="dialog" class="modal" :aria-labelledby="`${id}-title`" @cancel.prevent="close">
      <div class="modal-box w-[calc(100vw-2rem)] max-w-3xl">
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
          <span>{{ error }}</span><button class="btn btn-sm" @click="search(submittedKeyword)">重试</button>
        </div>
        <template v-else-if="product">
          <ProductSearchCard :product="product" :can-add-to-showcase="canAddToShowcase"
            :paginated="false" class="mt-6" />
          <div v-if="canAddToShowcase" class="mt-4 flex flex-wrap items-center justify-between gap-3">
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
        <div class="modal-action">
          <button class="btn" @click="close">关闭</button>
        </div>
      </div>
      <form method="dialog" class="modal-backdrop" @submit.prevent="close"><button>关闭</button></form>
    </dialog>
  </Teleport>
</template>
