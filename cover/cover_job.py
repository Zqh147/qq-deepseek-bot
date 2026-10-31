"""AI 翻唱的一次任务（由 AstrBot 的 ai_cover 插件调用，用 Applio\\env 的 Python 运行）。

python cover_job.py --input 歌.mp3 --out 结果.mp3 [--voice 音色文件夹名] [--pitch 0] [--clip 60]
  --voice 为空：不变声，只做"分离 → 重新混音"（用来测试流程）
  --clip  只唱人声最集中的那 N 秒（0 = 整首）
  --prefetch 只下载/加载所有分离模型（第一次用之前跑一次）
流程：分离人声/伴奏 → 从人声里再分出主唱/和声 → 去掉主唱的混响 → RVC 变声 → 加一点混响 → 和和声、伴奏混在一起
最后一行输出 RESULT {...json...}
"""

import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
APPLIO = os.path.join(BASE, "Applio")
PY = os.path.join(APPLIO, "env", "python.exe")
FFMPEG = os.path.join(APPLIO, "ffmpeg.exe")
SEP_DIR = os.path.join(BASE, "sep_models")
SEP_MODEL = "model_bs_roformer_ep_317_sdr_12.9755.ckpt"
KARAOKE_MODEL = "mel_band_roformer_karaoke_aufr33_viperx_sdr_10.1956.ckpt"  # 主唱 / 和声
DEREVERB_MODEL = "dereverb_mel_band_roformer_anvuew_sdr_19.1729.ckpt"  # 去混响
# 混音音量：人声 / 伴奏 / 和声（伴奏小一点，别盖住唱歌的声音）
VOCAL_GAIN = 1.4
INST_GAIN = 0.55
BACKING_GAIN = 0.45
# 唱歌时的变声参数：index_rate 低一点、protect 低一点（多保留原唱的辅音），咬字更清楚
SING_INDEX_RATE = 0.3
SING_PROTECT = 0.25
VOICES = os.path.join(BASE, "voices")
os.environ["PATH"] = APPLIO + os.pathsep + os.environ.get("PATH", "")


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def ff(*args: str) -> None:
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *args]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError("ffmpeg 失败：" + r.stderr[-800:])


def find_voice(name: str) -> tuple[str, str]:
    folder = os.path.join(VOICES, name)
    pths = glob.glob(os.path.join(folder, "*.pth"))
    if not pths:
        raise RuntimeError(f"音色文件夹 {folder} 里没有 .pth 模型")
    idx = [p for p in glob.glob(os.path.join(folder, "*.index")) if "trained" not in os.path.basename(p).lower()]
    idx = idx or glob.glob(os.path.join(folder, "*.index"))
    return pths[0], (idx[0] if idx else "")


def _separator(work: str):
    from audio_separator.separator import Separator

    return Separator(
        model_file_dir=SEP_DIR, output_dir=work, output_format="WAV", use_autocast=True,
        # overlap 默认 8，改成 2 速度快 4 倍，音质差别很小
        mdxc_params={"segment_size": 256, "override_model_segment_size": False, "batch_size": 1,
                     "overlap": 2, "pitch_shift": 0},
    )


def _rms(path: str) -> float:
    import numpy as np
    import soundfile as sf

    data, _ = sf.read(path, always_2d=True)
    return float(np.sqrt((data ** 2).mean())) if len(data) else 0.0


def run_model(src: str, work: str, model: str, tag: str) -> list[str]:
    """用一个分离模型处理 src，返回输出文件路径（放在 work/tag 下）"""
    out_dir = os.path.join(work, tag)
    os.makedirs(out_dir, exist_ok=True)
    sep = _separator(out_dir)
    sep.load_model(model_filename=model)
    files = sep.separate(src)
    paths = [f if os.path.isabs(f) else os.path.join(out_dir, os.path.basename(f)) for f in files]
    paths = [p for p in paths if os.path.exists(p)] or glob.glob(os.path.join(out_dir, "*.wav"))
    log(f"  {tag}: " + ", ".join(os.path.basename(p) for p in paths))
    return paths


def separate(src: str, work: str) -> tuple[str, str]:
    paths = run_model(src, work, SEP_MODEL, "sep")
    voc = next(p for p in paths if "(vocals)" in os.path.basename(p).lower())
    ins = next(p for p in paths if p != voc)
    return voc, ins


def split_lead(voc: str, work: str) -> tuple[str, str | None]:
    """主唱 / 和声。分不出来就整轨当主唱"""
    try:
        paths = run_model(voc, work, KARAOKE_MODEL, "karaoke")
        if len(paths) < 2:
            return voc, None
        paths.sort(key=_rms, reverse=True)  # 主唱一般比和声响
        return paths[0], paths[1]
    except Exception as exc:  # noqa: BLE001
        log(f"  主唱/和声分离失败，跳过：{exc!r}")
        return voc, None


def dereverb(lead: str, work: str) -> str:
    try:
        paths = run_model(lead, work, DEREVERB_MODEL, "dereverb")
        dry = [p for p in paths if "noreverb" in os.path.basename(p).lower() or "dry" in os.path.basename(p).lower()]
        if dry:
            return dry[0]
        paths.sort(key=_rms, reverse=True)
        return paths[0] if paths else lead
    except Exception as exc:  # noqa: BLE001
        log(f"  去混响失败，跳过：{exc!r}")
        return lead


def pitch_shift_file(src: str, dst: str, semitones: float) -> None:
    """不改变速度地升降调（用于伴奏和和声）：先改采样率变调，再用 atempo 把速度拉回来"""
    f = 2 ** (semitones / 12)
    ff("-i", src, "-af", f"asetrate={44100 * f:.0f},aresample=44100,atempo={1 / f:.6f}", dst)


def add_reverb(src: str, dst: str) -> None:
    """变声后的人声是"干"的：去掉低频杂音、轻微压缩，再加一点短混响，和伴奏融合得更自然"""
    ff("-i", src, "-af",
       "highpass=f=90,acompressor=threshold=0.125:ratio=2.5:attack=5:release=120:makeup=1.2,"
       "aecho=0.85:0.75:35|70|120:0.16|0.10|0.06", dst)


def best_window(audio: str, clip: float) -> float:
    """找最响（一般就是副歌）的 clip 秒，返回起点（秒）"""
    import numpy as np
    import soundfile as sf

    data, sr = sf.read(audio, always_2d=True)
    mono = np.abs(data).mean(axis=1)
    sec = int(len(mono) // sr)
    if sec <= clip:
        return 0.0
    per = np.array([float(np.sqrt((mono[i * sr:(i + 1) * sr] ** 2).mean())) for i in range(sec)])
    win = int(clip)
    sums = np.convolve(per, np.ones(win), mode="valid")
    start = int(sums.argmax())
    # 往前找一个比较安静的地方开始，避免从半句开始
    quiet = per.mean() * 0.35
    for back in range(0, min(6, start) + 1):
        if per[start - back] < quiet:
            start -= back
            break
    return float(start)


def voice_info(name: str) -> dict:
    """voices/<音色>/voice.json：{"gender": "female"/"male", "target_hz": 舒服的演唱音高（可选）}"""
    try:
        with open(os.path.join(VOICES, name, "voice.json"), encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {}


def vocal_median_f0(vocals: str) -> float:
    """估计原唱人声的中位音高（Hz）。优先用 Applio 自带的 RMVPE（准，显卡上很快），失败再用 librosa"""
    import librosa
    import numpy as np

    y, sr = librosa.load(vocals, sr=16000, mono=True)
    rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=512)[0]
    if float(rms.max()) < 0.02:  # 人声轨几乎是静音
        return 0.0
    cwd = os.getcwd()
    try:
        import torch

        if APPLIO not in sys.path:
            sys.path.insert(0, APPLIO)
        os.chdir(APPLIO)
        from rvc.lib.predictors.f0 import RMVPE

        dev = "cuda:0" if torch.cuda.is_available() else "cpu"
        f0 = RMVPE(device=dev, high_register={"enabled": False}).get_f0(y.astype(np.float32))
        voiced = f0[(f0 > 60) & (f0 < 1100)]
        if len(voiced) > 50:
            return float(np.median(voiced))
        return 0.0
    except Exception as exc:  # noqa: BLE001
        log(f"RMVPE 测音高失败，改用 librosa：{exc!r}")
    finally:
        os.chdir(cwd)
    f0 = librosa.yin(y, fmin=70, fmax=1000, sr=sr, frame_length=2048)
    n = min(len(f0), len(rms))
    f0, rms = f0[:n], rms[:n]
    voiced = f0[(rms > rms.max() * 0.08) & (f0 > 75) & (f0 < 950)]
    return float(np.median(voiced)) if len(voiced) > 20 else 0.0


# 是否允许为了音域把伴奏也一起变调（会让熟悉这首歌的人觉得"唱错了"，默认关）
ALLOW_KEY_SHIFT = False


def auto_pitch(med: float, voice: str) -> tuple[int, int, str]:
    """返回（人声升降半音，伴奏升降半音，说明）。

    只在"原调"和"升/降一个八度"之间选：哪个离音色舒服的音高（target_hz）更近就用哪个。
    这样人声和伴奏始终在同一个调上，听起来和原曲一样，只是换了个人唱。
    """
    import math

    if med <= 0:
        return 0, 0, "没测出音高"
    info = voice_info(voice)
    g = str(info.get("gender", "female")).lower()
    target = float(info.get("target_hz") or (320 if g == "female" else 160))
    options = (0, 12) if g == "female" else (0, -12)
    octave = min(options, key=lambda o: abs(math.log2(med * 2 ** (o / 12) / target)))
    key = 0
    if ALLOW_KEY_SHIFT:
        k = round(12 * math.log2(target / (med * 2 ** (octave / 12))))
        key = max(-5, min(5, k)) if abs(k) > 3 else 0
    why = f"原唱约 {med:.0f}Hz，音色{'女声' if g == 'female' else '男声'}（舒服音高约 {target:.0f}Hz），八度 {octave:+d}"
    return octave + key, key, why


def rvc_via_server(voc: str, conv: str, voice: str, pitch: int, index_rate: float, protect: float) -> bool:
    """优先用常驻变声服务（模型已在显存里，快很多）；没开就返回 False"""
    import urllib.request

    try:
        urllib.request.urlopen("http://127.0.0.1:6231/health", timeout=2).read()
    except Exception:  # noqa: BLE001
        return False
    body = json.dumps({"input": voc, "output": conv, "voice": voice, "pitch": pitch,
                       "index_rate": index_rate, "protect": protect}).encode("utf-8")
    req = urllib.request.Request("http://127.0.0.1:6231/convert", data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        res = json.loads(urllib.request.urlopen(req, timeout=600).read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        log(f"变声服务出错，改用单独进程：{exc}")
        return False
    return bool(res.get("ok")) and os.path.exists(conv)


def prefetch() -> None:
    work = os.path.join(BASE, "work", "prefetch")
    os.makedirs(work, exist_ok=True)
    for m in (SEP_MODEL, KARAOKE_MODEL, DEREVERB_MODEL):
        log(f"下载/加载 {m}")
        sep = _separator(work)
        sep.load_model(model_filename=m)
    shutil.rmtree(work, ignore_errors=True)
    print("RESULT " + json.dumps({"ok": True, "prefetch": True}), flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--voice", default="")
    ap.add_argument("--pitch", default="auto", help="整数，或 auto = 按男声/女声和音色音域自动调整")
    ap.add_argument("--clip", type=float, default=60)
    ap.add_argument("--index-rate", type=float, default=SING_INDEX_RATE)
    ap.add_argument("--protect", type=float, default=SING_PROTECT)
    ap.add_argument("--simple", action="store_true", help="不分主唱/和声、不去混响（旧流程）")
    ap.add_argument("--prefetch", action="store_true")
    a = ap.parse_args()
    if a.prefetch:
        prefetch()
        return

    t0 = time.time()
    work = os.path.join(BASE, "work", time.strftime("%Y%m%d_%H%M%S"))
    os.makedirs(work, exist_ok=True)
    try:
        log("1/5 转码")
        src = os.path.join(work, "src.wav")
        ff("-i", a.input, "-ac", "2", "-ar", "44100", src)

        start, dur = 0.0, 0.0
        if a.clip > 0:
            # 先挑片段，只分离这一段，省时间
            start = best_window(src, a.clip)
            dur = a.clip
            log(f"选取片段：{start:.0f}s 开始，{dur:.0f}s")
            clip_src = os.path.join(work, "clip.wav")
            ff("-ss", str(start), "-t", str(dur), "-i", src, clip_src)
            src = clip_src

        log("2/5 分离人声和伴奏")
        voc, ins = separate(src, work)
        backing = None
        lead = voc
        if not a.simple:
            log("3/5 分出主唱、去混响")
            lead, backing = split_lead(voc, work)
            lead = dereverb(lead, work)

        med_f0 = vocal_median_f0(lead)
        if med_f0 <= 0:
            raise RuntimeError("NO_VOCALS：分离出来几乎没有人声，可能是纯伴奏版")
        pitch, key = 0, 0
        if a.voice:
            if str(a.pitch).lower() == "auto":
                pitch, key, why = auto_pitch(med_f0, a.voice)
                log(f"自动调音高：人声 {pitch:+d}，伴奏 {key:+d}（{why}）")
            else:
                pitch = int(a.pitch)
            log(f"4/5 变声（音色：{a.voice}，人声 {pitch:+d}）")
            pth, index = find_voice(a.voice)
            conv = os.path.join(work, "converted.wav")
            if rvc_via_server(lead, conv, a.voice, pitch, a.index_rate, a.protect):
                log("  （用的是常驻变声服务）")
            else:
                cmd = [
                    PY, os.path.join(APPLIO, "core.py"), "infer",
                    "--input-path", lead, "--output-path", conv,
                    "--pth-path", pth, "--index-path", index,
                    "--pitch", str(pitch), "--f0-method", "rmvpe",
                    "--index-rate", str(a.index_rate), "--protect", str(a.protect),
                    "--volume-envelope", "1.0", "--export-format", "WAV",
                ]
                r = subprocess.run(cmd, cwd=APPLIO, capture_output=True, text=True, encoding="utf-8", errors="replace")
                print(r.stdout[-1500:], r.stderr[-3000:], flush=True)
                if r.returncode != 0 or not os.path.exists(conv):
                    raise RuntimeError("变声失败")
            lead = conv
        else:
            log("4/5 跳过变声（测试模式）")

        log("5/5 混音")
        if not a.simple:
            wet = os.path.join(work, "lead_fx.wav")
            add_reverb(lead, wet)
            lead = wet
        if key:
            for name in ("ins", "backing"):
                path = ins if name == "ins" else backing
                if path:
                    shifted = os.path.join(work, f"{name}_key.wav")
                    pitch_shift_file(path, shifted, key)
                    if name == "ins":
                        ins = shifted
                    else:
                        backing = shifted
        inputs = ["-i", lead, "-i", ins]
        filt = (
            f"[0:a]aformat=channel_layouts=stereo,aresample=44100,volume={VOCAL_GAIN}"
            + (",aecho=0.8:0.6:50:0.12" if a.simple else "") + "[v];"
            f"[1:a]aformat=channel_layouts=stereo,aresample=44100,volume={INST_GAIN}[i];"
        )
        if backing:
            inputs += ["-i", backing]
            filt += (f"[2:a]aformat=channel_layouts=stereo,aresample=44100,volume={BACKING_GAIN}[b];"
                     "[v][i][b]amix=inputs=3:duration=longest,volume=3")
        else:
            filt += "[v][i]amix=inputs=2:duration=longest,volume=2"
        filt += ",alimiter=limit=0.95,loudnorm=I=-15:TP=-1.5:LRA=11,aresample=44100"
        if dur:
            filt += f",afade=t=in:st=0:d=1.5,afade=t=out:st={dur - 2.5:.1f}:d=2.5"
        ff(*inputs, "-filter_complex", filt, "-b:a", "192k", a.out)
        res = {"ok": True, "out": a.out, "start": start, "pitch": pitch, "key": key,
               "seconds": round(time.time() - t0, 1)}
    except Exception as exc:  # noqa: BLE001
        import traceback

        traceback.print_exc()
        res = {"ok": False, "error": f"{exc}"[:500], "seconds": round(time.time() - t0, 1)}
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print("RESULT " + json.dumps(res, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
