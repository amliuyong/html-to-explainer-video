#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""timeline_build「拼音轨不漂移」的回归测试（python tests/test_timeline_build.py）。

守的错（lessons #136）：layout.json 按每段 MP3 的**容器时长**排，narration-full.mp3 却是把**解码后**的段
直接 concat —— 解码出来的采样比容器时长短 20–50 ms（MP3 的 priming / padding），段数一多，
音轨越往后越早于画面和字幕（8 分钟片累计近 2 s）。抽帧 QC、时长检查（容差 0.5 s）都看不出来。

A 段只测纯函数（零依赖）；B 段用 numpy + ffmpeg 合成一个小工程，跑真的 timeline_build.py：
每段是一段静音加一声 beep（段内偏移已知，各段频率不同），按 tts_build 的命令编码成 MP3。
断言每声 beep 都落在 start_sec + 段内偏移的 ±10 ms 内，总长与 layout 差不过 1 帧；
再按旧办法（解码后直接 concat）拼一遍，断言它过不了同一个检查 —— 样本有鉴别力。

C 段守 lessons #141：2.0.9–2.1.0 的拼法 concat 完直接进 libmp3lame，ffmpeg（6.1 起）替编码器按 1152 切帧时，
最后一帧可能是一截不拷贝、只挪了指针的尾巴，缓冲比 libmp3lame 要的少 4 个采样 →「inadequate AVFrame plane padding」。
合成一个两段的工程，让末段补的静音正好切出这样的尾巴：旧拼法加 `-cpuflags 0`（把对齐降到 8 字节，x86 上也能复现）
必须报这个错，新拼法同样加 `-cpuflags 0` 必须过；真的 timeline_build.py 要过（aarch64 等 16 字节对齐的机器上，
不加 -cpuflags 这就是原样复现）；重新分帧前后 concat 出来的 PCM 逐字节相同。
缺 numpy 或 ffmpeg 就跳过 B、C 段，不算失败。失败即以非零码退出。
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, rel))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


tb = _load("timeline_build", "scripts/timeline_build.py")
fails: list[str] = []


def check(label, got, want):
    if got != want:
        fails.append(f"{label}\n    得到: {got!r}\n    应为: {want!r}")
    else:
        print(f"  OK  {label}")


# ---------------- A. 纯函数 ----------------
print("A. slot_samples / length_problem")
lay = {"a": {"start_sec": 0.0, "duration_sec": 1.54},
       "b": {"start_sec": 1.89, "duration_sec": 2.4},
       "c": {"start_sec": 4.64, "duration_sec": 1.0}}
slots = tb.slot_samples(lay, ["a", "b", "c"], 44100)
check("每段占 round(dur×sr)，静音补到下一段 round(start×sr)", slots,
      [(67914, 15435), (105840, 15435), (44100, 0)])
starts, pos = [], 0
for n_seg, n_gap in slots:
    starts.append(pos)
    pos += n_seg + n_gap
check("每段起点 = round(start_sec × sr)，不随段数累积误差", starts, [0, 83349, 204624])
check("相邻两段舍入撞上 1 个采样时，上一段让出来、不重叠",
      tb.slot_samples({"a": {"start_sec": 0.0, "duration_sec": 0.00002},
                       "b": {"start_sec": 0.00001, "duration_sec": 1.0}}, ["a", "b"], 44100)[0], (0, 0))
check("差 1 帧以内不报", tb.length_problem(10.02, 10.0, 30), None)
check("差超过 1 帧要报", tb.length_problem(9.96, 10.0, 30) is not None, True)
_cmd = tb.concat_cmd("ffmpeg", ["a.mp3", "b.mp3"], [(100, 50), (200, 0)], "out.mp3")
check("concat 完先切成每帧 1152 个采样（p=0 不补尾）再进 libmp3lame（lessons #141）",
      (_cmd[_cmd.index("-filter_complex") + 1].endswith("concat=n=3:v=0:a=1,asetnsamples=n=1152:p=0[out]"),
       _cmd[_cmd.index("-c:a") + 1]), (True, "libmp3lame"))

# ---------------- B. 端到端（numpy + ffmpeg） ----------------
print("B. timeline_build 端到端")
try:
    import numpy as np
except ImportError:
    np = None
ff = shutil.which("ffmpeg")
if (np is None or ff is None) and os.environ.get("HX_NO_SKIP") == "1":   # CI：缺依赖不许静默跳过
    fails.append("缺 numpy 或 PATH 上的 ffmpeg，而 HX_NO_SKIP=1 不许跳过 B 段")
elif np is None or ff is None:
    print("  SKIP 缺 numpy 或 ffmpeg —— 只跑了 A 段")
else:
    SR, GAP, FPS, BEEP = 48000, 0.35, 30, 0.15
    # (段 id, 段长秒, beep 起点秒, beep 频率 Hz)：段长不取整，让每段的 MP3 padding 都不一样
    SEGS = [(f"s{i}", round(1.6 + 0.37 * (i % 4) + 0.013 * i, 3), round(0.2 + 0.11 * (i % 5), 3), 450 + 130 * i)
            for i in range(12)]

    def run(*args, data=None):
        p = subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-y", *args], input=data, capture_output=True)
        if p.returncode != 0:
            raise RuntimeError(p.stderr.decode(errors="replace")[-300:])
        return p.stdout

    def decode(path):
        return np.frombuffer(run("-i", path, "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"), dtype="<f4")

    def container(path):
        """tts_build 回填 container_duration_sec 的口径：ffmpeg 打印的 Duration:。"""
        err = subprocess.run([ff, "-hide_banner", "-i", path], capture_output=True, text=True,
                             errors="replace").stderr
        h, m, s = err.split("Duration:")[1].split(",")[0].strip().split(":")
        return round(int(h) * 3600 + int(m) * 60 + float(s), 3)

    def beep_errors(path, layout):
        """每声 beep 的能量重心 - (start_sec + 段内偏移 + BEEP/2)，单位秒。"""
        x = decode(path).astype(np.float64)
        errs = []
        for sid, _, at, _ in SEGS:
            want = layout[sid]["start_sec"] + at + BEEP / 2
            a, b = max(0, int((want - 0.7) * SR)), min(len(x), int((want + 0.7) * SR))
            e = x[a:b] ** 2
            errs.append((a + float((np.arange(len(e)) * e).sum() / e.sum())) / SR - want)
        return errs

    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, "audio"))
        items = []
        for sid, length, at, freq in SEGS:
            x = np.zeros(int(round(length * SR)), np.float32)
            t = np.arange(int(BEEP * SR)) / SR
            k = int(round(at * SR))
            x[k:k + len(t)] = 0.5 * np.hanning(len(t)) * np.sin(2 * np.pi * freq * t)
            mp3 = os.path.join(d, "audio", f"{sid}.mp3")
            # 与 tts_build.trim_edges 同一条编码命令：解码后正好 len(x) 个采样，容器时长要长 20–50 ms
            run("-f", "f32le", "-ac", "1", "-ar", str(SR), "-i", "-", "-c:a", "libmp3lame", "-b:a", "128k", mp3,
                data=x.astype("<f4").tobytes())
            items.append({"id": sid, "path": mp3, "duration_sec": length, "container_duration_sec": container(mp3)})
        order = [s[0] for s in SEGS]
        json.dump({"order": order, "gap": GAP, "fps": FPS}, open(os.path.join(d, "project.json"), "w"))
        json.dump({"items": items}, open(os.path.join(d, "audio-manifest.json"), "w"))
        check("样本像真实配音：每段容器时长都比解码长 20–50 ms",
              all(0.02 <= it["container_duration_sec"] - it["duration_sec"] <= 0.05 for it in items), True)

        p = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "timeline_build.py"), "--project", d],
                           capture_output=True, text=True, errors="replace")
        check("timeline_build 退出码 0", p.returncode, 0)
        if p.returncode != 0:
            fails.append("timeline_build 输出：" + (p.stdout + p.stderr)[-500:])
        else:
            layout = json.load(open(os.path.join(d, "layout.json"), encoding="utf-8"))
            # layout 的排法不变：容器时长（3 位小数），start = round(cursor, 3)，cursor += dur + gap
            want, cursor = {}, 0.0
            for it in items:
                dur = round(it["container_duration_sec"], 3)
                want[it["id"]] = {"start_sec": round(cursor, 3), "duration_sec": dur}
                cursor += dur + GAP
            total = cursor - GAP
            check("layout 各段与旧算法逐字相同", {k: layout[k] for k in order}, want)
            check("layout 总长 / 总帧数不变", (layout["_total"]["video_duration_sec"], layout["_total"]["total_frames"]),
                  (round(total, 3), int(round(total * FPS))))

            full = os.path.join(d, "audio", "narration-full.mp3")
            errs = beep_errors(full, layout)
            worst = max(abs(e) for e in errs)
            print(f"      新音轨 beep 最大误差 {worst * 1000:.1f} ms")
            check("每声 beep 都落在 start_sec + 段内偏移 ±10 ms 内", worst <= 0.010, True)
            check("音轨解码总长与 layout 差不过 1 帧", abs(len(decode(full)) / SR - total) <= 1 / FPS, True)
            check("_total.audio_duration_sec 记的是解码后的总长",
                  abs(layout["_total"]["audio_duration_sec"] - total) <= 1 / FPS, True)

            # 旧办法：解码后的段 + -t gap 的 anullsrc，直接 concat（2.0.8 及以前的 timeline_build）
            ins, fl, lb = [], [], []
            for i, sid in enumerate(order):
                ins += ["-i", os.path.join(d, "audio", f"{sid}.mp3")]
            for i in range(len(order)):
                fl.append(f"[{i}:a]aresample=44100[a{i}]")
                lb.append(f"[a{i}]")
                if i < len(order) - 1:
                    ins += ["-f", "lavfi", "-t", str(GAP), "-i", "anullsrc=channel_layout=mono:sample_rate=44100"]
                    fl.append(f"[{len(order) + i}:a]aresample=44100[g{i}]")
                    lb.append(f"[g{i}]")
            old = os.path.join(d, "old.mp3")
            run(*ins, "-filter_complex", ";".join(fl) + ";" + "".join(lb) + f"concat=n={len(lb)}:v=0:a=1[out]",
                "-map", "[out]", "-c:a", "libmp3lame", "-b:a", "128k", old)
            old_errs = beep_errors(old, layout)
            print(f"      旧音轨末段 beep 误差 {old_errs[-1] * 1000:+.0f} ms")
            check("反向验证：旧办法过不了 ±10 ms（音轨提前，越往后越多）",
                  max(abs(e) for e in old_errs) > 0.010 and old_errs[-1] < -0.2, True)

            first = open(os.path.join(d, "layout.json"), "rb").read()
            p2 = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "timeline_build.py"), "--project", d],
                                capture_output=True, text=True, errors="replace")
            check("重跑一遍：layout.json 逐字节不变",
                  p2.returncode == 0 and open(os.path.join(d, "layout.json"), "rb").read() == first, True)

    # ---------------- C. 末帧的缓冲够不够 libmp3lame（lessons #141） ----------------
    print("C. 最后一帧不是一截缓冲不够的尾巴")
    OUT_SR = tb.SR

    def tail_too_short(n_total, n_pad, align=4):
        """2.0.9–2.1.0 的拼法会不会把一截缓冲不够的尾巴递给 libmp3lame。

        末段补的静音是 apad 一次分配的一帧（≤ 4096 个采样，缓冲按 32 个采样取整）。ffmpeg 按 1152 切帧，
        这一帧前面的采样被拷走，剩下 r = n_total mod 1152 个时不拷贝、只挪指针 —— 前提是挪过的字节数是
        av_cpu_max_align 的倍数（align 是换算成采样数的值，16 字节 = 4 个）。剩下的缓冲是
        ceil32(n_pad) − (n_pad − r) 个采样，libmp3lame 要 ceil8(r) 个。
        """
        r = n_total % 1152
        off = n_pad - r
        if not 0 < r < n_pad <= 4096 or off % align:
            return False
        return -(-n_pad // 32) * 32 - off < -(-r // 8) * 8

    def old_graph(slots):
        """2.0.9–2.1.0 的拼法：concat 完直接进编码器。"""
        fl, lb = [], []
        for i, (n_seg, n_gap) in enumerate(slots):
            fl.append(f"[{i}:a]aresample={OUT_SR},apad=whole_len={n_seg},atrim=end_sample={n_seg}[a{i}]")
            lb.append(f"[a{i}]")
            if n_gap > 0:
                fl.append(f"anullsrc=channel_layout=mono:sample_rate={OUT_SR},atrim=end_sample={n_gap}[g{i}]")
                lb.append(f"[g{i}]")
        return ";".join(fl) + ";" + "".join(lb) + f"concat=n={len(lb)}:v=0:a=1[out]"

    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, "audio"))
        ids, parts, dec, cont = ["t0", "t1"], [], [], []
        for i, length in enumerate([1.234, 0.987]):
            x = 0.3 * np.sin(2 * np.pi * (300 + 200 * i) * np.arange(int(round(length * SR))) / SR)
            mp3 = os.path.join(d, "audio", f"{ids[i]}.mp3")
            run("-f", "f32le", "-ac", "1", "-ar", str(SR), "-i", "-", "-c:a", "libmp3lame", "-b:a", "128k", mp3,
                data=x.astype("<f4").tobytes())
            parts.append(mp3)
            dec.append(len(run("-i", mp3, "-af", f"aresample={OUT_SR}", "-f", "f32le", "-")) // 4)
            cont.append(container(mp3))

        # 末段的容器时长取比解码长 20–50 ms 的一个毫秒值、段间 gap 取 0.30–0.45 s（timeline_build 只读 manifest），
        # 找一组让旧拼法切出缓冲不够的尾巴的
        pick = None
        lo = int(dec[1] * 1000 / OUT_SR) + 21
        for c1_ms in range(lo, lo + 29):
            for gap_ms in range(300, 451):
                lay = {"t0": {"start_sec": 0.0, "duration_sec": round(cont[0], 3)},
                       "t1": {"start_sec": round(round(cont[0], 3) + gap_ms / 1000, 3), "duration_sec": c1_ms / 1000}}
                slots = tb.slot_samples(lay, ids)
                n_total = sum(a + b for a, b in slots)
                if tail_too_short(n_total, slots[-1][0] - dec[1]):
                    pick = (c1_ms, gap_ms, slots, n_total)
                    break
            if pick:
                break
        check("找得到能复现的样本（末段比解码长 20–50 ms，旧拼法会切出缓冲不够的尾巴）", pick is not None, True)
        if pick:
            c1_ms, gap_ms, slots, n_total = pick
            print(f"      末段补 {slots[-1][0] - dec[1]} 个采样静音，总 {n_total} 个采样（末帧 {n_total % 1152}）")
            json.dump({"order": ids, "gap": gap_ms / 1000, "fps": FPS}, open(os.path.join(d, "project.json"), "w"))
            json.dump({"items": [{"id": "t0", "path": parts[0], "duration_sec": dec[0] / OUT_SR,
                                  "container_duration_sec": cont[0]},
                                 {"id": "t1", "path": parts[1], "duration_sec": dec[1] / OUT_SR,
                                  "container_duration_sec": c1_ms / 1000}]},
                      open(os.path.join(d, "audio-manifest.json"), "w"))
            ins = [a for mp3 in parts for a in ("-i", mp3)]

            # 反向验证：-cpuflags 0 把 av_cpu_max_align 降到 8 字节，x86（AVX 是 32 字节，从来切不出这种尾巴）上也复现
            p = subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-y", "-cpuflags", "0", *ins,
                                "-filter_complex", old_graph(slots), "-map", "[out]",
                                "-c:a", "libmp3lame", "-b:a", "128k", os.path.join(d, "old.mp3")],
                               capture_output=True, text=True, errors="replace")
            if p.returncode != 0 and "inadequate AVFrame plane padding" in p.stderr:
                check("反向验证：旧拼法（-cpuflags 0）报 inadequate AVFrame plane padding", True, True)
            elif os.environ.get("HX_NO_SKIP") == "1":
                fails.append("反向验证：旧拼法在这个 ffmpeg 上没报 inadequate AVFrame plane padding —— 样本没有鉴别力"
                             f"（退出码 {p.returncode}）。ffmpeg 6.0 及以前不替编码器切帧；若是 FFmpeg 修了，"
                             "这条反向验证和 lessons #141 要一起改")
            else:
                print("  SKIP 反向验证：这个 ffmpeg 不替编码器切帧（6.0 及以前），旧拼法在这里本来就不出错")

            cmd = tb.concat_cmd(ff, parts, slots, os.path.join(d, "new.mp3"))
            cmd[1:1] = ["-cpuflags", "0"]
            p = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
            check("新拼法（同样 -cpuflags 0）退出码 0", p.returncode, 0)
            if p.returncode != 0:
                fails.append("新拼法输出：" + p.stderr[-600:])

            p = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "timeline_build.py"), "--project", d],
                               capture_output=True, text=True, errors="replace")
            check("timeline_build 在这份样本上退出码 0", p.returncode, 0)
            if p.returncode != 0:
                fails.append("timeline_build 输出：" + (p.stdout + p.stderr)[-500:])
            else:
                got = tb.decoded_seconds(os.path.join(d, "audio", "narration-full.mp3"), ff)
                print(f"      音轨解码 {got * OUT_SR:.0f} 个采样，槽位合计 {n_total}")
                check("音轨解码总长与槽位合计差不过 1 帧", abs(got - n_total / OUT_SR) <= 1 / FPS, True)

            fc_new = cmd[cmd.index("-filter_complex") + 1]
            pcm_old = run(*ins, "-filter_complex", old_graph(slots), "-map", "[out]", "-f", "f32le", "-")
            pcm_new = run(*ins, "-filter_complex", fc_new, "-map", "[out]", "-f", "f32le", "-")
            check("重新分帧不动采样：concat 出来的 PCM 与旧拼法逐字节相同，正好是槽位合计的采样数",
                  (pcm_new == pcm_old, len(pcm_new) // 4), (True, n_total))

if fails:
    print("\n✗ 失败：")
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("\n✓ timeline_build 拼音轨回归全部通过")
