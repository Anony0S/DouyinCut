<script setup lang="ts">
import MarkdownIt from 'markdown-it'
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { projectsApi, type CoverPrompt, type CoverPromptDraft } from '../services/projects'

defineProps<{ disabled: boolean }>()
const selectedPrompt = defineModel<string>({ required: true })
const prompts = ref<CoverPrompt[]>([])
const selectedId = ref('')
const loading = ref(false)
const loadError = ref('')
const dialog = ref<HTMLDialogElement>()
const mode = ref<'view' | 'edit' | 'new' | 'delete'>('view')
const editingId = ref('')
const draft = ref<CoverPromptDraft>({ name: '', prompt: '' })
const preview = ref(false)
const busy = ref(false)
const actionError = ref('')
const titles = { view: '查看提示词', edit: '编辑提示词', new: '新增提示词', delete: '删除提示词' }
const markdown = new MarkdownIt({ html: false, breaks: true })
markdown.renderer.rules.link_open = () => ''
markdown.renderer.rules.link_close = () => ''
markdown.renderer.rules.image = (tokens, index, options, env, renderer) =>
  markdown.utils.escapeHtml(renderer.renderInlineAsText(tokens[index].children ?? [], options, env))
const rendered = computed(() => markdown.render(draft.value.prompt))
const selected = computed(() => prompts.value.find(item => item.id === selectedId.value))
watch(selected, item => { selectedPrompt.value = item?.prompt ?? '' })

async function load() {
  loading.value = true
  loadError.value = ''
  try {
    prompts.value = await projectsApi.coverPrompts()
    selectedId.value = prompts.value[0]?.id ?? ''
  } catch (error) {
    loadError.value = error instanceof Error ? error.message : '提示词加载失败'
  } finally {
    loading.value = false
  }
}
onMounted(load)

async function open(action: typeof mode.value, item?: CoverPrompt) {
  mode.value = action
  editingId.value = item?.id ?? ''
  draft.value = { name: item?.name ?? '', prompt: item?.prompt ?? '' }
  preview.value = false
  actionError.value = ''
  await nextTick()
  dialog.value?.showModal()
}

function close() {
  if (!busy.value) dialog.value?.close()
}

async function save() {
  if (busy.value || !draft.value.name.trim() || !draft.value.prompt.trim()) return
  const creating = mode.value === 'new'
  busy.value = true
  actionError.value = ''
  try {
    const saved = creating
      ? await projectsApi.createCoverPrompt(draft.value)
      : await projectsApi.updateCoverPrompt(editingId.value, draft.value)
    prompts.value = creating
      ? [...prompts.value, saved]
      : prompts.value.map(item => item.id === saved.id ? saved : item)
    if (creating) selectedId.value = saved.id
    dialog.value?.close()
  } catch (error) {
    actionError.value = error instanceof Error ? error.message : '提示词保存失败'
  } finally {
    busy.value = false
  }
}

async function remove() {
  if (busy.value) return
  busy.value = true
  actionError.value = ''
  try {
    await projectsApi.deleteCoverPrompt(editingId.value)
    prompts.value = prompts.value.filter(item => item.id !== editingId.value)
    if (selectedId.value === editingId.value) selectedId.value = prompts.value[0]?.id ?? ''
    dialog.value?.close()
  } catch (error) {
    actionError.value = error instanceof Error ? error.message : '提示词删除失败'
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <section class="cover-prompt-picker" aria-labelledby="cover-prompt-heading">
    <div class="flex items-center justify-between gap-2">
      <h4 id="cover-prompt-heading" class="m-0 text-sm font-semibold">生成要求</h4>
      <button type="button" class="btn btn-sm" :disabled="disabled || loading || !!loadError" @click="open('new')">
        新增提示词
      </button>
    </div>
    <div class="cover-prompt-options">
      <p v-if="loading" class="text-sm text-base-content/60" role="status">正在读取提示词…</p>
      <div v-else-if="loadError" class="flex flex-wrap items-center gap-2 text-sm text-error" role="alert">
        <span>{{ loadError }}</span>
        <button type="button" class="btn btn-sm" @click="load">重试</button>
      </div>
      <p v-else-if="!prompts.length" class="rounded-box border border-dashed border-base-300 p-4 text-sm text-base-content/60">
        暂无提示词。新增一套后即可生成封面。
      </p>
      <ul v-else class="list gap-2 px-1 pb-2">
        <li v-for="item in prompts" :key="item.id"
          class="rounded-box border border-base-300 shadow-xs shadow-base-content/8 transition-colors"
          :class="selectedId === item.id ? 'bg-primary/8' : 'bg-base-100 hover:bg-base-200/65'">
          <div class="flex flex-wrap items-center gap-1 p-2">
            <button type="button"
              class="btn btn-ghost min-w-0 flex-1 justify-start border-transparent! bg-transparent! text-left font-semibold shadow-none!"
              :aria-pressed="selectedId === item.id" :disabled="disabled" @click="selectedId = item.id">
              <span class="truncate">{{ item.name }}</span>
              <span v-if="selectedId === item.id" class="shrink-0 text-xs text-primary">已选</span>
            </button>
            <div class="flex flex-wrap gap-0.5">
              <button type="button" class="btn btn-ghost btn-xs" :aria-label="'查看 ' + item.name" @click="open('view', item)">查看</button>
              <button type="button" class="btn btn-ghost btn-xs" :disabled="disabled"
                :aria-label="'编辑 ' + item.name" @click="open('edit', item)">编辑</button>
              <button type="button" class="btn btn-ghost btn-xs text-error" :disabled="disabled"
                :aria-label="'删除 ' + item.name" @click="open('delete', item)">删除</button>
            </div>
          </div>
        </li>
      </ul>
    </div>

    <dialog ref="dialog" class="modal" aria-labelledby="cover-prompt-editor-title" @cancel.stop.prevent="close">
      <div class="modal-box max-w-2xl">
        <h3 id="cover-prompt-editor-title" class="text-lg font-semibold">{{ titles[mode] }}</h3>
        <template v-if="mode === 'view'">
          <p class="mt-3 font-semibold break-words">{{ draft.name }}</p>
          <div class="prompt-markdown mt-2 max-h-[48dvh] overflow-auto rounded-box bg-base-200 p-3 text-sm"
            v-html="rendered"></div>
          <div class="modal-action"><button type="button" class="btn" @click="close">关闭</button></div>
        </template>
        <template v-else-if="mode === 'delete'">
          <p class="mt-3 break-words">确定删除「{{ draft.name }}」？</p>
          <p v-if="actionError" class="mt-2 text-sm text-error" role="alert">{{ actionError }}</p>
          <div class="modal-action">
            <button type="button" class="btn" :disabled="busy" @click="close">取消</button>
            <button type="button" class="btn btn-error" :disabled="busy" @click="remove">删除</button>
          </div>
        </template>
        <form v-else class="mt-4 grid gap-3" @submit.prevent="save">
          <label class="grid gap-1 text-sm" for="cover-prompt-name">
            名称
            <input id="cover-prompt-name" v-model="draft.name" class="input w-full" maxlength="80" required :disabled="busy" />
          </label>
          <div class="flex items-center justify-between gap-2">
            <span id="cover-prompt-content-label" class="text-sm">Markdown 提示词</span>
            <button type="button" class="btn btn-sm" :aria-pressed="preview" @click="preview = !preview">
              {{ preview ? '返回编辑' : '预览' }}
            </button>
          </div>
          <div class="h-[min(280px,40dvh)]">
            <div v-if="preview" class="prompt-markdown h-full overflow-auto rounded-box bg-base-200 p-3 text-sm"
              aria-label="Markdown 预览" v-html="rendered"></div>
            <textarea v-else id="cover-prompt-content" v-model="draft.prompt" aria-labelledby="cover-prompt-content-label"
              class="textarea h-full min-h-0 w-full resize-none font-mono" maxlength="4000" required :disabled="busy"></textarea>
          </div>
          <p v-if="actionError" class="text-sm text-error" role="alert">{{ actionError }}</p>
          <div class="modal-action mt-1">
            <button type="button" class="btn" :disabled="busy" @click="close">取消</button>
            <button type="submit" class="btn btn-primary" :disabled="busy || !draft.name.trim() || !draft.prompt.trim()">保存</button>
          </div>
        </form>
      </div>
      <form method="dialog" class="modal-backdrop" @submit.prevent="close"><button>关闭</button></form>
    </dialog>
  </section>
</template>

<style scoped>
.prompt-markdown { overflow-wrap: anywhere; }
.prompt-markdown > :deep(* + *) { margin-block-start: .65rem; }
.prompt-markdown :deep(:is(h1, h2, h3, h4, h5, h6)) { font-weight: 650; }
.prompt-markdown :deep(ul) { list-style: disc; padding-inline-start: 1.4em; }
.prompt-markdown :deep(ol) { list-style: decimal; padding-inline-start: 1.4em; }
.prompt-markdown :deep(pre) { overflow-x: auto; padding: .5rem; background: var(--color-base-100); }
.prompt-markdown :deep(blockquote) { border-inline-start: 2px solid var(--color-base-300); padding-inline-start: .75rem; }
.prompt-markdown :deep(:is(th, td)) { padding: .25rem .5rem; border: 1px solid var(--color-base-300); }
</style>
