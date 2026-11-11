<script setup lang="ts">
import { computed, nextTick, ref } from "vue";
import { duration } from "../../api";
import type { LiveSession, LiveProductPeriod } from "../../services/liveMonitoring";
import LiveIcon from "./LiveIcon.vue";
import ConfirmPopover from "../ConfirmPopover.vue";
import ProductSearchDialog from "../product/ProductSearchDialog.vue";
import {
  clipBadgeClasses,
  clipNames,
  formatBytes,
  formatOffset,
  listenerBadgeClasses,
  listenerNames,
  productPeriodColor,
} from "./presentation";

const props = defineProps<{
  session: LiveSession;
  monitorId: string;
  busy: boolean;
  selectedPeriodId: string;
  onDeleteProductClip: (id: string) => Promise<void>;
  onRetryProductListener: (monitorId: string, sessionId: string) => Promise<void>;
}>();
const visiblePathKey = ref<string | null>(null);
const queryProductName = ref("");
const emit = defineEmits<{
  "import-product-clip": [id: string];
  open: [assetId: string];
  "open-file": [id: string, reveal: boolean];
  "select-period": [id: string];
}>();
function pathKey(kind: "segment" | "clip", id: string) {
  return `${kind}:${id}`;
}
function pathTooltipId(kind: "segment" | "clip", id: string) {
  return `live-${kind}-path-${id}`;
}
function togglePath(kind: "segment" | "clip", id: string) {
  const key = pathKey(kind, id);
  visiblePathKey.value = visiblePathKey.value === key ? null : key;
}
function deleteProductClip(clip: LiveSession["product_clips"][number]) {
  return props.onDeleteProductClip(clip.id);
}
function productClipMessage(status: string, name: string) {
  return status === "imported"
    ? `只删除直播监控中的“${name}”商品片段记录，已导入项目和文件会保留。`
    : `将删除“${name}”商品片段及其本地文件。`;
}
const periods = computed(() =>
  [...props.session.product_periods].sort(
    (a, b) => Number(b.ended === null) - Number(a.ended === null) || b.started - a.started || b.id.localeCompare(a.id),
  ),
);
function splitSummary(period: LiveProductPeriod) {
  const generated = period.sources.filter(source => ["completed", "importing", "imported"].includes(source.clip_status || "")).length;
  const ready = period.sources.filter(source => source.state === "ready" && source.end - source.start >= 1 && !["completed", "importing", "imported"].includes(source.clip_status || "")).length;
  const pending = period.sources.some(source => source.state === "recording");
  const short = period.sources.filter(source => source.state !== "recording" && source.end - source.start < 1).length;
  return [generated ? `已生成 ${generated} 个片段` : "", ready ? `${ready} 个可切分` : "", pending ? "部分待封存" : "", short ? `${short} 处不足 1 秒，跳过` : ""].filter(Boolean).join(" · ");
}
function clipPeriodTime(periodId: string) {
  const period = props.session.product_periods.find(item => item.id === periodId);
  return period ? `${formatOffset(period.started, props.session.started)} – ${period.ended === null ? "至今" : formatOffset(period.ended, props.session.started)}` : "";
}
async function selectClipPeriod(periodId: string) {
  emit("select-period", periodId);
  await nextTick();
  document.getElementById(`live-period-${periodId}`)?.scrollIntoView({ block: "nearest" });
}
function productColorStyle(promotionId: string) {
  return { "--period-color": productPeriodColor(promotionId) };
}
const listenerStatus = computed(
  () => props.session.product_listener?.status || "",
);
const canRetryProductListener = computed(
  () => props.session.status === "recording"
    && !props.busy
    && listenerStatus.value !== "retrying"
    && (listenerStatus.value === "error" || Boolean(props.session.product_catalog_error)),
);
</script>

<template>
  <div class="live-session-content">
    <div class="live-content-columns">
      <section
        class="card card-border bg-base-100 w-full live-content-card live-events-card">
        <div class="live-content-card-heading">
          <div>
            <span class="live-section-icon"><LiveIcon name="bag" /></span>
            <h3>商品讲解</h3>
            <span class="badge badge-ghost badge-sm live-count">
              {{ periods.length }}
            </span>
          </div>
          <span
            :class="[
              'badge',
              'badge-sm',
              listenerBadgeClasses[listenerStatus] || 'badge-soft badge-neutral',
            ]">
            {{ listenerNames[listenerStatus] || "商品监听未启动" }}
          </span>
        </div>
        <details v-if="session.product_catalog_error" class="live-catalog-error">
          <summary>
            <LiveIcon name="alert" />
            <span>商品列表获取失败</span>
            <span>展开请求详情</span>
          </summary>
          <pre>{{
            JSON.stringify(session.product_catalog_error.detail, null, 2)
          }}</pre>
        </details>
        <button
          v-if="canRetryProductListener"
          type="button"
          class="btn btn-outline btn-sm mx-4 mt-3"
          :disabled="busy"
          @click="onRetryProductListener(monitorId, session.id)">
          <LiveIcon name="refresh" />
          重试商品连接
        </button>
        <p
          v-if="session.product_listener?.detail && (listenerStatus === 'error' || session.product_listener.detail !== '商品消息连接已建立')"
          class="live-listener-note pl-4 pt-2"
          :class="{ 'is-error': listenerStatus === 'error' }">
          <LiveIcon :name="listenerStatus === 'error' ? 'alert' : 'radio'" />
          {{ session.product_listener.detail }}
        </p>
        <div class="live-subheading">
          <span>讲解列表</span>
          <span>最新在前</span>
        </div>
        <div v-if="periods.length" class="live-product-event-list">
          <article
            v-for="(period, index) in periods"
            :id="`live-period-${period.id}`"
            :key="period.id"
            class="live-product-event"
            :class="{ 'is-latest': index === 0, 'is-selected': period.id === selectedPeriodId }"
            :style="productColorStyle(period.promotion_id)"
            @click="$emit('select-period', period.id)">
            <div class="live-event-marker">
              <i></i>
              <span></span>
            </div>
            <div class="live-event-content">
              <div class="live-event-time">
                <time>
                  {{ formatOffset(period.started, session.started) }} –
                  {{ period.ended === null ? "至今" : formatOffset(period.ended, session.started) }}
                </time>
                <span v-if="period.ended === null" class="badge badge-soft badge-info badge-xs">正在讲解</span>
                <span v-else-if="index === 0">最近讲解</span>
                <span v-if="period.ended !== null">{{ duration(Math.max(0, period.ended - period.started) * 1000000) }}</span>
              </div>
              <div class="flex items-center gap-4">
                <div class="live-event-info min-w-0 flex-1">
                  <strong>
                    {{ period.product_name || `商品 ${period.promotion_id}` }}
                  </strong>
                  <div class="live-event-meta mb-1">
                    <span>商品 ID {{ period.promotion_id }}</span>
                  </div>
                  <p v-if="splitSummary(period)" class="text-base-content/60 text-xs mt-2">{{ splitSummary(period) }}</p>
                  <p v-if="period.mapping_error" class="live-product-clip-error">{{ period.mapping_error }}</p>
                  <p v-if="period.clip_job?.state === 'failed' && period.clip_job.error" class="live-product-clip-error">
                    切分失败：{{ period.clip_job.error }}
                  </p>
                </div>
                <div class="flex shrink-0 flex-col items-stretch gap-2">
                  <button type="button" class="btn btn-soft btn-sm"
                    :disabled="busy || !period.product_name.trim()"
                    :title="period.product_name.trim() ? '查询星斗商品平台中的商品信息' : '暂无商品名称，无法查询'"
                    @click.stop="queryProductName = period.product_name.trim()"><LiveIcon name="search" />查询商品</button>
                </div>
              </div>
            </div>
          </article>
        </div>
        <div v-else class="live-card-empty">
          <LiveIcon name="clock" />
          <strong>还没有商品讲解记录</strong>
          <p>获取到讲解商品后自动记录，首次讲解也会出现在这里。</p>
        </div>
        <div class="live-card-foot">
          <LiveIcon name="clock" />
          时间为本场录制内的位置；每次只切所选讲解的已封存部分。
        </div>
      </section>

      <section
        class="card card-border bg-base-100 w-full live-content-card live-product-clips-card">
        <div class="live-content-card-heading">
          <div>
            <span class="live-section-icon"><LiveIcon name="layers" /></span>
            <h3>商品视频片段</h3>
            <span class="badge badge-ghost badge-sm live-count">
              {{ session.product_clips.length }}
            </span>
          </div>
          <span>从讲解列表手动切分</span>
        </div>
        <div v-if="session.product_clips.length" class="live-segment-list">
          <article
            v-for="clip in session.product_clips"
            :key="clip.id"
            class="live-segment-row live-product-clip-row">
            <div class="live-segment-info">
              <div class="live-segment-title">
                <strong :title="clip.product_name || clip.promotion_id">
                  {{ clip.product_name || `商品 ${clip.promotion_id}` }}
                </strong>
                <span
                  :class="[
                    'badge',
                    'badge-xs',
                    clipBadgeClasses[clip.status] || 'badge-soft badge-neutral',
                  ]">
                  {{ clipNames[clip.status] || clip.status }}
                </span>
              </div>
              <div class="live-segment-meta items-center">
                <span v-if="clipPeriodTime(clip.period_id)" class="live-clip-period-time" :style="productColorStyle(clip.promotion_id)">
                  <button type="button"
                    class="btn btn-link btn-xs h-auto min-h-0 border-0 p-0 text-xs text-inherit no-underline hover:underline"
                    :aria-pressed="clip.period_id === selectedPeriodId"
                    :aria-controls="`live-period-${clip.period_id}`"
                    title="选中对应讲解并定位到讲解列表"
                    @click="selectClipPeriod(clip.period_id)">所属讲解 {{ clipPeriodTime(clip.period_id) }}</button>
                </span>
                <span>
                  {{ formatOffset(clip.start, session.started) }} –
                  {{ formatOffset(clip.end, session.started) }}
                </span>
                <span v-if="clip.duration_us">{{ duration(clip.duration_us) }}</span>
                <span v-if="clip.size_bytes">{{ formatBytes(clip.size_bytes) }}</span>
                <span
                  v-if="clip.path"
                  class="live-segment-path-tooltip"
                  :class="visiblePathKey === pathKey('clip', clip.id)
                    ? 'tooltip tooltip-bottom tooltip-center tooltip-open'
                    : ''">
                  <button
                    type="button"
                    class="btn btn-ghost btn-square btn-xs live-segment-path-trigger"
                    :aria-label="`查看商品片段 ${clip.product_name || clip.promotion_id} 的文件位置`"
                    :aria-expanded="visiblePathKey === pathKey('clip', clip.id)"
                    :aria-describedby="pathTooltipId('clip', clip.id)"
                    title="查看文件位置"
                    @click="togglePath('clip', clip.id)"
                    @keydown.esc="visiblePathKey = null">
                    <LiveIcon name="folder" />
                  </button>
                  <span
                    :id="pathTooltipId('clip', clip.id)"
                    class="tooltip-content live-segment-path-content"
                    role="tooltip">
                    <code>{{ clip.path }}</code>
                  </span>
                </span>
              </div>
              <p v-if="clip.error" class="live-product-clip-error">
                {{ clip.error }}
              </p>
            </div>
            <div class="live-segment-actions live-recording-actions">
              <button
                v-if="clip.status !== 'failed'"
                class="btn btn-ghost btn-sm live-segment-action live-segment-action-open-file"
                :disabled="busy"
                aria-label="打开文件"
                title="用系统默认播放器打开"
                @click="$emit('open-file', clip.id, false)">
                <LiveIcon name="play" />
                <span class="live-segment-action-label" aria-hidden="true">打开文件</span>
              </button>
              <button
                class="btn btn-ghost btn-sm live-segment-action live-segment-action-open-folder"
                :disabled="busy"
                title="在文件夹中显示"
                aria-label="打开文件夹"
                @click="$emit('open-file', clip.id, true)">
                <LiveIcon name="folder" />
                <span class="live-segment-action-label" aria-hidden="true">打开文件夹</span>
              </button>
              <button
                v-if="clip.status === 'completed'"
                class="btn btn-primary btn-sm live-segment-action live-segment-action-import"
                :disabled="busy"
                aria-label="导入剪辑"
                @click="$emit('import-product-clip', clip.id)">
                <LiveIcon name="arrow" />
                <span class="live-segment-action-label" aria-hidden="true">导入剪辑</span>
              </button>
              <button
                v-else-if="clip.status === 'importing'"
                class="btn btn-ghost btn-sm live-segment-action live-segment-action-importing"
                aria-label="导入中"
                title="导入中"
                disabled>
                <LiveIcon name="refresh" class="is-spinning" />
                <span class="live-segment-action-label" aria-hidden="true">导入中…</span>
              </button>
              <button
                v-else-if="clip.status === 'imported' && clip.asset_id"
                class="btn btn-outline btn-sm live-segment-action live-segment-action-project"
                :disabled="busy"
                aria-label="打开项目"
                @click="$emit('open', clip.asset_id)">
                <LiveIcon name="external" />
                <span class="live-segment-action-label" aria-hidden="true">打开项目</span>
              </button>
              <ConfirmPopover
                v-if="!['processing', 'importing'].includes(clip.status)"
                :title="`删除“${clip.product_name || clip.promotion_id}”商品片段？`"
                :message="productClipMessage(clip.status, clip.product_name || clip.promotion_id)"
                :confirm-action="() => deleteProductClip(clip)"
                :disabled="busy"
                v-slot="{ open, expanded, popoverId }">
                <button
                  class="btn btn-ghost btn-sm live-segment-action live-segment-action-delete text-error"
                  :disabled="busy"
                  :aria-label="`删除商品片段 ${clip.product_name || clip.promotion_id}`"
                  title="删除商品片段"
                  :aria-expanded="expanded"
                  :aria-controls="popoverId"
                  aria-haspopup="dialog"
                  @click="open">
                  <LiveIcon name="trash" />
                  <span class="live-segment-action-label" aria-hidden="true">删除片段</span>
                </button>
              </ConfirmPopover>
            </div>
          </article>
        </div>
        <div v-else class="live-card-empty">
          <LiveIcon name="layers" />
          <strong>还没有商品视频片段</strong>
          <p>在商品讲解轨中选择范围并切分商品；原始录制内容会保留。</p>
        </div>
        <div class="live-card-foot">
          <LiveIcon name="video" />
          切点受关键帧精度限制，导入后可在粗剪中微调。
        </div>
      </section>
    </div>
    <ProductSearchDialog v-if="queryProductName" :keyword="queryProductName" @close="queryProductName = ''" />
  </div>
</template>
