"""主动找你聊天。

隔一段时间没聊时，在允许的时间段里随机挑时机，用当前私聊人设主动发一条消息。
所有规则都在下面的设置区，改完在 AstrBot 后台“插件”页面重载本插件即可。
"""

import asyncio
import json
import os
import random
import time
from datetime import datetime

from quart import jsonify

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.event.filter import EventMessageType
from astrbot.api.star import Context, Star
from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path

# ===================== 设置区 =====================
# 主动找谁（只支持私聊会话）
TARGETS = ["qq_xiaohao:FriendMessage:YOUR_OWNER_QQ"]
# 允许主动发消息的时间段（24 小时制，小时可以带小数：22.5 = 22:30）
ACTIVE_START_HOUR = 10
ACTIVE_END_HOUR = 22.5
# 距离上一次聊天至少多少小时，才考虑主动找
MIN_SILENCE_HOURS = 3
# 每天最多主动发几条
MAX_PER_DAY = 2
# 两条主动消息之间至少隔多少小时
MIN_GAP_HOURS = 4
# 多久“想一次”要不要发（分钟），以及每次真的发出去的概率
CHECK_INTERVAL_MINUTES = 20
SEND_PROBABILITY = 0.12
# 上一条主动消息对方还没回时，不再发新的
WAIT_FOR_REPLY = True
# 生成时参考最近多少条聊天记录
CONTEXT_MESSAGES = 16
# 模型（留空则用该会话当前使用的模型）
CHAT_PROVIDER_ID = ""
# =================================================

PLUGIN_NAME = "proactive_chat"
WEEKDAYS = "一二三四五六日"

INSTRUCTION = """（这是给你的内部提示，不是对方发来的消息。）
现在是{now}，星期{weekday}。你们已经大约{hours}没聊天了。
请你像好朋友一样，主动给对方发一条QQ消息。可以接着你们之前聊过的事关心一下（比如问问后来怎么样了），也可以分享一件小事、提醒吃饭休息、或者就是想他了打个招呼。
要求：
- 只写你要发的那一两句话，口语自然，符合你的人设
- 不要解释，不要加引号，不要提这是系统让你发的，不要说"好久不见"这类太正式的话
- 结合现在的时间（早上、中午、晚上）自然一点
- 如果实在没什么自然的话想说，只回复 [SKIP]"""


def _content_to_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for p in content:
            if isinstance(p, dict) and p.get("type") == "text":
                parts.append(str(p.get("text") or ""))
        return "".join(parts)
    return ""


def _hours_text(hours: float) -> str:
    if hours >= 48:
        return f"{int(hours // 24)}天"
    if hours >= 24:
        return "一天多"
    return f"{max(1, int(hours))}个小时"


class ProactiveChat(Star):
    def __init__(self, context: Context) -> None:
        super().__init__(context)
        self._task = None
        self._lock = asyncio.Lock()
        self._data_dir = os.path.join(get_astrbot_plugin_data_path(), PLUGIN_NAME)
        self._state_path = os.path.join(self._data_dir, "state.json")
        self._state = self._load_state()
        context.register_web_api(
            f"/{PLUGIN_NAME}/status", self._api_status, ["GET"], "主动聊天状态"
        )
        context.register_web_api(
            f"/{PLUGIN_NAME}/send_now", self._api_send_now, ["POST"], "立即主动发一条"
        )

    # ---------- 状态 ----------
    def _load_state(self) -> dict:
        try:
            with open(self._state_path, encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception:  # noqa: BLE001
            pass
        return {}

    def _save_state(self) -> None:
        try:
            os.makedirs(self._data_dir, exist_ok=True)
            tmp = self._state_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._state, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self._state_path)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[proactive_chat] 保存状态失败：{exc}")

    def _target_state(self, umo: str) -> dict:
        st = self._state.setdefault(umo, {})
        st.setdefault("last_chat_ts", time.time())
        st.setdefault("proactive_ts", [])
        st.setdefault("awaiting_reply", False)
        return st

    # ---------- 生命周期 ----------
    async def initialize(self) -> None:
        for umo in TARGETS:
            self._target_state(umo)
        self._save_state()
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())
        logger.info(f"[proactive_chat] 已启动，对象：{TARGETS}")

    async def terminate(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()

    # ---------- 记录对方的消息 ----------
    @filter.event_message_type(EventMessageType.ALL)
    async def record_chat(self, event: AstrMessageEvent):
        umo = event.unified_msg_origin
        if umo not in TARGETS:
            return
        st = self._target_state(umo)
        st["last_chat_ts"] = time.time()
        st["awaiting_reply"] = False
        self._save_state()

    # ---------- 定时检查 ----------
    async def _loop(self) -> None:
        await asyncio.sleep(60)
        while True:
            try:
                for umo in TARGETS:
                    if self._should_send(umo) and random.random() < SEND_PROBABILITY:
                        await self._send_proactive(umo)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.exception(f"[proactive_chat] 检查时出错：{exc!r}")
            await asyncio.sleep(CHECK_INTERVAL_MINUTES * 60)

    def _should_send(self, umo: str) -> bool:
        now = datetime.now()
        hour = now.hour + now.minute / 60
        if not (ACTIVE_START_HOUR <= hour < ACTIVE_END_HOUR):
            return False
        st = self._target_state(umo)
        t = time.time()
        if WAIT_FOR_REPLY and st.get("awaiting_reply"):
            return False
        if t - float(st.get("last_chat_ts") or 0) < MIN_SILENCE_HOURS * 3600:
            return False
        sent = [float(x) for x in st.get("proactive_ts") or []]
        today = now.strftime("%Y-%m-%d")
        sent_today = [x for x in sent if datetime.fromtimestamp(x).strftime("%Y-%m-%d") == today]
        if len(sent_today) >= MAX_PER_DAY:
            return False
        if sent and t - max(sent) < MIN_GAP_HOURS * 3600:
            return False
        return True

    # ---------- 生成并发送 ----------
    async def _persona_prompt(self, umo: str, conv) -> str:
        persona_id = getattr(conv, "persona_id", None) if conv else None
        if not persona_id or persona_id == "[%None]":
            cfg = self.context.get_config(umo=umo)
            persona_id = (
                cfg.get("agent_runner", {}).get("config", {}).get("persona", {}).get("persona_id")
            )
        persona = self.context.persona_manager.get_persona_v3_by_id(persona_id)
        if not persona:
            return ""
        return str(persona.get("prompt") or "")

    async def _bot_online(self, umo: str) -> bool:
        """小号掉线时 NapCat 连不上，直接跳过，不浪费模型调用、也不计数"""
        try:
            platform = self.context.get_platform_inst(umo.split(":", 1)[0])
            bot = getattr(platform, "bot", None) if platform else None
            if bot is None:
                return False
            status = await bot.call_action("get_status")
            return bool((status or {}).get("online", True))
        except Exception:  # noqa: BLE001
            return False

    async def _send_proactive(self, umo: str, force: bool = False) -> str:
        if not await self._bot_online(umo):
            logger.warning("[proactive_chat] 小号当前不在线（NapCat 未连接或 QQ 已掉线），这次跳过")
            return "小号不在线，跳过"
        async with self._lock:
            conv_mgr = self.context.conversation_manager
            cid = await conv_mgr.get_curr_conversation_id(umo)
            conv = await conv_mgr.get_conversation(umo, cid) if cid else None
            history = []
            if conv:
                try:
                    history = json.loads(conv.history or "[]")
                except Exception:  # noqa: BLE001
                    history = []
            contexts = []
            for m in history[-CONTEXT_MESSAGES:]:
                role = m.get("role") if isinstance(m, dict) else None
                if role not in ("user", "assistant"):
                    continue
                text = _content_to_text(m.get("content")).strip()
                if text:
                    contexts.append({"role": role, "content": text})

            st = self._target_state(umo)
            hours = (time.time() - float(st.get("last_chat_ts") or time.time())) / 3600
            now = datetime.now()
            prompt = INSTRUCTION.format(
                now=now.strftime("%m月%d日 %H:%M"),
                weekday=WEEKDAYS[now.weekday()],
                hours=_hours_text(hours),
            )
            provider_id = CHAT_PROVIDER_ID
            if not provider_id:
                prov = self.context.get_using_provider(umo=umo)
                provider_id = prov.meta().id if prov else ""
            if not provider_id:
                return "没有可用的模型"

            resp = await self.context.llm_generate(
                chat_provider_id=provider_id,
                system_prompt=await self._persona_prompt(umo, conv),
                contexts=contexts,
                prompt=prompt,
            )
            text = (getattr(resp, "completion_text", "") or "").strip().strip('"“”')
            if not text or "[SKIP]" in text.upper():
                logger.info("[proactive_chat] 模型觉得这次没什么想说的，跳过")
                return "跳过"

            # 先记账再发送：即使发送环节报错，也不会在下次检查时重复发
            st["proactive_ts"] = ([float(x) for x in st.get("proactive_ts") or []] + [time.time()])[-20:]
            st["awaiting_reply"] = True
            self._save_state()
            try:
                await self.context.send_message(umo, MessageChain().message(text))
            except Exception as exc:  # noqa: BLE001
                logger.exception(f"[proactive_chat] 发送失败（本次已计数，不会重发）：{exc!r}")
                st["awaiting_reply"] = False
                self._save_state()
                return f"发送失败：{exc!r}"
            if cid:
                try:
                    await conv_mgr.add_message_pair(
                        cid,
                        {"role": "user", "content": "（你有一阵子没和对方聊天了，于是主动给他发了条消息）"},
                        {"role": "assistant", "content": text},
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"[proactive_chat] 写入聊天记录失败：{exc}")
            logger.info(f"[proactive_chat] 已主动发送给 {umo}：{text}")
            return text

    # ---------- 后台接口（测试用） ----------
    async def _api_status(self):
        out = {}
        for umo in TARGETS:
            st = self._target_state(umo)
            out[umo] = {
                "eligible_now": self._should_send(umo),
                "last_chat": datetime.fromtimestamp(float(st["last_chat_ts"])).strftime("%Y-%m-%d %H:%M"),
                "awaiting_reply": st.get("awaiting_reply"),
                "proactive_sent": [
                    datetime.fromtimestamp(float(x)).strftime("%Y-%m-%d %H:%M")
                    for x in st.get("proactive_ts") or []
                ],
            }
        return jsonify({"targets": out, "running": bool(self._task and not self._task.done())})

    async def _api_send_now(self):
        try:
            text = await self._send_proactive(TARGETS[0], force=True)
            return jsonify({"ok": True, "text": text})
        except Exception as exc:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(exc)}), 500
