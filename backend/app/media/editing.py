from ..core.domain import Asset, Batch, Candidate, Canvas, Clip, Plan, Product, Span, Subtitle, Transcript


def make_batches(assets: list[Asset], window_us: int = 1_800_000_000, flush: bool = False) -> list[Batch]:
    if window_us <= 0:
        raise ValueError("窗口必须大于零")
    result, spans, total = [], [], 0
    for asset in assets:
        cursor = 0
        while cursor < asset.duration_us:
            size = min(window_us - total, asset.duration_us - cursor)
            spans.append(Span(source_asset_id=asset.id, start_us=cursor, end_us=cursor + size))
            total += size
            cursor += size
            if total == window_us:
                result.append(Batch(index=len(result), spans=spans, duration_us=total, partial=False))
                spans, total = [], 0
    if spans and flush:
        result.append(Batch(index=len(result), spans=spans, duration_us=total, partial=True))
    return result


def remap_subtitles(clips: list[Clip], transcripts: list[Transcript]) -> list[Subtitle]:
    result = []
    for clip in clips:
        for sentence in transcripts:
            if sentence.source_asset_id != clip.source_asset_id:
                continue
            start = max(sentence.start_us, clip.source_start_us)
            end = min(sentence.end_us, clip.source_end_us)
            if start >= end:
                continue
            # Full sentences only: a clipped transcript would claim omitted words were spoken.
            if start != sentence.start_us or end != sentence.end_us:
                continue
            result.append(Subtitle(
                start_us=clip.timeline_start_us + start - clip.source_start_us,
                end_us=clip.timeline_start_us + end - clip.source_start_us,
                text=sentence.text,
            ))
    return sorted(result, key=lambda line: line.start_us)


def build_plan(product: Product, candidates: list[Candidate], assets: dict[str, Asset],
               transcripts: list[Transcript], target_us: int = 60_000_000,
               instructions: str = "", exclude_tags: list[str] | None = None,
               ordered_ids: list[str] | None = None) -> Plan:
    if not product.confirmed:
        raise ValueError("请先确认商品归属，再生成单款方案")
    banned = set(exclude_tags or [])
    eligible = {c.id: c for c in candidates if c.product_id == product.id and not banned.intersection(c.tags)}
    if ordered_ids is not None:
        if len(set(ordered_ids)) != len(ordered_ids) or any(i not in eligible for i in ordered_ids):
            raise ValueError("选片结果包含重复、跨款或被排除的片段")
        selected = [eligible[i] for i in ordered_ids]
    else:
        role = {"hook": 0, "front": 1, "side": 1, "back": 1, "walk": 1,
                "fabric": 2, "detail": 2, "fit": 2, "size": 3, "outfit": 3}
        selected = sorted(eligible.values(), key=lambda c: (min((role.get(t, 2) for t in c.tags), default=2), -c.quality))
    clips, cursor = [], 0
    warnings = []
    for candidate in selected:
        asset = assets.get(candidate.source_asset_id)
        if asset is None or candidate.end_us > asset.duration_us:
            raise ValueError("片段引用未知素材或超出源素材时长")
        if any(clip.source_asset_id == asset.id and candidate.start_us < clip.source_end_us and candidate.end_us > clip.source_start_us for clip in clips):
            if ordered_ids is not None:
                raise ValueError("选中的片段包含重叠源区间")
            continue
        duration = candidate.end_us - candidate.start_us
        if ordered_ids is None and clips and cursor + duration > target_us + 15_000_000:
            continue
        clips.append(Clip(source_asset_id=asset.id, source_start_us=candidate.start_us,
                          source_end_us=candidate.end_us, timeline_start_us=cursor,
                          candidate_id=candidate.id))
        cursor += duration
        if ordered_ids is None and cursor >= target_us:
            break
    if not clips:
        raise ValueError("没有符合要求的有效片段")
    if cursor < max(0, target_us - 15_000_000):
        warnings.append("素材不足：保留完整内容，未强行补足目标时长")
    if cursor > target_us + 15_000_000:
        warnings.append("片段较长：为保留完整内容超过目标时长，请审核切点")
    first = assets[clips[0].source_asset_id]
    if any(t.timing == "audio_chunk" and any(
            t.source_asset_id == c.source_asset_id and t.start_us < c.source_end_us
            and t.end_us > c.source_start_us for c in clips) for t in transcripts):
        warnings.append("语音字幕按音频短段粗略定位，请在剪映中校准；被切断的字幕段不会导出，需人工补齐")
    return Plan(product_id=product.id, target_duration_us=target_us,
                canvas=Canvas(width=first.width // 2 * 2, height=first.height // 2 * 2),
                clips=clips, subtitles=remap_subtitles(clips, transcripts),
                instructions=instructions, warnings=warnings)


def validate_plan(plan: Plan, product: Product, candidates: dict[str, Candidate], assets: dict[str, Asset]):
    if not product.confirmed or plan.product_id != product.id:
        raise ValueError("方案必须引用已确认商品")
    for clip in plan.clips:
        candidate = candidates.get(clip.candidate_id)
        asset = assets.get(clip.source_asset_id)
        if candidate is None or candidate.product_id != plan.product_id:
            raise ValueError("方案包含未归属或其他款式片段")
        if asset is None or clip.source_asset_id != candidate.source_asset_id:
            raise ValueError("切片来源与候选片段不一致")
        if clip.source_end_us > asset.duration_us or clip.source_start_us < candidate.start_us or clip.source_end_us > candidate.end_us:
            raise ValueError("切点超出已识别商品片段或源素材范围")


def srt_text(subtitles: list[Subtitle]) -> str:
    def stamp(us: int):
        ms = us // 1000
        hours, remainder = divmod(ms, 3_600_000)
        minutes, remainder = divmod(remainder, 60_000)
        seconds, millis = divmod(remainder, 1000)
        return f"{hours:02}:{minutes:02}:{seconds:02},{millis:03}"
    return "\n".join(f"{i}\n{stamp(s.start_us)} --> {stamp(s.end_us)}\n{s.text}\n"
                      for i, s in enumerate(subtitles, 1))
