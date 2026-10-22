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

另守一条（lessons #148）：画布、字体栈、色值写在帧引用的本地样式表（common.css）里时照样能查到；
英文片画面里没有中文，不要求中文字体族。

再守一条（lessons #151）：色值字面量只在颜色上下文里找 —— CSS 声明的值、`style="…"`、颜色类的标签属性
（fill / stroke / stop-color …）、脚本里赋给颜色类属性的值、整串就是 #rrggbb 的字符串、字符串里的 rgb()/rgba()。
脚本字符串里的「#301–」、id 选择器「#c13」、注释、画面文字、`url(#fade)` 都不是颜色，不报；真的颜色照旧报。

失败即以非零码退出（CI 直接吃退出码）。
"""
from __future__ import annotations

import importlib.util
import io
import os
import sys
import tempfile
from contextlib import redirect_stdout

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


def issues_for(files: dict[str, str], frame: str, lang: str = "zh") -> list[str]:
    """在临时项目里写文件，返回 frame 的 lint 结果。"""
    with tempfile.TemporaryDirectory() as d:
        for rel, text in files.items():
            p = os.path.join(d, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            open(p, "w", encoding="utf-8").write(text)
        return lint.check_file(os.path.join(d, "frames", frame), 1920, 1080, lang=lang)


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

# ---- 本地样式表与英文帧（lessons #148）----
# 帧把画布、字体栈放进共享的 common.css 时，只扫 HTML 会误报「未见画布」「没有中文字体」，
# 共享样式表里写死的色值反而漏掉。英文片的画面里没有中文，不该要求中文字体族。
EN_FRAME = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<link rel="stylesheet" href="../theme.css">
<link rel="stylesheet" href="common.css">
</head><body>
<!-- 注释里的中文不上画面 -->
<div class="box">Queryable in minutes</div>
<script src="../assets/gsap.min.js"></script>
<script>
(function () {
  var tl = gsap.timeline({ paused: true });
  // 出场：注释里的中文不算
  tl.to('.box', { x: 10 }, 0);
  window.__tl = tl;
})();
</script>
</body></html>
"""
COMMON = "html, body { width: 1920px; height: 1080px; font-family: 'Inter', sans-serif; }\n/* 共享样式 */\n"
THEME = ":root { --accent: #7ED9C3; }"

print("本地样式表与英文帧")
got = issues_for({**VENDOR, "theme.css": THEME, "frames/common.css": COMMON, "frames/a.html": EN_FRAME}, "a.html", "en")
check("英文帧：画布与字体栈在 common.css 里、画面没有中文 → 零违规（theme.css 的色值不扫）", got == [], f"得到 {got}")

got = issues_for({**VENDOR, "theme.css": THEME, "frames/common.css": COMMON + ".x { color: #ff0000; }",
                  "frames/a.html": EN_FRAME}, "a.html", "en")
check("common.css 里写死的色值被报，并注明出处", any("#ff0000" in x and "common.css" in x for x in got), f"得到 {got}")

got = issues_for({**VENDOR, "theme.css": THEME, "frames/common.css": COMMON + ".x { transition: opacity .3s; }",
                  "frames/a.html": EN_FRAME}, "a.html", "en")
check("common.css 里的 transition 被报", any("transition" in x for x in got), f"得到 {got}")

got = issues_for({**VENDOR, "theme.css": THEME, "frames/common.css": COMMON, "frames/a.html": EN_FRAME}, "a.html", "zh")
check("lang 是 zh 时仍要求中文字体族", any("中文字体族" in x for x in got), f"得到 {got}")

got = issues_for({**VENDOR, "theme.css": THEME, "frames/common.css": COMMON,
                  "frames/a.html": EN_FRAME.replace("Queryable in minutes", "分钟级可查")}, "a.html", "en")
check("画面里有中文时，lang 是 en 也要求中文字体族", any("中文字体族" in x for x in got), f"得到 {got}")

got = issues_for({**VENDOR, "theme.css": THEME, "frames/a.html": EN_FRAME}, "a.html", "en")
check("引用的本地样式表不存在时报出来", any("common.css" in x and "不存在" in x for x in got), f"得到 {got}")

got = issues_for({**VENDOR, "theme.css": THEME,
                  "frames/common.css": "@import url(https://fonts.googleapis.com/css2?family=Inter);\n" + COMMON,
                  "frames/a.html": EN_FRAME}, "a.html", "en")
check("common.css 里 @import 远程字体被报", any("@import" in x and "common.css" in x for x in got), f"得到 {got}")

got = issues_for({**VENDOR, "theme.css": THEME, "frames/common.css": COMMON + ".row::before { top: 6px; bottom: 6px; }",
                  "frames/a.html": EN_FRAME}, "a.html", "en")
check("共享样式表里的 bottom 不按字幕带启发式报（卡片内边距一类；几何以 check_layout 为准）",
      not any("bottom" in x for x in got), f"得到 {got}")
got = issues_for({**VENDOR, "theme.css": THEME, "frames/common.css": COMMON,
                  "frames/a.html": EN_FRAME.replace("</head>", "<style>.band { bottom: 100px; }</style></head>")},
                 "a.html", "en")
check("帧自己的 CSS 里压进字幕带的 bottom 照旧报", any("bottom:100px" in x for x in got), f"得到 {got}")

got = issues_for({**VENDOR, "theme.css": THEME, "frames/common.css": COMMON + ".x { color: #ff0000; }",
                  "frames/a.html": EN_FRAME.replace('rel="stylesheet" href="common.css"', 'rel=stylesheet href=common.css')},
                 "a.html", "en")
check("不带引号的 rel=stylesheet href=common.css 也扫", any("#ff0000" in x for x in got), f"得到 {got}")

got = issues_for({**VENDOR, "theme.css": THEME, "frames/common.css": COMMON,
                  "frames/a.html": EN_FRAME.replace("Queryable in minutes", "MySQL // 分钟级")}, "a.html", "en")
check("正文里的「// 中文」是画面文字，要求中文字体族", any("中文字体族" in x for x in got), f"得到 {got}")

got = issues_for({**VENDOR, "theme.css": THEME, "frames/common.css": COMMON,
                  "frames/a.html": EN_FRAME.replace("tl.to('.box', { x: 10 }, 0);",
                                                    "document.querySelector('.box').textContent = '分钟级';")},
                 "a.html", "en")
check("脚本字符串里的中文要上画面，要求中文字体族", any("中文字体族" in x for x in got), f"得到 {got}")

got = issues_for({**VENDOR, "theme.css": THEME, "frames/common.css": COMMON,
                  "frames/a.html": EN_FRAME.replace("tl.to('.box', { x: 10 }, 0);",
                                                    "document.querySelector('.box').textContent = \"注意 // 中文\";")},
                 "a.html", "en")
check("字符串里的「//」不是注释：字符串里的中文照样要求中文字体族", any("中文字体族" in x for x in got), f"得到 {got}")

got = issues_for({**VENDOR, "theme.css": THEME, "frames/common.css": COMMON + '.tag::after { content: "A // 中文"; }',
                  "frames/a.html": EN_FRAME}, "a.html", "en")
check("CSS content 里的中文要求中文字体族", any("中文字体族" in x for x in got), f"得到 {got}")

got = issues_for({**VENDOR, "theme.css": THEME, "frames/common.css": COMMON,
                  "frames/a.html": EN_FRAME.replace("<head>", "<head><title>场景 · 开场</title>")
                                           .replace('<div class="box">', '<div class="box" data-note="中文备注">')},
                 "a.html", "en")
check("<title> 与标签属性里的中文不上画面，英文帧不要求中文字体族", got == [], f"得到 {got}")

# ---- 色值字面量只在颜色上下文里找（lessons #151）----
# 旧代码把 #[0-9a-fA-F]{3,8} 套在整份源码上：脚本字符串里的格名「#301–」、id 选择器「#c13」、注释、画面文字都算色值。
# 一个中文系列 07-1 的 split1 为此把「'#' + n + '–'」拆开拼（lessons #108 也只能叫人改 id）。
def colours(got):
    """lint 结果里报出来的色值（「色值字面量 X →」里的 X）。"""
    return {x.split("色值字面量 ", 1)[1].split(" →", 1)[0] for x in got if "色值字面量 " in x}


def colours_in(body="", markup="", css="", extra=""):
    html = frame(body, extra)
    if markup:
        html = html.replace('<div class="box">内容</div>', '<div class="box">内容</div>\n' + markup)
    if css:
        html = html.replace("</style>", "\n" + css + "\n</style>", 1)
    return colours(issues_for({**VENDOR, "frames/a.html": html}, "a.html"))


print("色值：不是颜色的「#…」不报")
# 07-1 split1 第 95–96 行原样（复现目录 lint-repro 里旧代码报 #301 #601 #901 #c13 四条）
SPLIT1 = ("function lo(n) { return { 1: '#1–', 301: '#301–', 601: '#601–', 901: '#901–' }[n]; }\n"
          "  var CUR = '#c13';   // 06-4 G08：格的 id 是 c13，选择器写 '#c13'\n"
          "  tl.to(CUR, { fill: 'var(--accent)', duration: 0.4 }, 0);")
NOT_COLOUR = [
    ("07-1 split1 原样：格名字符串「#301–」、选择器变量 '#c13'、注释里的 '#c13'", dict(body=SPLIT1)),
    ("脚本字符串里的「#」加数字", dict(body="var t = '#301'; var u = \"#1000 号\"; var v = `#600–#899`;")),
    ("querySelector / gsap.to 的 id 选择器", dict(body="var el = document.querySelector('#c13'); "
                                                     "tl.to('#bd1', { x: 10 }, 0); gsap.set(\"#db2\", { opacity: 0 });")),
    ("脚本注释（含颜色属性的写法）", dict(body="// 选择器写 '#c13'，别写 #fff\n  /* 原稿 #ff0000 */\n"
                                         "  // 旧写法 { fill: '#f00' }、el.setAttribute('stroke', '#bd1')\n"
                                         "  /* el.innerHTML = '<rect fill=\"#abc\" style=\"color:#fff\"/>' */")),
    ("画面文字（HTML 正文、SVG <text>）", dict(markup="<p>节点 #301 与 #c13</p><svg><text x='0' y='0'>#bd1</text></svg>")),
    ("<style> 里的选择器：id（lessons #108 的 #bd1）、伪类后面的 id、属性选择器", dict(
        css="#bd1, #c13:hover > .x { opacity: 0; } .y:not(#bad), a:hover #c0de { opacity: 1; } rect[fill=\"#abc\"] { opacity: 0; }")),
    ("url(#…) 引用渐变 / 图案：CSS、标签属性、脚本", dict(css=".x { fill: url(#fade); }",
                                                   markup='<rect fill="url(#bad)" stroke="url(\'#cab\')"/>',
                                                   body="tl.set('.y', { fill: 'url(#dead)' }, 0);")),
    ("CSS 注释与 content 里的文字", dict(css="/* 原稿 #ff0000 */ .x::after { content: \"#1 #abc\"; }")),
    ("HTML 注释", dict(markup="<!-- 原稿配色 #ff0000 rgba(214,137,81,.45) -->")),
    ("脚本注释与画面文字里的 rgba()", dict(body="// 别写 rgba(214,137,81,.45)", markup="<p>rgb(255, 0, 0) 是红色</p>")),
    ("非颜色属性的值：text / id / label", dict(body="var c = { id: 'c13', text: '#301–', label: '#abc' };")),
    ("画面文字里大写开头的「Color: #1」", dict(body="var t = { text: 'Color: #1, Border: #c13' };")),
]
for label, kw in NOT_COLOUR:
    got = colours_in(**kw)
    check(f"不报：{label}", got == set(), f"报了 {sorted(got)}")

print("色值：真的颜色照旧报")
IS_COLOUR = [
    ("帧 <style> 的声明", dict(css=".x { color: #ff0000; background: linear-gradient(#0d0f14, #1b2130); }"),
     {"#ff0000", "#0d0f14", "#1b2130"}),
    ("帧 <style> 的自定义属性与 rgba()", dict(css=":root { --glow: #E8912B; } .y { box-shadow: 0 0 9px rgba(6, 37, 51, 0.2); }"),
     {"#E8912B", "rgba(6, 37, 51, 0.2)"}),
    ("<style> 里 id 选择器后面的值", dict(css="#c13 { fill: #bd1; }"), {"#bd1"}),
    ("style=\"…\" 属性（含自定义属性）", dict(markup='<div style="left: 3px; color:#f00; --stamp: #0E7C6B">x</div>'),
     {"#f00", "#0E7C6B"}),
    ("SVG 颜色属性 fill / stroke / stop-color", dict(markup='<rect fill="#abc"/><line stroke=\'#123456\'/>'
                                                        '<stop offset="0" stop-color="#fff"/><circle fill=#c13 />'),
     {"#abc", "#123456", "#fff", "#c13"}),
    ("SVG 属性里的 rgba()", dict(markup='<rect fill="rgba(184, 58, 42, 0.12)"/>'), {"rgba(184, 58, 42, 0.12)"}),
    ("gsap 补间的 fill / stroke / color", dict(body="tl.to('.x', { fill: '#f00' }, 0); gsap.set('.y', { stroke: \"#123\" });"
                                                  " tl.to('.z', { color: '#c13', duration: 1 }, 0);"),
     {"#f00", "#123", "#c13"}),
    ("backgroundColor / borderTopColor / stopColor / background", dict(
        body="tl.set('.a', { backgroundColor: '#0f0', borderTopColor: '#00f', stopColor: '#fff' }); "
             "tl.set('.b', { background: 'linear-gradient(#a00, #0a0)' });"),
     {"#0f0", "#00f", "#fff", "#a00", "#0a0"}),
    ("gsap attr 里带引号的 'stop-color'", dict(body="tl.to('#g1', { attr: { 'stop-color': '#abc' } }, 0);"), {"#abc"}),
    ("setAttribute('fill', …) / style.setProperty('--x', …)", dict(
        body="el.setAttribute('fill', '#bd1'); el.style.setProperty('--accent', '#f0f0f0');"),
     {"#bd1", "#f0f0f0"}),
    ("赋值 el.style.color = … / ctx.fillStyle = …", dict(body="el.style.color = '#c13'; ctx.fillStyle = '#fff';"),
     {"#c13", "#fff"}),
    ("三元表达式的两支", dict(body="tl.set('.x', { fill: on ? '#f00' : '#0f0' });"), {"#f00", "#0f0"}),
    ("同一行的选择器不报、颜色报", dict(body="tl.to('#c13', { fill: '#f00' }, 0);"), {"#f00"}),
    ("调色板：整串就是 #rrggbb / #rrggbbaa 的字符串", dict(
        body="var CREAM = \"#F4EFE4\"; var P = ['#4e79a7', '#f28e2b80'];"), {"#F4EFE4", "#4e79a7", "#f28e2b80"}),
    ("字符串里的 rgba()：存进变量也报，纯黑白阴影照旧放行", dict(
        body="var GLOW = '0 0 40px rgba(214,137,81,.45)'; var S = 'rgba(0,0,0,.4)';"), {"rgba(214,137,81,.45)"}),
    ("脚本拼的标记：innerHTML 里的 style 与 fill", dict(
        body="el.innerHTML = '<span style=\"color:#f00\">x</span><rect fill=\"#0f0\"/>';"), {"#f00", "#0f0"}),
    ("脚本拼的 CSS 文本：cssText", dict(body="el.style.cssText = 'left:3px;border:1px solid #abcdef';"), {"#abcdef"}),
]
for label, kw, want in IS_COLOUR:
    got = colours_in(**kw)
    check(f"报：{label}", got == want, f"想要 {sorted(want)}，报了 {sorted(got)}")

got = issues_for({**VENDOR, "theme.css": THEME,
                  "frames/common.css": COMMON + "#bd1 { opacity: 0; } /* #ff0000 */ .x::after { content: '#1'; }"
                                                " .y { border-color: #c13; fill: url(#fade); }",
                  "frames/a.html": EN_FRAME}, "a.html", "en")
check("本地样式表：id 选择器、注释、content、url(#…) 不报，声明里的色值报",
      colours(got) == {"#c13"} and any("#c13" in x and "common.css" in x for x in got), f"得到 {got}")

print("共享样式表的问题只报一次")
with tempfile.TemporaryDirectory() as d:
    files = {**VENDOR, "theme.css": THEME, "frames/common.css": COMMON + ".x { color: #ff0000; }",
             "frames/a.html": EN_FRAME, "frames/b.html": EN_FRAME,
             "project.json": '{"width": 1920, "height": 1080, "lang": "en"}'}
    for rel, text in files.items():
        p = os.path.join(d, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "w", encoding="utf-8").write(text)
    argv, buf = sys.argv, io.StringIO()
    sys.argv = ["lint_frames.py", "--project", d]
    try:
        with redirect_stdout(buf):
            lint.main()
    finally:
        sys.argv = argv
    lines = [x for x in buf.getvalue().splitlines() if "#ff0000" in x]
check("两个帧共用 common.css：#ff0000 只报一行，记在 common.css 名下",
      len(lines) == 1 and "common.css:" in lines[0], f"得到 {lines}")

if fails:
    print("\n✗ 失败：")
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("\n✓ lint_frames 回归全部通过")
