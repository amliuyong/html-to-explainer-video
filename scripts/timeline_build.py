#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
timeline_build.py —— 全局时间轴：layout.json + 拼接音轨。

做两件事：
  ① 按 project.json 的 order 把各段容器时长 + 段间 gap 排成全局时间轴
     → layout.json（每段 start_sec / duration_sec，渲染器与字幕都读它）
  ② 把逐段 MP3 拼成一条 narration-full.mp3，每段**正好占住它在 layout 里的槽位**：
     解码 → 重采样 → apad + atrim 补齐/截到 round(duration_sec × sr) 个采样，
     段间静音也按采样数补到下一段的 round(start_sec × sr)。
     ★ 不能直接 concat 解码后的段：解码出来的采样比容器时长平均短约 36 ms（MP3 的
       priming / padding），50 段累计近 2 s —— 音轨越往后越早于画面和字幕（lessons #136）。
     concat 之后先切成每帧正好 1152 个采样再交给 libmp3lame（lessons #141，见 concat_cmd）。
  拼完解码一遍量总长，和 layout 总长差超过 1 帧就非零退出。

用法：
  python timeline_build.py --project . [--gap 0.35]
产出：
  layout.json + audio/narration-full.mp3
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys


def ffmpeg_exe() -> str:
    from shutil import which
    p = which("ffmpeg")
    if p:
        return p
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


SR = 44100  # narration-full 的采样率
MP3_FRAME = 1152  # libmp3lame 在 32 / 44.1 / 48 kHz（MPEG-1 Layer III）每帧的采样数


def slot_samples(layout: dict, order: list, sr: int = SR) -> list:
    """每段在音轨里的 (段采样数, 其后的静音采样数)。

    第 i 段从 round(start_sec × sr) 开始，占 round(duration_sec × sr) 个采样，静音补到下一段的起点
    —— 每段起点都落在 layout 的采样位置上，误差不随段数累积。两段因舍入相差 1 个采样时，
    让上一段让出这个采样，不重叠。
    """
    starts = [int(round(layout[nid]["start_sec"] * sr)) for nid in order]
    slots = []
    for i, nid in enumerate(order):
        n = int(round(layout[nid]["duration_sec"] * sr))
        if i + 1 < len(order):
            n = min(n, starts[i + 1] - starts[i])
            slots.append((n, starts[i + 1] - starts[i] - n))
        else:
            slots.append((n, 0))
    return slots


def concat_cmd(ff: str, parts: list, slots: list, out: str) -> list:
    """拼 narration-full.mp3 的 ffmpeg 命令：每段补齐/截到槽位的采样数，静音按采样数补，再 concat。

    concat 之后用 asetnsamples 切成每帧正好 MP3_FRAME 个采样，再交给 libmp3lame。p=0：最后一帧不补静音，
    采样一个不多、一个不少。
    ★ 不切的话，ffmpeg（6.1 起）替编码器按 1152 切帧：一帧的前一部分拷走，剩下的尾巴不拷贝，只挪数据指针，
      linesize 跟着减小。最后一帧要是这样一截尾巴（末段补的静音帧），缓冲会比 libmp3lame 要的
      FFALIGN(nb_samples, 8) 个采样少 4 个，它就报 `inadequate AVFrame plane padding` 退出（lessons #141）。
      只有 av_cpu_max_align() 是 16 字节的机器（aarch64 / Apple Silicon、只有 SSE 的 x86）会碰上；
      碰不碰上取决于总采样数和末段补了多少静音，一集约 1/64。
    """
    inputs = []
    for p in parts:
        inputs += ["-i", p]
    filters, labels = [], []
    for i, (n_seg, n_gap) in enumerate(slots):
        filters.append(f"[{i}:a]aresample={SR},apad=whole_len={n_seg},atrim=end_sample={n_seg}[a{i}]")
        labels.append(f"[a{i}]")
        if n_gap > 0:
            filters.append(f"anullsrc=channel_layout=mono:sample_rate={SR},atrim=end_sample={n_gap}[g{i}]")
            labels.append(f"[g{i}]")
    fc = (";".join(filters) + ";" + "".join(labels)
          + f"concat=n={len(labels)}:v=0:a=1,asetnsamples=n={MP3_FRAME}:p=0[out]")
    return [ff, "-hide_banner", "-loglevel", "error", "-y",
            *inputs, "-filter_complex", fc, "-map", "[out]",
            "-c:a", "libmp3lame", "-b:a", "128k", out]


def decoded_seconds(path: str, ff: str, sr: int = SR) -> float:
    """解码后的真实时长（数采样，不信容器的 Duration:）。"""
    p = subprocess.Popen([ff, "-v", "error", "-i", path, "-f", "s16le", "-ac", "1", "-ar", str(sr), "-"],
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    n = 0
    for chunk in iter(lambda: p.stdout.read(1 << 20), b""):
        n += len(chunk)
    p.wait()
    return n / 2 / sr


def length_problem(audio_sec: float, video_sec: float, fps: float):
    """音轨与 layout 总长差超过 1 帧时返回说明，否则 None。"""
    drift = audio_sec - video_sec
    if abs(drift) > 1.0 / fps:
        return (f"音轨解码后 {audio_sec:.3f}s，layout 总长 {video_sec:.3f}s，差 {drift * 1000:+.0f} ms"
                f"（超过 1 帧 {1000.0 / fps:.0f} ms）")
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=".")
    ap.add_argument("--gap", type=float, default=-1, help="段间静音秒（默认取 project.json 的 gap）")
    args = ap.parse_args()

    proj = os.path.abspath(args.project)
    pj = json.load(open(os.path.join(proj, "project.json"), encoding="utf-8"))
    order = pj.get("order") or []
    gap = args.gap if args.gap >= 0 else float(pj.get("gap", 0.35))

    if not order:
        # 不拦这一下的话，下游 ffmpeg 会报 `Failed to set value ';concat=n=0:...'`
        # —— 看不出根因在哪。直接说清楚该改哪个文件的哪个字段。
        print("✗ project.json 的 order 是空的 —— 它是场景顺序表，先填上再跑。\n"
              "  形如：\"order\": [\"intro\", \"point1\", \"ending\"]（= narration.json 里 id 的顺序）",
              file=sys.stderr)
        return 1

    man_path = os.path.join(proj, "audio-manifest.json")
    if not os.path.exists(man_path):
        print("✗ 先跑 tts_build.py（缺 audio-manifest.json）", file=sys.stderr)
        return 1
    man = json.load(open(man_path, encoding="utf-8"))
    by_id = {}
    for it in man.get("items", []):
        by_id[it.get("id") or os.path.splitext(os.path.basename(it["path"]))[0]] = it

    ff = ffmpeg_exe()
    layout, cursor, parts = {}, 0.0, []
    for nid in order:
        it = by_id.get(nid)
        if not it:
            print(f"✗ manifest 里没有 {nid}", file=sys.stderr)
            return 1
        dur = round(float(it.get("container_duration_sec") or it["duration_sec"]), 3)
        layout[nid] = {"start_sec": round(cursor, 3), "duration_sec": dur}
        parts.append(os.path.join(proj, "audio", f"{nid}.mp3"))
        cursor += dur + gap

    total = cursor - (gap if order else 0.0)

    # ---- 拼音轨：每段补齐/截到槽位的采样数，静音按采样数补，再 concat ----
    out = os.path.join(proj, "audio", "narration-full.mp3")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    cmd = concat_cmd(ff, parts, slot_samples(layout, order), out)
    r = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    if r.returncode != 0:
        print("✗ ffmpeg 拼接失败:\n" + (r.stderr or "")[-800:], file=sys.stderr)
        return 1

    audio_total = decoded_seconds(out, ff)
    layout["_total"] = {"audio_duration_sec": round(audio_total, 3),
                        "video_duration_sec": round(total, 3),
                        "gap_sec": gap, "fps": pj.get("fps", 30),
                        "total_frames": int(round(total * pj.get("fps", 30)))}
    lp = os.path.join(proj, "layout.json")
    json.dump(layout, open(lp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    print(f"[timeline] {len(order)} 段 · gap {gap}s · 视频 {total:.2f}s / "
          f"{int(round(total * pj.get('fps', 30)))} 帧 · 音轨 {audio_total:.2f}s → {lp}")
    for nid in order:
        print(f"  {nid:<12} start {layout[nid]['start_sec']:>7.2f}s  dur {layout[nid]['duration_sec']:>6.3f}s")
    problem = length_problem(audio_total, total, pj.get("fps", 30))
    if problem:
        print(f"✗ {problem} —— narration-full.mp3 和画面对不上，别拿它渲染（lessons #136）", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
