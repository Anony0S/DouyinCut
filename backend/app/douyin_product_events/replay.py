from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


def replay_samples(path: Path) -> dict:
    methods: Counter[str] = Counter()
    shapes: dict[str, set[str]] = {}
    rows = 0
    with path.open("r", encoding="utf-8") as source:
        for line_number, line in enumerate(source, 1):
            if not line.strip():
                continue
            if len(line) > 1_000_000:
                raise ValueError(f"JSONL 第 {line_number} 行超过 1 MB")
            try:
                sample = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"JSONL 第 {line_number} 行格式无效") from exc
            method = sample.get("method") if isinstance(sample, dict) else None
            summary = sample.get("summary") if isinstance(sample, dict) else None
            if not isinstance(method, str) or not isinstance(summary, dict):
                raise ValueError(f"JSONL 第 {line_number} 行缺少 method 或 summary")
            if "fields" not in summary or not isinstance(summary["fields"], list):
                raise ValueError(f"JSONL 第 {line_number} 行缺少脱敏 fields 结构")
            methods[method] += 1
            shape = json.dumps(summary["fields"], sort_keys=True, separators=(",", ":"))
            shapes.setdefault(method, set()).add(shape)
            rows += 1
            if rows > 1000:
                raise ValueError("JSONL 样本数量超过 1000 条")
    return {
        "samples": rows,
        "methods": dict(sorted(methods.items())),
        "unique_shapes": {method: len(values) for method, values in sorted(shapes.items())},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="回放并汇总本地脱敏抖音商品消息 JSONL 样本")
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    print(json.dumps(replay_samples(args.path), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
