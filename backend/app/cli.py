"""Offline-capable vertical slice; does not import FastAPI or cloud SDKs."""
import argparse
import json
from pathlib import Path

from .core.config import Settings
from .core.domain import Asset, Candidate, Plan, Product, Transcript, new_id
from .media.editing import build_plan, validate_plan
from .media.exporting import export_plan
from .media.processing import diagnostics, import_asset
from .core.storage import Store


def main():
    parser = argparse.ArgumentParser(description="DouyinCut 核心处理命令")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor")
    imp = sub.add_parser("import")
    imp.add_argument("path", type=Path)
    render = sub.add_parser("render")
    render.add_argument("recipe", type=Path, help="人工审核的单款裁切配方 JSON")
    args = parser.parse_args()
    settings = Settings.from_env()
    settings.prepare()
    store = Store(settings.database)
    if args.command == "doctor":
        print(json.dumps({**diagnostics(settings), "data_dir": str(settings.data_dir)}, ensure_ascii=False, indent=2))
    elif args.command == "import":
        asset = import_asset(args.path, settings)
        store.put("asset", asset.model_dump())
        print(asset.model_dump_json(indent=2))
    else:
        recipe = json.loads(args.recipe.read_text(encoding="utf-8-sig"))
        product = Product(name=recipe["product_name"], variant=recipe.get("variant", ""), confirmed=True)
        assets, candidates, transcripts = {}, [], []
        for item in recipe["clips"]:
            path = Path(item["path"])
            if not path.is_absolute():
                path = args.recipe.resolve().parent / path
            asset = import_asset(path, settings)
            assets[asset.id] = asset
            candidates.append(Candidate(source_asset_id=asset.id, product_id=product.id,
                                        start_us=item["start_us"], end_us=item["end_us"],
                                        tags=item.get("tags", []), reason="用户人工确认的源区间"))
            for sentence in item.get("transcripts", []):
                transcripts.append(Transcript(source_asset_id=asset.id, **sentence))
        plan = build_plan(product, candidates, assets, transcripts,
                          target_us=recipe.get("target_duration_us", 60_000_000),
                          ordered_ids=[c.id for c in candidates])
        plan = Plan.model_validate({**plan.model_dump(), "status": "approved"})
        validate_plan(plan, product, {c.id: c for c in candidates}, assets)
        result = export_plan(plan, assets, settings, new_id("cli"), package=True)
        store.put("product", product.model_dump())
        for asset in assets.values():
            store.put("asset", asset.model_dump())
        for candidate in candidates:
            store.put("candidate", candidate.model_dump())
        store.put("plan", plan.model_dump())
        store.put("export", result)
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
