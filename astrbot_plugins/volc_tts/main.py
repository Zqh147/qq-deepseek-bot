"""火山引擎豆包语音（新版 V3 接口）。

AstrBot 自带的火山引擎 TTS 用的是旧版 V1 接口（AppID + Token），新版控制台只给 API Key，
而且 V1 不支持 2.0 音色。本插件注册一个新的语音提供商类型 volcengine_tts_v3。

密钥放在：AstrBot/data/plugin_data/volc_tts/api_key.txt（用记事本打开，粘贴，保存）。
- 新版控制台：只写一行 API Key
- 旧版控制台：第一行写 APP ID，第二行写 Access Token
"""

import base64
import json
import os
import uuid

import aiohttp
from quart import jsonify, request

from astrbot.api import logger
from astrbot.api.star import Context, Star
from astrbot.core.provider.entities import ProviderType
from astrbot.core.provider.provider import TTSProvider
from astrbot.core.provider.register import provider_cls_map, register_provider_adapter
from astrbot.core.utils.astrbot_path import (
    get_astrbot_plugin_data_path,
    get_astrbot_temp_path,
)
from astrbot.core.utils.datetime_utils import generate_timestamp_id

PLUGIN_NAME = "volc_tts"
PROVIDER_TYPE = "volcengine_tts_v3"
API_URL = "https://openspeech.bytedance.com/api/v3/tts/unidirectional"
KEY_FILE = os.path.join(get_astrbot_plugin_data_path(), PLUGIN_NAME, "api_key.txt")


def _read_key(cfg_key: str = "") -> str:
    if cfg_key and cfg_key.strip():
        return cfg_key.strip()
    try:
        with open(KEY_FILE, encoding="utf-8-sig") as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""


def _auth_headers(key_text: str) -> dict:
    """一行 = 新版 API Key；两行 = 旧版 APP ID（第一行）+ Access Token（第二行）。"""
    lines = [ln.strip() for ln in key_text.splitlines() if ln.strip()]
    if len(lines) >= 2:
        return {"X-Api-App-Id": lines[0], "X-Api-Access-Key": lines[1]}
    return {"X-Api-Key": lines[0] if lines else ""}


def _resource_for(speaker: str, resource_id: str = "") -> str:
    if resource_id:
        return resource_id
    if speaker.startswith("S_"):
        return "seed-icl-2.0"
    if "_uranus_" in speaker:
        return "seed-tts-2.0"
    return "seed-tts-1.0"


async def synthesize(
    text: str,
    speaker: str,
    *,
    api_key: str = "",
    resource_id: str = "",
    speech_rate: int = 0,
    loudness_rate: int = 0,
    emotion: str = "",
    timeout: int = 30,
) -> bytes:
    key = _read_key(api_key)
    if not key:
        raise RuntimeError(f"还没有填写火山引擎 API Key：{KEY_FILE}")
    headers = {
        "Content-Type": "application/json",
        **_auth_headers(key),
        "X-Api-Resource-Id": _resource_for(speaker, resource_id),
        "X-Api-Request-Id": str(uuid.uuid4()),
    }
    audio_params = {
        "format": "mp3",
        "sample_rate": 24000,
        "speech_rate": int(speech_rate),
        "loudness_rate": int(loudness_rate),
    }
    if emotion:
        audio_params["emotion"] = emotion
    body = {
        "user": {"uid": "astrbot"},
        "req_params": {"text": text, "speaker": speaker, "audio_params": audio_params},
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(
            API_URL,
            headers=headers,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            raw = await resp.text()
            if resp.status != 200:
                raise RuntimeError(f"火山引擎返回 HTTP {resp.status}：{raw[:300]}")

    audio = bytearray()
    error = None
    decoder = json.JSONDecoder()
    idx = 0
    raw = raw.strip()
    while idx < len(raw):
        while idx < len(raw) and raw[idx] in " \r\n\t,":
            idx += 1
        if idx >= len(raw):
            break
        if raw.startswith("data:", idx):  # SSE 格式兼容
            idx += 5
            continue
        try:
            obj, end = decoder.raw_decode(raw, idx)
        except json.JSONDecodeError:
            break
        idx = end
        if not isinstance(obj, dict):
            continue
        code = obj.get("code", 0)
        if obj.get("data"):
            audio.extend(base64.b64decode(obj["data"]))
        elif code not in (0, 20000000):
            error = f"{code} {obj.get('message', '')}"
    if not audio:
        raise RuntimeError(f"火山引擎没有返回音频：{error or raw[:300]}")
    return bytes(audio)


class VolcTTSV3(TTSProvider):
    def __init__(self, provider_config: dict, provider_settings: dict) -> None:
        super().__init__(provider_config, provider_settings)
        self.cfg = provider_config
        self.set_model(_resource_for(provider_config.get("speaker", ""), provider_config.get("resource_id", "")))

    async def get_audio(self, text: str) -> str:
        data = await synthesize(
            text,
            self.cfg.get("speaker", "zh_female_vv_uranus_bigtts"),
            api_key=self.cfg.get("api_key", ""),
            resource_id=self.cfg.get("resource_id", ""),
            speech_rate=self.cfg.get("speech_rate", 0),
            loudness_rate=self.cfg.get("loudness_rate", 0),
            emotion=self.cfg.get("emotion", ""),
            timeout=int(self.cfg.get("timeout", 30)),
        )
        temp_dir = get_astrbot_temp_path()
        os.makedirs(temp_dir, exist_ok=True)
        path = os.path.join(temp_dir, f"volc_tts_{generate_timestamp_id()}.mp3")
        with open(path, "wb") as f:
            f.write(data)
        return path


# 注册语音提供商类型；插件重载时只更新类，避免重复注册报错
if PROVIDER_TYPE in provider_cls_map:
    provider_cls_map[PROVIDER_TYPE].cls_type = VolcTTSV3
else:
    register_provider_adapter(
        PROVIDER_TYPE,
        "火山引擎豆包语音（新版 V3 / API Key）",
        provider_type=ProviderType.TEXT_TO_SPEECH,
    )(VolcTTSV3)


class VolcTTSPlugin(Star):
    def __init__(self, context: Context) -> None:
        super().__init__(context)
        os.makedirs(os.path.dirname(KEY_FILE), exist_ok=True)
        if not os.path.exists(KEY_FILE):
            with open(KEY_FILE, "w", encoding="utf-8") as f:
                f.write("")
        context.register_web_api(
            f"/{PLUGIN_NAME}/preview", self._api_preview, ["POST"], "批量生成试听"
        )
        context.register_web_api(
            f"/{PLUGIN_NAME}/status", self._api_status, ["GET"], "是否已填写 Key"
        )

    async def _api_status(self):
        return jsonify({"key_file": KEY_FILE, "has_key": bool(_read_key())})

    async def _api_preview(self):
        data = await request.get_json() or {}
        text = str(data.get("text") or "你好呀，今天过得怎么样？")
        out = {}
        for spk in data.get("speakers") or []:
            try:
                audio = await synthesize(text, str(spk), emotion=str(data.get("emotion") or ""))
                if data.get("save"):
                    d = os.path.join(os.path.dirname(KEY_FILE), "preview")
                    os.makedirs(d, exist_ok=True)
                    with open(os.path.join(d, f"{spk}.mp3"), "wb") as f:
                        f.write(audio)
                    out[spk] = {"ok": True, "bytes": len(audio)}
                else:
                    out[spk] = {"ok": True, "mp3": base64.b64encode(audio).decode()}
            except Exception as exc:  # noqa: BLE001
                out[spk] = {"ok": False, "error": str(exc)[:300]}
                logger.warning(f"[volc_tts] 试听 {spk} 失败：{exc}")
        return jsonify(out)
