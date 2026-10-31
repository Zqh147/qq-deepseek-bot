"""语音识别（听懂语音）环境安装：在翻唱用的 Applio\\env 里装 FunASR，并下载阿里的 SenseVoiceSmall 模型。
由 install_stt.bat 调用。日志：cover\\logs\\install_stt.log。可重复运行。
"""

import os
import subprocess
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
PY = os.path.join(BASE, "Applio", "env", "python.exe")
ASR_DIR = os.path.join(BASE, "asr_models")
LOG = os.path.join(BASE, "logs", "install_stt.log")
PYPI_MIRROR = "https://pypi.tuna.tsinghua.edu.cn/simple"
os.makedirs(os.path.dirname(LOG), exist_ok=True)
os.makedirs(ASR_DIR, exist_ok=True)
os.environ["UV_CACHE_DIR"] = os.path.join(BASE, ".uv_cache")
os.environ["MODELSCOPE_CACHE"] = ASR_DIR
_logf = open(LOG, "a", encoding="utf-8", buffering=1)


def log(msg: str) -> None:
    line = f"[{time.strftime('%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    _logf.write(line + "\n")


def run(cmd: list[str]) -> int:
    log("$ " + " ".join(cmd))
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
    for raw in iter(p.stdout.readline, b""):
        line = raw.decode("utf-8", "replace").rstrip()
        print(line, flush=True)
        _logf.write("    " + line + "\n")
    rc = p.wait()
    log(f"exit code {rc}")
    return rc


def torch_ok() -> bool:
    r = subprocess.run([PY, "-c", "import torch,sys;sys.exit(0 if torch.version.cuda else 1)"])
    return r.returncode == 0


def main() -> None:
    log("########## 安装语音识别 ##########")
    if not os.path.exists(PY):
        raise SystemExit("找不到翻唱环境，先运行 install_cover.bat")
    cons = os.path.join(BASE, "constraints.txt")
    log("===== 1. 安装 FunASR =====")
    args = [PY, "-m", "uv", "pip", "install", "funasr", "modelscope", "-c", cons, "--python", PY]
    if run(args + ["--index-url", PYPI_MIRROR]) != 0 and run(args) != 0:
        raise SystemExit("FunASR 安装失败")
    if not torch_ok():
        raise SystemExit("装完后 PyTorch 变成了 CPU 版，需要处理")
    log("===== 2. 下载 SenseVoiceSmall 模型（约 900 MB，来自 ModelScope）=====")
    code = (
        "from modelscope import snapshot_download;"
        f"p=snapshot_download('iic/SenseVoiceSmall', cache_dir=r'{ASR_DIR}');"
        f"open(r'{os.path.join(ASR_DIR, 'SENSEVOICE_PATH.txt')}','w',encoding='utf-8').write(p);print('model at',p)"
    )
    if run([PY, "-c", code]) != 0:
        raise SystemExit("模型下载失败")
    log("===== 3. 识别测试 =====")
    code = (
        "import os;from funasr import AutoModel;"
        f"p=open(r'{os.path.join(ASR_DIR, 'SENSEVOICE_PATH.txt')}',encoding='utf-8').read().strip();"
        "m=AutoModel(model=p,device='cuda:0',disable_update=True);"
        "w=os.path.join(p,'example','zh.mp3');"
        "print('test:', m.generate(input=w, language='auto', use_itn=True)[0]['text'] if os.path.exists(w) else 'no example file, model loaded ok')"
    )
    if run([PY, "-c", code]) != 0:
        raise SystemExit("识别测试失败")
    with open(os.path.join(BASE, "STT_OK"), "w") as f:
        f.write(time.strftime("%Y-%m-%d %H:%M:%S"))
    run([PY, "-m", "uv", "cache", "clean"])
    log("########## 语音识别安装完成 ##########")


if __name__ == "__main__":
    try:
        main()
    except SystemExit as exc:
        log(f"!!! 安装中断：{exc}")
        sys.exit(1)
