"""表情包发送开关。

表情包管理器（meme_manager）默认在 AI 想配图时就发。本插件在它之前执行：
只有当对方这条消息里带了表情包（收藏表情、商城表情、QQ 小黄脸），
或者文字里提到 TRIGGER_WORDS 时，才放行表情包；否则只发文字。
"""

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Face, Image
from astrbot.api.star import Context, Star

# 文字里出现这些词就放行
TRIGGER_WORDS = ("表情", "斗图")
# 普通照片（相册图片、截图）是否也算表情包：False = 不算
COUNT_PHOTOS_AS_STICKER = False

_STICKER_SEGMENT_TYPES = {"face", "mface", "marketface", "dice", "rps"}


def _segment_is_sticker(seg: dict) -> bool:
    seg_type = seg.get("type")
    data = seg.get("data") or {}
    if seg_type in _STICKER_SEGMENT_TYPES:
        return True
    if seg_type != "image":
        return False
    if COUNT_PHOTOS_AS_STICKER:
        return True
    summary = str(data.get("summary") or "")
    if "表情" in summary:
        return True
    sub_type = data.get("sub_type", data.get("subType"))
    if sub_type is None:
        # 拿不到类型信息时按表情包处理，宁可多放行
        return not summary
    try:
        return int(sub_type) == 1
    except (TypeError, ValueError):
        return False


def _incoming_has_sticker(event: AstrMessageEvent) -> bool:
    raw = getattr(event.message_obj, "raw_message", None)
    segments = None
    try:
        segments = raw["message"] if raw is not None else None
    except Exception:  # noqa: BLE001
        segments = getattr(raw, "message", None)
    if isinstance(segments, list):
        return any(isinstance(s, dict) and _segment_is_sticker(s) for s in segments)
    # 兜底：用 AstrBot 解析后的消息段判断
    return any(isinstance(c, (Face, Image)) for c in event.get_messages())


class MemeGate(Star):
    def __init__(self, context: Context) -> None:
        super().__init__(context)

    # 优先级高于表情包管理器（99999），在它附加图片之前先做判断
    @filter.on_decorating_result(priority=1000000)
    async def gate(self, event: AstrMessageEvent):
        result = event.get_result()
        if result is None or not result.is_llm_result():
            return

        text = event.message_str or ""
        if any(w in text for w in TRIGGER_WORDS) or _incoming_has_sticker(event):
            return  # 放行

        # AI 只回了一张表情、没有文字时保留，否则这次就什么都没回
        if event.get_extra("meme_manager_image_only_reply"):
            return

        event.set_extra("meme_manager_legacy_probability_hit", False)
        logger.debug("[meme_gate] 对方没发表情包也没提到表情，本次不发表情包")
