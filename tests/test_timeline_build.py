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
缺 numpy 或 ffmpeg 就跳过 B 段，不算失败。失败即以非零码退出。
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

# ---------------- B. 端到端（numpy + ffmpeg） ----------------
print("B. timeline_build 端到端")
try:
    import numpy as np
except ImportError:
    np = None
ff = shutil.which("ffmpeg")
if np is None or ff is None:
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

if fails:
    print("\n✗ 失败：")
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("\n✓ timeline_build 拼音轨回归全部通过")
