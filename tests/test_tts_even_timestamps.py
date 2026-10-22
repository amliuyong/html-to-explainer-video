#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""tts_build「字级时间戳是插值的」提醒的回归测试（不联网、不需要密钥：python tests/test_tts_even_timestamps.py）。

守的错（lessons #140）：阿里云百炼对某些写法不给真的字级时间戳，而是把一段（或从某个字起到段尾）的时长按字数分给每个字。
音频完全正常，subs.py 照样切块、B() 照样能定位 —— 只是字幕和画面节拍跟着插值的时刻走，对不上吐字，而且不报错。
真的时间戳落在网格上，插值出来的不在：中文的字级时间戳在 1/32 秒网格上（多数在 1/16 秒上），
英文的词级时间戳在 0.08 秒网格上（lessons #146）。判定（至少 6 个字）：

  ① 这一段取落点多的那个网格（一样多取 1/32 秒），至少一半的 offset 不在它上面（离格点 > 1 ms），或有连续 8 个不在；
  ② 每个字的时长 max − min < 0.02 s（每字一样长；插值碰巧落在网格上时靠它，比如每字正好 0.25 s）；
  ③ 段尾 ≥ 8 个相邻字的 offset 步长相等（取整到毫秒后容差 2.5 ms）。
  插值从第 1、2 个字起是整段（whole），从后面某个字起是段尾（tail）。

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
# 整段插值，每字一样长（32 字，每字 0.232–0.233 s）
WHOLE = bounds(
    [0.0, 0.233, 0.465, 0.697, 0.93, 1.163, 1.395, 1.627, 1.859, 2.092, 2.324, 2.556, 2.789, 3.022, 3.254, 3.486, 3.719,
     3.952, 4.184, 4.416, 4.648, 4.881, 5.113, 5.345, 5.578, 5.811, 6.043, 6.275, 6.508, 6.741, 6.973, 7.205],
    [0.233, 0.232, 0.232, 0.233, 0.233, 0.232, 0.232, 0.232, 0.233, 0.232, 0.232, 0.233, 0.233, 0.232, 0.232, 0.233, 0.233,
     0.232, 0.232, 0.232, 0.233, 0.232, 0.232, 0.233, 0.233, 0.232, 0.232, 0.233, 0.233, 0.232, 0.232, 0.233])
# 整段插值（38 字，0.196 s 一个单位，「2023」「96」这类多字的词分到 4 倍、2 倍）：每字不一样长
TAIL = bounds(
    [0.0, 0.196, 0.392, 1.177, 1.373, 1.57, 1.766, 2.355, 2.551, 2.747, 2.943, 3.14, 3.336, 3.532, 3.728, 3.924, 4.12, 4.317,
     4.513, 4.709, 4.905, 5.102, 5.298, 5.495, 5.691, 5.887, 6.083, 6.279, 6.475, 6.672, 6.868, 7.064, 7.26, 7.456, 7.652,
     7.849, 8.045, 8.241],
    [0.196, 0.196, 0.785, 0.196, 0.197, 0.196, 0.589, 0.196, 0.196, 0.196, 0.197, 0.196, 0.196, 0.196, 0.196, 0.196, 0.197,
     0.196, 0.196, 0.196, 0.197, 0.196, 0.197, 0.196, 0.196, 0.196, 0.196, 0.196, 0.197, 0.196, 0.196, 0.196, 0.196, 0.196,
     0.197, 0.196, 0.196, 0.197])
# 正常：段尾 7 个相等步长，都在网格上（真实时间戳里的巧合），要 ≥ 8 才算
TAIL7 = bounds(
    [0.25, 0.5, 0.688, 0.813, 0.938, 1.188, 1.438, 1.625, 1.813, 2.375, 2.625, 2.875, 3.063, 3.188, 3.313, 3.813, 4.063,
     4.344, 4.625, 4.75, 4.938, 5.5, 5.688, 5.875, 6.063, 6.25, 6.438, 6.625, 6.813],
    [0.25, 0.188, 0.125, 0.125, 0.25, 0.187, 0.187, 0.188, 0.125, 0.25, 0.25, 0.188, 0.125, 0.125, 0.25, 0.25, 0.281, 0.281,
     0.125, 0.188, 0.187, 0.188, 0.187, 0.188, 0.187, 0.188, 0.187, 0.188, 0.187])
# 整段插值，步长 0.27 左右、取整到毫秒后 0.269–0.271 交替（旧的 1.5 ms 步长容差认不出），有一个两倍长的词
ROUNDED = bounds(
    [0.0, 0.27, 0.54, 0.81, 1.08, 1.351, 1.62, 1.891, 2.161, 2.43, 2.971, 3.241, 3.511, 3.781, 4.052, 4.321, 4.591, 4.862,
     5.132, 5.402, 5.672, 5.942, 6.212, 6.482, 6.752, 7.023, 7.292],
    [0.27, 0.27, 0.27, 0.27, 0.271, 0.269, 0.271, 0.27, 0.269, 0.541, 0.27, 0.27, 0.27, 0.271, 0.269, 0.27, 0.271, 0.27,
     0.27, 0.27, 0.27, 0.27, 0.27, 0.27, 0.271, 0.269, 0.271])
# 段尾插值：前 3 个字在网格上（真的），第 4 个字起不在（28 字里后 25 个）
LATE = bounds(
    [0.313, 0.5, 0.625, 0.803, 0.98, 1.159, 1.337, 1.514, 1.692, 1.87, 2.048, 2.226, 2.404, 2.582, 2.759, 2.938, 3.116,
     3.293, 3.471, 3.649, 3.827, 4.005, 4.183, 4.361, 4.538, 4.716, 4.895, 5.072],
    [0.187, 0.125, 0.178, 0.177, 0.179, 0.178, 0.177, 0.178, 0.178, 0.178, 0.178, 0.178, 0.178, 0.177, 0.179, 0.178, 0.177,
     0.178, 0.178, 0.178, 0.178, 0.178, 0.178, 0.177, 0.178, 0.179, 0.177, 0.178])
# 整段插值，正好落在网格上：每字 0.25 s（15 字）
GRID_EQUAL = bounds([i * 0.25 for i in range(15)], [0.25] * 15)
# 连续 8 个不在网格上（不到一半）：把 NORMAL 最后 8 个字的 offset 挪开 7 ms
RUN8 = [dict(b) for b in NORMAL]
for b in RUN8[-8:]:
    b["offset_sec"] = round(b["offset_sec"] + 0.007, 3)

# 英文（lessons #146）：阿里给英文的是词级时间戳，真的落在 0.08 秒网格上，不在 1/32 秒网格上。
# 取自 2026-10-22 的真实配音（阿里 Andy_v3.1，+0%），英文宣传片的三段。
def words(offsets, durations):
    return [{"text": "word", "offset_sec": o, "duration_sec": d} for o, d in zip(offsets, durations)]


# 正常：真的词级时间戳（24 个词，都在 0.08 秒网格上，只有 1 个在 1/32 秒网格上）
EN_REAL = words(
    [0.16, 0.4, 0.96, 1.28, 1.6, 2.0, 2.24, 2.56, 2.8, 2.96, 3.6, 3.68, 4.48, 4.96, 5.12, 5.36, 6.16, 6.24, 6.8, 7.12, 7.2,
     7.44, 7.6, 7.76],
    [0.24, 0.48, 0.32, 0.32, 0.32, 0.24, 0.32, 0.24, 0.16, 0.56, 0.08, 0.32, 0.48, 0.16, 0.24, 0.4, 0.08, 0.32, 0.32, 0.08,
     0.24, 0.16, 0.16, 0.24])
# 整段插值，每词一样长（11 个词，每词 0.421–0.422 s）
EN_EVEN = words(
    [0.0, 0.422, 0.844, 1.265, 1.687, 2.109, 2.531, 2.953, 3.375, 3.796, 4.218],
    [0.422, 0.422, 0.421, 0.422, 0.422, 0.422, 0.422, 0.422, 0.421, 0.422, 0.422])
# 整段插值，0.342 s 一个单位、长词分到整数倍（31 个词）：每词不一样长，两个网格都不在
EN_UNITS = words(
    [0.0, 0.342, 0.684, 1.369, 1.711, 2.053, 2.396, 2.738, 3.08, 3.422, 3.764, 4.107, 4.449, 4.791, 5.133, 5.476, 5.818,
     6.16, 6.502, 6.844, 7.187, 7.529, 7.871, 8.213, 9.24, 9.582, 9.924, 10.951, 11.293, 11.636, 11.978],
    [0.342, 0.342, 0.685, 0.342, 0.342, 0.343, 0.342, 0.342, 0.342, 0.342, 0.343, 0.342, 0.342, 0.342, 0.343, 0.342,
     0.342, 0.342, 0.342, 0.343, 0.342, 0.342, 0.342, 1.027, 0.342, 0.342, 1.027, 0.342, 0.343, 0.342, 0.342])

# ---------------- A. 判定函数 ----------------
print("A. even_timestamps")
check("真的时间戳 → None", tb.even_timestamps(NORMAL), None)
check("每字一样长 → whole", tb.even_timestamps(WHOLE), "whole")
check("多字词分到整数倍时长、每字不一样长 → whole（看网格）", tb.even_timestamps(TAIL), "whole")
check("步长取整后差 2 ms → whole（看网格）", tb.even_timestamps(ROUNDED), "whole")
check("前 3 个字真的 → tail，从第 4 个字起", tb.even_assess(LATE), ("tail", 3))
check("正好在网格上、每字一样长 → whole", tb.even_assess(GRID_EQUAL), ("whole", 0))
check("连续 8 个不在网格上 → tail", tb.even_assess(RUN8), ("tail", 28))
check("段尾只有 7 个相等步长、都在网格上 → None", tb.even_timestamps(TAIL7), None)
check("不到 6 个字不判", tb.even_timestamps(WHOLE[:5]), None)
check("正好 6 个字照判", tb.even_timestamps(WHOLE[:6]), "whole")
check("空白边界项不算字", tb.even_timestamps(WHOLE[:2] + [{"text": " ", "offset_sec": 0.5, "duration_sec": 0.9}] + WHOLE[2:5]),
      None)
check("没有时间戳 → None", tb.even_timestamps([]), None)
check("英文：真的词级时间戳在 0.08 秒网格上 → None", tb.even_timestamps(EN_REAL), None)
check("英文：每词一样长 → whole", tb.even_timestamps(EN_EVEN), "whole")
check("英文：整数倍单位、两个网格都不在 → whole", tb.even_timestamps(EN_UNITS), "whole")

# ---------------- B. run() 写 manifest、打提醒 ----------------
print("B. run() → audio-manifest.json 的 even_timestamps + ⚠ 提醒")
SAMPLES = {"hook1": NORMAL, "mem6": WHOLE, "recap2": LATE, "pat2": TAIL7, "empty": []}


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
          [("hook1", None), ("mem6", "whole"), ("recap2", "tail"), ("pat2", None), ("empty", None)])
    warn = [ln for ln in err.getvalue().splitlines() if "⚠" in ln]
    check("只为被标出来的段各打一行 ⚠", [next(s for s in ("mem6", "recap2") if s in ln) for ln in warn[:2]], ["mem6", "recap2"])
    check("⚠ 行写明整段 / 段尾", ("整段" in warn[0], "25/28" in warn[1]), (True, True))
    check("⚠ 行建议改写后重配", all("改写" in ln and "重配" in ln for ln in warn[:2]), True)
    check("末尾汇总一行", any("2 段" in ln for ln in warn[2:]), True)

if fails:
    print(f"\n✗ {len(fails)} 项失败：")
    for x in fails:
        print("  " + x)
    sys.exit(1)
print("\n全部通过")
