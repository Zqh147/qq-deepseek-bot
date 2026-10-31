"""掉线补回复。

小号掉线期间收到的消息，AstrBot 不会自动处理。本插件提供两个后台接口：
- GET  /catch_up/scan?since=<时间戳>   列出白名单会话里漏掉的消息（私聊：对方发的全部；群聊：@小号的）
- POST /catch_up/reply {"since":..., "sessions":[...]}  为这些会话各补发一条回复
"""

import json
import time
from datetime import datetime

from quart import jsonify, request

from astrbot.api import logger
from astrbot.api.event import MessageChain
from astrbot.api.star import Context, Star

PLATFORM_ID = "qq_xiaohao"
HISTORY_COUNT = 60
CONTEXT_MESSAGES = 16

INSTRUCTION = """（这是给你的内部提示，不是对方发来的消息。）
你刚才掉线了一段时间（大约从{since}到{now}），现在才看到这期间{who}发来的这些消息：
{lines}

请像平时一样自然地回复一条消息：可以先简单说一句刚才没看到消息/掉线了，然后回应他们说的内容。
{extra}只写你要发的话，不要解释，不要加引号。"""


def _seg_text(segs) -> str:
    if isinstance(segs, str):
        return segs
    out = []
    for s in segs or []:
        t = s.get("type")
        d = s.get("data") or {}
        if t == "text":
            out.append(str(d.get("text") or ""))
        elif t == "image":
            out.append("[图片]" if str(d.get("sub_type", "0")) != "1" else "[表情包]")
        elif t in ("face", "mface"):
            out.append("[表情]")
        elif t == "record":
            out.append("[语音]")
        elif t == "at":
            out.append(f"@{d.get('qq')}")
        elif t == "reply":
            out.append("[引用]")
        elif t == "video":
            out.append("[视频]")
        elif t == "file":
            out.append("[文件]")
    return "".join(out).strip()


def _content_to_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(str(p.get("text") or "") for p in content if isinstance(p, dict) and p.get("type") == "text")
    return ""


class CatchUp(Star):
    def __init__(self, context: Context) -> None:
        super().__init__(context)
        context.register_web_api("/catch_up/scan", self._api_scan, ["GET"], "列出漏掉的消息")
        context.register_web_api("/catch_up/reply", self._api_reply, ["POST"], "补发回复")

    def _bot(self):
        inst = self.context.get_platform_inst(PLATFORM_ID)
        if inst is None:
            raise RuntimeError("找不到 QQ 平台适配器")
        return inst.bot

    def _sessions(self) -> list[str]:
        cfg = self.context.get_config()
        wl = cfg.get("platform_settings", {}).get("id_whitelist", []) or []
        return [s for s in wl if isinstance(s, str) and s.startswith(PLATFORM_ID + ":")]

    async def _missed(self, since: float) -> dict:
        bot = self._bot()
        info = await bot.call_action("get_login_info")
        self_id = str(info.get("user_id"))
        result = {}
        for umo in self._sessions():
            _, mtype, sid = umo.split(":", 2)
            try:
                if mtype == "FriendMessage":
                    ret = await bot.call_action("get_friend_msg_history", user_id=int(sid), count=HISTORY_COUNT)
                else:
                    ret = await bot.call_action("get_group_msg_history", group_id=int(sid), count=HISTORY_COUNT)
            except Exception as exc:  # noqa: BLE001
                result[umo] = {"error": str(exc)}
                continue
            msgs = (ret or {}).get("messages") or []
            missed = []
            for m in msgs:
                if float(m.get("time") or 0) <= since:
                    continue
                sender = m.get("sender") or {}
                uid = str(sender.get("user_id") or m.get("user_id") or "")
                if uid == self_id:
                    continue
                segs = m.get("message")
                if mtype == "GroupMessage":
                    ats = [s for s in (segs or []) if isinstance(s, dict) and s.get("type") == "at"]
                    if not any(str((a.get("data") or {}).get("qq")) == self_id for a in ats):
                        continue
                text = _seg_text(segs)
                if not text:
                    continue
                name = sender.get("card") or sender.get("nickname") or uid
                missed.append({
                    "time": datetime.fromtimestamp(float(m["time"])).strftime("%H:%M"),
                    "name": name,
                    "uid": uid,
                    "text": text.replace(f"@{self_id}", "").strip(),
                })
            if missed:
                result[umo] = {"messages": missed}
        return result

    async def _api_scan(self):
        try:
            since = float(request.args.get("since") or (time.time() - 12 * 3600))
            return jsonify(await self._missed(since))
        except Exception as exc:  # noqa: BLE001
            logger.exception("[catch_up] 扫描失败")
            return jsonify({"error": str(exc)}), 500

    async def _persona_prompt(self, umo: str, conv) -> str:
        pid = getattr(conv, "persona_id", None) if conv else None
        if not pid or pid == "[%None]":
            cfg = self.context.get_config(umo=umo)
            pid = cfg.get("agent_runner", {}).get("config", {}).get("persona", {}).get("persona_id")
        p = self.context.persona_manager.get_persona_v3_by_id(pid)
        return str(p.get("prompt") or "") if p else ""

    async def _reply_one(self, umo: str, missed: list, since: float) -> str:
        conv_mgr = self.context.conversation_manager
        cid = await conv_mgr.get_curr_conversation_id(umo)
        if not cid:
            cid = await conv_mgr.new_conversation(umo)
        conv = await conv_mgr.get_conversation(umo, cid)
        history = []
        try:
            history = json.loads(conv.history or "[]") if conv else []
        except Exception:  # noqa: BLE001
            history = []
        contexts = []
        for m in history[-CONTEXT_MESSAGES:]:
            if isinstance(m, dict) and m.get("role") in ("user", "assistant"):
                t = _content_to_text(m.get("content")).strip()
                if t:
                    contexts.append({"role": m["role"], "content": t})
        is_group = ":GroupMessage:" in umo
        lines = "\n".join(f"[{x['time']}] {x['name']}：{x['text']}" for x in missed)
        prompt = INSTRUCTION.format(
            since=datetime.fromtimestamp(since).strftime("%H:%M"),
            now=datetime.now().strftime("%H:%M"),
            who="群里大家" if is_group else "对方",
            lines=lines,
            extra="群里有好几个人@你时，按昵称分别简短回应一下。" if is_group else "",
        )
        prov = self.context.get_using_provider(umo=umo)
        if not prov:
            raise RuntimeError("没有可用的模型")
        resp = await self.context.llm_generate(
            chat_provider_id=prov.meta().id,
            system_prompt=await self._persona_prompt(umo, conv),
            contexts=contexts,
            prompt=prompt,
        )
        text = (getattr(resp, "completion_text", "") or "").strip().strip('"“”')
        if not text:
            raise RuntimeError("模型没有生成内容")
        await self.context.send_message(umo, MessageChain().message(text))
        try:
            await conv_mgr.add_message_pair(
                cid,
                {"role": "user", "content": "（你掉线期间收到的消息）\n" + lines},
                {"role": "assistant", "content": text},
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[catch_up] 写入聊天记录失败：{exc}")
        logger.info(f"[catch_up] 已补回复 {umo}：{text}")
        return text

    async def _api_reply(self):
        try:
            data = await request.get_json() or {}
            since = float(data.get("since") or (time.time() - 12 * 3600))
            only = set(data.get("sessions") or [])
            missed = await self._missed(since)
            out = {}
            for umo, info in missed.items():
                if only and umo not in only:
                    continue
                if "messages" not in info:
                    out[umo] = info
                    continue
                try:
                    out[umo] = {"sent": await self._reply_one(umo, info["messages"], since)}
                except Exception as exc:  # noqa: BLE001
                    logger.exception(f"[catch_up] 补回复 {umo} 失败")
                    out[umo] = {"error": str(exc)}
            return jsonify(out)
        except Exception as exc:  # noqa: BLE001
            logger.exception("[catch_up] 补回复失败")
            return jsonify({"error": str(exc)}), 500
