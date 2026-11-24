import json
import time

from pydantic import Field, field_validator

from ..core.domain import Model, new_id
from ..core.storage import Store


_CONFIG_ID = "cover_prompts"
_DEFAULT_PROMPT = {
    "id": "default",
    "name": "商品上身效果",
    "prompt": "生成一张用于抖音商品短视频的竖版封面，画面比例严格为3:4。请从商品名称中提炼一个简短、准确、易读的中文标题。封面必须同时展示人物上身效果图和商品单品图：人物穿着商品作为主要视觉主体，清楚呈现真实上身效果；将同款未上身的单品图以较小尺寸放在人物旁边，作为辅助对照，不能省略或用文字代替。两种商品展示需款式、颜色和细节一致；单品图可用平铺或电商静物形式，不要再添加其他人物或商品。标题与人物主体清晰醒目、互不遮挡，商品单品图保持可辨识但视觉权重低于上身图。若参考图包含人物，尽量忠实保留人物穿着商品的效果；若参考图仅为单品图，则以它还原同款商品并生成自然可信的上身效果，同时保留较小的单品展示。采用简洁、有质感且衬托商品的背景，标题、人物和单品图避开边缘并留出安全空间。标题精炼，不添加价格、折扣、功效或名称之外的营销承诺。不要添加水印、平台标识、无关文字、额外商品或与参考商品不符的细节。",
}


class CoverPromptDraft(Model):
    name: str = Field(min_length=1, max_length=80)
    prompt: str = Field(min_length=1, max_length=4000)

    @field_validator("name", "prompt", mode="before")
    @classmethod
    def trim(cls, value):
        return value.strip() if isinstance(value, str) else value


def _read_prompts(conn):
    row = conn.execute("SELECT body FROM objects WHERE kind='config' AND id=? AND revision=1",
                       (_CONFIG_ID,)).fetchone()
    return json.loads(row["body"])["prompts"] if row else [_DEFAULT_PROMPT.copy()]


def list_cover_prompts(store: Store):
    with store.connection() as conn:
        return _read_prompts(conn)


def change_cover_prompt(store: Store, prompt_id: str | None, draft: CoverPromptDraft | None):
    with store.connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        prompts = _read_prompts(conn)
        if prompt_id is None and draft is not None:
            result = {"id": new_id("coverprompt"), **draft.model_dump()}
            prompts.append(result)
        else:
            index = next((i for i, item in enumerate(prompts) if item["id"] == prompt_id), None)
            if index is None:
                raise KeyError("提示词已删除，请重新加载")
            if draft is None:
                prompts.pop(index)
                result = {"deleted": True}
            else:
                result = {"id": prompt_id, **draft.model_dump()}
                prompts[index] = result
        conn.execute("INSERT OR REPLACE INTO objects VALUES ('config', ?, 1, ?, ?)",
                     (_CONFIG_ID, json.dumps({"id": _CONFIG_ID, "prompts": prompts}, ensure_ascii=False), time.time()))
        return result
