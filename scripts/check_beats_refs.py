#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
check_beats_refs.py —— B()/Be() 节拍引用的**构建期校验**（渲染前跑，秒级出结果）。

为什么需要它
------------
`B()` 的匹配规则是**前缀匹配**：
    bt === t  ||  bt.startsWith(t)  ||  t.startsWith(bt)      （归一化后）
也就是 text 必须是**块文本的开头**，或者块文本本身以 text 开头。取中间一段会抛错：

    块文本「一位父亲写下《被网游毁掉的孩子》」→  B('一位父亲写下') ✓    B('被网游毁掉的孩子') ✗

归一化会去掉 ，。、！？；：,.!?;:| 与空白（**保留词里的点**：左右都是数字或 ASCII 字母，2.4% / X.509；**不去《》「」**）。
拿不准就抄块文本的前 6–10 个字。

匹配到不止一块时：**一模一样的块优先**，没有一模一样的取第一个匹配的块（lessons #153，与 beats.js 的 find() 同一条规则，
本脚本的 resolve() 就是它）。2.2.3 以前 beats.js 取第一个匹配的块：块「提交；」排在「提交之后，」前面，
B('提交之后') 落在「提交；」，后一块用 B() 根本落不到 —— 而本脚本只问「有没有块匹配」，照样 OK。

  · 匹配到不止一块、又没有一模一样的 → **歧义，算错**（退出码 1）：落在哪块取决于块的先后，解说词一改切块就可能换一块，
    不报错。报告写明每块为什么匹配（块文本以它开头 / 块文本是它的前缀）、要那一块最短怎么写。
    一个系列 38 集交付的 7402 个 B()/Be() 里这种调用是 0 个，所以算错不会让交付过的集变红。
  · 一模一样的块不止一块 → 提醒（不算错）：B() 只落得到第一块，后面那块要用它前一块的 Be()，或者改词。

而 `B()` 抛错**只在渲染到那一帧时才发生** —— 前面几百帧白渲，长片要等十几分钟才炸。
这类「只在运行时才炸的静态错误」应当有一个构建期出口，这就是本脚本。

用法：
    python check_beats_refs.py --project .                  # 全量
    python check_beats_refs.py --project . --only hook_q,r1_data
退出码：有失配或歧义 = 1，全绿 = 0（提醒不影响；可直接串进 CI / 流水线 && 链）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import List, Optional, Tuple

# 同时抓 B('…') 与 Be('…')，单双引号、内部可含转义
CALL = re.compile(r"\bB(?:e)?\(\s*(['\"])((?:\\.|(?!\1).)*)\1\s*\)")
BEATS_DATA = re.compile(r"window\.__BEATS__\s*=\s*(\[.*?\])\s*;", re.S)


def norm(s: str) -> str:
    """与 subs.py 生成的 beats.js 里的 norm() 逐字对齐（改一处等于没改）。"""
    s = re.sub(r"([0-9A-Za-z])\.(?=[0-9A-Za-z])", lambda m: m.group(1) + "\ue000", s)
    s = re.sub(r"[\s，。、！？；：,.!?;:|]", "", s)
    return s.replace("\ue000", ".")


def unescape(q: str) -> str:
    """把 JS 字符串字面量里的 \\' \\" \\\\ 还原成原字符，否则带撇号的查询会假失配。"""
    return re.sub(r"\\(.)", lambda m: {"n": "\n", "t": "\t"}.get(m.group(1), m.group(1)), q)


def load_beats(path: str) -> List[dict]:
    if not os.path.exists(path):
        return []
    m = BEATS_DATA.search(open(path, encoding="utf-8").read())
    return json.loads(m.group(1)) if m else []


def matches(nb: List[str], t: str) -> List[Tuple[int, str]]:
    """t 匹配到的块（按块的先后）：(下标, 方式)，方式是 exact（一模一样）/ starts（块文本以它开头）/
    prefix（块文本是它的前缀，即 t 多写了尾巴）。nb 与 t 都是归一化过的。"""
    out = []
    for i, bt in enumerate(nb):
        if bt == t:
            out.append((i, "exact"))
        elif bt.startswith(t):
            out.append((i, "starts"))
        elif t.startswith(bt):
            out.append((i, "prefix"))
    return out


def resolve(nb: List[str], t: str) -> Optional[int]:
    """B(t) / Be(t) 落到的块下标：一模一样的块优先，没有就取第一个匹配的块；都不匹配 None。
    与 beats.js 的 find()（subs.py 的 BEATS_TMPL、new_project.py 的占位 beats）逐字同一条规则。"""
    hits = matches(nb, t)
    for i, how in hits:
        if how == "exact":
            return i
    return hits[0][0] if hits else None


def ambiguous(nb: List[str], t: str) -> bool:
    """匹配到不止一块、又没有一模一样的块：落在哪块只看块的先后。"""
    hits = matches(nb, t)
    return len(hits) > 1 and all(how != "exact" for _, how in hits)


def shortest_ref(nb: List[str], j: int) -> Optional[str]:
    """要落到第 j 块、又不歧义的最短写法（第 j 块归一化文本的开头）；落不到（前面有一模一样的块）返回 None。"""
    for n in range(1, len(nb[j]) + 1):
        p = nb[j][:n]
        if resolve(nb, p) == j and not ambiguous(nb, p):
            return p
    return None


def as_written(text: str, ref: str) -> str:
    """把归一化的写法还原成块原文的开头（「拿ZooKeeper来」→「拿 ZooKeeper 来」），对不上就原样返回。"""
    for n in range(1, len(text) + 1):
        if norm(text[:n]) == ref:
            return text[:n]
    return ref


def js_quote(q: str) -> str:
    """放进单引号字面量：反斜杠与撇号要转义（英文片的「Let's」），unescape() 的逆。"""
    return q.replace("\\", "\\\\").replace("'", "\\'")


HOW = {"starts": "块文本以它开头", "prefix": "块文本是它的前缀"}
# 块是首尾相接的（subs.py：前一块的收尾 = 这一块的起点），落不到的那块从邻块取时刻
UNREACHABLE = {"B": "要后面那块开始的时刻，写它前一块的 Be()，或者改词",
               "Be": "要后面那块收尾的时刻，写它后一块的 B()（末块用 __SEG__.speech_end），或者改词"}


def check_one(fdir: str, sid: str) -> Tuple[int, List[str]]:
    """返回 (失配数 + 歧义数, 报告行)。"""
    miss, amb, lines = check_scene(fdir, sid)
    return miss + amb, lines


def check_scene(fdir: str, sid: str) -> Tuple[int, int, List[str]]:
    """返回 (失配数, 歧义数, 报告行)。"""
    hp = os.path.join(fdir, sid + ".html")
    if not os.path.exists(hp):
        return 0, 0, [f"  ?  {sid:<14} 无帧文件（跳过）"]

    beats = load_beats(os.path.join(fdir, sid + ".beats.js"))
    texts = [b.get("text", "") for b in beats]
    nb = [norm(t) for t in texts]
    src = open(hp, encoding="utf-8").read()
    calls = []
    for m in CALL.finditer(src):
        fn = "Be" if m.group(0).startswith("Be") else "B"
        calls.append((fn, unescape(m.group(2)), src.count("\n", 0, m.start()) + 1))

    errs: List[str] = []
    amb: List[str] = []
    warns: List[str] = []

    if calls and not nb:
        errs.append("本帧有 B() 调用，但 frames/%s.beats.js 不存在或为空 —— 先跑 subs.py" % sid)
    for fn, q, line in calls:
        t = norm(q)
        hits = matches(nb, t)
        if not hits:
            errs.append(
                "B('%s') 在本帧块表里解析不到（前缀匹配失败）\n"
                "          本帧可用块：%s" % (q, texts)
            )
        elif ambiguous(nb, t):
            msg = ["第 %d 行 %s('%s') 有歧义：匹配到 %d 块、没有一模一样的，落在第 %d 块「%s」（排在前面）"
                   % (line, fn, q, len(hits), hits[0][0] + 1, texts[hits[0][0]])]
            for i, how in hits:
                ref = shortest_ref(nb, i)
                fix = ("写 %s('%s')" % (fn, js_quote(as_written(texts[i], ref)))) if ref is not None \
                    else "落不到（和前面一块一模一样）；" + UNREACHABLE[fn]
                msg.append("第 %d 块「%s」（%s）→ %s" % (i + 1, texts[i], HOW[how], fix))
            amb.append("\n          ".join(msg))
        else:
            same = [i for i, how in hits if how == "exact"]
            if len(same) > 1:
                warns.append("第 %d 行 %s('%s')：第 %s 块一模一样（「%s」），只落得到第 %d 块；%s"
                             % (line, fn, q, "、".join(str(i + 1) for i in same), texts[same[0]], same[0] + 1,
                                UNREACHABLE[fn]))

    bad = len(errs) + len(amb)
    tag = "OK " if not bad else "ERR"
    lines: List[str] = [
        f"  {tag} {sid:<14} B()/Be() 调用 {len(calls):2d} · 命中 {len(calls) - bad:2d} · 失配 {len(errs):2d} · 歧义 {len(amb):2d}"
    ]
    for e in errs + amb:
        lines.append(f"        ✗ {e}")
    for w in warns:
        lines.append(f"        ⚠ {w}")
    return len(errs), len(amb), lines


def main() -> int:
    ap = argparse.ArgumentParser(description="构建期校验 B()/Be() 节拍引用")
    ap.add_argument("--project", default=".")
    ap.add_argument("--only", default="", help="逗号分隔的场景 id；缺省 = 全量")
    args = ap.parse_args()

    fdir = os.path.join(args.project, "frames")
    if not os.path.isdir(fdir):
        print(f"[beats] 找不到 {fdir}", file=sys.stderr)
        return 2

    want = [s.strip() for s in args.only.split(",") if s.strip()]
    ids = want or sorted(
        f[:-5] for f in os.listdir(fdir)
        if f.endswith(".html") and not f.startswith("_")
    )

    miss = amb = 0
    for sid in ids:
        m, a, lines = check_scene(fdir, sid)
        miss += m
        amb += a
        for ln in lines:
            print(ln)

    if miss or amb:
        print(f"\n[beats] ✗ 失配 {miss} 处、歧义 {amb} 处 —— B() 只在前缀处命中，改抄块文本的前 6–10 个字；"
              f"歧义按报告里那一块的写法改")
    else:
        print(f"\n[beats] ✓ {len(ids)} 个场景的节拍引用全部可解析")
    return 1 if miss or amb else 0


if __name__ == "__main__":
    raise SystemExit(main())
