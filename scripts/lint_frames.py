#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
lint_frames.py —— 场景帧静态体检（渲染前跑，比渲完全片再发现问题便宜得多）。

检查项（全部来自 references/frame-contract.md 的十条契约 + 已知踩坑）：
  1. 画布：html,body 是否声明 1920×1080（或 project.json 的尺寸）
  2. 外部字体 / CDN：<link href="http...">、fonts.googleapis、@import url(http
  3. 色值字面量：#RRGGBB / rgb() / rgba() 出现在颜色上下文里（theme.css 变量之外）
     —— 例外：纯黑/纯白阴影 rgba(0,0,0,x) 允许（阴影不是"配色"）
     —— 只在颜色上下文里找（colour_spans，lessons #151）：CSS 声明的值（<style>、style="…"、本地样式表）、
        颜色类的标签属性、脚本里赋给颜色类属性的值、整串就是 #rrggbb(aa) 的字符串、字符串里的 rgb()/rgba()。
        脚本字符串「#301–」、id 选择器「#c13」、注释、画面文字、url(#fade) 不算
  4. 时间轴注册：window.__tl = （或 window.__timelines）
  5. GSAP 本地引用：../assets/gsap.min.js（禁 CDN）
  6. 墙钟逻辑：setInterval / setTimeout(数字) / requestAnimationFrame 计数
  7. CSS transition 入场
  8. B() 兜底：B('x') || 数字
  9. 字幕带禁区：CSS 里 bottom < 176px 的规则且带内容属性（启发式，转人工确认）
 10. 字体栈：画面里有中文（或 project.json 的 lang 是 zh）时必须含中文字体族（Microsoft YaHei / PingFang 等）
 11. 搭建期读布局：getBoundingClientRect / getBBox / offsetHeight / clientWidth …
     —— --workers 并行渲染下各进程量到的值不一致，成片「每 N 帧跳一下」（lessons #134）

第 6、11 条连帧引用的**本地脚本**（<script src="kit.js"> 这类共享搭建器）一起扫；
技能自带的 gsap.min.js / motion.js 与 subs.py 生成的 *.beats.js 不扫。
第 1、2、3、7、10 条连帧引用的**本地样式表**（<link rel="stylesheet" href="common.css">）一起扫，
出自样式表的问题记在样式表名下、几个帧共用也只报一次；theme.css 是色值的唯一来源，不扫（lessons #148）。

用法：
  python lint_frames.py --project .            # 全量
  python lint_frames.py --project . --only hook,decision
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

HEX = re.compile(r"#[0-9a-fA-F]{3,8}\b")
RGBA = re.compile(r"\brgba?\([^)]*\)")
FONT_LINK = re.compile(r"<link[^>]+href\s*=\s*[\"']https?://", re.I)
IMPORT_HTTP = re.compile(r"@import\s+url\(\s*[\"']?https?://", re.I)
GSAP_CDN = re.compile(r"<script[^>]+src\s*=\s*[\"']https?://", re.I)
B_FALLBACK = re.compile(r"B\([^)]*\)\s*\|\|")
WALLCLOCK = re.compile(r"setInterval\s*\(|setTimeout\s*\(\s*[a-zA-Z_]|requestAnimationFrame\s*\(\s*(?!function)")
CSS_TRANSITION = re.compile(r"transition\s*:", re.I)
BOTTOM_RULE = re.compile(r"(?<![-\w])bottom\s*:\s*(-?\d+)(px|%)")
CJK_FONT = re.compile(r"YaHei|PingFang|Noto Sans CJK|Source Han|Hiragino|SimHei|SimSun")
# 读排版结果的 API（值取决于字体与排版就绪时刻）。SVG 路径几何 getTotalLength / getPointAtLength
# 只依赖 d 属性，与字体无关，不在此列。
LAYOUT_READ = re.compile(r"\.(getBoundingClientRect|getClientRects|getBBox|getComputedTextLength)\s*\("
                         r"|\.(offset|client|scroll)(Width|Height|Top|Left)\b")
LOCAL_SCRIPT = re.compile(r"<script[^>]+src\s*=\s*[\"']([^\"']+)[\"']", re.I)
VENDOR_SCRIPT = re.compile(r"(^|/)(gsap(\.min)?\.js|motion\.js|[^/]+\.beats\.js)$")
LOCAL_STYLE = re.compile(r"<link\b[^>]*\brel\s*=\s*[\"']?stylesheet\b[^>]*>", re.I)
HREF = re.compile(r"\bhref\s*=\s*(?:\"([^\"]+)\"|'([^']+)'|([^\s>]+))", re.I)
CJK_CHAR = re.compile(r"[\u3400-\u9fff\uf900-\ufaff\u3000-\u303f\uff00-\uffef]")
# 画面上的文字：<body> 里标签之间的文字，加上脚本与样式里的字符串（JS 设的 textContent、CSS 的 content）。
# 注释、<title>、标签属性不算。
HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
SCRIPT_OR_STYLE = re.compile(r"<(script|style)\b[^>]*>(.*?)</\1\s*>", re.S | re.I)
BODY = re.compile(r"<body\b[^>]*>(.*)</body\s*>", re.S | re.I)
TAG = re.compile(r"<[^>]+>")

# 允许出现的颜色字面量（阴影/透视遮罩，不参与配色语义）
ALLOWED_RGBA_PREFIX = ("rgba(0,0,0", "rgba(0, 0, 0", "rgba(255,255,255", "rgba(255, 255, 255")

# 第 3 条的颜色上下文（colour_spans）。CSS 里先抹掉注释、引号里的字符串、url(…)（url(#fade) 引用的是渐变 id），
# 再只看声明的值：选择器与 @规则的前导不算（#bd1 { … } 是 id 选择器，lessons #108）。
CSS_NOISE = re.compile(r"/\*.*?\*/|\"(?:\\.|[^\"\\\n])*\"|'(?:\\.|[^'\\\n])*'"
                       r"|\burl\(\s*(?:\"[^\"]*\"|'[^']*'|[^)]*)\)", re.S | re.I)
URL_REF = re.compile(r"\burl\([^)]*\)", re.I)
STYLE_ATTR = re.compile(r"(?<![\w-])style\s*=\s*(?:\"([^\"]*)\"|'([^']*)')", re.I)
# 标签属性 name="…" / name='…' / name=…；名字是否算颜色类由 colourish 判
MARKUP_ATTR = re.compile(r"(?<![\w-])(-{0,2}[A-Za-z][\w-]*)\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s\"'>]+))")
# 脚本里颜色类属性的名字：{ fill: … } / { 'stop-color': … } / el.style.color = … / ctx.fillStyle = …（== 与 => 不算）
JS_PROP = re.compile(r"(?<![\w$-])[\"']?(-{0,2}[A-Za-z][\w-]*)[\"']?\s*(?::|=(?![=>]))\s*")
# setAttribute('fill', …) / style.setProperty('--accent', …) / gsap.set(el, 'fill', …)
JS_PROP_ARG = re.compile(r"[\"'](-{0,2}[A-Za-z][\w-]*)[\"']\s*,\s*")
# 名字后面的值：到同一层的 , ; } 或换行为止，字符串整串跳过（三元的两支、linear-gradient(#a, #b) 都在里面）。
# 名字与值分开找：{ attr: { fill: … } } 里 attr 的值不能把里面的 fill 吞掉
JS_VALUE = re.compile(r"(?:\"[^\"\n]*\"|'[^'\n]*'|`[^`]*`|[^,;}\n\"'`])*")
# 调色板：整串就是 6 / 8 位色值的字符串（var CREAM = "#F4EFE4"）。3 / 4 位的「#c13」「#bd1」多半是 id，只在颜色类属性旁边报
HEX_WHOLE = re.compile(r"#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{8})")


def colourish(name: str) -> bool:
    """属性名是不是颜色类：fill / stroke / *color / *shadow / background* / border* / outline* / filter /
    fillStyle / strokeStyle，以及 CSS 自定义属性 --*。带不带连字符、data- 与浏览器前缀都认；大写开头的不算
    （CSS 与 JS 的属性名都是小写开头，「Color: #1」这类是画面文字）。"""
    if name.startswith("--"):
        return True
    n = re.sub(r"^-(?:webkit|moz|ms|o)-", "", name)
    if n.startswith("data-"):
        n = n[5:]
    if not n[:1].islower():
        return False
    n = n.replace("-", "").lower()
    return (n in ("fillstyle", "strokestyle", "filter") or n.endswith(("color", "shadow", "fill", "stroke"))
            or n.startswith(("background", "border", "outline")))


def check_file(path: str, want_w: int, want_h: int, is_cover: bool = False, lang: str = "zh") -> list[str]:
    src = open(path, encoding="utf-8").read()
    name = os.path.basename(path)
    out: list[str] = []
    sheets = local_refs(path, src, out, "样式表")
    sheet_out: dict[str, list[str]] = {}    # 出自共享样式表的问题记在样式表名下：几个帧共用一份，只报一次
    frame_and_css = "\n".join([src] + [text for _, text in sheets])

    # 1. 画布（封面有自己的规格：16:9 版 1920×1080 / 3:4 版 1440×1080）
    if is_cover:
        if not re.search(r"width\s*:\s*\d+px", frame_and_css) or not re.search(r"height\s*:\s*\d+px", frame_and_css):
            out.append("封面：未见 width/height 像素声明")
    elif not re.search(rf"width\s*:\s*{want_w}px", frame_and_css) or not re.search(rf"height\s*:\s*{want_h}px", frame_and_css):
        out.append(f"画布：未见 width:{want_w}px / height:{want_h}px 声明")

    # 2. 外部字体 / 远程资源
    if FONT_LINK.search(src):
        out.append("外部 <link href=http…>（字体/样式）→ 违反契约 2（渲染器 5s 字体硬顶）")
    for label, text in [(None, src)] + sheets:
        if IMPORT_HTTP.search(text):
            (sheet_out.setdefault(label, []) if label else out).append("@import url(http…) → 违反契约 2")
    if GSAP_CDN.search(src):
        out.append("<script src=http…> → 违反契约 8（必须用 ../assets/gsap.min.js）")

    # 3. 色值字面量（帧本身 + 引用的本地样式表），只在颜色上下文里找（colour_spans，lessons #151）
    for label, text in [(None, src)] + sheets:
        dest = sheet_out.setdefault(label, []) if label else out
        for _, val in colour_spans(text, is_css=label is not None):
            if val.startswith("#"):
                dest.append(f"色值字面量 {val} → 违反契约 3（颜色只取 theme.css 变量）")
                continue
            v = val.replace(" ", "").lower()
            if v.startswith(ALLOWED_RGBA_PREFIX):
                continue
            if "var(" in v:      # rgba(var(--signal-rgb), .4) —— 取自主题变量，合规
                continue
            dest.append(f"色值字面量 {val} → 违反契约 3")

    # 4/5. 时间轴 + GSAP（封面是静帧，没有时间轴，不作要求）
    if not is_cover and "window.__tl" not in src and "window.__timelines" not in src:
        out.append("未见 window.__tl 注册 → 渲染器找不到时间轴（契约 4）")
    if "gsap" in src.lower() and "../assets/gsap.min.js" not in src:
        out.append("用了 gsap 但没引 ../assets/gsap.min.js")

    # 6/7. 墙钟 / transition
    if WALLCLOCK.search(src):
        out.append("疑似墙钟逻辑（setInterval / setTimeout / rAF）→ seek 渲染下必然失控")
    for label, text in [(None, src)] + sheets:
        if CSS_TRANSITION.search(text):
            (sheet_out.setdefault(label, []) if label else out).append("CSS transition: → 逐帧 seek 不生效，入场必须用 GSAP")

    # 8. B() 兜底
    if B_FALLBACK.search(src):
        out.append("B('…') || 数字 兜底写法 → 禁止（beats 缺失要在构建期暴露）")

    # 9. 字幕带禁区（启发式，只看帧自己的 CSS）：共享样式表里的 bottom 多是卡片内边距一类的相对定位，
    #    逐帧报会把一条规则报成几十条；字幕带的几何以 check_layout 实测为准（lessons #148）
    for m in BOTTOM_RULE.finditer(src):
        v = int(m.group(1))
        unit = m.group(2)
        if unit == "px" and 0 <= v < 176:
            # 取该行上下文，判断是不是有内容的容器
            line_start = src.rfind("\n", 0, m.start()) + 1
            line_end = src.find("\n", m.end())
            line = src[line_start:line_end].strip()
            out.append(f"bottom:{v}px 落在字幕带/进度条禁区 → {line[:70]}")
    for m in re.finditer(r"bottom\s*:\s*calc\([^)]*\)", src):
        out.append(f"bottom:calc(…) 请人工核对是否 ≥176px → {m.group(0)[:70]}")

    # 10. 中文字体栈：画面里有中文才需要（英文片不必为它挂中文字体，lessons #148）
    needs_cjk = lang.lower().startswith("zh") or CJK_CHAR.search(shown_text(src, sheets)) is not None
    if needs_cjk and CJK_FONT.search(frame_and_css) is None:
        out.append("字体栈里没看到中文字体族 → 可能落到默认字体")

    # 11. 搭建期读布局（帧内联脚本 + 引用的本地脚本）
    for label, text in [(None, src)] + local_refs(path, src, out, "脚本"):
        where = f"（{label}）" if label else ""
        m = LAYOUT_READ.search(text)
        if m:
            api = f"{m.group(1)}()" if m.group(1) else m.group(2) + m.group(3)
            out.append(f"搭建期读布局 {api}{where} → 并行渲染（--workers）下各进程量到的值"
                       f"不一致，成片每 N 帧整块跳一下；改成生成期量好写成常量（frame-contract.md「渲染期一致性」）")
        if label and WALLCLOCK.search(text):
            out.append(f"疑似墙钟逻辑（setInterval / setTimeout / rAF）{where} → seek 渲染下必然失控")

    return [f"{name}: {x}" for x in sorted(set(out))] + \
        [f"{ref}: {x}" for ref, xs in sheet_out.items() for x in sorted(set(xs))]


def scan_code(code: str) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    """脚本或样式逐字扫一遍，返回（注释的区间，字符串字面量内容的区间）。
    引号里的 // 和 /* 不是注释，注释里的引号也不是字符串。"""
    comments, strings, i, n = [], [], 0, len(code)
    while i < n:
        two = code[i:i + 2]
        if two == "/*":
            j = code.find("*/", i + 2)
            j = n if j < 0 else j + 2
            comments.append((i, j))
            i = j
        elif two == "//" and (i == 0 or code[i - 1] != ":"):      # url(http://…) 里的 // 不是注释
            j = code.find("\n", i)
            j = n if j < 0 else j
            comments.append((i, j))
            i = j + 1
        elif code[i] in "\"'`":
            q, j = code[i], i + 1
            while j < n and code[j] != q:
                j += 2 if code[j] == "\\" else 1
            strings.append((i + 1, min(j, n)))
            i = j + 1
        else:
            i += 1
    return comments, strings


def code_strings(code: str) -> list[str]:
    """脚本或样式里的字符串字面量。"""
    return [code[a:b] for a, b in scan_code(code)[1]]


def _blank(text: str, spans: list[tuple[int, int]]) -> str:
    """把这些区间抹成空格（换行留着），长度不变 —— 后面找到的位置仍是原文的位置。"""
    chars = list(text)
    for a, b in spans:
        for i in range(a, min(b, len(chars))):
            if chars[i] != "\n":
                chars[i] = " "
    return "".join(chars)


def _literals(text: str, a: int, b: int) -> list[tuple[int, str]]:
    """text[a:b] 里的 #hex 与 rgb()/rgba()，[(原文位置, 字面量)]；url(…) 里的不算。"""
    seg = URL_REF.sub(lambda m: " " * len(m.group(0)), text[a:b])
    return [(a + m.start(), m.group(0)) for pat in (HEX, RGBA) for m in pat.finditer(seg)]


def css_value_spans(text: str, a: int, b: int, inline: bool = False) -> list[tuple[int, str]]:
    """text[a:b] 是一段 CSS（inline=True 时是 style="…" 里的声明表）：只看声明的值。
    注释、引号里的字符串（content: "#1" 是画面文字）、url(…) 先抹掉；选择器与 @规则的前导不看。"""
    seg = CSS_NOISE.sub(lambda m: " " * len(m.group(0)), text[a:b])
    out, depth, start = [], (1 if inline else 0), 0
    for i, c in enumerate(seg + ";"):        # 补一个 ;：style="…" 的最后一条声明可以不带分号
        if c == "{":
            depth, start = depth + 1, i + 1     # { 前面是选择器 / @media 前导
        elif c in ";}":
            colon = seg.find(":", start, i)
            if depth > 0 and colon >= 0:
                out += _literals(seg, colon + 1, i)
            depth, start = (max(depth - 1, 0) if c == "}" else depth), i + 1
    return [(a + p, v) for p, v in out]


def script_value_spans(code: str, a: int, b: int) -> list[tuple[int, str]]:
    """code[a:b] 是一段（注释已抹掉的）脚本：颜色类属性的值、整串 6 / 8 位色值的字符串、字符串里的 rgb()/rgba()。"""
    out = []
    for pat in (JS_PROP, JS_PROP_ARG):
        for m in pat.finditer(code, a, b):
            if colourish(m.group(1)):
                v = JS_VALUE.match(code, m.end(), b)
                out += _literals(code, v.start(), v.end())
    for s, e in scan_code(code[a:b])[1]:
        lit = code[a + s:a + e]
        if HEX_WHOLE.fullmatch(lit):
            out.append((a + s, lit))
        out += [(a + s + m.start(), m.group(0)) for m in RGBA.finditer(lit)]
    return out


def colour_spans(text: str, is_css: bool = False) -> list[tuple[int, str]]:
    """第 3 条要查的色值字面量：[(原文位置, 字面量)]，按位置排好、去重（lessons #151）。

    只在颜色上下文里找：
      - CSS 声明的值：<style> 块、style="…" 属性、本地样式表（is_css=True）；
      - 颜色类的标签属性：fill / stroke / stop-color / flood-color / color / bgcolor …（colourish）；
      - 脚本：赋给颜色类属性的值（{ fill: … }、el.style.color = …、setAttribute('fill', …)），
        整串就是 #rrggbb / #rrggbbaa 的字符串（调色板），字符串里的 rgb()/rgba()；
      - 脚本里拼的标记与 CSS 文本（innerHTML 里的 style="…"、fill="…"，cssText）同样按上面几条查。
    不查：注释（HTML / CSS / JS）、画面文字、选择器（#c13 { … }、querySelector('#c13')、gsap.to('#c13')）、
    别的字符串（格名「#301–」）、url(#fade)。"""
    if is_css:
        return sorted(set(css_value_spans(text, 0, len(text))))
    t = _blank(text, [m.span() for m in HTML_COMMENT.finditer(text)])
    out, scripts, gone = [], [], []
    for m in SCRIPT_OR_STYLE.finditer(t):
        a, b = m.start(2), m.end(2)
        if m.group(1).lower() == "style":
            out += css_value_spans(t, a, b)
            gone.append((a, b))           # 后面按标签属性扫时不再看 <style> 里的选择器
        else:
            gone += [(a + s, a + e) for s, e in scan_code(t[a:b])[0]]     # 脚本注释
            scripts.append((a, b))
    t = _blank(t, gone)
    for a, b in scripts:
        out += script_value_spans(t, a, b)
    for m in STYLE_ATTR.finditer(t):
        g = 1 if m.group(1) is not None else 2
        out += css_value_spans(t, m.start(g), m.end(g), inline=True)
    for m in MARKUP_ATTR.finditer(t):
        g = next(i for i in (2, 3, 4) if m.group(i) is not None)
        if m.group(1).lower() != "style" and colourish(m.group(1)):
            out += _literals(t, m.start(g), m.end(g))
    return sorted(set(out))


def shown_text(src: str, sheets: list[tuple[str, str]]) -> str:
    """画面上可能出现的文字：<body> 里标签之间的文字 + 脚本与样式里的字符串字面量（textContent、CSS 的 content）。
    注释、<title>、标签属性不算 —— 中文注释不上画面。用来判断要不要中文字体族（lessons #148）。"""
    html = HTML_COMMENT.sub("", src)
    code = [m.group(2) for m in SCRIPT_OR_STYLE.finditer(html)] + [text for _, text in sheets]
    m = BODY.search(html)
    markup = TAG.sub(" ", SCRIPT_OR_STYLE.sub("", m.group(1) if m else html))
    return "\n".join([markup] + [lit for c in code for lit in code_strings(c)])


def local_refs(path: str, src: str, out: list[str], kind: str) -> list[tuple[str, str]]:
    """帧引用的本地脚本或样式表 [(相对路径, 内容)]；不存在的记进 out。远程的由第 2、5 条管。
    脚本跳过技能自带的 gsap.min.js / motion.js 与 *.beats.js；样式表跳过 theme.css（色值的唯一来源）。"""
    if kind == "脚本":
        refs = [m.group(1) for m in LOCAL_SCRIPT.finditer(src)]
        skip = lambda ref: VENDOR_SCRIPT.search(ref)
    else:
        refs = [next(g for g in m.groups() if g) for m in map(HREF.search, LOCAL_STYLE.findall(src)) if m]
        skip = lambda ref: os.path.basename(ref) == "theme.css"
    found = []
    for raw in refs:
        ref = raw.split("?")[0]
        if re.match(r"^[a-z]+:", ref, re.I) or skip(ref):
            continue
        p = os.path.normpath(os.path.join(os.path.dirname(path), ref))
        if not os.path.isfile(p):
            out.append(f"引用的本地{kind}不存在：{ref}")
            continue
        found.append((ref, open(p, encoding="utf-8", errors="replace").read()))
    return found


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=".")
    ap.add_argument("--only", default="")
    ap.add_argument("--include-covers", action="store_true")
    args = ap.parse_args()

    proj = os.path.abspath(args.project)
    pj = json.load(open(os.path.join(proj, "project.json"), encoding="utf-8"))
    W, H = pj.get("width", 1920), pj.get("height", 1080)

    frames_dir = os.path.join(proj, "frames")
    names = sorted(f for f in os.listdir(frames_dir) if f.endswith(".html"))
    # 封面一律默认跳过（封面有独立质检 check_cover.mjs）。
    # ★ 不要写成 {"cover_169.html","cover_34.html"} —— 那样 9:16 等第三张封面
    #   会被当成普通帧去验契约（缺 window.__tl）而误报，必现假失败。
    skip = {"_template.html"}
    only = {s.strip() for s in args.only.split(",") if s.strip()}
    names = [n for n in names
             if n not in skip
             and (args.include_covers or not n.startswith("cover_"))
             and (not only or n[:-5] in only)]

    all_issues: list[str] = []
    for n in names:
        all_issues += check_file(os.path.join(frames_dir, n), W, H, is_cover=n.startswith("cover_"),
                                 lang=str(pj.get("lang", "zh")))

    all_issues = list(dict.fromkeys(all_issues))     # 共享样式表的问题，每个引用它的帧都会带回来一份
    print(f"[lint] 检查 {len(names)} 个帧 · 画布 {W}×{H}")
    if not all_issues:
        print("[lint] ✓ 无契约违规")
        return 0
    for line in all_issues:
        print("  ✗", line)
    print(f"[lint] 共 {len(all_issues)} 条待确认")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
