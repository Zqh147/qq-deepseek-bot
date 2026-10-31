"""RVC 音色语音 + 声音切换 + 本地语音识别。

0. 注册语音转文字提供商类型 local_sensevoice：把收到的语音交给常驻服务里的 SenseVoiceSmall 识别。

1. 注册一种语音提供商类型 rvc_tts：先让"底层 TTS"（默认小何）说出来，再交给常驻 RVC 变声服务
   （<COVER_DIR>\\rvc_server.py）换成指定音色。变声失败时自动退回底层 TTS 的原声，不会没声音。
2. 管理员命令（私聊直接发，群里 @ 小号或带 / 前缀）：
     /声音            查看现在用哪个声音、有哪些可选
     /声音 <名字>      换成别的声音：可以写下面 ALIASES 里的别名，也可以直接写语音提供商 ID
   切换会同时改所有配置文件（私聊、群聊），也可以在 AstrBot 后台 配置 -> 文字转语音 里改。
"""

import asyncio
import os
import subprocess

import aiohttp

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star
from astrbot.core.provider.entities import ProviderType
from astrbot.core.provider.provider import STTProvider, TTSProvider
from astrbot.core.provider.register import provider_cls_map, register_provider_adapter
from astrbot.core.utils.astrbot_path import get_astrbot_temp_path
from astrbot.core.utils.datetime_utils import generate_timestamp_id

PROVIDER_TYPE = "rvc_tts"
# 翻唱/本地语音程序所在文件夹（cover/）。默认是和 AstrBot 文件夹并列的 cover：
#   <根目录>\AstrBot\data\plugins\<本插件>\main.py  ->  <根目录>\cover
# 放在别处时用环境变量 QQBOT_COVER_DIR 指定
_BOT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
COVER_DIR = os.environ.get("QQBOT_COVER_DIR") or os.path.join(_BOT_ROOT, "cover")
PY = os.path.join(COVER_DIR, "Applio", "env", "python.exe")
SERVER = os.path.join(COVER_DIR, "rvc_server.py")
LOGS = os.path.join(COVER_DIR, "logs")
URL = "http://127.0.0.1:6231"

# /声音 后面可以跟的别名 -> 语音提供商 ID（示例，按你在后台建的提供商 ID 改）
ALIASES = {
    "小何": "volc_xiaohe", "豆包": "volc_xiaohe", "原声": "volc_xiaohe",
    "rvc": "rvc_myvoice", "变声": "rvc_myvoice",
}
# 回复里显示的名字（没写的就直接显示提供商 ID）
NAMES = {"volc_xiaohe": "小何（豆包语音）", "rvc_myvoice": "RVC 音色"}

_ctx: Context | None = None
_start_lock = asyncio.Lock()


SERVER_VERSION = 2  # 和 rvc_server.py 里的 VERSION 对应


async def _health() -> dict | None:
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(URL + "/health", timeout=aiohttp.ClientTimeout(total=2)) as r:
                return await r.json(content_type=None) if r.status == 200 else None
    except Exception:  # noqa: BLE001
        return None


async def _healthy() -> bool:
    h = await _health()
    return bool(h) and int(h.get("version") or 1) >= SERVER_VERSION


def _kill_port_owner() -> None:
    """旧版本的服务还占着端口时，结束它（只结束监听 6231 端口的那个进程）"""
    try:
        out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True, timeout=10).stdout
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 5 and parts[1].endswith(":6231") and parts[3].upper() == "LISTENING":
                subprocess.run(["taskkill", "/PID", parts[4], "/F"], capture_output=True, timeout=10)
                logger.info(f"[rvc_voice] 已结束旧版语音服务 PID {parts[4]}")
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[rvc_voice] 结束旧服务失败：{exc}")


async def ensure_server(wait: int = 90) -> bool:
    if await _healthy():
        return True
    async with _start_lock:
        if await _healthy():
            return True
        if await _health():  # 在跑，但是旧版本
            await asyncio.to_thread(_kill_port_owner)
            await asyncio.sleep(2)
        os.makedirs(LOGS, exist_ok=True)
        logf = open(os.path.join(LOGS, "rvc_server.log"), "ab")
        subprocess.Popen(
            [PY, "-u", SERVER], cwd=COVER_DIR, stdout=logf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"}, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        logger.info("[rvc_voice] 正在启动 RVC 变声服务……")
        for _ in range(wait):
            await asyncio.sleep(1)
            if await _healthy():
                logger.info("[rvc_voice] RVC 变声服务已就绪")
                return True
    logger.error(f"[rvc_voice] RVC 变声服务启动失败，看 {os.path.join(LOGS, 'rvc_server.log')}")
    return False


async def rvc_convert(src: str, voice: str, pitch: int = 0, index_rate: float = 0.5) -> str:
    if not await ensure_server():
        raise RuntimeError("RVC 变声服务没启动")
    out = os.path.join(get_astrbot_temp_path(), f"rvc_{generate_timestamp_id()}.wav")
    body = {"input": src, "output": out, "voice": voice, "pitch": pitch, "index_rate": index_rate}
    async with aiohttp.ClientSession() as s:
        async with s.post(URL + "/convert", json=body, timeout=aiohttp.ClientTimeout(total=120)) as r:
            res = await r.json(content_type=None)
    if not res.get("ok"):
        raise RuntimeError(res.get("error") or "变声失败")
    return res["out"]


class RVCTTS(TTSProvider):
    def __init__(self, provider_config: dict, provider_settings: dict) -> None:
        super().__init__(provider_config, provider_settings)
        self.cfg = provider_config
        self.set_model(str(provider_config.get("voice", "")))

    async def get_audio(self, text: str) -> str:
        base_id = self.cfg.get("base_provider_id", "volc_xiaohe")
        base = _ctx.get_provider_by_id(base_id) if _ctx else None
        if base is None:
            raise RuntimeError(f"找不到底层语音提供商 {base_id}")
        src = await base.get_audio(text)
        try:
            return await rvc_convert(
                src, str(self.cfg.get("voice", "")),
                int(self.cfg.get("pitch", 0)), float(self.cfg.get("index_rate", 0.5)),
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[rvc_voice] 变声失败，这次先用原声：{exc!r}")
            return src


if PROVIDER_TYPE in provider_cls_map:
    provider_cls_map[PROVIDER_TYPE].cls_type = RVCTTS
else:
    register_provider_adapter(
        PROVIDER_TYPE, "RVC 音色（底层 TTS + 本地变声）", provider_type=ProviderType.TEXT_TO_SPEECH
    )(RVCTTS)


STT_TYPE = "local_sensevoice"


class LocalSenseVoiceSTT(STTProvider):
    def __init__(self, provider_config: dict, provider_settings: dict) -> None:
        super().__init__(provider_config, provider_settings)
        self.set_model("SenseVoiceSmall")

    async def get_text(self, audio_url: str) -> str:
        if not os.path.exists(audio_url):
            raise FileNotFoundError(audio_url)
        if not await ensure_server():
            raise RuntimeError("本地语音服务没启动")
        async with aiohttp.ClientSession() as s:
            async with s.post(URL + "/asr", json={"input": audio_url}, timeout=aiohttp.ClientTimeout(total=120)) as r:
                res = await r.json(content_type=None)
        if not res.get("ok"):
            raise RuntimeError(res.get("error") or "识别失败")
        return str(res.get("text") or "")


if STT_TYPE in provider_cls_map:
    provider_cls_map[STT_TYPE].cls_type = LocalSenseVoiceSTT
else:
    register_provider_adapter(
        STT_TYPE, "本地 SenseVoice 语音识别", provider_type=ProviderType.SPEECH_TO_TEXT
    )(LocalSenseVoiceSTT)


class RVCVoice(Star):
    def __init__(self, context: Context) -> None:
        super().__init__(context)
        global _ctx
        _ctx = context

    def _confs(self):
        mgr = getattr(self.context, "astrbot_config_mgr", None)
        confs = list(getattr(mgr, "confs", {}).values()) if mgr else []
        return confs or [self.context.get_config()]

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("声音")
    async def switch_voice(self, event: AstrMessageEvent, name: str = ""):
        current = self.context.get_config(umo=event.unified_msg_origin).get("provider_tts_settings", {}).get("provider_id")
        tts_ids = [p.meta().id for p in self.context.get_all_tts_providers()]
        options = "、".join(f"{NAMES.get(i, i)}" for i in tts_ids)
        if not name:
            yield event.plain_result(f"现在的声音：{NAMES.get(current, current)}\n可选：{options}\n用法：/声音 <名字或提供商ID>")
            return
        pid = ALIASES.get(name.strip().lower(), ALIASES.get(name.strip(), name.strip()))
        if pid not in tts_ids:
            yield event.plain_result(f"没有「{name}」这个声音。可选：{options}")
            return
        for conf in self._confs():
            conf.setdefault("provider_tts_settings", {})["provider_id"] = pid
            conf.save_config()
        if pid.startswith("rvc_"):
            asyncio.create_task(ensure_server())
        yield event.plain_result(f"好啦，以后说话用「{NAMES.get(pid, pid)}」的声音~")
