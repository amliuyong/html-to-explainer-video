#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""qc_check 实测帧数的回归测试（零依赖，不跑真的 ffmpeg：python tests/test_qc_frame_count.py）。

守的错（lessons #149）：qc 先用流复制（`-c copy -f null -`）数视频流的帧，从 `-progress pipe:1` 与 stats 行里读
`frame=`。有的 ffmpeg 构建（Ubuntu 24.04 的 6.1.1）流复制时根本不数帧，两条通道都没有 `frame=`，帧数校验就被跳过，
QC 里只剩一句 WARN。修法：流复制没给帧数时再解码一遍（解码一定数帧）。

这里把 subprocess.run 换成假的：带 `-c copy` 的那次按场景给或不给 `frame=`，解码那次给真帧数。
失败即以非零码退出。
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import types

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


def fake_ffmpeg(copy_out, decode_out):
    """copy_out / decode_out：(stdout, stderr)。记下每次调用是不是流复制。"""
    calls = []

    def run(cmd, **kw):
        copy = "-c" in cmd and cmd[cmd.index("-c") + 1] == "copy"
        calls.append("copy" if copy else "decode")
        out, err = copy_out if copy else decode_out
        return types.SimpleNamespace(returncode=0, stdout=out, stderr=err)
    return run, calls


PROGRESS_END = "fps=0.00\nprogress=continue\nprogress=end\n"
STATS_NO_FRAME = "size=N/A time=00:01:12.36 bitrate=N/A speed=2.33e+03x\n"

print("count_video_frames")
real_run = subprocess.run
try:
    run, calls = fake_ffmpeg((PROGRESS_END, STATS_NO_FRAME), ("frame=2174\nprogress=end\n", "frame= 2174 fps=500\n"))
    qc.subprocess.run = run
    check("流复制没给帧数 → 解码一遍再数", qc.count_video_frames("ffmpeg", "x.mp4"), 2174)
    check("先流复制、后解码", calls, ["copy", "decode"])

    run, calls = fake_ffmpeg(("frame=900\nprogress=end\n", ""), ("frame=1\n", ""))
    qc.subprocess.run = run
    check("流复制给了帧数 → 不再解码", qc.count_video_frames("ffmpeg", "x.mp4"), 900)
    check("只跑一遍", calls, ["copy"])

    run, calls = fake_ffmpeg(("", "frame=  450 fps=0.0 q=-1.0 size=N/A\n"), ("", ""))
    qc.subprocess.run = run
    check("stats 行里的 frame= 也认", qc.count_video_frames("ffmpeg", "x.mp4"), 450)

    run, calls = fake_ffmpeg((PROGRESS_END, STATS_NO_FRAME), (PROGRESS_END, STATS_NO_FRAME))
    qc.subprocess.run = run
    check("两遍都没给帧数 → None（QC 打 WARN，不当作 0 帧）", qc.count_video_frames("ffmpeg", "x.mp4"), None)
finally:
    qc.subprocess.run = real_run

if fails:
    print(f"\n失败 {len(fails)} 项：\n")
    for f in fails:
        print("  ✗ " + f)
    sys.exit(1)
print("\n全部通过")
