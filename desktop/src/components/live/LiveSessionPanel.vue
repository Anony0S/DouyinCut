<script setup lang="ts">
import { computed, ref } from "vue";
import { duration, seconds } from "../../api";
import type { LiveSegment, LiveSession } from "../../services/liveMonitoring";
import LiveIcon from "./LiveIcon.vue";
import ConfirmPopover from "../ConfirmPopover.vue";
import {
  activeClipJobStates,
  clipBadgeClasses,
  clipNames,
  formatBytes,
  formatLiveTime,
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
}>();
defineEmits<{
  import: [id: string];
  "import-product-clip": [id: string];
  open: [assetId: string];
  split: [id: string];
  "open-file": [id: string, reveal: boolean];
  "open-segment-file": [id: string, reveal: boolean];
}>();
const splitting = (segment: LiveSegment) =>
  Boolean(segment.clip_job && activeClipJobStates.has(segment.clip_job.state));
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
const events = computed(() =>
  [...props.session.product_events].sort(
    (a, b) => b.event_time - a.event_time || b.received_at - a.received_at,
  ),
);
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
        class="card card-border bg-base-100 w-full live-content-card live-events-card">
        <div class="live-content-card-heading">
          <div>
            <span class="live-section-icon"><LiveIcon name="bag" /></span>
            <h3>商品讲解</h3>
            <span class="badge badge-ghost badge-sm live-count">
              {{ events.length }}
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
        <section
          class="live-product-current"
          :class="{ 'is-history': session.status !== 'recording' }"
          aria-label="当前讲解商品">
          <span class="live-section-kicker">
            {{ session.status === "recording" ? "正在讲解" : "场次结束时讲解" }}
          </span>
          <strong>
            {{
              session.current_product
                ? session.current_product.product_name ||
                  `商品 ${session.current_product.promotion_id}`
                : "暂未获取到讲解商品"
            }}
          </strong>
          <div v-if="session.current_product" class="live-event-meta">
            <span>ID {{ session.current_product.promotion_id }}</span>
            <span>
              · 记录于
              {{
                formatOffset(session.current_product.event_time, session.started)
              }}
            </span>
          </div>
          <p v-else>商品列表加载完成或收到商品消息后自动更新。</p>
        </section>
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
          <span>讲解变更</span>
          <span>最新在前</span>
        </div>
        <div v-if="events.length" class="live-product-event-list">
          <article
            v-for="(event, index) in events"
            :key="event.id"
            class="live-product-event"
            :class="{ 'is-latest': index === 0 }">
            <div class="live-event-marker">
              <i></i>
              <span></span>
            </div>
            <div class="live-event-content">
              <div class="live-event-time">
                <time
                  :title="`${Math.max(0, event.event_time - session.started).toFixed(1)} 秒`">
                  {{ formatOffset(event.event_time, session.started) }}
                </time>
                <span>{{ index === 0 ? "最近一次变更" : "讲解切换" }}</span>
              </div>
              <strong>
                {{ event.product_name || `商品 ${event.promotion_id}` }}
              </strong>
              <div class="live-event-meta">
                <span>商品 ID {{ event.promotion_id }}</span>
                <span v-if="event.explain_type !== null">
                  讲解类型 {{ event.explain_type }}
                </span>
              </div>
              <small :title="formatLiveTime(event.received_at)">
                <span class="text-zinc-500 font-bold">接收于 {{ formatLiveTime(event.received_at, true) }}</span>
              </small>
            </div>
          </article>
        </div>
        <div v-else class="live-card-empty">
          <LiveIcon name="clock" />
          <strong>还没有商品切换记录</strong>
          <p>
            {{
              session.status === "recording"
                ? "后续讲解切换会记录于此。"
                : "这个场次没有记录到商品讲解变更。"
            }}
          </p>
        </div>
        <div class="live-card-foot">
          <LiveIcon name="clock" />
          时间为相对本场录制开始的位置。
        </div>
      </section>

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
              <div class="live-segment-meta">
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
              </div>
              <p
                v-if="segment.clip_job?.state === 'failed' && segment.clip_job.error"
                class="live-product-clip-error">
                切分失败：{{ segment.clip_job.error }}
              </p>
              <details class="live-segment-path">
                <summary>文件位置</summary>
                <code>{{ segment.path }}</code>
                <small v-if="segment.duration_us">
                  时长 {{ seconds(segment.duration_us) }} 秒
                </small>
              </details>
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
                v-if="['completed', 'importing', 'imported'].includes(segment.status)"
                class="btn btn-soft btn-sm live-segment-action live-segment-action-split"
                :disabled="busy || splitting(segment)"
                :aria-label="splitting(segment) ? '切分任务进行中' : '切分商品'"
                :title="splitting(segment) ? '切分任务进行中' : '按本分段内的商品讲解区间切分'"
                @click="$emit('split', segment.id)">
                <LiveIcon name="layers" />
                <span class="live-segment-action-label" aria-hidden="true">
                  {{ splitting(segment) ? "切分中…" : "切分商品" }}
                </span>
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
                message="将删除该分段及其本地录制文件；已被视频项目引用的分段不能删除。"
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
          封存后可导入剪辑或切分商品片段，录制中内容持续写入。
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
          <span>从录制分段手动切分</span>
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
              <div class="live-segment-meta">
                <span>分段 {{ String(clip.segment_index).padStart(2, "0") }}</span>
                <span>
                  {{ formatOffset(clip.start, session.started) }} –
                  {{ formatOffset(clip.end, session.started) }}
                </span>
                <span v-if="clip.duration_us">{{ duration(clip.duration_us) }}</span>
                <span v-if="clip.size_bytes">{{ formatBytes(clip.size_bytes) }}</span>
              </div>
              <p v-if="clip.error" class="live-product-clip-error">
                {{ clip.error }}
              </p>
              <details v-if="clip.path" class="live-segment-path">
                <summary>文件位置</summary>
                <code>{{ clip.path }}</code>
              </details>
            </div>
            <div class="live-segment-actions">
              <button
                v-if="clip.status !== 'failed'"
                class="btn btn-ghost btn-sm"
                :disabled="busy"
                title="用系统默认播放器打开"
                @click="$emit('open-file', clip.id, false)">
                <LiveIcon name="play" />
                打开
              </button>
              <button
                class="btn btn-ghost btn-square btn-sm"
                :disabled="busy"
                title="在文件夹中显示"
                aria-label="在文件夹中显示"
                @click="$emit('open-file', clip.id, true)">
                <LiveIcon name="folder" />
              </button>
              <button
                v-if="clip.status === 'completed'"
                class="btn btn-primary btn-sm"
                :disabled="busy"
                @click="$emit('import-product-clip', clip.id)">
                导入剪辑
                <LiveIcon name="arrow" />
              </button>
              <button
                v-else-if="clip.status === 'importing'"
                class="btn btn-ghost btn-sm"
                disabled>
                导入中…
              </button>
              <button
                v-else-if="clip.status === 'imported' && clip.asset_id"
                class="btn btn-outline btn-sm"
                :disabled="busy"
                @click="$emit('open', clip.asset_id)">
                打开项目
                <LiveIcon name="external" />
              </button>
              <ConfirmPopover
                v-if="!['processing', 'importing'].includes(clip.status)"
                :title="`删除“${clip.product_name || clip.promotion_id}”商品片段？`"
                :message="productClipMessage(clip.status, clip.product_name || clip.promotion_id)"
                :confirm-action="() => deleteProductClip(clip)"
                :disabled="busy"
                v-slot="{ open, expanded, popoverId }">
                <button
                  class="btn btn-ghost btn-square btn-xs text-error"
                  :disabled="busy"
                  :aria-label="`删除商品片段 ${clip.product_name || clip.promotion_id}`"
                  title="删除商品片段"
                  :aria-expanded="expanded"
                  :aria-controls="popoverId"
                  aria-haspopup="dialog"
                  @click="open">
                  <LiveIcon name="trash" />
                </button>
              </ConfirmPopover>
            </div>
          </article>
        </div>
        <div v-else class="live-card-empty">
          <LiveIcon name="layers" />
          <strong>还没有商品视频片段</strong>
          <p>在录制分段中点击“切分商品”，按该分段内的商品讲解区间裁切，原始录制文件保留。</p>
        </div>
        <div class="live-card-foot">
          <LiveIcon name="video" />
          切点受关键帧精度限制，导入后可在粗剪中微调。
        </div>
      </section>
    </div>
  </div>
</template>
