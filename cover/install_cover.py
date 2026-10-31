"""AI 翻唱环境一键安装（由 install_cover.bat 调用，用 Anaconda / Miniconda 的 Python 运行）。

装到本脚本所在的文件夹（cover/）下：
  Applio\\        RVC 变声（Applio）
  Applio\\env\\   独立的 Python 3.12 环境（PyTorch CUDA 12.8，支持 50 系显卡）
  sep_models\\    人声/伴奏分离模型
全部日志写到 cover\\logs\\install.log，可以随时重新运行，已完成的步骤会跳过。
"""

import os
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile

BASE = os.path.dirname(os.path.abspath(__file__))
APPLIO = os.path.join(BASE, "Applio")
ENV = os.path.join(APPLIO, "env")
PY = os.path.join(ENV, "python.exe")
LOGDIR = os.path.join(BASE, "logs")
LOG = os.path.join(LOGDIR, "install.log")
SEP_DIR = os.path.join(BASE, "sep_models")
def _find_conda() -> str:
    """找 conda.exe：环境变量 CONDA_EXE → PATH → 常见安装位置"""
    cands = [os.environ.get("CONDA_EXE", ""), shutil.which("conda") or ""]
    for root in (os.path.expanduser("~"), "C:\\ProgramData", "D:\\", "E:\\"):
        for name in ("anaconda3", "miniconda3", "Anaconda3", "Miniconda3", "anaconda", "miniconda"):
            cands.append(os.path.join(root, name, "Scripts", "conda.exe"))
    for c in cands:
        if c and os.path.exists(c):
            return c
    return ""


CONDA = _find_conda()
PYPI_MIRROR = "https://pypi.tuna.tsinghua.edu.cn/simple"
TORCH_INDEX = "https://download.pytorch.org/whl/cu128"
TORCH_FIND_LINKS_FALLBACK = "https://mirrors.aliyun.com/pytorch-wheels/cu128/"
SEP_MODEL = "model_bs_roformer_ep_317_sdr_12.9755.ckpt"
APPLIO_ZIP = "https://codeload.github.com/IAHispano/Applio/zip/refs/heads/main"
HF_FILES = [
    ("predictors/rmvpe.pt", r"rvc\models\predictors\rmvpe.pt"),
    ("embedders/contentvec/pytorch_model.bin", r"rvc\models\embedders\contentvec\pytorch_model.bin"),
    ("embedders/contentvec/config.json", r"rvc\models\embedders\contentvec\config.json"),
]
HF_EXE = [("ffmpeg.exe", "ffmpeg.exe"), ("ffprobe.exe", "ffprobe.exe")]
HF_BASES = [
    "https://huggingface.co/IAHispano/Applio/resolve/main/Resources/",
    "https://hf-mirror.com/IAHispano/Applio/resolve/main/Resources/",
]

os.makedirs(LOGDIR, exist_ok=True)
# 下载缓存放在 cover 文件夹里，不占 C 盘（uv 默认放在 C:\Users\...\AppData\Local\uv\cache）
UV_CACHE = os.path.join(BASE, ".uv_cache")
os.environ["UV_CACHE_DIR"] = UV_CACHE
os.environ["PIP_NO_CACHE_DIR"] = "1"
# ffmpeg.exe / ffprobe.exe 放在 Applio 目录（Applio 在 Windows 上本来就从这里找）
os.environ["PATH"] = APPLIO + os.pathsep + os.environ.get("PATH", "")
_logf = open(LOG, "a", encoding="utf-8", buffering=1)


def log(msg: str) -> None:
    line = f"[{time.strftime('%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    _logf.write(line + "\n")


def run(cmd: list[str], cwd: str | None = None, env_extra: dict | None = None) -> int:
    log("$ " + " ".join(cmd))
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    if env_extra:
        env.update(env_extra)
    p = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
    last = 0.0
    for raw in iter(p.stdout.readline, b""):
        try:
            line = raw.decode("utf-8")
        except UnicodeDecodeError:
            line = raw.decode("gbk", "replace")
        line = line.rstrip()
        print(line, flush=True)
        # 进度条刷屏的行只隔几秒记一次
        if "%" in line and ("━" in line or "MiB" in line or "MB" in line) and time.time() - last < 5:
            continue
        last = time.time()
        _logf.write("    " + line + "\n")
    rc = p.wait()
    log(f"exit code {rc}")
    return rc


def download(url: str, dest: str) -> bool:
    tmp = dest + ".part"
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    try:
        log(f"下载 {url}")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            done, last = 0, 0.0
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if time.time() - last > 3:
                    last = time.time()
                    pct = f"{done * 100 / total:.0f}%" if total else ""
                    print(f"    {done / 2**20:.0f} MB {pct}", flush=True)
        os.replace(tmp, dest)
        log(f"完成 {dest}（{os.path.getsize(dest) / 2**20:.1f} MB）")
        return True
    except Exception as exc:  # noqa: BLE001
        log(f"下载失败：{exc!r}")
        if os.path.exists(tmp):
            os.remove(tmp)
        return False


def step_sysinfo() -> None:
    log("===== 0. 检查电脑 =====")
    drive = os.path.splitdrive(BASE)[0] + "\\"
    free = shutil.disk_usage(drive).free / 2**30
    log(f"{drive} 剩余空间：{free:.1f} GB")
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv"],
            capture_output=True, text=True, timeout=30,
        ).stdout
        log("显卡：" + out.strip().replace("\n", " | "))
    except Exception as exc:  # noqa: BLE001
        log(f"nvidia-smi 运行失败：{exc!r}")
    if free < 15:
        raise SystemExit(f"{drive} 剩余空间不足 15 GB，先清理一些空间再运行。")
    if not os.path.exists(CONDA):
        raise SystemExit("找不到 conda.exe：请先安装 Anaconda 或 Miniconda，或者设置环境变量 CONDA_EXE")


def step_applio() -> None:
    log("===== 1. 下载 Applio =====")
    if os.path.exists(os.path.join(APPLIO, "core.py")):
        log("已存在，跳过")
        return
    zpath = os.path.join(BASE, "applio.zip")
    if not download(APPLIO_ZIP, zpath):
        raise SystemExit("Applio 源码下载失败（GitHub 连不上）。可以稍后重试。")
    with zipfile.ZipFile(zpath) as z:
        top = z.namelist()[0].split("/")[0]
        z.extractall(BASE)
    os.replace(os.path.join(BASE, top), APPLIO)
    os.remove(zpath)
    log("Applio 解压完成")


def step_env() -> None:
    log("===== 2. 创建 Python 3.12 环境 =====")
    if os.path.exists(PY):
        log("已存在，跳过")
        return
    rc = run([CONDA, "create", "-y", "-p", ENV, "python=3.12", "pip", "--override-channels", "-c", "conda-forge"])
    if rc != 0:
        log("官方源失败，换清华镜像重试")
        rc = run([
            CONDA, "create", "-y", "-p", ENV, "python=3.12", "pip", "--override-channels",
            "-c", "https://mirrors.tuna.tsinghua.edu.cn/anaconda/cloud/conda-forge",
        ])
    if rc != 0 or not os.path.exists(PY):
        raise SystemExit("Python 环境创建失败")


def uv(*args: str) -> int:
    return run([PY, "-m", "uv", "pip", *args, "--python", PY])


def step_uv() -> None:
    log("===== 3. 安装 uv =====")
    if subprocess.run([PY, "-m", "uv", "--version"], capture_output=True).returncode == 0:
        log("已存在，跳过")
        return
    if run([PY, "-m", "pip", "install", "uv", "-i", PYPI_MIRROR]) != 0:
        if run([PY, "-m", "pip", "install", "uv"]) != 0:
            raise SystemExit("uv 安装失败")


def torch_ok() -> bool:
    r = subprocess.run(
        [PY, "-c", "import torch,sys;print(torch.__version__, torch.version.cuda);sys.exit(0 if torch.version.cuda else 1)"],
        capture_output=True, text=True,
    )
    if r.returncode == 0:
        log("PyTorch：" + r.stdout.strip())
    return r.returncode == 0


def step_torch() -> None:
    log("===== 4. 安装 PyTorch（CUDA 12.8，约 3 GB）=====")
    if torch_ok():
        log("已存在，跳过")
        return
    pins = ["torch==2.11.0", "torchaudio==2.11.0"]
    if uv("install", *pins, "--index-url", TORCH_INDEX) != 0 or not torch_ok():
        log("官方源失败，换阿里云镜像重试")
        if uv("install", *pins, "--reinstall", "--no-index", "--find-links", TORCH_FIND_LINKS_FALLBACK) != 0 or not torch_ok():
            raise SystemExit("PyTorch 安装失败")


def step_requirements() -> None:
    log("===== 5. 安装 Applio 依赖 =====")
    req = os.path.join(APPLIO, "requirements.txt")
    if uv("install", "-r", req, "--index-url", PYPI_MIRROR) != 0:
        log("清华镜像失败，换官方 PyPI 重试")
        if uv("install", "-r", req) != 0:
            raise SystemExit("Applio 依赖安装失败")
    if not torch_ok():
        raise SystemExit("安装依赖后 PyTorch 变成了 CPU 版，需要处理")


def step_separator() -> None:
    log("===== 6. 安装人声分离（audio-separator）=====")
    cons = os.path.join(BASE, "constraints.txt")
    with open(cons, "w", encoding="utf-8") as f:
        f.write("torch==2.11.0\ntorchaudio==2.11.0\nnumpy==2.4.6\n")
    if uv("install", "audio-separator", "-c", cons, "--index-url", PYPI_MIRROR) != 0:
        if uv("install", "audio-separator", "-c", cons) != 0:
            raise SystemExit("audio-separator 安装失败")
    if not torch_ok():
        raise SystemExit("安装 audio-separator 后 PyTorch 变成了 CPU 版，需要处理")


def step_models() -> None:
    log("===== 7. 下载变声需要的基础模型 =====")
    for remote, local in HF_FILES + HF_EXE:
        dest = os.path.join(APPLIO, local)
        if os.path.exists(dest) and os.path.getsize(dest) > 0:
            log(f"已存在：{local}")
            continue
        if not any(download(b + remote, dest) for b in HF_BASES):
            raise SystemExit(f"{remote} 下载失败")


def step_sep_model() -> None:
    log("===== 8. 下载人声分离模型（约 600 MB）=====")
    os.makedirs(SEP_DIR, exist_ok=True)
    code = (
        "from audio_separator.separator import Separator;"
        f"s=Separator(model_file_dir=r'{SEP_DIR}', output_dir=r'{SEP_DIR}');"
        f"s.load_model(model_filename='{SEP_MODEL}');print('separator model ok')"
    )
    if run([PY, "-c", code]) != 0:
        raise SystemExit("分离模型下载/加载失败")


def step_gpu_test() -> None:
    log("===== 9. 显卡测试 =====")
    code = (
        "import torch;print('cuda:',torch.cuda.is_available());"
        "print(torch.cuda.get_device_name(0));"
        "x=torch.randn(2048,2048,device='cuda');print('matmul ok',float((x@x).abs().mean()))"
    )
    if run([PY, "-c", code]) != 0:
        raise SystemExit("显卡测试失败")


def main() -> None:
    log("########## 开始安装 AI 翻唱环境 ##########")
    steps = [step_sysinfo, step_applio, step_env, step_uv, step_torch, step_requirements,
             step_separator, step_models, step_sep_model, step_gpu_test]
    t0 = time.time()
    try:
        for s in steps:
            s()
    except SystemExit as exc:
        log(f"!!! 安装中断：{exc}")
        log("详细日志在 logs\\install.log，修好问题后重新运行即可（已完成的步骤会跳过）。")
        sys.exit(1)
    except Exception as exc:  # noqa: BLE001
        log(f"!!! 意外错误：{exc!r}")
        sys.exit(1)
    log("===== 10. 清理下载缓存 =====")
    run([PY, "-m", "uv", "cache", "clean"])
    with open(os.path.join(BASE, "INSTALL_OK"), "w") as f:
        f.write(time.strftime("%Y-%m-%d %H:%M:%S"))
    log(f"########## 全部完成，用时 {(time.time() - t0) / 60:.0f} 分钟 ##########")


if __name__ == "__main__":
    main()
