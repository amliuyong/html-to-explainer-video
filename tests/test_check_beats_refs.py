#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""check_beats_refs 的回归测试（python tests/test_check_beats_refs.py）。

守两件事（lessons #153）：

  ① 构建期校验和 beats.js 落到同一块。B() 的规则是「一模一样的块优先，没有就取第一个前缀匹配的块」；
     2.2.3 以前 beats.js 取第一个匹配的块，check_beats_refs 只问「有没有块匹配」—— 一个系列 08-6 eo4 的
     B('提交之后') 落到了前面的「提交；」，校验照样 OK。
  ② 匹配到不止一块、又没有一模一样的那块时报歧义（退出码 1），写明每块怎么写才落得到；
     一模一样的块不止一块时提醒（不算失败）：B() 只落得到第一块。

A 段只用 Python；B 段用 subs.write_beats 写真的 beats.js，在 node 里跑 B()，和 resolve() 逐条对
（缺 node 跳过 B 段；HX_NO_SKIP=1 时不许跳过）。失败即以非零码退出。
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, rel))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cbr = _load("check_beats_refs", "scripts/check_beats_refs.py")
subs = _load("subs", "scripts/subs.py")
fails: list[str] = []


def check(label, got, want):
    if got != want:
        fails.append(f"{label}\n    得到: {got!r}\n    应为: {want!r}")
    else:
        print(f"  OK  {label}")


EO4 = ["先扣余额，", "再改订单，", "两边都写好，", "协调者说：", "可以提交了，", "大家各自", "提交；", "提交之后，", "才算数。"]
ZK = ["拿 ZooKeeper 来说，", "拿 ZooKeeper 举例，", "对。", "好的，", "对。"]


def seg(sid, texts):
    return {"id": sid, "duration_sec": 0.6 * len(texts) + 0.5, "speech_end_sec": 0.6 * len(texts),
            "tail_silence_sec": 0.5,
            "blocks": [{"spoken": t, "local_start_sec": round(0.6 * i, 3), "global_start_sec": round(0.6 * i, 3),
                        "global_end_sec": round(0.6 * i + 0.5, 3)} for i, t in enumerate(texts)]}


def project(d, scenes):
    """scenes = {sid: (块文本, 帧里的调用)}；beats.js 用 subs.write_beats 写（脚本真源）。"""
    os.makedirs(os.path.join(d, "frames"), exist_ok=True)
    for sid, (texts, calls) in scenes.items():
        subs.write_beats(os.path.join(d, "frames", f"{sid}.beats.js"), seg(sid, texts))
        body = "\n".join(f"tl.to('.a', {{opacity: 1}}, {c});" for c in calls)
        with open(os.path.join(d, "frames", f"{sid}.html"), "w", encoding="utf-8") as fh:
            fh.write(f"<script src=\"{sid}.beats.js\"></script>\n<script>\n{body}\n</script>\n")
    return d


def run_main(d, *extra):
    argv = sys.argv
    sys.argv = ["check_beats_refs.py", "--project", d, *extra]
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            code = cbr.main()
    finally:
        sys.argv = argv
    return code, buf.getvalue()


# ---------------- A. resolve / 歧义 / 报告 ----------------
print("A. resolve() 与 check_one()")
nb = [cbr.norm(t) for t in EO4]
check("一模一样的块优先：「提交之后」落在第 8 块，不落在前面的「提交；」", cbr.resolve(nb, cbr.norm("提交之后")), 7)
check("前一块自己一模一样：「提交；」落在第 7 块", cbr.resolve(nb, cbr.norm("提交；")), 6)
check("没有一模一样的块：取第一个匹配的（和 beats.js 一致）", cbr.resolve(nb, cbr.norm("提交之")), 6)
check("块文本以它开头，只有一块", cbr.resolve(nb, cbr.norm("协调者")), 3)
check("取中间一段：落不到", cbr.resolve(nb, cbr.norm("余额")), None)
check("「提交之」有歧义（两块匹配、没有一模一样的）", cbr.ambiguous(nb, cbr.norm("提交之")), True)
check("「提交之后」不算歧义（有一模一样的块）", cbr.ambiguous(nb, cbr.norm("提交之后")), False)
check("「提交」不算歧义（第 7 块一模一样）", cbr.ambiguous(nb, cbr.norm("提交")), False)
check("最短写法：第 8 块要写全「提交之后」（更短的都会先撞上「提交」）", cbr.shortest_ref(nb, 7), "提交之后")
check("最短写法：第 7 块「提交」", cbr.shortest_ref(nb, 6), "提交")
zk = [cbr.norm(t) for t in ZK]
check("同一个开头的两块：「拿ZooKeeper」有歧义", cbr.ambiguous(zk, cbr.norm("拿 ZooKeeper")), True)
check("最短写法：多写一个字就分开了", (cbr.shortest_ref(zk, 0), cbr.shortest_ref(zk, 1)),
      ("拿ZooKeeper来", "拿ZooKeeper举"))
check("一模一样的两块：后一块落不到（None）", (cbr.shortest_ref(zk, 2), cbr.shortest_ref(zk, 4)), ("对", None))

with tempfile.TemporaryDirectory() as d:
    project(d, {
        "eo4": (EO4, ["B('提交之后')", "Be('提交；')", "B('才算')"]),
        "amb": (EO4, ["B('先扣')", "Be('提交之')"]),
        "zk": (ZK, ["B('拿 ZooKeeper')"]),
        "dup": (ZK, ["B('对。')", "B('好的')"]),
        "miss": (EO4, ["B('余额')"]),
        "en": (["Let's say, ", "Let's see."], ["B(\"Let's\")"]),
    })
    fdir = os.path.join(d, "frames")
    n, lines = cbr.check_one(fdir, "eo4")
    out = "\n".join(lines)
    check("一模一样的块优先之后，eo4 三条都不算错", n, 0)
    check("报告行格式不变（下游按「失配  0」判全绿）", "失配  0" in out and "歧义  0" in out, True)

    n, lines = cbr.check_one(fdir, "amb")
    out = "\n".join(lines)
    check("歧义算错（ERR）", (n, out.split("\n")[0].strip().startswith("ERR")), (1, True))
    check("歧义：写明调用、所在行和落到的块", "第 4 行 Be('提交之')" in out and "落在第 7 块「提交；」" in out, True)
    check("歧义：每块为什么匹配、怎么写才落得到",
          "第 7 块「提交；」（块文本是它的前缀）→ 写 Be('提交')" in out
          and "第 8 块「提交之后，」（块文本以它开头）→ 写 Be('提交之后')" in out, True)

    n, lines = cbr.check_one(fdir, "zk")
    out = "\n".join(lines)
    check("同一个开头的两块：报歧义，给两块各自的写法（按块原文写，空格留着）",
          (n, "B('拿 ZooKeeper 来')" in out and "B('拿 ZooKeeper 举')" in out), (1, True))

    n, lines = cbr.check_one(fdir, "en")
    check("写法里的撇号转义好，能直接贴回单引号里", (n, "B('Let\\'s sa')" in "\n".join(lines)), (1, True))

    n, lines = cbr.check_one(fdir, "dup")
    out = "\n".join(lines)
    check("一模一样的块不止一块：不算错，但提醒", (n, "⚠" in out and "第 3、5 块" in out), (0, True))
    check("提醒写明 B() 只落得到第一块，后面那块从前一块的 Be() 取", "只落得到第 3 块" in out and "写它前一块的 Be()" in out,
          True)

    n, lines = cbr.check_one(fdir, "miss")
    check("取中间一段照旧是失配", (n, "解析不到" in "\n".join(lines)), (1, True))

    code, out = run_main(d, "--only", "eo4,dup")
    check("全绿（有提醒）退出码 0", code, 0)
    code, out = run_main(d, "--only", "eo4,amb")
    check("有歧义退出码 1", code, 1)
    check("汇总写出歧义数", "歧义 1 处" in out, True)

# ---------------- B. 与 beats.js 落到同一块（node） ----------------
print("B. resolve() 与 beats.js 的 B() 逐条一致")
node = shutil.which("node")
if node is None and os.environ.get("HX_NO_SKIP") == "1":
    fails.append("缺 node，而 HX_NO_SKIP=1 不许跳过 B 段")
elif node is None:
    print("  SKIP 缺 node —— 只跑了 A 段")
else:
    queries = {
        "eo4": ["提交之后", "提交；", "提交之", "提交之后，才去", "提交", "协调者说", "可以", "大家各自提交", "才算数。"],
        "zk": ["拿 ZooKeeper", "拿 ZooKeeper 来说", "拿 ZooKeeper 举", "对", "好的，", "对。好的"],
    }
    with tempfile.TemporaryDirectory() as d:
        js = ("global.window = {}; require(process.argv[1]); var out = [];"
              "process.argv.slice(2).forEach(function (q) { try { out.push(window.B(q)); } catch (e) { out.push(null); } });"
              "console.log(JSON.stringify(out));")
        for sid, texts in (("eo4", EO4), ("zk", ZK)):
            p = os.path.join(d, f"{sid}.beats.js")
            subs.write_beats(p, seg(sid, texts))
            r = subprocess.run([node, "-e", js, p, *queries[sid]], capture_output=True, text=True)
            got = json.loads(r.stdout) if r.returncode == 0 else r.stderr
            nb2 = [cbr.norm(t) for t in texts]
            want = []
            for q in queries[sid]:
                i = cbr.resolve(nb2, cbr.norm(q))
                want.append(None if i is None else round(0.6 * i, 3))
            check(f"{sid}：{len(want)} 条查询落到同一块", got, want)

if fails:
    print("\n✗ 失败：")
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("\n✓ check_beats_refs 回归全部通过")
