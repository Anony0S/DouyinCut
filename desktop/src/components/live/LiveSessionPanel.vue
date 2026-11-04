<script setup lang="ts">
import { computed, ref } from "vue";
import { duration, seconds } from "../../api";
import type { LiveSegment, LiveSession, LiveProductPeriod, LivePeriodSource, LiveSplitResult } from "../../services/liveMonitoring";
import LiveIcon from "./LiveIcon.vue";
import LivePeriodSplit from "./LivePeriodSplit.vue";
import ConfirmPopover from "../ConfirmPopover.vue";
import {
  clipBadgeClasses,
  clipNames,
  formatBytes,
  formatOffset,
  listenerBadgeClasses,
  listenerNames,
  segmentBadgeClasses,
  segmentNames,
} from "./presentation";

const props = defineProps<{
  session: LiveSession;
  busy: boolean;
  onDelete: (id: string) => Promise<void>;
  onDeleteProductClip: (id: string) => Promise<void>;
  onSplit: (id: string, confirmedMissingSegmentIds: string[]) => Promise<LiveSplitResult>;
}>();
const visiblePathKey = ref<string | null>(null);
defineEmits<{
  import: [id: string];
  "import-product-clip": [id: string];
  open: [assetId: string];
  "open-file": [id: string, reveal: boolean];
  "open-segment-file": [id: string, reveal: boolean];
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
const splitting = (segment: LiveSegment) => segment.splitting;
function deleteSegment(segment: LiveSegment) {
  return props.onDelete(segment.id);
}
function deleteProductClip(clip: LiveSession["product_clips"][number]) {
  return props.onDeleteProductClip(clip.id);
}
function productClipMessage(status: string, name: string) {
  return status === "imported"
    ? `只删除直播监控中的“${name}”商品片段记录，已导入项目和文件会保留。`
    : `将删除“${name}”商品片段及其本地文件。`;
}
const segmentFilter = ref("all");
const periods = computed(() =>
  [...props.session.product_periods].sort(
    (a, b) => Number(b.ended === null) - Number(a.ended === null) || b.started - a.started || b.id.localeCompare(a.id),
  ),
);
const sourceStates: Record<LivePeriodSource["state"], string> = {
  ready: "", recording: "录制中", deleted: "已删除", missing: "文件不存在", failed: "文件异常",
};
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
const readyCount = computed(
  () =>
    props.session.segments.filter((item) => item.status === "completed").length,
);
const importedCount = computed(
  () =>
    props.session.segments.filter((item) => item.status === "imported").length,
);
const segments = computed(() =>
  [...props.session.segments]
    .filter(
      (item) =>
        segmentFilter.value === "all" ||
        (segmentFilter.value === "ready"
          ? item.status === "completed"
          : item.status === "imported"),
    )
    .sort((a, b) => b.segment_index - a.segment_index),
);
const listenerStatus = computed(
  () => props.session.product_listener?.status || "",
);
const filters = computed(() => [
  { id: "all", name: "全部", count: props.session.segments.length },
  { id: "ready", name: "待导入", count: readyCount.value },
  { id: "imported", name: "已导入", count: importedCount.value },
]);
</script>

<template>
  <div class="live-session-content">
    <div class="live-content-columns">
      <section
        class="card card-border bg-base-100 w-full live-content-card live-recordings-card">
        <div class="live-content-card-heading">
          <div>
            <span class="live-section-icon"><LiveIcon name="video" /></span>
            <h3>录制分段</h3>
            <span class="badge badge-ghost badge-sm live-count">
              {{ session.segments.length }}
            </span>
          </div>
          <span>每 20 分钟封存</span>
        </div>
        <div class="live-segment-filters join" aria-label="筛选录制分段">
          <button
            v-for="item in filters"
            :key="item.id"
            class="btn btn-sm join-item"
            :class="{ 'btn-active': segmentFilter === item.id }"
            :aria-pressed="segmentFilter === item.id"
            @click="segmentFilter = item.id">
            {{ item.name }}
            <span class="badge badge-ghost badge-xs">{{ item.count }}</span>
          </button>
        </div>
        <div v-if="segments.length" class="live-segment-list">
          <article
            v-for="segment in segments"
            :key="segment.id"
            class="live-segment-row">
            <div class="live-segment-cover" :class="segment.status">
              <LiveIcon
                :name="segment.status === 'recording' ? 'record' : 'video'" />
              <span>{{ String(segment.segment_index).padStart(2, "0") }}</span>
            </div>
            <div class="live-segment-info">
              <div class="live-segment-title">
                <strong>
                  分段 {{ String(segment.segment_index).padStart(2, "0") }}
                </strong>
                <span
                  :class="[
                    'badge',
                    'badge-xs',
                    segmentBadgeClasses[segment.status] || 'badge-soft badge-neutral',
                  ]">
                  {{ segmentNames[segment.status] || segment.status }}
                </span>
              </div>
              <div class="live-segment-meta items-center">
                <span>
                  {{
                    segment.duration_us
                      ? duration(segment.duration_us)
                      : segment.status === "recording"
                        ? "录制进行中"
                        : "时长待探测"
                  }}
                </span>
                <span>{{ formatBytes(segment.size_bytes) }}</span>
                <span
                  class="live-segment-path-tooltip"
                  :class="visiblePathKey === pathKey('segment', segment.id)
                    ? 'tooltip tooltip-bottom tooltip-center tooltip-open'
                    : ''">
                  <button
                    type="button"
                    class="btn btn-ghost btn-square btn-xs live-segment-path-trigger"
                    :aria-label="`查看录制分段 ${segment.segment_index} 的文件位置`"
                    :aria-expanded="visiblePathKey === pathKey('segment', segment.id)"
                    :aria-describedby="pathTooltipId('segment', segment.id)"
                    title="查看文件位置"
                    @click="togglePath('segment', segment.id)"
                    @keydown.esc="visiblePathKey = null">
                    <LiveIcon name="folder" />
                  </button>
                  <span
                    :id="pathTooltipId('segment', segment.id)"
                    class="tooltip-content live-segment-path-content"
                    role="tooltip">
                    <code>{{ segment.path }}</code>
                    <small v-if="segment.duration_us">
                      时长 {{ seconds(segment.duration_us) }} 秒
                    </small>
                  </span>
                </span>
              </div>
              <p v-if="segment.splitting" class="text-base-content/60 text-xs mt-2">正在切分商品讲解，暂不可删除</p>
            </div>
            <div class="live-segment-actions live-recording-actions">
              <button
                class="btn btn-ghost btn-sm live-segment-action live-segment-action-open-file"
                :disabled="busy"
                aria-label="打开文件"
                title="用系统默认播放器打开录制分段"
                @click="$emit('open-segment-file', segment.id, false)">
                <LiveIcon name="play"/>
                <span class="live-segment-action-label" aria-hidden="true">打开文件</span>
              </button>
              <button
                class="btn btn-ghost btn-sm live-segment-action live-segment-action-open-folder"
                :disabled="busy"
                aria-label="打开文件夹"
                title="在文件夹中显示录制分段"
                @click="$emit('open-segment-file', segment.id, true)">
                <LiveIcon name="folder" />
                <span class="live-segment-action-label" aria-hidden="true">打开文件夹</span>
              </button>
              <button
                v-if="segment.status === 'completed'"
                class="btn btn-primary btn-sm live-segment-action live-segment-action-import"
                :disabled="busy"
                aria-label="导入剪辑"
                @click="$emit('import', segment.id)">
                <LiveIcon name="arrow" />
                <span class="live-segment-action-label" aria-hidden="true">导入剪辑</span>
              </button>
              <button
                v-else-if="segment.status === 'importing'"
                class="btn btn-ghost btn-sm live-segment-action live-segment-action-importing"
                aria-label="导入中"
                title="导入中"
                disabled>
                <LiveIcon name="refresh" class="is-spinning" />
                <span class="live-segment-action-label" aria-hidden="true">导入中…</span>
              </button>
              <button
                v-else-if="segment.status === 'imported' && segment.asset_id"
                class="btn btn-outline btn-sm live-segment-action live-segment-action-project"
                :disabled="busy"
                aria-label="打开项目"
                @click="$emit('open', segment.asset_id)">
                <LiveIcon name="external" />
                <span class="live-segment-action-label" aria-hidden="true">打开项目</span>
              </button>
              <ConfirmPopover
                v-if="!['recording', 'importing'].includes(segment.status)"
                :title="`删除分段 ${segment.segment_index}？`"
                message="将删除该分段的本地录制文件，商品讲解、分段编号和已生成商品片段保留；已被视频项目引用的分段不能删除。"
                :confirm-action="() => deleteSegment(segment)"
                :disabled="busy || splitting(segment)"
                v-slot="{ open, expanded, popoverId }">
                <button
                  class="btn btn-ghost btn-sm live-segment-action live-segment-action-delete text-error"
                  :disabled="busy || splitting(segment)"
                  :aria-label="`删除分段 ${segment.segment_index}`"
                  title="删除分段与本地文件"
                  :aria-expanded="expanded"
                  :aria-controls="popoverId"
                  aria-haspopup="dialog"
                  @click="open">
                  <LiveIcon name="trash" />
                  <span class="live-segment-action-label" aria-hidden="true">删除分段</span>
                </button>
              </ConfirmPopover>
            </div>
          </article>
        </div>
        <div v-else class="live-card-empty">
          <LiveIcon name="video" />
          <strong>
            {{
              segmentFilter === "all"
                ? "等待生成录制分段"
                : segmentFilter === "ready"
                  ? "暂无待导入分段"
                  : "暂无已导入分段"
            }}
          </strong>
          <p>
            {{
              segmentFilter === "all"
                ? "直播录制后，分段文件会自动出现在这里。"
                : "切换到“全部”查看其他分段。"
            }}
          </p>
        </div>
        <div class="live-card-foot">
          <LiveIcon name="folder" />
          封存后可导入剪辑；在讲解列表中切分商品，录制中内容持续写入。
        </div>
      </section>

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
        <p
          v-if="session.product_listener?.detail"
          class="live-listener-note pl-6"
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
            :key="period.id"
            class="live-product-event"
            :class="{ 'is-latest': index === 0 }">
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
                  <div class="flex flex-wrap gap-1.5 mt-1" aria-label="来源录制分段">
                    <span v-for="source in period.sources" :key="source.segment_id"
                      class="badge badge-soft badge-sm"
                      :class="['deleted', 'missing', 'failed'].includes(source.state) ? 'badge-warning' : 'badge-neutral'">
                      分段 {{ String(source.segment_index).padStart(2, "0") }}{{ sourceStates[source.state] ? `（${sourceStates[source.state]}）` : '' }}
                    </span>
                    <span v-if="!period.sources.length" class="text-base-content/60 text-xs">暂无对应分段</span>
                  </div>
                </div>
                <div class="shrink-0">
                  <LivePeriodSplit :period="period" :busy="busy" :on-split="onSplit" />
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
                <span>分段 {{ String(clip.segment_index).padStart(2, "0") }}</span>
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
              <p v-if="clipPeriodTime(clip.period_id)" class="text-base-content/60 text-xs mt-2">所属讲解 {{ clipPeriodTime(clip.period_id) }}</p>
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
          <p>在讲解列表中点击“切分商品”，每个来源分段各生成一个片段，原始录制文件保留。</p>
        </div>
        <div class="live-card-foot">
          <LiveIcon name="video" />
          切点受关键帧精度限制，导入后可在粗剪中微调。
        </div>
      </section>
    </div>
  </div>
</template>
