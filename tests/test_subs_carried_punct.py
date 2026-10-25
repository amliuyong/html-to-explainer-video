#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""英文块首带着前一句标点时块起点的回归测试（python tests/test_subs_carried_punct.py）。

守的错（lessons #158）：阿里的英文词级时间戳把前一句的标点并进下一个词，给成一项（「. Today」「. Colvenna」「, and」），
这一项的开头离开口还有一截静音。`subs.chunk_starts` 拿这一项的开头当块起点，字幕和挂在这一块上的 `B()` 入场一起提前：
一部英文宣传片（阿里 Andy_v3.1，+0%）里这 3 块比开口早 0.24 / 0.33 / 0.35 s（按 #144 的口径：停顿之后第一次到峰值
−20 dB 算开口；普通块早 0.12 s 左右）。只看时间戳分不出来 —— 同样带前置标点的「. Maintenance」「, MongoDB」
开头就在开口前 0.10 / 0.13 s。所以英文片读这一段的配音：块从开口往前退 0.125 s（普通块的提前量）起，只往后挪。

  A. onset_after_pause（零依赖）：静音盖住这一项的开头 / 开头之后不久才开始（前面是上一个词的弱尾音）→ 静音之后的开口；
     静音之前已经开口（后面那段静音是词里的闭塞）、静音太短、开始得太晚、没在这一项里结束、开口不在这一项里 → None。
  B. chunk_starts（零依赖）：那部片子 4 段的真实时间戳 + 真实配音的能量包络（5 ms 一格，取整到 1 dB，只抄了带前置标点的
     块首附近）：「. Today」「, and」「. Colvenna」「. The」挪到开口前 0.125 s；「: your」「, MongoDB」「, are」
     「. Maintenance」本来就不早，不动；不给音频照旧（和 2.2.5 逐块相同）；块首不带标点的段不解码音频；
     中文片的块首（全角标点，「，|过」「：|p」）一块不动、不读音频；#144 的「：|p」照旧由 #144 管。
  C. 真的 subs.py main()（要 ffmpeg）：合成一段 MP3（停顿的位置照抄那一段），lang en 时块起点挪到开口前 0.125 s，
     beats.js 的 B() 跟着变；lang zh、插值的段（even_timestamps）、读不了音频时一块不动，读不了时提醒。

缺 ffmpeg 时跳过 C 段（打印 SKIP）；设了 HX_NO_SKIP=1（CI）时不许跳过。失败即以非零码退出。
"""
from __future__ import annotations

import importlib.util
import io
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
from array import array
from contextlib import redirect_stderr, redirect_stdout

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NO_SKIP = os.environ.get("HX_NO_SKIP") == "1"


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, rel))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


subs = _load("subs", "scripts/subs.py")
fails: list[str] = []


def check(label, got, want):
    if got != want:
        fails.append(f"{label}\n    得到: {got!r}\n    应为: {want!r}")
    else:
        print(f"  OK  {label}")


def finish():
    if fails:
        print(f"\n失败 {len(fails)} 项：\n")
        for f in fails:
            print("  ✗ " + f)
        sys.exit(1)
    print("\n全部通过")
    sys.exit(0)


def need(name):
    """新代码里的函数；旧代码上没有就记一项失败，不让整个测试崩掉（反向验证要数得出失败项）。"""
    fn = getattr(subs, name, None)
    if fn is None:
        fails.append(f"subs 没有 {name}()")
    return fn


def bounds(rows):
    return [{"text": t, "offset_sec": o, "duration_sec": d} for t, o, d in rows]


def starts(text, rows, lead_cut, **kw):
    chunks = subs.split_chunks(text)
    try:
        got = subs.chunk_starts("".join(chunks), chunks, bounds(rows), lead_cut, **kw)
    except TypeError as e:                  # 旧的 chunk_starts 不认 audio=
        return f"TypeError: {e}"
    return [round(x, 3) for x in got]


# ---------------- 真实样本：一部英文宣传片（阿里 Andy_v3.1，+0%）----------------
HOOK_TEXT = ("Every database change and every app event |should be queryable in minutes. "
             "|Today it takes Apache Kafka, a warehouse, |and a lake to get there.")
HOOK = [["Every", 0.16, 0.24], ["database", 0.4, 0.4], ["change", 0.88, 0.32], ["and", 1.2, 0.4], ["every", 1.6, 0.32],
        ["app", 1.92, 0.24], ["event", 2.16, 0.4], ["should", 2.8, 0.16], ["be", 2.96, 0.16], ["queryable", 3.12, 0.56],
        ["in", 3.76, 0.16], ["minutes", 3.92, 0.32], [". Today", 4.64, 0.48], ["it", 5.12, 0.16], ["takes", 5.28, 0.24],
        ["Apache", 5.52, 0.24], ["Kafka", 6.0, 0.32], [", a", 6.72, 0.08], ["warehouse", 6.8, 0.32], [", and", 7.36, 0.4],
        ["a", 7.76, 0.16], ["lake", 7.92, 0.24], ["to", 8.16, 0.16], ["get", 8.32, 0.16], ["there.", 8.48, 0.24]]
HOOK_LC = 0.126
CLOSE_TEXT = "One command installs a complete environment. |Colvenna Lake: |your streams and your analytics, one lake."
CLOSE = [["One", 0.16, 0.16], ["command", 0.32, 0.32], ["installs", 0.72, 0.4], ["a", 1.12, 0.24], ["complete", 1.36, 0.32],
         ["environment", 1.76, 0.64], [". Colvenna", 2.8, 0.72], ["Lake", 3.52, 0.24], [": your", 4.16, 0.16],
         ["streams", 4.32, 0.4], ["and", 4.72, 0.24], ["your", 4.96, 0.24], ["analytics", 5.2, 0.32], [", one", 5.92, 0.24],
         ["lake.", 6.16, 0.24]]
CLOSE_LC = 0.124
INGEST_TEXT = ("Declare a source: MySQL, PostgreSQL, |MongoDB or Kafka. "
               "|The operator creates the Apache Paimon tables |and runs the Apache Flink jobs for you.")
INGEST = [["Declare", 0.08, 0.4], ["a", 0.48, 0.16], ["source", 0.64, 0.24], [": My", 1.36, 0.24], ["SQL", 1.6, 0.32],
          [", PostgreSQL", 2.32, 0.8], [", MongoDB", 3.52, 0.72], ["or", 4.48, 0.48], ["Kafka", 4.96, 0.48],
          [". The", 5.92, 0.24], ["operator", 6.16, 0.32], ["creates", 6.72, 0.32], ["the", 7.12, 0.16],
          ["Apache", 7.28, 0.48], ["Paimon", 7.84, 0.4], ["tables", 8.24, 0.32], ["and", 8.64, 0.32], ["runs", 8.96, 0.24],
          ["the", 9.2, 0.16], ["Apache", 9.36, 0.4], ["Flink", 9.92, 0.24], ["jobs", 10.16, 0.32], ["for", 10.48, 0.24],
          ["you.", 10.72, 0.32]]
INGEST_LC = 0.125
LAYERS_TEXT = ("Layered tables, from ODS to DWD to DWS, |are SQL you declare. "
               "|Maintenance runs as tasks |from the CLI, the console or your scheduler.")
LAYERS = [["Layered", 0.16, 0.32], ["tables", 0.48, 0.32], [", from", 1.28, 0.16], ["ODS", 1.44, 0.48], ["to", 2.08, 0.24],
          ["D", 2.32, 0.32], ["W", 2.64, 0.24], ["D", 2.96, 0.24], ["to", 3.36, 0.24], ["D", 3.6, 0.16], ["W", 3.76, 0.4],
          ["S", 4.16, 0.24], [", are", 4.8, 0.16], ["SQL", 4.96, 0.4], ["you", 5.36, 0.24], ["declare", 5.6, 0.32],
          [". Maintenance", 6.64, 0.48], ["runs", 7.12, 0.32], ["as", 7.44, 0.24], ["tasks", 7.68, 0.4], ["from", 8.16, 0.24],
          ["the", 8.4, 0.16], ["CLI", 8.56, 0.4], [", the", 9.36, 0.16], ["console", 9.52, 0.4], ["or", 10.0, 0.4],
          ["your", 10.4, 0.16], ["scheduler.", 10.56, 0.32]]
LAYERS_LC = 0.124
INTRO_TEXT = "Colvenna Lake turns that pipeline |into a few declarations on Kubernetes."
INTRO = [["Colvenna", 0.08, 0.48], ["Lake", 0.56, 0.32], ["turns", 0.88, 0.4], ["that", 1.28, 0.24], ["pipeline", 1.52, 0.48],
         ["into", 2.0, 0.32], ["a", 2.4, 0.16], ["few", 2.56, 0.16], ["declarations", 2.72, 0.56], ["on", 3.44, 0.24],
         ["Kubernetes.", 3.68, 0.48]]
INTRO_LC = 0.122

# 真实配音的能量包络（subs.speech_envelope 的口径：5 ms 一格、20 ms 窗、相对段内峰值的 dB，取整到 1 dB）。
# (段, 块首那一项, 起始格号, 每格的 dB)：只抄了带前置标点的块首那一项前 0.35 s 到它结尾后 0.05 s
ENV_EXCERPTS = [
    ('hook', '. Today', 833,
     "-25 -26 -27 -27 -27 -27 -27 -27 -26 -27 -28 -29 -33 -36 -39 -43 -46 -48 -50 -52 -53 -54 -59 -65 -66 -66 "
     "-66 -67 -67 -70 -69 -68 -69 -69 -69 -69 -70 -70 -70 -71 -70 -70 -71 -73 -71 -73 -73 -72 -72 -67 -66 -65 "
     "-64 -67 -68 -70 -72 -72 -73 -73 -73 -73 -73 -72 -71 -71 -70 -71 -71 -70 -69 -69 -69 -69 -71 -72 -72 -74 "
     "-73 -73 -73 -73 -74 -72 -71 -72 -71 -71 -72 -72 -73 -75 -75 -75 -73 -72 -66 -65 -56 -55 -55 -55 -60 -66 "
     "-68 -68 -68 -68 -68 -68 -41 -34 -30 -27 -24 -23 -24 -25 -20 -12 -6 -3 -2 -1 -1 -1 -1 -2 -4 -7 -9 -13 -14 "
     "-14 -15 -16 -16 -15 -13 -7 -4 -2 0 0 0 -1 -1 -2 -2 -2 -3 -3 -3 -3 -2 -2 -2 -2 -2 -2 -1 -1 -2 -2 -2 -2 -2 "
     "-2 -3 -2 -2 -2 -2 -2 -2 -2"),
    ('hook', ', and', 1377,
     "-5 -4 -4 -4 -3 -3 -3 -3 -4 -5 -5 -6 -6 -6 -7 -8 -10 -12 -12 -14 -14 -13 -13 -13 -12 -12 -12 -11 -10 -9 "
     "-7 -6 -5 -4 -5 -5 -5 -6 -5 -7 -7 -7 -8 -7 -8 -7 -8 -7 -7 -6 -6 -6 -6 -6 -6 -6 -6 -6 -6 -6 -7 -7 -9 -11 "
     "-14 -19 -21 -24 -24 -25 -24 -26 -26 -27 -27 -27 -26 -26 -26 -25 -25 -24 -23 -23 -22 -23 -23 -23 -25 -25 "
     "-27 -28 -29 -29 -30 -30 -32 -34 -39 -42 -46 -49 -52 -53 -56 -59 -62 -63 -68 -68 -71 -72 -72 -71 -71 -68 "
     "-68 -69 -68 -70 -71 -69 -69 -69 -70 -71 -76 -76 -76 -73 -72 -71 -69 -69 -67 -66 -64 -53 -32 -21 -13 -11 "
     "-9 -7 -7 -7 -7 -7 -7 -6 -6 -6 -6 -6 -5 -5 -6 -6 -6 -6"),
    ('close', '. Colvenna', 465,
     "-29 -33 -36 -36 -37 -38 -40 -41 -44 -46 -48 -51 -52 -57 -63 -65 -67 -68 -68 -69 -69 -69 -70 -69 -68 -67 "
     "-67 -67 -68 -71 -71 -73 -73 -72 -71 -72 -71 -71 -73 -72 -74 -74 -73 -72 -70 -71 -70 -70 -71 -71 -72 -72 "
     "-73 -73 -74 -73 -71 -71 -71 -70 -70 -70 -69 -70 -70 -70 -70 -68 -69 -70 -71 -75 -69 -69 -68 -68 -71 -71 "
     "-74 -74 -75 -74 -75 -73 -71 -71 -67 -68 -68 -69 -74 -75 -75 -74 -72 -71 -70 -69 -69 -69 -70 -71 -72 -72 "
     "-70 -70 -71 -70 -70 -70 -69 -69 -68 -68 -68 -69 -69 -67 -63 -62 -62 -62 -66 -67 -68 -68 -58 -52 -28 -24 "
     "-23 -23 -24 -28 -30 -30 -23 -16 -10 -8 -6 -4 -4 -2 -1 -2 -1 -1 -1 -1 -2 -2 -2 -4 -5 -5 -8 -8 -10 -12 -12 "
     "-15 -15 -17 -17 -18 -18 -16 -13 -12 -6 -4 -3 -1 -1 -1 0 0 0 0 0 -1 -1 -1 -1 -2 -3 -4 -5 -6 -6 -6 -6 -6 "
     "-6 -6 -7 -6 -5 -4 -3 -2 -2 -1 -1 -1 -1 -2 -3 -4 -5 -6 -6 -6 -6 -5 -5 -6 -5 -5 -6 -6 -5 -6"),
    ('close', ': your', 737,
     "-31 -32 -33 -34 -35 -36 -36 -37 -38 -39 -42 -44 -46 -47 -48 -49 -50 -53 -57 -58 -58 -59 -61 -64 -64 -66 "
     "-68 -69 -70 -71 -71 -71 -71 -71 -71 -72 -72 -72 -72 -74 -74 -75 -74 -73 -74 -72 -73 -73 -73 -76 -75 -74 "
     "-74 -74 -71 -71 -70 -69 -70 -68 -69 -67 -67 -68 -69 -71 -74 -73 -73 -75 -75 -75 -73 -73 -73 -71 -72 -72 "
     "-70 -71 -70 -71 -73 -71 -71 -71 -70 -69 -65 -60 -48 -30 -18 -14 -12 -10 -9 -7 -7 -5 -4 -5 -3 -2 -3 -2 -3 "
     "-4 -4 -7 -9 -12"),
    ('ingest', ', MongoDB', 609,
     "-7 -7 -7 -7 -7 -7 -8 -8 -8 -8 -8 -9 -9 -11 -11 -14 -16 -19 -22 -23 -25 -28 -30 -35 -38 -42 -45 -45 -46 "
     "-47 -49 -50 -52 -56 -58 -61 -61 -63 -65 -68 -69 -72 -72 -71 -72 -72 -71 -73 -72 -73 -73 -73 -74 -73 -74 "
     "-74 -73 -75 -74 -74 -75 -74 -75 -75 -74 -77 -75 -76 -77 -77 -77 -78 -78 -78 -78 -78 -76 -77 -77 -73 -71 "
     "-71 -71 -72 -75 -73 -69 -67 -64 -62 -59 -52 -45 -38 -25 -17 -15 -14 -13 -12 -11 -10 -10 -9 -9 -8 -7 -5 "
     "-4 -4 -4 -4 -4 -3 -3 -3 -3 -3 -3 -4 -4 -4 -4 -5 -5 -5 -6 -6 -7 -7 -8 -8 -8 -9 -8 -8 -8 -8 -8 -9 -10 -12 "
     "-13 -14 -15 -14 -12 -10 -8 -7 -6 -5 -5 -5 -5 -5 -5 -6 -6 -5 -5 -5 -5 -5 -4 -4 -5 -7 -10 -13 -13 -14 -15 "
     "-17 -19 -20 -23 -26 -29 -35 -39 -31 -27 -24 -19 -11 -7 -6 -5 -4 -4 -5 -5 -5 -5 -5 -5 -6 -6 -6 -6 -6 -6 "
     "-6 -6 -6 -5 -5 -5 -5 -6 -8 -9 -12 -13 -13 -15 -16 -18 -18 -19 -20 -21 -23"),
    ('ingest', '. The', 1089,
     "-64 -65 -67 -68 -69 -70 -69 -70 -71 -70 -71 -71 -71 -72 -72 -72 -73 -75 -74 -75 -74 -73 -72 -72 -72 -73 "
     "-73 -73 -76 -76 -75 -74 -73 -73 -73 -73 -73 -74 -73 -74 -74 -74 -73 -72 -72 -72 -75 -76 -75 -73 -72 -72 "
     "-71 -72 -72 -71 -71 -72 -71 -71 -72 -71 -73 -75 -74 -74 -73 -72 -72 -72 -72 -74 -75 -74 -76 -77 -75 -75 "
     "-75 -76 -76 -75 -74 -73 -72 -72 -74 -75 -78 -78 -78 -78 -79 -78 -77 -74 -67 -67 -67 -67 -73 -71 -68 -62 "
     "-53 -42 -31 -23 -21 -20 -19 -18 -12 -11 -9 -7 -7 -6 -6 -6 -6 -6 -5 -5 -5 -5 -5 -5"),
    ('layers', ', are', 865,
     "-27 -27 -27 -27 -26 -26 -25 -25 -25 -26 -26 -26 -27 -27 -27 -25 -25 -24 -24 -24 -24 -24 -24 -27 -29 -31 "
     "-34 -35 -40 -43 -47 -50 -52 -55 -56 -58 -59 -60 -61 -66 -67 -69 -70 -71 -71 -73 -75 -75 -75 -74 -72 -70 "
     "-70 -70 -70 -73 -73 -73 -73 -73 -73 -72 -73 -72 -71 -70 -70 -69 -67 -68 -68 -69 -72 -72 -74 -74 -75 -73 "
     "-71 -68 -66 -65 -65 -63 -62 -23 -16 -9 -8 -5 -4 -4 -3 -3 -4 -3 -4 -5 -5 -6 -8 -9 -13 -15 -19 -21 -19 -18 "
     "-17 -17 -17 -17"),
    ('layers', '. Maintenance', 1233,
     "-71 -70 -71 -71 -71 -71 -72 -72 -71 -70 -69 -69 -69 -70 -71 -73 -75 -75 -73 -71 -71 -71 -73 -77 -76 -74 "
     "-71 -69 -68 -67 -68 -69 -73 -74 -74 -73 -73 -73 -72 -74 -74 -74 -75 -74 -75 -76 -73 -72 -71 -71 -72 -73 "
     "-74 -73 -72 -72 -72 -71 -72 -73 -72 -72 -71 -71 -70 -72 -73 -72 -72 -72 -72 -73 -75 -74 -69 -68 -66 -66 "
     "-66 -67 -68 -65 -63 -58 -57 -56 -54 -48 -32 -20 -13 -10 -9 -8 -8 -7 -8 -7 -6 -5 -5 -4 -4 -3 -2 -2 -1 -1 "
     "-2 -2 -3 -3 -4 -4 -4 -4 -4 -5 -5 -5 -5 -6 -6 -7 -9 -11 -14 -18 -20 -24 -26 -33 -42 -44 -45 -46 -48 -50 "
     "-37 -25 -25 -18 -15 -13 -11 -9 -9 -7 -6 -6 -5 -5 -5 -5 -5 -5 -5 -5 -6 -5 -5 -5 -4 -4 -4 -4 -6 -7 -10 -13 "
     "-19 -24 -24 -24 -24 -23"),
]

ENV_LEN = {"hook": 1755, "close": 1807, "ingest": 2203, "layers": 2222}   # 各段包络的格数


def full_envelope(sid):
    """一段的包络：抄下来的几截照原样，别处填 −10 dB（在念）—— 别处不会被读到，读到了就说明读错了地方。"""
    env = [-10.0] * ENV_LEN.get(sid, 2000)
    for s, _, i0, vals in ENV_EXCERPTS:
        if s == sid:
            env[i0:i0 + len(vals.split())] = [float(v) for v in vals.split()]
    return env


# ---------------- A. onset_after_pause ----------------
print("A. onset_after_pause：这一项里停顿之后的开口")
oap = need("onset_after_pause")
if oap:
    H = 0.005

    def env_of(*runs):
        """[(dB, 秒), …] → 包络"""
        out = []
        for db, sec in runs:
            out += [float(db)] * int(round(sec / H))
        return out

    E1 = env_of((-8, 1.0), (-70, 0.3), (-30, 0.02), (-6, 0.5))          # 静音 1.0–1.3，开口（−20 dB）1.32
    check("静音盖住这一项的开头 → 静音之后第一次到 −20 dB", oap(E1, 1.1, 1.6), 1.32)
    check("静音从开头之后不久才开始，前面是 < −20 dB 的弱尾音（「, and」前面的「s」）",
          oap(env_of((-8, 1.0), (-25, 0.15), (-70, 0.2), (-6, 0.5)), 1.0, 1.6), 1.35)
    check("静音之前已经到 −20 dB：这一项开头就在念，后面那段静音是词里的闭塞 → None",
          oap(env_of((-8, 1.0), (-6, 0.05), (-70, 0.1), (-6, 0.5)), 1.0, 1.6), None)
    check("静音不到 80 ms → None（这一项从静音里开始，静音从它前面 10 ms 起、共 75 ms）",
          oap(env_of((-8, 1.0), (-70, 0.075), (-6, 0.5)), 1.01, 1.5), None)
    check("静音在开头 0.25 s 之后才开始 → None", oap(env_of((-30, 1.3), (-70, 0.2), (-6, 0.5)), 1.0, 1.9), None)
    check("静音没在这一项里结束 → None", oap(E1, 1.1, 1.25), None)
    check("开口不在这一项里 → None", oap(E1, 1.1, 1.31), None)
    check("包络比这一项短（音频比时间戳短）→ None，不越界", oap(E1[:250], 1.1, 1.6), None)

# ---------------- B. chunk_starts ----------------
print("B. chunk_starts：真实时间戳 + 真实配音的能量包络")
calls = []
real_env = getattr(subs, "speech_envelope", None)


def fake_envelope(path):
    calls.append(os.path.basename(path))
    return full_envelope(os.path.basename(path).split(".")[0])


subs.speech_envelope = fake_envelope
try:
    check("hook「. Today」4.514 → 4.63、「, and」7.234 → 7.46（开口 4.755 / 7.585 前 0.125 s）；「should」不带标点，不动",
          starts(HOOK_TEXT, HOOK, HOOK_LC, audio="hook.mp3"), [0.0, 2.674, 4.63, 7.46])
    check("close「. Colvenna」2.676 → 2.885（开口 3.01）；「: your」本来只早 0.11 s，不动",
          starts(CLOSE_TEXT, CLOSE, CLOSE_LC, audio="close.mp3"), [0.0, 2.885, 4.036])
    check("ingest「. The」5.795 → 5.865；「, MongoDB」本来就早 0.125 s，不动",
          starts(INGEST_TEXT, INGEST, INGEST_LC, audio="ingest.mp3"), [0.0, 3.395, 5.865, 8.515])
    check("layers「, are」「. Maintenance」本来只早 0.08 / 0.10 s，不动",
          starts(LAYERS_TEXT, LAYERS, LAYERS_LC, audio="layers.mp3"), [0.0, 4.676, 6.516, 8.036])
    check("每段只解码一次", calls, ["hook.mp3", "close.mp3", "ingest.mp3", "layers.mp3"])
    calls.clear()
    check("intro 的块首不带标点：不解码音频", (starts(INTRO_TEXT, INTRO, INTRO_LC, audio="intro.mp3"), calls),
          ([0.0, 1.878], []))
    check("不给音频：和 2.2.5 逐块相同", [starts(HOOK_TEXT, HOOK, HOOK_LC), starts(CLOSE_TEXT, CLOSE, CLOSE_LC)],
          [[0.0, 2.674, 4.514, 7.234], [0.0, 2.676, 4.036]])
    check("读不了音频（speech_envelope 返回 None）：照旧从时间戳起",
          (setattr(subs, "speech_envelope", lambda p: None), starts(HOOK_TEXT, HOOK, HOOK_LC, audio="hook.mp3"))[1],
          [0.0, 2.674, 4.514, 7.234])

    # 中文片的块首带的是全角标点（「，|过」「：|p」）：一块不动、一次音频都不读 —— 哪怕包络处处是静音
    subs.speech_envelope = lambda p: calls.append(p) or [-70.0] * 4000
    DF4_TEXT = "它也能给每行数据套函数，|过滤、分组汇总，|按键连接两张表：|pandas\xa0里叫合并\xa0merge。"
    DF4 = [["它", 0.063, 0.375], ["也", 0.438, 0.062], ["能", 0.5, 0.125], ["给", 0.625, 0.063], ["每", 0.688, 0.187],
           ["行", 0.875, 0.125], ["数", 1.0, 0.188], ["据", 1.188, 0.187], ["套", 1.438, 0.187], ["函", 1.625, 0.188],
           ["数", 1.813, 0.25], ["，|过", 2.313, 0.187], ["滤", 2.5, 0.188], ["、分", 2.875, 0.188], ["组", 3.063, 0.187],
           ["汇", 3.313, 0.312], ["总", 3.625, 0.188], ["，|按", 4.125, 0.188], ["键", 4.313, 0.125], ["连", 4.438, 0.187],
           ["接", 4.625, 0.188], ["两", 4.813, 0.187], ["张", 5.0, 0.25], ["表", 5.25, 0.188], ["：|p", 5.438, 0.375],
           ["andas", 5.813, 0.375], ["里", 6.25, 0.125], ["叫", 6.375, 0.25], ["合", 6.625, 0.25], ["并", 6.875, 0.188],
           ["merge。", 7.25, 0.25]]
    calls.clear()
    check("中文（DDIA 03-4 df4）：块首「，|过」「，|按」不动，「：|p」照旧由 #144 管（5.5），不读音频",
          (starts(DF4_TEXT, DF4, 0.313, audio="df4.mp3"), calls), ([0.0, 2.0, 3.812, 5.5], []))
    # 英文片里 #144 形状的项（半角标点 + 一个字母 + 小写后半截）：#144 先管，不读音频
    calls.clear()
    check("「: p」+「andas」（#144 的形状）照旧由 #144 管，不读音频",
          (starts("Two tables: |pandas calls it merge.",
                  [["Two", 0.1, 0.3], ["tables", 0.4, 0.4], [": p", 0.8, 0.375], ["andas", 1.175, 0.3],
                   ["calls", 1.5, 0.3], ["it", 1.8, 0.1], ["merge.", 1.9, 0.4]], 0.1, audio="x.mp3"), calls),
          ([0.0, 1.075], []))
finally:
    subs.speech_envelope = real_env

# ---------------- C. 真的 subs.py main() ----------------
print("C. subs.py main()：合成的配音、真的解码")
FF = shutil.which("ffmpeg")
if FF is None:
    if NO_SKIP:
        fails.append("缺 PATH 上的 ffmpeg，而 HX_NO_SKIP=1 不许跳过 C 段")
    else:
        print("  SKIP 缺 ffmpeg")
    finish()

SR = 16000


def synth_mp3(path, quiet, length):
    """噪声「语音」（−6 dBFS 上下）+ 几段静音 [(起, 止), …]；静音前 0.15 s 是 −30 dB 的弱尾音。按 tts_build 的命令编码。"""
    rnd = random.Random(7)
    x = array("h", bytes(2 * int(length * SR)))
    for i in range(len(x)):
        t = i / SR
        if any(a <= t < b for a, b in quiet):
            continue
        amp = 0.03 if any(a - 0.15 <= t < a for a, _ in quiet) else 0.5
        x[i] = int(32767 * amp * (rnd.random() * 2 - 1))
    p = subprocess.run([FF, "-hide_banner", "-loglevel", "error", "-y", "-f", "s16le", "-ac", "1", "-ar", str(SR),
                        "-i", "-", "-c:a", "libmp3lame", "-b:a", "128k", path], input=x.tobytes(), capture_output=True)
    if p.returncode:
        raise RuntimeError(p.stderr.decode(errors="replace")[-300:])


def run_main(proj):
    argv, err = sys.argv, io.StringIO()
    sys.argv = ["subs.py", "--project", proj]
    try:
        with redirect_stdout(io.StringIO()), redirect_stderr(err):
            subs.main()
    finally:
        sys.argv = argv
    out = json.load(open(os.path.join(proj, "subs.json"), encoding="utf-8"))
    js = open(os.path.join(proj, "frames", "hook.beats.js"), encoding="utf-8").read()
    beats = json.loads(re.search(r"window\.__BEATS__\s*=\s*(\[.*?\])\s*;", js, re.S).group(1))
    return [b["local_start_sec"] for b in out["segments"][0]["blocks"]], [b["start"] for b in beats], err.getvalue()


def project(d, lang, audio="mp3", even=None):
    os.makedirs(os.path.join(d, "audio"), exist_ok=True)
    json.dump({"slug": "t", "fps": 30, "lang": lang, "order": ["hook"]}, open(os.path.join(d, "project.json"), "w"))
    json.dump([{"id": "hook", "text": HOOK_TEXT}], open(os.path.join(d, "narration.json"), "w"))
    json.dump({"trimmed": True, "items": [{"id": "hook", "duration_sec": 8.72, "lead_cut_sec": HOOK_LC,
                                           "container_duration_sec": 8.83, "speech_end_sec": 8.673,
                                           "even_timestamps": even, "boundaries": bounds(HOOK)}]},
              open(os.path.join(d, "audio-manifest.json"), "w"))
    json.dump({"hook": {"start_sec": 0.0, "duration_sec": 8.83}, "_total": {"gap_sec": 0.35}},
              open(os.path.join(d, "layout.json"), "w"))
    mp3 = os.path.join(d, "audio", "hook.mp3")
    if audio == "mp3":
        # 停顿的位置照抄 hook 那一段：「minutes」后静音 4.25–4.72（「. Today」从 4.514 起），
        # 「warehouse」后静音 7.385–7.575（「, and」从 7.234 起，静音前是弱尾音）
        synth_mp3(mp3, [(4.25, 4.72), (7.385, 7.575)], 8.83)
    elif audio == "junk":
        open(mp3, "w").write("not audio")


OLD = [0.0, 2.674, 4.514, 7.234]
with tempfile.TemporaryDirectory() as tmp:
    d = os.path.join(tmp, "en")
    project(d, "en")
    got, beats, err = run_main(d)
    # 合成的开口是从静音直接跳到 −6 dB：20 ms 窗在开口前 19 ms 就到 −20 dB → 4.70 / 7.555，再退 0.125；MP3 编码再挪几毫秒
    near = lambda a, b: abs(a - b) <= 0.015
    check("lang en：「. Today」挪到 4.70 − 0.125 = 4.575（±15 ms），「, and」挪到 7.555 − 0.125 = 7.43",
          (got[:2], near(got[2], 4.575), near(got[3], 7.43)), (OLD[:2], True, True))
    check("beats.js 的 B() 跟着变", beats, got)
    check("没有提醒", err.strip(), "")

    d = os.path.join(tmp, "zh")
    project(d, "zh")
    got, _, err = run_main(d)
    check("lang zh：一块不动（中文片不读音频）", (got, err.strip()), (OLD, ""))

    d = os.path.join(tmp, "even")
    project(d, "en", even="whole")
    got, _, err = run_main(d)
    check("插值的段（even_timestamps: whole）：一块不动、不读音频", (got, err.strip()), (OLD, ""))

    d = os.path.join(tmp, "junk")
    project(d, "en", audio="junk")
    got, _, err = run_main(d)
    check("读不了音频：一块不动", got, OLD)
    check("读不了音频：提醒「读不了 …hook.mp3」", "读不了" in err and "hook.mp3" in err, True)

    d = os.path.join(tmp, "missing")
    project(d, "en", audio=None)
    got, _, err = run_main(d)
    check("音频文件不在：一块不动、提醒", (got, "读不了" in err), (OLD, True))

    d = os.path.join(tmp, "zhjunk")
    project(d, "zh", audio="junk")
    got, _, err = run_main(d)
    check("中文片音频是坏的：不读，所以也不提醒", (got, err.strip()), (OLD, ""))

finish()
