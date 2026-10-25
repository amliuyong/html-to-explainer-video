#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""场景停留（project.json 的 hold）的回归测试（python tests/test_scene_hold.py）。

守的错（lessons #157）：末场没有 gap，语音一停片子就完，片尾卡（品牌锁定、商标署名）来不及读。流水线里没有
「语音之后再停几秒」的设置，项目只好给 MP3 补静音、改 manifest 的 container_duration_sec —— tts_build 一重跑
（哪怕走缓存）就覆盖掉，忘了再补，成片悄悄少了这几秒。

  A. timeline_build.scene_holds（零依赖）：{"close": 2.5} 照收、0 秒不收；写成列表、id 拼错、负数、字符串、
     布尔、NaN 都报错 —— 拼错的 id 不报错，就是静默不停留。
  B. 真的 timeline_build.py（要 ffmpeg）：两场 a（中间，停 0.5 s）、b（末场，停 1.5 s）。停留算进 duration_sec、
     layout 记 hold_sec，下一场的起点、总长、总帧数都跟着加；拼出的音轨里停留那段是静音，b 的 beep 落在
     start_sec + 段内偏移 ±10 ms。hold 里有拼错的 id 时退出码 1、不写 layout.json。
  C. 真的 subs.py：段时长与 beats.js 的 __SEG__.duration 含停留；字幕不挂到停留上 —— 末块收在语音真实结束，
     SRT 最后一条也是。
  D. 真的 render_video.mjs（要浏览器 + ffmpeg）：总帧数含停留；停留里片尾卡还在、字幕带是空的；中间那场照模板
     写的出场（排在 [speech_end, duration + gap]）在停留之后才开始，停留里卡片还在。

缺 ffmpeg / 浏览器时跳过 B–D（打印 SKIP）；设了 HX_NO_SKIP=1（CI）时不许跳过。失败即以非零码退出。
"""
from __future__ import annotations

import importlib.util
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from array import array

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NO_SKIP = os.environ.get("HX_NO_SKIP") == "1"


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


def finish():
    if fails:
        print(f"\n失败 {len(fails)} 项：\n")
        for f in fails:
            print("  ✗ " + f)
        sys.exit(1)
    print("\n全部通过")
    sys.exit(0)


def skip_or_fail(what):
    if NO_SKIP:
        fails.append(f"缺 {what}，而 HX_NO_SKIP=1 不许跳过")
        finish()
    print(f"  SKIP 缺 {what}")
    finish()


# ---------------- A. scene_holds ----------------
print("A. timeline_build.scene_holds")
holds_fn = getattr(tb, "scene_holds", None)
if holds_fn is None:
    fails.append("timeline_build 没有 scene_holds()：project.json 的 hold 没人读")
else:
    ORDER = ["hook", "close"]
    check("没写 hold：没有停留", holds_fn({}, ORDER), {})
    check('{"close": 2.5}：close 停 2.5 s', holds_fn({"hold": {"close": 2.5}}, ORDER), {"close": 2.5})
    check("整数秒也收、舍入到 3 位；0 秒不收", holds_fn({"hold": {"close": 3, "hook": 0}}, ORDER), {"close": 3.0})
    check("舍入到 3 位", holds_fn({"hold": {"close": 1.23456}}, ORDER), {"close": 1.235})
    for label, bad, needle in [("写成列表", ["close", 2.5], "{"), ("id 拼错", {"clsoe": 2.5}, "clsoe"),
                               ("负数", {"close": -1}, "close"), ("字符串", {"close": "2.5"}, "close"),
                               ("布尔", {"close": True}, "close"), ("NaN", {"close": float("nan")}, "close")]:
        try:
            holds_fn({"hold": bad}, ORDER)
            fails.append(f"hold {label} 应该报错，却照收了")
        except ValueError as e:
            check(f"hold {label}：报错，写明「{needle}」", needle in str(e), True)


# ---------------- B–D 的工程 ----------------
FF = shutil.which("ffmpeg")
if FF is None:
    skip_or_fail("PATH 上的 ffmpeg（B–D 段）")

SR, FPS, GAP = 48000, 30, 0.35
# (段 id, 段长秒, beep 起点, beep 终点, 停留秒, 解说词)：beep 是「语音」，speech_end = beep 终点
SEGS = [("a", 1.25, 0.10, 1.00, 0.5, "Hello there"),
        ("b", 1.05, 0.10, 0.80, 1.5, "Goodbye now")]


def ff(*args, data=None):
    p = subprocess.run([FF, "-hide_banner", "-loglevel", "error", "-y", *args], input=data, capture_output=True)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.decode(errors="replace")[-300:])
    return p.stdout


def container(path):
    """tts_build 回填 container_duration_sec 的口径：ffmpeg 打印的 Duration:。"""
    err = subprocess.run([FF, "-hide_banner", "-i", path], capture_output=True, text=True, errors="replace").stderr
    h, m, s = err.split("Duration:")[1].split(",")[0].strip().split(":")
    return round(int(h) * 3600 + int(m) * 60 + float(s), 3)


def pcm(path, sr=SR):
    x = array("h")
    x.frombytes(ff("-i", path, "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"))
    return x


def rms_db(x, a, b):
    seg = x[max(0, int(a * SR)):int(b * SR)]
    if not seg:
        return -200.0
    e = sum(v * v for v in seg) / len(seg)
    return 10 * math.log10(e / 32768.0 ** 2) if e > 0 else -200.0


def make_project(d, hold):
    os.makedirs(os.path.join(d, "audio"))
    items = []
    for sid, length, b0, b1, _, text in SEGS:
        n = int(round(length * SR))
        x = array("h", bytes(2 * n))
        freq = 440 if sid == "a" else 660
        for i in range(int(b0 * SR), int(b1 * SR)):
            x[i] = int(12000 * math.sin(2 * math.pi * freq * i / SR))
        mp3 = os.path.join(d, "audio", f"{sid}.mp3")
        ff("-f", "s16le", "-ac", "1", "-ar", str(SR), "-i", "-", "-c:a", "libmp3lame", "-b:a", "128k", mp3,
           data=x.tobytes())
        w1, w2 = text.split()
        mid = round((b0 + b1) / 2, 3)
        items.append({"id": sid, "path": mp3, "duration_sec": b1, "lead_cut_sec": 0.0,
                      "container_duration_sec": container(mp3), "speech_start_sec": b0, "speech_end_sec": b1,
                      "boundaries": [{"text": w1, "offset_sec": b0, "duration_sec": round(mid - b0, 3)},
                                     {"text": w2 + ".", "offset_sec": mid, "duration_sec": round(b1 - mid, 3)}]})
    pj = {"slug": "hold", "lang": "en", "width": 480, "height": 270, "fps": FPS, "gap": GAP,
          "order": [s[0] for s in SEGS], "progress": False}
    if hold is not None:
        pj["hold"] = hold
    json.dump(pj, open(os.path.join(d, "project.json"), "w"))
    json.dump([{"id": s[0], "text": s[5]} for s in SEGS], open(os.path.join(d, "narration.json"), "w"))
    json.dump({"trimmed": True, "items": items}, open(os.path.join(d, "audio-manifest.json"), "w"))
    return {it["id"]: it for it in items}


def run_py(script, d):
    return subprocess.run([sys.executable, os.path.join(ROOT, "scripts", script), "--project", d],
                          capture_output=True, text=True, errors="replace")


tmp = tempfile.mkdtemp(prefix="hx-hold-")
try:
    # ---------------- B. timeline_build ----------------
    print("B. timeline_build：停留算进 duration_sec，音轨补静音")
    bad = os.path.join(tmp, "typo")
    os.makedirs(bad)
    make_project(bad, {"a": 0.5, "nope": 1.0})
    p = run_py("timeline_build.py", bad)
    check("hold 里有拼错的 id：退出码 1", p.returncode, 1)
    check("报错写明拼错的 id「nope」", "nope" in p.stderr, True)
    check("不写 layout.json", os.path.exists(os.path.join(bad, "layout.json")), False)

    d = os.path.join(tmp, "proj")
    os.makedirs(d)
    man = make_project(d, {s[0]: s[4] for s in SEGS})
    p = run_py("timeline_build.py", d)
    check("timeline_build 退出码 0（自己的「音轨与 layout 总长差 ≤ 1 帧」也过了）", p.returncode, 0)
    if p.returncode != 0:
        print(p.stderr)
        finish()
    lay = json.load(open(os.path.join(d, "layout.json")))
    ca, cb = man["a"]["container_duration_sec"], man["b"]["container_duration_sec"]
    dur_a, dur_b = round(ca + 0.5, 3), round(cb + 1.5, 3)
    check("a：duration_sec = 容器 + 0.5，hold_sec 0.5", (lay["a"]["duration_sec"], lay["a"].get("hold_sec")),
          (dur_a, 0.5))
    check("b 的起点跟着往后挪 0.5 s", lay["b"]["start_sec"], round(dur_a + GAP, 3))
    check("b（末场）：duration_sec = 容器 + 1.5，hold_sec 1.5",
          (lay["b"]["duration_sec"], lay["b"].get("hold_sec")), (dur_b, 1.5))
    total = round(dur_a + GAP + dur_b, 3)
    check("总长与总帧数含两段停留", (lay["_total"]["video_duration_sec"], lay["_total"]["total_frames"]),
          (total, int(round(total * FPS))))
    full = pcm(os.path.join(d, "audio", "narration-full.mp3"))
    sb = lay["b"]["start_sec"]
    check("末场停留的 1.5 s 是静音（< −60 dBFS）", rms_db(full, sb + 0.85, total - 0.02) < -60, True)
    check("中间那场的停留是静音", rms_db(full, 1.05, dur_a) < -60, True)
    check("b 的「语音」在它的槽位里（> −20 dBFS）", rms_db(full, sb + 0.2, sb + 0.7) > -20, True)
    # beep 起点：b 段第一次超过 −30 dBFS 的 5 ms 窗
    win = int(0.005 * SR)
    onset = next((i * win / SR for i in range(int((sb - 0.2) * SR) // win, len(full) // win)
                  if rms_db(full, i * win / SR, (i + 1) * win / SR) > -30), None)
    check("b 的 beep 落在 start_sec + 0.10 ±10 ms", onset is not None and abs(onset - (sb + 0.10)) <= 0.01, True)

    # ---------------- C. subs ----------------
    print("C. subs：段时长含停留，字幕不挂到停留上")
    p = run_py("subs.py", d)
    check("subs 退出码 0", p.returncode, 0)
    if p.returncode != 0:
        print(p.stderr)
        finish()
    sj = {s["id"]: s for s in json.load(open(os.path.join(d, "subs.json")))["segments"]}
    check("subs.json：duration_sec 与 layout 一样（含停留）",
          (sj["a"]["duration_sec"], sj["b"]["duration_sec"]), (dur_a, dur_b))
    check("b：tail_silence_sec = 容器 + 1.5 − 语音止", sj["b"]["tail_silence_sec"], round(cb + 1.5 - 0.8, 3))
    check("b 末块收在语音真实结束（0.8 s），不挂到停留上",
          sj["b"]["blocks"][-1]["global_end_sec"], round(sb + 0.8, 3))
    seg = {}
    for sid in ("a", "b"):
        js = open(os.path.join(d, "frames", f"{sid}.beats.js"), encoding="utf-8").read()
        seg[sid] = json.loads(re.search(r"window\.__SEG__\s*=\s*(\{.*?\});", js).group(1))
    check("beats.js：__SEG__.duration 含停留、gap 照旧（末场 0）",
          [(seg[k]["duration"], seg[k]["gap"]) for k in ("a", "b")], [(dur_a, GAP), (dur_b, 0.0)])
    srt = open(os.path.join(d, "out", "hold.srt"), encoding="utf-8").read()
    last_end = re.findall(r"--> (\d\d):(\d\d):(\d\d),(\d{3})", srt)[-1]
    h, m, s_, ms = map(int, last_end)
    check("SRT 最后一条收在语音结束（±1 帧），不挂到停留上",
          abs(h * 3600 + m * 60 + s_ + ms / 1000 - (sb + 0.8)) <= 1.0 / FPS + 1e-6, True)

    # ---------------- D. render_video ----------------
    print("D. render_video：停留里片尾卡还在、字幕带是空的")
    node = shutil.which("node")
    if node is None:
        skip_or_fail("node（D 段）")
    os.makedirs(os.path.join(d, "assets"))
    shutil.copy(os.path.join(ROOT, "assets", "gsap.min.js"), os.path.join(d, "assets", "gsap.min.js"))
    for sid, _, _, _, _, _ in SEGS:
        # 卡片 0.1 s 淡入；出场照模板（frame-contract 第 10 条）排在 [speech_end, duration + gap]
        frame = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>html, body {{ margin: 0; width: 480px; height: 270px; overflow: hidden; background: #000; }}
.card {{ position: absolute; left: 20px; top: 20px; width: 120px; height: 60px; background: #33cccc; opacity: 0; }}</style>
</head><body><div class="card"></div>
<script src="../assets/gsap.min.js"></script><script src="{sid}.beats.js"></script>
<script>
  var SEG = window.__SEG__, tl = gsap.timeline({{ paused: true }});
  tl.to('.card', {{ opacity: 1, duration: 0.1 }}, 0.05);
  if (SEG.gap > 0) {{
    var x1 = SEG.duration + SEG.gap - 0.05;
    var x0 = Math.min(Math.max(SEG.speech_end, SEG.duration - 0.15), x1 - 0.3);
    tl.to('.card', {{ opacity: 0, duration: x1 - x0 }}, x0);
  }}
  window.__tl = tl;
</script></body></html>"""
        open(os.path.join(d, "frames", f"{sid}.html"), "w", encoding="utf-8").write(frame)
    r = subprocess.run([node, os.path.join(ROOT, "scripts", "render_video.mjs"), d, "--png-fast", "--keep-frames",
                        "--workers", "1", "--shutter", "0"], capture_output=True, text=True, errors="replace")
    out = (r.stdout or "") + (r.stderr or "")
    if r.returncode != 0:
        if not NO_SKIP and re.search(r"浏览器|browser|chrom", out, re.I):
            print("  SKIP 渲染器起不来（缺浏览器）\n" + "\n".join(out.splitlines()[-4:]))
            finish()
        fails.append("render_video 失败：\n" + "\n".join(out.splitlines()[-12:]))
        finish()
    fdir = os.path.join(d, "render", "frames")
    pngs = sorted(f for f in os.listdir(fdir) if f.endswith(".png"))
    check("渲出的帧数 = 总帧数（含停留）", len(pngs), lay["_total"]["total_frames"])

    def frame_stats(k):
        """第 k 帧（0 起）：卡片位置的 RGB、字幕带（y 100–190）里接近白色的像素数。没有这一帧返回 (None, None)。"""
        png = os.path.join(fdir, f"f_{k + 1:06d}.png")
        if not os.path.exists(png):
            return None, None
        raw = ff("-i", png, "-f", "rawvideo", "-pix_fmt", "rgb24", "-")
        px = lambda x, y: tuple(raw[(y * 480 + x) * 3:(y * 480 + x) * 3 + 3])
        white = sum(1 for y in range(100, 190) for x in range(0, 480, 2) if min(px(x, y)) > 200)
        return px(80, 50), white

    gb = int(round(sb * FPS))
    card_b_speech, sub_b_speech = frame_stats(gb + 12)            # b 里 0.4 s：在念
    card_b_hold, sub_b_hold = frame_stats(gb + int(1.6 * FPS))    # b 里 1.6 s：语音 0.8 s 之后的停留
    card_last, sub_last = frame_stats(len(pngs) - 1)              # 片尾最后一帧
    card_a_hold, sub_a_hold = frame_stats(int(round((1.0 + 0.3) * FPS)))  # a 的语音 1.0 s 止，1.3 s 在停留里
    teal = lambda c: c is not None and c[0] < 90 and c[1] > 160 and c[2] > 160
    check("b 在念的时候字幕带里有字", (sub_b_speech or 0) > 40, True)
    check("b 的停留里卡片还在、字幕带是空的", (teal(card_b_hold), sub_b_hold), (True, 0))
    check("最后一帧：卡片还在、没有字幕", (teal(card_last), sub_last), (True, 0))
    check("a 的停留里卡片还在（出场排在停留之后）、字幕带是空的", (teal(card_a_hold), sub_a_hold), (True, 0))
    check("b 在念的时候卡片在", teal(card_b_speech), True)
    mp4 = os.path.join(d, "out", "hold.mp4")
    dur = subprocess.run([FF, "-hide_banner", "-i", mp4], capture_output=True, text=True, errors="replace").stderr
    hh, mm, ss = dur.split("Duration:")[1].split(",")[0].strip().split(":")
    check("成片时长 = 总长（±1 帧）",
          abs(int(hh) * 3600 + int(mm) * 60 + float(ss) - total) <= 1.0 / FPS + 0.02, True)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

finish()
