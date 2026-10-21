#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""tts_build「字级时间戳均分」提醒的回归测试（不联网、不需要密钥：python tests/test_tts_even_timestamps.py）。

守的错（lessons #140）：阿里云百炼对某些写法不给真的字级时间戳，而是把一段（或段尾一串字）的时长平均分给每个字。
音频完全正常，subs.py 照样切块、B() 照样能定位 —— 只是字幕和画面节拍跟着均分的时刻走，对不上吐字，而且不报错。
真的时间戳落在 1/16 秒的网格上、每个字长短不一；均分的两种样子：

  整段（whole）：每个字的时长 max − min < 0.02 s（至少 6 个字）；
  段尾（tail） ：段尾 ≥ 8 个相邻字的 offset 步长相等（容差 1.5 ms）。

A 段测判定函数；B 段把 synth 换成假的（不合成、不联网），跑真的 run()：每段在 manifest 里记
"even_timestamps": "whole" | "tail" | null，被标出来的段在 stderr 打一行 ⚠，写明段 id 和改写建议。
用例的 offset / 时长取自 2026-10-21 的真实配音（阿里 xuyuyuan_v3.1，+28%），字换成了占位字。
tts_build 顶层 import edge_tts；没装时这里塞一个空模块（本测试不走 edge 引擎）。失败即以非零码退出。
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import io
import json
import os
import sys
import tempfile
import types
from contextlib import redirect_stderr

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))   # tts_build 会 import tts_ali / tts_volcano

try:
    import edge_tts  # noqa: F401
except ImportError:
    sys.modules["edge_tts"] = types.SimpleNamespace(__version__="stub", Communicate=None)


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, rel))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


tb = _load("tts_build", "scripts/tts_build.py")
fails: list[str] = []


def check(label, got, want):
    if got != want:
        fails.append(f"{label}\n    得到: {got!r}\n    应为: {want!r}")
    else:
        print(f"  OK  {label}")


def bounds(offsets, durations):
    return [{"text": "字", "offset_sec": o, "duration_sec": d} for o, d in zip(offsets, durations)]


# 正常：真的时间戳（36 字）
NORMAL = bounds(
    [0.25, 0.438, 0.563, 0.75, 0.875, 1.0, 1.125, 1.313, 1.375, 1.563, 2.0, 2.125, 2.313, 2.5, 2.625, 2.813, 3.25, 3.438,
     3.563, 3.875, 4.063, 4.438, 4.625, 4.938, 5.063, 5.125, 5.563, 5.75, 5.875, 6.313, 6.563, 6.75, 6.938, 7.063, 7.188, 7.313],
    [0.188, 0.125, 0.187, 0.125, 0.125, 0.125, 0.188, 0.062, 0.188, 0.187, 0.125, 0.188, 0.187, 0.125, 0.188, 0.187, 0.188,
     0.125, 0.187, 0.188, 0.25, 0.187, 0.313, 0.125, 0.062, 0.188, 0.187, 0.125, 0.188, 0.25, 0.187, 0.188, 0.125, 0.125,
     0.125, 0.187])
# 整段均分（32 字，每字 0.232–0.233 s）
WHOLE = bounds(
    [0.0, 0.233, 0.465, 0.697, 0.93, 1.163, 1.395, 1.627, 1.859, 2.092, 2.324, 2.556, 2.789, 3.022, 3.254, 3.486, 3.719,
     3.952, 4.184, 4.416, 4.648, 4.881, 5.113, 5.345, 5.578, 5.811, 6.043, 6.275, 6.508, 6.741, 6.973, 7.205],
    [0.233, 0.232, 0.232, 0.233, 0.233, 0.232, 0.232, 0.232, 0.233, 0.232, 0.232, 0.233, 0.233, 0.232, 0.232, 0.233, 0.233,
     0.232, 0.232, 0.232, 0.233, 0.232, 0.232, 0.233, 0.233, 0.232, 0.232, 0.233, 0.233, 0.232, 0.232, 0.233])
# 段尾均分（38 字：前 7 字真的，后 31 字步长都是 0.196 s 左右）
TAIL = bounds(
    [0.0, 0.196, 0.392, 1.177, 1.373, 1.57, 1.766, 2.355, 2.551, 2.747, 2.943, 3.14, 3.336, 3.532, 3.728, 3.924, 4.12, 4.317,
     4.513, 4.709, 4.905, 5.102, 5.298, 5.495, 5.691, 5.887, 6.083, 6.279, 6.475, 6.672, 6.868, 7.064, 7.26, 7.456, 7.652,
     7.849, 8.045, 8.241],
    [0.196, 0.196, 0.785, 0.196, 0.197, 0.196, 0.589, 0.196, 0.196, 0.196, 0.197, 0.196, 0.196, 0.196, 0.196, 0.196, 0.197,
     0.196, 0.196, 0.196, 0.197, 0.196, 0.197, 0.196, 0.196, 0.196, 0.196, 0.196, 0.197, 0.196, 0.196, 0.196, 0.196, 0.196,
     0.197, 0.196, 0.196, 0.197])
# 正常：段尾 7 个相等步长是真实时间戳里也会碰上的网格巧合，要 ≥ 8 才算
TAIL7 = bounds(
    [0.25, 0.5, 0.688, 0.813, 0.938, 1.188, 1.438, 1.625, 1.813, 2.375, 2.625, 2.875, 3.063, 3.188, 3.313, 3.813, 4.063,
     4.344, 4.625, 4.75, 4.938, 5.5, 5.688, 5.875, 6.063, 6.25, 6.438, 6.625, 6.813],
    [0.25, 0.188, 0.125, 0.125, 0.25, 0.187, 0.187, 0.188, 0.125, 0.25, 0.25, 0.188, 0.125, 0.125, 0.25, 0.25, 0.281, 0.281,
     0.125, 0.188, 0.187, 0.188, 0.187, 0.188, 0.187, 0.188, 0.187, 0.188, 0.187])

# ---------------- A. 判定函数 ----------------
print("A. even_timestamps")
check("真的时间戳 → None", tb.even_timestamps(NORMAL), None)
check("每字一样长 → whole", tb.even_timestamps(WHOLE), "whole")
check("段尾 31 字步长相同 → tail", tb.even_timestamps(TAIL), "tail")
check("段尾只有 7 个相等步长 → None", tb.even_timestamps(TAIL7), None)
check("不到 6 个字不判", tb.even_timestamps(WHOLE[:5]), None)
check("正好 6 个字照判", tb.even_timestamps(WHOLE[:6]), "whole")
check("空白边界项不算字", tb.even_timestamps(WHOLE[:2] + [{"text": " ", "offset_sec": 0.5, "duration_sec": 0.9}] + WHOLE[2:5]),
      None)
check("没有时间戳 → None", tb.even_timestamps([]), None)

# ---------------- B. run() 写 manifest、打提醒 ----------------
print("B. run() → audio-manifest.json 的 even_timestamps + ⚠ 提醒")
SAMPLES = {"hook1": NORMAL, "mem6": WHOLE, "sm7": TAIL, "pat2": TAIL7, "empty": []}


async def fake_synth(text, out_path, voice, rate, **kw):
    sid = os.path.basename(out_path)[:-4]
    b = SAMPLES[sid]
    return {"path": out_path, "bytes": 1, "voice": voice, "rate": rate, "spoken_text": text, "original_text": text,
            "duration_sec": 1.0, "lead_cut_sec": 0.0, "container_duration_sec": 1.0, "speech_start_sec": 0.0,
            "speech_end_sec": 1.0, "tail_pad_sec": 0.0, "boundary_count": len(b), "boundaries": b}


tb.synth = fake_synth
with tempfile.TemporaryDirectory() as d:
    with open(os.path.join(d, "project.json"), "w", encoding="utf-8") as f:
        json.dump({"provider": "ali", "voice": "xuyuyuan_v3.1", "rate": "+28%"}, f)
    with open(os.path.join(d, "narration.json"), "w", encoding="utf-8") as f:
        json.dump([{"id": k, "text": "占位"} for k in SAMPLES], f, ensure_ascii=False)
    err = io.StringIO()
    args = argparse.Namespace(project=d, provider="", voice="", rate="", no_trim=False, no_cache=True)
    with redirect_stderr(err):
        code = asyncio.run(tb.run(args))
    check("run() 退出码 0（提醒不算失败）", code, 0)
    with open(os.path.join(d, "audio-manifest.json"), encoding="utf-8") as f:
        items = json.load(f)["items"]
    check("每段都记 even_timestamps", [(it["id"], it["even_timestamps"]) for it in items],
          [("hook1", None), ("mem6", "whole"), ("sm7", "tail"), ("pat2", None), ("empty", None)])
    warn = [ln for ln in err.getvalue().splitlines() if "⚠" in ln]
    check("只为被标出来的段各打一行 ⚠", [next(s for s in ("mem6", "sm7") if s in ln) for ln in warn[:2]], ["mem6", "sm7"])
    check("⚠ 行写明整段 / 段尾", ("整段" in warn[0], "31/38" in warn[1]), (True, True))
    check("⚠ 行建议改写后重配", all("改写" in ln and "重配" in ln for ln in warn[:2]), True)
    check("末尾汇总一行", any("2 段" in ln for ln in warn[2:]), True)

if fails:
    print(f"\n✗ {len(fails)} 项失败：")
    for x in fails:
        print("  " + x)
    sys.exit(1)
print("\n全部通过")
