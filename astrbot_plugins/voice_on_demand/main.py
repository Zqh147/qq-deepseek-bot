"""按需语音。

消息里带上 TRIGGER_WORDS（例如"发个语音""用语音说"）时，把这次回复整段转成一条语音。
- 群聊：GROUP_ANYONE=True 时谁都能触发，否则只认 VOICE_OWNER_IDS；超过 GROUP_MAX_CHARS 字改发文字
- 私聊：PRIVATE_ANYONE=True 时谁都能触发（私聊本来就受白名单限制）
私聊里没触发时，仍按 AstrBot 自带的随机语音概率。
"""

import re

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Plain, Record
from astrbot.api.star import Context, Star
from astrbot.core.platform.message_type import MessageType

# 可以触发语音的 QQ 号（大号）
VOICE_OWNER_IDS = {"YOUR_OWNER_QQ"}  # 改成你的大号 QQ 号
# 群聊里是否任何人都能触发（False = 只认 VOICE_OWNER_IDS）
GROUP_ANYONE = True
# 群聊里回复超过多少字就不转语音、直接发文字（0 = 不限制）
GROUP_MAX_CHARS = 50
# 私聊里是否任何人都能触发（False = 私聊也只认 VOICE_OWNER_IDS）
PRIVATE_ANYONE = True
# 消息里包含这些词之一才触发
TRIGGER_WORDS = ("语音",)
# 用哪个声音：跟随当前配置里选的语音提供商（/声音 命令或后台 配置 -> 文字转语音 切换）；
# 配置里没选时才用这个备用 ID
TTS_PROVIDER_ID = "volc_xiaohe"

_MEME_TAG = re.compile(r"&&[^&\s]{1,40}&&")


class VoiceOnDemand(Star):
    def __init__(self, context: Context) -> None:
        super().__init__(context)

    # 优先级低于表情包插件（99999），在它处理完表情标记之后再转语音
    @filter.on_decorating_result(priority=-100)
    async def to_voice(self, event: AstrMessageEvent):
        msg_type = event.get_message_type()
        is_owner = str(event.get_sender_id()) in VOICE_OWNER_IDS
        if msg_type == MessageType.GROUP_MESSAGE:
            if not (GROUP_ANYONE or is_owner):
                return
        elif msg_type == MessageType.FRIEND_MESSAGE:
            if not (PRIVATE_ANYONE or is_owner):
                return
        else:
            return
        if not any(w in (event.message_str or "") for w in TRIGGER_WORDS):
            return

        result = event.get_result()
        if result is None or not result.chain or not result.is_llm_result():
            return

        text = "".join(c.text for c in result.chain if isinstance(c, Plain))
        text = _MEME_TAG.sub("", text).strip()
        if len(text) < 2:
            return
        if (
            msg_type == MessageType.GROUP_MESSAGE
            and GROUP_MAX_CHARS > 0
            and len("".join(text.split())) > GROUP_MAX_CHARS
        ):
            logger.info(f"[voice_on_demand] 回复超过 {GROUP_MAX_CHARS} 字，改发文字")
            return

        cfg = self.context.get_config(umo=event.unified_msg_origin)
        pid = (cfg.get("provider_tts_settings", {}) or {}).get("provider_id") or TTS_PROVIDER_ID
        provider = self.context.get_provider_by_id(pid) or self.context.get_provider_by_id(TTS_PROVIDER_ID)
        if provider is None:
            logger.warning(f"[voice_on_demand] 找不到 TTS 提供商 {TTS_PROVIDER_ID}，改发文字")
            return

        try:
            audio_path = await provider.get_audio(text)
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[voice_on_demand] 语音生成失败，改发文字：{exc}")
            return
        if not audio_path:
            return

        others = [c for c in result.chain if not isinstance(c, Plain)]
        result.chain = [Record(file=audio_path, url=audio_path, text=text), *others]
        logger.info(f"[voice_on_demand] 已按需转为语音：{text[:30]}")
