"""AI 翻唱。

用法（群里要 @ 小号）：
  唱一首晴天 / 唱 周杰伦 晴天 / 翻唱《晴天》 / 来一首晴天
  在后面加 +3 / -2（或"升3调""降2调"）调整音高
  带上"完整"两个字唱整首，作为文件发送（默认只唱人声最集中的 60 秒，发语音）

翻唱程序：<COVER_DIR>\\cover_job.py（用 <COVER_DIR>\\Applio\\env 的 Python 跑，只在唱歌时占用显卡）
音色模型：<COVER_DIR>\\voices\\<音色名>\\xxx.pth（+ xxx.index）
COVER_DIR 默认是和 AstrBot 文件夹并列的 cover，可用环境变量 QQBOT_COVER_DIR 修改
"""

import asyncio
import json
import os
import re
import subprocess
import time
from datetime import date

import aiohttp
from quart import jsonify, request

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import Plain, Record
from astrbot.api.star import Context, Star
from astrbot.core.platform.message_type import MessageType

# 翻唱/本地语音程序所在文件夹（cover/）。默认是和 AstrBot 文件夹并列的 cover：
#   <根目录>\AstrBot\data\plugins\<本插件>\main.py  ->  <根目录>\cover
# 放在别处时用环境变量 QQBOT_COVER_DIR 指定
_BOT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
COVER_DIR = os.environ.get("QQBOT_COVER_DIR") or os.path.join(_BOT_ROOT, "cover")
PY = os.path.join(COVER_DIR, "Applio", "env", "python.exe")
JOB = os.path.join(COVER_DIR, "cover_job.py")
VOICES = os.path.join(COVER_DIR, "voices")
SONGS = os.path.join(COVER_DIR, "songs")
OUT = os.path.join(COVER_DIR, "out")
LOGS = os.path.join(COVER_DIR, "logs")

# 使用的音色（voices 下的文件夹名）；留空 = voices 里只有一个音色时自动用它
VOICE_NAME = ""
# 升降调默认自动：原唱是男声、音色是女声就升 12（一个八度），反过来降 12。
# 音色性别写在 voices/<音色>/voice.json：{"gender": "female"} 或 {"gender": "male"}
# 默认只唱多少秒（发语音）；"完整"时唱整首、发文件
CLIP_SECONDS = 60
# 每天最多唱几首（所有人加起来），避免显卡和 QQ 风控压力
DAILY_LIMIT = 30
JOB_TIMEOUT = 900

_TRIGGER = re.compile(
    r"^\s*(?:/)?(?:那你|那|你再|再|你|请|帮我|可以|能不能|可不可以|能)?(?:给我|给大家|帮我)?(?:再)?"
    r"(?:翻唱|唱|来一首|来首)(?:一首|首|一下|个|一个)?\s*(.+?)\s*$"
)
_STOP = {"歌", "首歌", "个歌", "一首歌", "歌吧", "歌呗", "吧", "呗", "什么", "啥", "得好", "得不错", "的", "一下"}
_PITCH = re.compile(r"(?:([+-]\d{1,2})|升(\d{1,2})个?调|降(\d{1,2})个?调)")
# 语音识别出来的文字会带表情和标点，先去掉
_NOISE = re.compile(r"[\U0001F000-\U0001FAFF☀-➿️。，！,!、~～…·]+")
_POLITE = re.compile(r"(好不好|好吗|可以吗|行吗|行不行|可不可以|吗|呗|吧|呀|嘛|啊|哦|听听|给我听|给大家听|听一下)+[?？]*$")
_GENERIC = {"", "歌", "首歌", "一首歌", "的歌", "歌曲", "一首", "的"}


def _parse(text: str):
    text = _NOISE.sub(" ", text or "").strip()
    m = _TRIGGER.match(text)
    if not m:
        return None
    kw = _POLITE.sub("", m.group(1).strip()).strip()
    if any(x in kw for x in ("吗", "？", "?", "么", "怎么")):
        return None
    full = "完整" in kw
    pitch = None
    pm = _PITCH.search(kw)
    if pm:
        pitch = int(pm.group(1)) if pm.group(1) else (int(pm.group(2)) if pm.group(2) else -int(pm.group(3)))
        kw = _PITCH.sub(" ", kw)
    for w in ("完整版", "完整的", "完整", "整首"):
        kw = kw.replace(w, " ")
    kw = re.sub(r"[《》\"“”]", " ", kw)
    kw = _POLITE.sub("", kw.strip()).strip()
    kw = re.sub(r"^的", "", kw).strip()
    if not kw or kw in _STOP or len(kw) > 40:
        return None
    return kw, pitch, full


# 不是原版的标记：用户没特意说就尽量不选
_VERSION_TAGS = ("吉他", "live", "现场", "dj", "remix", "翻自", "cover", "翻唱", "女声", "男声", "片段", "抖音",
                 "加速", "降调", "升调", "伴奏", "纯音乐", "instrumental", "karaoke", "八音盒", "钢琴", "铃声", "版")
_NO_VOCAL_TAGS = ("伴奏", "纯音乐", "instrumental", "inst.", "karaoke", "八音盒", "钢琴版", "铃声", "纯享伴")


def _split_query(kw: str) -> tuple[str, str]:
    """"方大同的麦恩莉" -> ("麦恩莉", "方大同")；"周杰伦的歌" -> ("", "周杰伦")；"周杰伦 晴天" 交给搜索自己处理"""
    kw = re.sub(r"^(一句|一段|几句|两句)", "", kw.strip())
    if "的" in kw:
        artist, title = kw.rsplit("的", 1)
        if artist.strip() and title.strip() and len(artist) <= 12:
            title = "" if title.strip() in _GENERIC else title.strip()
            return title, artist.strip()
    return kw.strip(), ""


def _norm(t: str) -> str:
    return re.sub(r"[\s\-_·.()（）\[\]【】]", "", (t or "").lower())


def _rank(songs: list[dict], title: str, artist: str, kw: str) -> list[dict]:
    """同样的歌名每次都选同一个版本：歌名对得上、歌手对得上、是原版、能免费播放的排前面"""
    t, a, k = _norm(title), _norm(artist), kw.lower()

    def score(x: dict) -> float:
        name = (x["name"] or "").lower()
        base = _norm(re.sub(r"[（(].*?[）)]", "", name))
        sc = 0.0
        if t and base == t:
            sc += 6
        elif t and t in _norm(name):
            sc += 3
        elif t:
            sc -= 4  # 歌名都对不上
        if a and a in _norm(x["artist"]):
            sc += 4
        elif a:
            sc -= 3
        tags = [g for g in _VERSION_TAGS if g in name and g not in k]
        sc -= 3 * len(tags)
        if x["fee"] not in (0, 8):
            sc -= 2
        return sc

    # 分数一样时保持网易云原来的顺序（越靠前越热门）
    return [x for _, x in sorted(enumerate(songs), key=lambda p: (-score(p[1]), p[0]))]


def _voice() -> str:
    if VOICE_NAME:
        return VOICE_NAME
    if not os.path.isdir(VOICES):
        return ""
    names = [n for n in os.listdir(VOICES) if os.path.isdir(os.path.join(VOICES, n))]
    return names[0] if len(names) == 1 else ""


async def _search(session: aiohttp.ClientSession, kw: str) -> list[dict]:
    url = "https://music.163.com/api/search/get/web"
    params = {"s": kw, "type": "1", "offset": "0", "limit": "15"}
    headers = {"Referer": "https://music.163.com/", "User-Agent": "Mozilla/5.0"}
    async with session.get(url, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as r:
        data = json.loads(await r.text())
    songs = (data.get("result") or {}).get("songs") or []
    out = []
    for s in songs:
        out.append({
            "id": s.get("id"),
            "name": s.get("name"),
            "artist": "/".join(a.get("name", "") for a in s.get("artists") or []),
            "fee": s.get("fee"),
            "duration": (s.get("duration") or 0) / 1000,
        })
    return out


async def _download(session: aiohttp.ClientSession, song_id: int) -> str | None:
    os.makedirs(SONGS, exist_ok=True)
    path = os.path.join(SONGS, f"{song_id}.mp3")
    if os.path.exists(path) and os.path.getsize(path) > 100_000:
        return path
    url = f"https://music.163.com/song/media/outer/url?id={song_id}.mp3"
    headers = {"Referer": "https://music.163.com/", "User-Agent": "Mozilla/5.0"}
    async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=60)) as r:
        ctype = r.headers.get("Content-Type", "")
        if r.status != 200 or "404" in str(r.url) or "audio" not in ctype and "octet" not in ctype:
            return None
        body = await r.read()
    if len(body) < 100_000:
        return None
    with open(path, "wb") as f:
        f.write(body)
    return path


def _run_job(args: list[str]) -> dict:
    os.makedirs(LOGS, exist_ok=True)
    log_path = os.path.join(LOGS, f"job_{time.strftime('%m%d_%H%M%S')}.log")
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    try:
        p = subprocess.run(
            [PY, JOB, *args], cwd=COVER_DIR, capture_output=True, timeout=JOB_TIMEOUT, env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        out = (p.stdout + p.stderr).decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "超时", "log": log_path}
    with open(log_path, "w", encoding="utf-8") as f:
        f.write(out)
    for line in reversed(out.splitlines()):
        if line.startswith("RESULT "):
            res = json.loads(line[7:])
            res["log"] = log_path
            return res
    return {"ok": False, "error": "没有结果", "log": log_path}


RVC_SERVER = os.path.join(COVER_DIR, "rvc_server.py")
RVC_URL = "http://127.0.0.1:6231"


async def _ensure_rvc_server(wait: int = 90) -> bool:
    """常驻变声服务没开就启动它（模型留在显存里，翻唱和说话都能用，闲置 30 分钟自动退出）"""
    async def healthy() -> bool:
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(RVC_URL + "/health", timeout=aiohttp.ClientTimeout(total=2)) as r:
                    return r.status == 200
        except Exception:  # noqa: BLE001
            return False

    if await healthy():
        return True
    os.makedirs(LOGS, exist_ok=True)
    logf = open(os.path.join(LOGS, "rvc_server.log"), "ab")
    subprocess.Popen(
        [PY, "-u", RVC_SERVER], cwd=COVER_DIR, stdout=logf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"}, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    for _ in range(wait):
        await asyncio.sleep(1)
        if await healthy():
            return True
    return False


def song_name(res: dict) -> str:
    return str((res.get("song") or {}).get("name") or "这首歌")


class AICover(Star):
    def __init__(self, context: Context) -> None:
        super().__init__(context)
        self._lock = asyncio.Lock()
        self._count_day = date.today()
        self._count = 0
        context.register_web_api("/ai_cover/test", self._api_test, ["POST"], "翻唱测试")
        context.register_web_api("/ai_cover/fetch_voice", self._api_fetch_voice, ["POST"], "下载音色模型")
        context.register_web_api("/ai_cover/prefetch", self._api_prefetch, ["POST"], "预先下载分离模型")

    async def cover(self, kw: str, pitch: int | None, full: bool, voice: str, simple: bool = False) -> dict:
        """找歌 → 下载 → 翻唱，返回 {ok, out, song...}"""
        title, artist = _split_query(kw)
        async with aiohttp.ClientSession() as s:
            songs = await _search(s, f"{title} {artist}".strip() or kw)
            if not songs and artist:
                songs = await _search(s, kw)
            # 去掉伴奏/纯音乐这类没有人声的版本
            songs = [x for x in songs if not any(b in (x["name"] or "").lower() for b in _NO_VOCAL_TAGS)]
            if not songs:
                return {"ok": False, "reason": "notfound"}
            songs = _rank(songs, title, artist, kw)
            kw = title or f"{artist}的歌"
            logger.info("[ai_cover] 候选：" + " | ".join(f"{x['name']}-{x['artist']}(fee{x['fee']})" for x in songs[:5]))
            path, song = None, None
            for cand in songs[:5]:
                path = await _download(s, cand["id"])
                if path:
                    song = cand
                    break
            if not path:
                return {"ok": False, "reason": "vip", "song": songs[0]}
        clip = 0 if full else CLIP_SECONDS
        # 没指定升降调时交给翻唱程序：按原唱男声/女声和音色性别自动升降八度
        pitch = "auto" if pitch is None else pitch
        os.makedirs(OUT, exist_ok=True)
        tag = "s" if simple else "v4"  # v4 = 分主唱/和声 + 去混响 + 伴奏调小 + 不改伴奏的调
        out = os.path.join(OUT, f"{song['id']}_{voice or 'orig'}_{pitch}_{clip}_{tag}.mp3")
        if os.path.exists(out):
            return {"ok": True, "out": out, "song": song, "cached": True}
        args = ["--input", path, "--out", out, "--pitch", str(pitch), "--clip", str(clip)]
        if voice:
            args += ["--voice", voice]
            await _ensure_rvc_server()
        if simple:
            args.append("--simple")
        res = await asyncio.to_thread(_run_job, args)
        res["song"] = song
        if not res.get("ok") and "NO_VOCALS" in str(res.get("error")):
            res["reason"] = "novocal"
        return res

    def _quota_ok(self) -> bool:
        if self._count_day != date.today():
            self._count_day, self._count = date.today(), 0
        return self._count < DAILY_LIMIT

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent):
        if event.get_message_type() == MessageType.GROUP_MESSAGE and not event.is_at_or_wake_command:
            return
        parsed = _parse(event.message_str)
        if not parsed:
            return
        kw, pitch, full = parsed
        event.stop_event()
        voice = _voice()
        if not voice:
            await event.send(MessageChain().message("我还没学会唱歌呢（还没有装好声音模型），再等等我~"))
            return
        if self._lock.locked():
            await event.send(MessageChain().message("我还在练上一首呢，等我唱完这首再点~"))
            return
        if not self._quota_ok():
            await event.send(MessageChain().message("今天嗓子唱累了，明天再给你唱吧~"))
            return
        self._count += 1
        await event.send(MessageChain().message(f"好呀，我去练一下「{kw}」，稍等一两分钟~"))
        asyncio.create_task(self._do(event.unified_msg_origin, event, kw, pitch, full, voice))

    async def _do(self, umo: str, event: AstrMessageEvent, kw, pitch, full, voice):
        async with self._lock:
            try:
                res = await self.cover(kw, pitch, full, voice)
            except Exception as exc:  # noqa: BLE001
                logger.exception(f"[ai_cover] 出错：{exc!r}")
                res = {"ok": False, "reason": "error"}
        logger.info(f"[ai_cover] {kw} -> {res}")
        if not res.get("ok"):
            reason = res.get("reason")
            if reason == "notfound":
                text = f"没找到「{kw}」这首歌诶，换个说法试试？"
            elif reason == "novocal":
                text = f"「{song_name(res)}」我只找到了纯伴奏版，没有原唱可以学，换一首吧~"
            elif reason == "vip":
                text = f"「{res['song']['name']}」是会员歌曲，我拿不到伴奏，换一首吧~"
            else:
                text = "唱砸了……嗓子出了点问题，等会儿再试试吧"
            await self.context.send_message(umo, MessageChain().message(text))
            return
        song = res["song"]
        out = res["out"]
        if full:
            try:
                await self._send_file(event, out, f"{song['name']}（AI翻唱）.mp3")
                await self.context.send_message(umo, MessageChain().message(f"「{song['name']}」整首唱完啦，文件发你了~"))
            except Exception as exc:  # noqa: BLE001
                logger.exception(f"[ai_cover] 发文件失败：{exc!r}")
                await self.context.send_message(umo, MessageChain(chain=[Record(file=out, url=out)]))
        else:
            await self.context.send_message(umo, MessageChain(chain=[Record(file=out, url=out)]))

    async def _send_file(self, event: AstrMessageEvent, path: str, name: str):
        bot = self.context.get_platform_inst(event.get_platform_id()).bot
        if event.get_message_type() == MessageType.GROUP_MESSAGE:
            await bot.call_action("upload_group_file", group_id=int(event.get_group_id()), file=path, name=name)
        else:
            await bot.call_action("upload_private_file", user_id=int(event.get_sender_id()), file=path, name=name)

    async def _api_test(self):
        data = await request.get_json()
        kw = str(data.get("keyword") or "")
        voice = data.get("voice")
        voice = _voice() if voice is None else str(voice)
        full = bool(data.get("full"))
        pitch = data.get("pitch")
        if self._lock.locked():
            return jsonify({"ok": False, "error": "正在唱别的"})
        async with self._lock:
            res = await self.cover(kw, None if pitch is None else int(pitch), full, voice, bool(data.get("simple")))
        send_to = data.get("send_to")
        if send_to and res.get("ok"):
            await self.context.send_message(send_to, MessageChain(chain=[Record(file=res["out"], url=res["out"])]))
        return jsonify(res)

    async def _api_fetch_voice(self):
        """把 HuggingFace 上的音色模型（.pth/.index）下载到 voices/<name>/，只允许 HF 域名"""
        data = await request.get_json()
        name = "".join(ch for ch in str(data.get("name") or "") if ch.isalnum() or ch in "-_")
        urls = [str(u) for u in data.get("urls") or []]
        if not name or not urls:
            return jsonify({"ok": False, "error": "缺少 name 或 urls"})
        folder = os.path.join(VOICES, name)
        os.makedirs(folder, exist_ok=True)
        done = []
        async with aiohttp.ClientSession() as s:
            for u in urls:
                host = u.split("/")[2] if u.count("/") >= 3 else ""
                fname = u.split("?")[0].rsplit("/", 1)[-1]
                if host not in ("huggingface.co", "hf-mirror.com") or not fname.endswith((".pth", ".index")):
                    return jsonify({"ok": False, "error": f"不允许的地址：{u}"})
                dest = os.path.join(folder, fname)
                async with s.get(u, timeout=aiohttp.ClientTimeout(total=900)) as r:
                    if r.status != 200:
                        return jsonify({"ok": False, "error": f"{u} 返回 {r.status}", "done": done})
                    with open(dest + ".part", "wb") as f:
                        async for chunk in r.content.iter_chunked(1 << 20):
                            f.write(chunk)
                os.replace(dest + ".part", dest)
                done.append({"file": dest, "mb": round(os.path.getsize(dest) / 2**20, 1)})
        return jsonify({"ok": True, "done": done})

    async def _api_prefetch(self):
        res = await asyncio.to_thread(_run_job, ["--prefetch"])
        return jsonify(res)
