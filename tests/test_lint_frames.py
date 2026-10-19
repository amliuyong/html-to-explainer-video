#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""lint_frames 的回归测试（零依赖，直接 python tests/test_lint_frames.py）。

守的是「搭建期不读布局」这条规则（frame-contract.md「渲染期一致性」）：

  帧在搭建时间轴时读布局（getBoundingClientRect / getBBox / offsetHeight …）去定位置，
  在 --workers 并行渲染下，各浏览器进程量到的值会不一样（字体与排版就绪的时刻不同），
  某个进程负责的那一组帧就整体错位 —— 成片里「每 N 帧跳一下」，lint、几何体检、QC 抽帧全绿。

所以 lint 必须①认出这些读法②**顺着 <script src> 扫进帧引用的本地脚本**（共享搭建器
kit.js 这类文件里的测量，只扫 HTML 是看不见的），同时③不误伤技能自带的 gsap.min.js /
beats.js，也不误伤与字体无关的 SVG 几何（getTotalLength / getPointAtLength）。

失败即以非零码退出（CI 直接吃退出码）。
"""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, rel))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


lint = _load("lint_frames", "scripts/lint_frames.py")

FRAME = """<!DOCTYPE html>
<html><head><meta charset="utf-8">
<link rel="stylesheet" href="../theme.css">
<style>html, body {{ width: 1920px; height: 1080px; font-family: 'Noto Sans CJK SC', sans-serif; }}</style>
</head><body>
<div class="box">内容</div>
<script src="../assets/gsap.min.js"></script>
<script src="{id}.beats.js"></script>
{extra}
<script>
(function () {{
  var tl = gsap.timeline({{ paused: true }});
  {body}
  window.__tl = tl;
}})();
</script>
</body></html>
"""

fails: list[str] = []


def check(label, cond, detail=""):
    if cond:
        print(f"  OK  {label}")
    else:
        fails.append(f"{label}\n    {detail}")


def issues_for(files: dict[str, str], frame: str) -> list[str]:
    """在临时项目里写文件，返回 frame 的 lint 结果。"""
    with tempfile.TemporaryDirectory() as d:
        for rel, text in files.items():
            p = os.path.join(d, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            open(p, "w", encoding="utf-8").write(text)
        return lint.check_file(os.path.join(d, "frames", frame), 1920, 1080)


def frame(body="", extra="", id_="a"):
    return FRAME.format(id=id_, body=body, extra=extra)


# 技能自带的库本身满是布局读取 —— 引用它们不能算帧的违规
VENDOR = {
    "assets/gsap.min.js": "function q(e){return e.getBoundingClientRect()} setInterval(f,1)",
    "frames/a.beats.js": "window.__BEATS__=[]; // offsetHeight",
}

print("对照：合规帧不报")
got = issues_for({**VENDOR, "frames/a.html": frame("tl.to('.box', { x: 10 }, 0);")}, "a.html")
check("合规帧零违规（gsap.min.js / beats.js 不被扫）", got == [], f"得到 {got}")

print("帧内联脚本里的布局读取")
for api in ("el.getBoundingClientRect()", "svg.getBBox()", "box.offsetHeight", "box.clientWidth",
            "t.getComputedTextLength()", "n.scrollHeight"):
    got = issues_for({**VENDOR, "frames/a.html": frame(f"var v = {api};")}, "a.html")
    check(f"{api} 被报", any("读布局" in x for x in got), f"得到 {got}")

print("顺着 <script src> 扫本地脚本")
kit = "function center(st){ var r = st.getBoundingClientRect(); st.style.top = r.top + 'px'; }"
got = issues_for({**VENDOR, "frames/kit.js": kit,
                  "frames/a.html": frame(extra='<script src="kit.js"></script>')}, "a.html")
check("kit.js 里的 getBoundingClientRect 被报", any("读布局" in x and "kit.js" in x for x in got), f"得到 {got}")

got = issues_for({**VENDOR, "frames/kit.js": "setInterval(tick, 16);",
                  "frames/a.html": frame(extra='<script src="kit.js"></script>')}, "a.html")
check("kit.js 里的墙钟逻辑被报", any("墙钟" in x and "kit.js" in x for x in got), f"得到 {got}")

got = issues_for({**VENDOR, "frames/a.html": frame(extra='<script src="missing.js"></script>')}, "a.html")
check("引用的本地脚本不存在时报出来", any("missing.js" in x for x in got), f"得到 {got}")

print("与字体无关的 SVG 几何不误伤")
got = issues_for({**VENDOR, "frames/a.html": frame(
    "var p = document.querySelector('path'); var L = p.getTotalLength(); var m = p.getPointAtLength(L / 2);")},
    "a.html")
check("getTotalLength / getPointAtLength 不报", got == [], f"得到 {got}")

if fails:
    print("\n✗ 失败：")
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("\n✓ lint_frames 回归全部通过")
