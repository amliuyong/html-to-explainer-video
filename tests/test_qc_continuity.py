#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""qc_check「画面连续性」的回归测试（python tests/test_qc_continuity.py）。

守两类「抽帧看不出来、连着播才露馅」的错：

  ① 单帧跳动：某一帧和前后两帧都不一样，而前后两帧彼此一样 —— 典型来源是帧在搭建期读布局，
     --workers 并行渲染时某个进程量歪了，它负责的帧（每 N 帧一帧）整块错位。
     抽帧 QC 每次只看一张，永远看不出「这一张比邻居低了 12px」。
  ② 场间硬切：渲染器不做转场，段间 gap 停在上一场末帧，下一场第 0 帧直接切进来。

A 段只测纯函数（零依赖）；B 段用 numpy + ffmpeg 合成一段小视频端到端跑扫描
（缺任一依赖就跳过 B 段，不算失败）。失败即以非零码退出。
"""
from __future__ import annotations

import importlib.util
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


qc = _load("qc_check", "scripts/qc_check.py")
fails: list[str] = []


def check(label, got, want):
    if got != want:
        fails.append(f"{label}\n    得到: {got!r}\n    应为: {want!r}")
    else:
        print(f"  OK  {label}")


# ---------------- A. 纯函数 ----------------
print("A. find_spikes / hard_cuts / spike_period")
INF = float("inf")
# 帧 3 与帧 9 各自错位一帧（step 大、跨一帧比 skip ≈ 0）；帧 6 是正常运动（skip 也大）
step = [0, .1, .1, 3.0, 3.0, .1, 2.0, 2.0, .1, 3.2, 3.1, .1]
skip = [INF, .1, .1, .2, 3.0, .1, 4.0, 2.0, .1, .2, 3.1, INF]
check("只认「前后相同、中间不同」的帧", qc.find_spikes(step, skip), [3, 9])
check("正常运动不算跳动", 6 in qc.find_spikes(step, skip), False)
check("首尾帧不判（没有两个邻居）", qc.find_spikes([0, 9.0], [INF, INF]), [])

check("周期：每 6 帧一次 → 6", qc.spike_period([3, 9, 15, 21, 27, 39]), 6)
check("周期：无规律 → None", qc.spike_period([3, 4, 11, 30]), None)
check("周期：少于 3 处不下结论", qc.spike_period([3, 9]), None)

step2 = [0, .1, 9.0, .1, .2, .8, .1, 7.5, .1]
check("硬切：边界帧 step 超阈值才算", qc.hard_cuts(step2, [2, 5, 7], 3.0), [2, 7])
check("硬切：越界边界忽略", qc.hard_cuts(step2, [0, 99], 3.0), [])

check("场景边界帧号与渲染器同口径（round(start×fps)）",
      qc.scene_boundaries({"a": {"start_sec": 0.0}, "b": {"start_sec": 6.7 + 0.35},
                           "c": {"start_sec": 16.42}}, ["a", "b", "c"], 30), [("b", 212), ("c", 493)])

# ---------------- B. 端到端（numpy + ffmpeg） ----------------
print("B. scan_video 端到端")
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
    W, H, N = 960, 540, 72
    frames = []
    for i in range(N):
        f = np.full((H, W), 40, np.uint8)
        if i < 48:                      # 场景 1：一张卡片，渐入后静止
            y = 150 + (24 if i % 6 == 3 and i >= 12 else 0)   # 每 6 帧有一帧整块下移 24px
            f[y:y + 160, 200:760] = min(220, 40 + i * 20)   # 第 9 帧起已静止
            f[y + 200:y + 230, 200:600] = 200
        else:                           # 场景 2（第 48 帧硬切进来）：完全不同的版式
            f[300:420, 100:900] = 230
        frames.append(f)
    with tempfile.TemporaryDirectory() as d:
        mp4 = os.path.join(d, "t.mp4")
        p = subprocess.run([ff, "-v", "error", "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{W}x{H}",
                            "-r", "30", "-i", "-", "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", mp4],
                           input=b"".join(x.tobytes() for x in frames), capture_output=True)
        if p.returncode != 0:
            fails.append("合成测试视频失败: " + p.stderr.decode(errors="replace")[:300])
        else:
            res = qc.scan_video(ff, mp4)
            want = [i for i in range(12, 48) if i % 6 == 3]
            check("扫出每一处单帧跳动、且只有这些", res and qc.find_spikes(res["step"], res["skip"]), want)
            check("跳动周期 = 6", res and qc.spike_period(qc.find_spikes(res["step"], res["skip"])), 6)
            check("第 48 帧被认成硬切", res and qc.hard_cuts(res["step"], [48], qc.HARD_CUT_STEP), [48])

if fails:
    print("\n✗ 失败：")
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("\n✓ qc 连续性回归全部通过")
