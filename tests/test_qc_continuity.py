#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""qc_check「画面连续性」的回归测试（python tests/test_qc_continuity.py）。

守两类「抽帧看不出来、连着播才露馅」的错：

  ① 单帧跳动：某一帧和前后两帧都不一样，而前后两帧彼此一样 —— 典型来源是帧在搭建期读布局，
     --workers 并行渲染时某个进程量歪了，它负责的帧（每 N 帧一帧）整块错位。
     抽帧 QC 每次只看一张，永远看不出「这一张比邻居低了 12px」。
  ② 场间硬切：渲染器不做转场，段间 gap 停在上一场末帧，下一场第 0 帧直接切进来。
     量的是「下一场第 0 帧 − 上一帧」，所以边界帧号必须和渲染器一帧不差（lessons #154）。

A 段只测纯函数（零依赖），A2 钉住小数时刻（.5 帧、累加略大 / 略小于 .5）的边界帧号；
B 段用 numpy + ffmpeg 合成一段小视频端到端跑扫描（缺任一依赖就跳过 B 段，不算失败）；
C 段从 render_video.mjs 取出排场景起点的那几行在 node 里跑，逐场对 qc 的公式；D 段查 frame_at 打印的帧号。
HX_NO_SKIP=1 时 B、C 段缺依赖算失败。失败即以非零码退出。
"""
from __future__ import annotations

import importlib.util
import json
import os
import random
import re
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

# ---------------- A2. 场景边界：和渲染器同一条公式（lessons #154） ----------------
# 渲染器：c 从 0 起按 order 累加 duration_sec + gap（浮点，不舍入），第 i 场第 0 帧 = Math.round(c × fps)。
# 2.2.3 以前 qc 用 Python round(start_sec × fps)：start_sec 是舍入到 3 位的，round 又是银行家舍入，
# 一个系列 37 集 1872 个边界里 92 个差一帧（09-2 tmo7：87.45 × 30 = 2623.5 → 2624，渲染器是 2623），
# 「场间硬切」量的就成了下一场第 1 帧 − 第 0 帧。
print("A2. 场景边界（小数时刻）")
check("js_round：.5 往上入（Math.round），不是银行家舍入",
      [qc.js_round(x) for x in (4.5, 58.5, 211.5, 2.5, -2.5, 0.49999999999999994, 2623.4999999999995)],
      [5, 59, 212, 3, -2, 0, 2623])


def lay_of(durs, gap, ids=None):
    """按 timeline_build 的写法排 layout：start_sec 舍入到 3 位（qc 旧口径读的就是它）。"""
    ids = ids or [f"s{i}" for i in range(len(durs))]
    out, c = {}, 0.0
    for sid, d in zip(ids, durs):
        out[sid] = {"start_sec": round(c, 3), "duration_sec": d}
        c += d + gap
    return ids, out


ids, lay = lay_of([6.7, 9.02, 3.0], 0.35, ["a", "b", "c"])
check("整帧之外：7.05 s → 211.5 → 212，16.42 s → 492.6 → 493", qc.scene_boundaries(lay, ids, 30, 0.35),
      [("b", 212), ("c", 493)])
ids, lay = lay_of([0.15, 1.0], 0.0)
check("正好 .5 帧（0.15 s × 30 = 4.5）：往上入 5，不是银行家舍入的 4", qc.scene_boundaries(lay, ids, 30, 0.0), [("s1", 5)])
ids, lay = lay_of([1.6, 1.0], 0.35)
check("累加出来略大于 .5（1.9500000000000002 × 30）：59，不是 round(1.95 × 30) = 58",
      qc.scene_boundaries(lay, ids, 30, 0.35), [("s1", 59)])
TMO = [3.82, 7.1, 11.45, 6.53, 6.98, 4.87, 6.05, 7.63, 6.31, 7.44, 7.46, 7.61, 5.0]   # 一个系列 09-2 的前 13 场
ids, lay = lay_of(TMO, 0.35)
check("累加出来略小于 .5（09-2 tmo7：87.44999999999999 × 30）：2623，不是 round(87.45 × 30) = 2624",
      (lay["s12"]["start_sec"], qc.scene_boundaries(lay, ids, 30, 0.35)[-1]), (87.45, ("s12", 2623)))
check("scene_frame_starts 含首场（0），边界不含", qc.scene_frame_starts(lay, ids, 30, 0.35)[:2], [("s0", 0), ("s1", 125)])
check("order 里有、layout 里没有的场按 0 秒长算（渲染器 `|| 0`）",
      qc.scene_frame_starts({"a": {"duration_sec": 1.0}}, ["a", "x", "b"], 30, 0.35), [("a", 0), ("x", 41), ("b", 51)])

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

# ---------------- C. 与渲染器源码逐场一致（node） ----------------
# 从 scripts/render_video.mjs 里取出它排场景起点的那几行（脚本真源，不复制一份），在 node 里跑随机的小数时长，
# 和 qc.scene_frame_starts 逐场比。渲染器那段改了写法、这里取不到，就是失败：两边的公式要一起改。
print("C. 场景起点帧号与 render_video.mjs 逐场一致")
node = shutil.which("node")
if node is None and os.environ.get("HX_NO_SKIP") == "1":
    fails.append("缺 node，而 HX_NO_SKIP=1 不许跳过 C 段")
elif node is None:
    print("  SKIP 缺 node")
else:
    src = open(os.path.join(ROOT, "scripts", "render_video.mjs"), encoding="utf-8").read()
    acc = re.search(r"const sceneStarts = \[\];\n([ \t]*)\{\n[\s\S]*?\n\1\}\n", src)   # 到同一缩进的 }
    gfe = re.search(r"const gf = (Math\.round\(sceneStarts\[i\] \* fps\));", src)
    if not acc or not gfe:
        fails.append("没能从 render_video.mjs 取到 sceneStarts 的累加与 gf 的舍入 —— 渲染器改了写法，"
                     "qc_check.scene_frame_starts 要跟着核对")
    else:
        rnd = random.Random(154)
        cases = []
        for k in range(400):
            n = rnd.randint(2, 60)
            ids = [f"s{i}" for i in range(n)]
            nd = rnd.choice((2, 3))
            lay = {sid: {"duration_sec": round(rnd.uniform(0.4, 25.0), nd)} for sid in ids}
            cases.append({"order": ids, "layout": lay, "pj": {"gap": rnd.choice((0.35, 0.3, 0.25, 0.5, 0))},
                          "fps": rnd.choice((30, 60, 24, 25, 29.97))})
        ids, lay = lay_of(TMO, 0.35)
        cases.append({"order": ids, "layout": lay, "pj": {"gap": 0.35}, "fps": 30})
        cases.append({"order": ["a", "x", "b"], "layout": {"a": {"duration_sec": 1.0}}, "pj": {"gap": 0.35}, "fps": 30})
        cases.append({"order": ["a", "b"], "layout": {"a": {"duration_sec": 0.15}, "b": {"duration_sec": 1}}, "pj": {}, "fps": 30})
        js = ("const cases = JSON.parse(require('fs').readFileSync(0, 'utf8'));\n"
              "console.log(JSON.stringify(cases.map(({ order, layout, pj, fps }) => {\n"
              f"{acc.group(0)}\n  return order.map((id, i) => {gfe.group(1)});\n}})));")
        r = subprocess.run([node, "-e", js], input=json.dumps(cases), capture_output=True, text=True)
        if r.returncode != 0:
            fails.append("node 跑渲染器那段失败: " + r.stderr[:300])
        else:
            got = json.loads(r.stdout)
            bad = [k for k, c in enumerate(cases)
                   if [b for _, b in qc.scene_frame_starts(c["layout"], c["order"], c["fps"], c["pj"].get("gap") or 0)]
                   != got[k]]
            check(f"{len(cases)} 组小数时长（gap 0–0.5，24–60 fps）逐场一致", bad, [])
            def old_starts(c):   # 2.2.3 以前的口径：timeline_build 写的 start_sec（舍入到 3 位）× fps，Python round
                out, t = [], 0.0
                for sid in c["order"]:
                    out.append(int(round(round(t, 3) * c["fps"])))
                    t += (c["layout"].get(sid) or {}).get("duration_sec", 0) + (c["pj"].get("gap") or 0)
                return out
            old = sum(1 for k, c in enumerate(cases) for a, b in zip(old_starts(c), got[k]) if a != b)
            check("这批样本里旧口径 round(start_sec × fps) 确实有差一帧的（样本有鉴别力）", old > 0, True)

# ---------------- D. frame_at --list 的起帧 / 止帧走同一个函数 ----------------
print("D. frame_at 的场景帧号")
with tempfile.TemporaryDirectory() as d:
    ids, lay = lay_of([0.15, 1.0], 0.0, ["a", "b"])
    lay["_total"] = {"total_frames": 35, "video_duration_sec": 1.15}
    for name, obj in (("layout.json", lay), ("project.json", {"fps": 30, "gap": 0, "order": ids})):
        with open(os.path.join(d, name), "w", encoding="utf-8") as fh:
            json.dump(obj, fh)
    r = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "frame_at.py"), "--project", d, "--list"],
                       capture_output=True, text=True, encoding="utf-8")
    rows = {ln.split()[0]: ln.split()[-2:] for ln in r.stdout.splitlines() if ln.split()[:1] in (["a"], ["b"])}
    check("a 是第 1–5 帧，b 从第 6 帧起（渲染器 Math.round(0.15 × 30 = 4.5) = 5）", rows,
          {"a": ["1", "5"], "b": ["6", "35"]})
    r = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "frame_at.py"), "--project", d,
                        "--at", "0.02", "--no-images"], capture_output=True, text=True, encoding="utf-8")
    check("--at 的「场景终态帧」是 a 的最后一帧 5，不是 b 的第 0 帧", "场景终态帧 5" in r.stdout, True)

if fails:
    print("\n✗ 失败：")
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("\n✓ qc 连续性回归全部通过")
