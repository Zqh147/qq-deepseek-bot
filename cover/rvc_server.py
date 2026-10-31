"""常驻的 RVC 变声服务（用 Applio\\env 的 Python 运行，由 AstrBot 插件按需自动启动）。

- 只监听 127.0.0.1:6231
- 模型加载一次后留在显存里（约 1~2 GB），之后每次变声只要一两秒
- 超过 IDLE_MINUTES 分钟没人用就自动退出，把显存还给电脑

POST /convert {"input": 路径, "output": 路径(.wav), "voice": 音色名, "pitch": 0, "index_rate": 0.5}
POST /asr     {"input": 音频路径}  -> {"text": 识别出的文字}（SenseVoiceSmall，第一次用时才加载）
GET  /health
"""

import glob
import json
import os
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
APPLIO = os.path.join(BASE, "Applio")
VOICES = os.path.join(BASE, "voices")
PORT = 6231
VERSION = 2  # 改了这个文件就加 1，插件发现版本旧了会自动重启服务
IDLE_MINUTES = 30

os.chdir(APPLIO)
sys.path.insert(0, APPLIO)
os.environ["PATH"] = APPLIO + os.pathsep + os.environ.get("PATH", "")

from rvc.infer.infer import VoiceConverter  # noqa: E402

_vc = VoiceConverter()
_lock = threading.Lock()
_asr_lock = threading.Lock()
_asr = None
_last = time.time()
ASR_PATH_FILE = os.path.join(BASE, "asr_models", "SENSEVOICE_PATH.txt")


def log(msg: str) -> None:
    print(f"[{time.strftime('%m-%d %H:%M:%S')}] {msg}", flush=True)


def find_voice(name: str) -> tuple[str, str]:
    folder = os.path.join(VOICES, name)
    pths = glob.glob(os.path.join(folder, "*.pth"))
    if not pths:
        raise RuntimeError(f"音色 {name} 不存在（{folder} 里没有 .pth）")
    idx = [p for p in glob.glob(os.path.join(folder, "*.index")) if "trained" not in os.path.basename(p).lower()]
    idx = idx or glob.glob(os.path.join(folder, "*.index"))
    return pths[0], (idx[0] if idx else "")


def convert(req: dict) -> dict:
    pth, index = find_voice(str(req["voice"]))
    t0 = time.time()
    out = str(req["output"])
    _vc.convert_audio(
        audio_input_path=str(req["input"]),
        audio_output_path=out,
        model_path=pth,
        index_path=index,
        pitch=int(req.get("pitch", 0)),
        f0_method="rmvpe",
        index_rate=float(req.get("index_rate", 0.5)),
        volume_envelope=float(req.get("volume_envelope", 1.0)),
        protect=float(req.get("protect", 0.33)),
        export_format="WAV",
    )
    if not os.path.exists(out):
        raise RuntimeError("变声后没有生成文件")
    return {"ok": True, "out": out, "seconds": round(time.time() - t0, 2)}


def asr(req: dict) -> dict:
    global _asr
    from funasr.utils.postprocess_utils import rich_transcription_postprocess

    t0 = time.time()
    if _asr is None:
        from funasr import AutoModel

        with open(ASR_PATH_FILE, encoding="utf-8") as f:
            model_dir = f.read().strip()
        _asr = AutoModel(model=model_dir, device="cuda:0", disable_update=True)
        log("SenseVoice 已加载")
    res = _asr.generate(input=str(req["input"]), language="auto", use_itn=True, batch_size_s=60)
    text = rich_transcription_postprocess(res[0]["text"]) if res else ""
    return {"ok": True, "text": text.strip(), "seconds": round(time.time() - t0, 2)}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # 不打印每个请求
        pass

    def _send(self, code: int, obj: dict) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/health"):
            self._send(200, {"ok": True, "version": VERSION, "pid": os.getpid(), "idle_seconds": int(time.time() - _last)})
        else:
            self._send(404, {"ok": False})

    def do_POST(self):
        global _last
        if not self.path.startswith(("/convert", "/asr")):
            self._send(404, {"ok": False})
            return
        try:
            n = int(self.headers.get("Content-Length") or 0)
            req = json.loads(self.rfile.read(n).decode("utf-8"))
            if self.path.startswith("/asr"):
                with _asr_lock:
                    _last = time.time()
                    res = asr(req)
                    _last = time.time()
                log(f"识别完成 用时 {res['seconds']}s：{res['text'][:40]}")
                self._send(200, res)
                return
            with _lock:
                _last = time.time()
                res = convert(req)
                _last = time.time()
            log(f"变声完成 {req.get('voice')} pitch={req.get('pitch', 0)} 用时 {res['seconds']}s")
            self._send(200, res)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self._send(500, {"ok": False, "error": f"{exc}"[:500]})


def idle_watch(server: ThreadingHTTPServer) -> None:
    while True:
        time.sleep(30)
        if not _lock.locked() and not _asr_lock.locked() and time.time() - _last > IDLE_MINUTES * 60:
            log(f"{IDLE_MINUTES} 分钟没人用，退出释放显存")
            server.shutdown()
            return


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    threading.Thread(target=idle_watch, args=(server,), daemon=True).start()
    log(f"RVC 变声服务已启动：127.0.0.1:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
