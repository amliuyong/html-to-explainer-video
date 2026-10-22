#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""subs 文本规则的回归测试（零依赖，直接 python tests/test_subs_text.py）。

守的是一条不变式：

  去句读标点，但**词里的点不是标点**：点的左右都是数字或 ASCII 字母时留着
  （2.4% / 1.2.3 / ASN.1 / X.509 / v1.5 / node.js / S3.Select，lessons #88、#143）。

五个出口共用这一个定义 —— 屏幕字幕、SRT/VTT、B() 节拍匹配、火山长文本切句、
以及给下游读的 subs.json。任何一处把小数点当标点删掉，屏幕上就会出现
「念 2.4%、写 24%」这种差一个数量级的假数字，或者「ASN.1」写成「ASN1」；这类错不会报错，只会静静地骗人。

另守一条（lessons #145）：英文片（project.json 的 lang 是 en）里没有中文的块保留句内标点，只去块尾的 . , ; : …；
中文片一块都不变。

失败即以非零码退出（CI 直接吃退出码）。
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import re
import sys
import tempfile
from contextlib import redirect_stdout

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, rel))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


subs = _load("subs", "scripts/subs.py")
tts_volcano = _load("tts_volcano", "scripts/tts_volcano.py")

fails: list[str] = []


def check(label, got, want):
    if got != want:
        fails.append(f"{label}\n    得到: {got!r}\n    应为: {want!r}")
    else:
        print(f"  OK  {label}")


# ---- 屏幕文本：小数点保留 ----
print("屏幕文本 clean_sub_text")
check("数字间小数点保留", subs.clean_sub_text("涨了2.4%，注意"), "涨了2.4%注意")
check("纯小数", subs.clean_sub_text("2.4%"), "2.4%")
check("多位小数", subs.clean_sub_text("3.14159"), "3.14159")
check("版本号（两个点都在数字间）", subs.clean_sub_text("升到1.2.3"), "升到1.2.3")
check("英文数字（lang=en 保留词间空格）", subs.clean_sub_text("up 2.5x", "en"), "up 2.5x")
check("句号仍删（数字后不是数字）", subs.clean_sub_text("结束了."), "结束了")
check("省略号仍删", subs.clean_sub_text("等等..."), "等等")
check("前导小数点仍删", subs.clean_sub_text("口径.5"), "口径5")
check("逗号仍删（用户要求）", subs.clean_sub_text("1,234万"), "1234万")
check("冒号仍删（用户要求）", subs.clean_sub_text("12:30 开播"), "1230开播")
check("句号仍删", subs.clean_sub_text("甲、乙。"), "甲、乙")
check("全角数字也认", subs.clean_sub_text("涨２.４％"), "涨２.４％")
# ↓ 词里的点（lessons #143）：左右都是数字或 ASCII 字母就留着。2.1.2 只认数字，05-2 的「A S N.1」上屏成了「ASN1」
check("字母.数字（ASN.1，中文块去空格）", subs.clean_sub_text("1984 年的 A S N.1 就靠标签号演化"), "1984年的ASN.1就靠标签号演化")
check("字母.数字（X.509）", subs.clean_sub_text("X.509 证书，"), "X.509证书")
check("字母+数字.数字（v1.5）", subs.clean_sub_text("升到 v1.5。"), "升到v1.5")
check("字母.字母（node.js）", subs.clean_sub_text("用 node.js 写，"), "用node.js写")
check("数字.字母（S3.Select）", subs.clean_sub_text("S3.Select 只读要的列"), "S3.Select只读要的列")
check("英文块里的词内点", subs.clean_sub_text("Node.js and X.509 certs.", "en"), "Node.js and X.509 certs")
check("词末的句点仍删", subs.clean_sub_text("用的是 node.js."), "用的是node.js")
check("字母后接空格的点仍删", subs.clean_sub_text("e.g. 这样"), "e.g这样")
check("中文夹着的点仍删", subs.clean_sub_text("甲.乙"), "甲乙")
check("中文.数字仍删（前导小数点）", subs.clean_sub_text("版本.5"), "版本5")
# 逗号照删（lessons #88 用户拍板）：19 集交付的字幕里没有一处「1,000」写法，数字都写成 1000 / 32768
check("字母,字母 的逗号也删", subs.clean_sub_text("a,b"), "ab")

# ---- 对齐用的去标点：同一套规则 ----
print("对齐用 _PUNCT_RE")
check("小数点不被吃", subs._PUNCT_RE.sub("", "涨2.4%"), "涨2.4%")
check("句末点被吃", subs._PUNCT_RE.sub("", "结束."), "结束")
check("逗号被吃", subs._PUNCT_RE.sub("", "1,234"), "1234")
# 注意这个正则是「匹配用归一化」：连空格一起削（两侧同规则，所以照样对得上）。
# 屏幕文本那条路（clean_sub_text）才管空格，别把两者混为一谈。
check("中英混排（空格也归一化）", subs._PUNCT_RE.sub("", "a. 2.4% b"), "a2.4%b")
# ↓ 两条专门钉住「断言写在点之前还是之后」：写反了它们会静默通过。
# `X(?<!\d)` 断言的是 X 右边的字符，所以「点的左边是数字」根本管不住。
# （2.1.3 起字母也算词里的字，「a.2」要留，所以用中文当「不是词里的字」）
check("点左边是中文 → 删", subs._PUNCT_RE.sub("", "甲.2"), "甲2")
check("点右边是中文 → 删", subs._PUNCT_RE.sub("", "2.甲"), "2甲")
check("字母.数字 → 留（ASN.1）", subs._PUNCT_RE.sub("", "ASN.1"), "ASN.1")
check("字母.字母 → 留（node.js）", subs._PUNCT_RE.sub("", "用node.js。"), "用node.js")
check("词末的点 → 删", subs._PUNCT_RE.sub("", "X.509."), "X.509")
check("两点夹数字 → 留", subs._PUNCT_RE.sub("", "2.4"), "2.4")
check("点多侧数字（版本号）→ 全留", subs._PUNCT_RE.sub("", "1.2.3"), "1.2.3")

# ---- 火山长文本切句：不在小数点处切 ----
print("火山 split_long_text")
long_text = "营收增长2.4%，其中线上部分贡献了大头，这一段的文字故意写得很长" * 4
parts = tts_volcano.split_long_text(long_text, limit=40)
check("切分后无碎片以数字点收尾", any(p.endswith("2.") for p in parts), False)
check("切分不丢字（拼回等于原文）", "".join(parts), long_text)
check("短文本原样返回", tts_volcano.split_long_text("2.4%", limit=40), ["2.4%"])
tok_text = "证书用的是X.509，这一段的文字故意写得很长很长很长很长" * 4
tok_parts = tts_volcano.split_long_text(tok_text, limit=30)
check("不在 X.509 的点处切（字母.数字）", any(p.endswith("X.") for p in tok_parts), False)
check("切分不丢字（X.509）", "".join(tok_parts), tok_text)

# ---- 节拍模板：B() 的归一化不削小数点 ----
print("beats.js 模板自洽")
beats_tmpl = subs.BEATS_TMPL
check("模板含 norm()", "function norm(" in beats_tmpl, True)
check("norm 保护词里的点（左右是数字或字母）", "([0-9A-Za-z])\\.(?=[0-9A-Za-z])" in beats_tmpl, True)
check("模板仍会丢弃逗号冒号", ",.!?;:|" in beats_tmpl, True)


def _norm_src(text):
    m = re.search(r"function norm\(s\) \{.*?\n  \}", text, re.S)
    return m.group(0) if m else None


# lessons #88：B() 的归一化有两份模板（subs.py 与 new_project.py 的占位节拍），改一份漏一份。
# 两份都是普通三引号字符串，转义层数一样，所以直接比源码
def _src(rel):
    return open(os.path.join(ROOT, rel), encoding="utf-8").read()


check("new_project.py 占位节拍的 norm() 与 subs.py 一致",
      _norm_src(_src("scripts/new_project.py")), _norm_src(_src("scripts/subs.py")))

# ---- 英文片的英文块：句内标点留着，块尾的 . , ; : … 去掉（2026-10-22 用户拍板，lessons #145）----
print("英文片的屏幕文本 clean_sub_text(keep_punct=True)")
en = lambda t: subs.clean_sub_text(t, "en", keep_punct=True)
check("句内逗号留着", en("Today it takes Kafka, a warehouse,"), "Today it takes Kafka, a warehouse")
check("句内冒号留着", en("Declare a source: MySQL, PostgreSQL,"), "Declare a source: MySQL, PostgreSQL")
check("块尾句号去掉", en("should be queryable in minutes."), "should be queryable in minutes")
check("块尾冒号去掉", en("cove-lake:"), "cove-lake")
check("问号留着", en("Is it fast?"), "Is it fast?")
check("千分位逗号留着", en("1,000 changes per second."), "1,000 changes per second")
check("词里的点留着、省略号去掉", en("in 2.6 minutes..."), "in 2.6 minutes")
check("块尾的「…」也去掉", en("and then…"), "and then")
check("切块符与多余空白", en("one |  two"), "one two")
print("中文片不变（lessons #88）")
check("中文片里全是英文的块照旧全删", subs.clean_sub_text("Kafka, Flink, Paimon.", "en"), "Kafka Flink Paimon")
check("中文片里全角标点的英文块照旧全删", subs.clean_sub_text("Kafka，Paimon。", "en"), "KafkaPaimon")
check("中文块照旧全删", subs.clean_sub_text("工具：pandas，很好用。"), "工具pandas很好用")

print("subs.py main()：按 project.json 的 lang 选规则")


def _screen_texts(lang):
    """跑真的 subs.py，返回屏幕文本。词级时间戳每词 0.24 s（在 0.08 秒网格上）。"""
    chunks = ["Today it takes Kafka, a warehouse,", "and a lake to get there."]
    words = " ".join(chunks).split()
    bounds = [{"text": w, "offset_sec": round(0.24 * i, 2), "duration_sec": 0.24} for i, w in enumerate(words)]
    with tempfile.TemporaryDirectory() as proj:
        def put(name, obj):
            with open(os.path.join(proj, name), "w", encoding="utf-8") as f:
                json.dump(obj, f, ensure_ascii=False)
        put("project.json", {"slug": "t", "fps": 30, "lang": lang})
        put("narration.json", [{"id": "s1", "text": " |".join(chunks)}])
        put("audio-manifest.json", {"trimmed": True, "items": [
            {"id": "s1", "duration_sec": 3.4, "lead_cut_sec": 0.0, "container_duration_sec": 3.4,
             "speech_end_sec": 3.36, "boundaries": bounds}]})
        put("layout.json", {"s1": {"start_sec": 0.0}, "_total": {"gap_sec": 0.0}})
        argv = sys.argv
        sys.argv = ["subs.py", "--project", proj]
        try:
            with redirect_stdout(io.StringIO()):
                subs.main()
        finally:
            sys.argv = argv
        out = json.load(open(os.path.join(proj, "subs.json"), encoding="utf-8"))
    return [b["text"] for b in out["segments"][0]["blocks"]]


check("lang: en → 英文块保留句内标点", _screen_texts("en"), ["Today it takes Kafka, a warehouse", "and a lake to get there"])
check("lang: zh → 照 #88 全删", _screen_texts("zh"), ["Today it takes Kafka a warehouse", "and a lake to get there"])

# ---- 构建期校验 check_beats_refs.norm：与 beats.js 的 norm() 同规则 ----
print("check_beats_refs.norm")
cbr = _load("check_beats_refs", "scripts/check_beats_refs.py")
check("小数点留", cbr.norm("涨了2.4%，"), "涨了2.4%")
check("版本号的两个点都留", cbr.norm("升到1.2.3之后"), "升到1.2.3之后")
check("ASN.1 留点", cbr.norm("ASN.1 很复杂，"), "ASN.1很复杂")
check("X.509 与 X509 不归一成同一个", cbr.norm("X.509证书") != cbr.norm("X509证书"), True)
check("句末的点照删", cbr.norm("用node.js."), "用node.js")

if fails:
    print(f"\n失败 {len(fails)} 项：\n")
    for f in fails:
        print("  ✗ " + f)
    sys.exit(1)
print("\n全部通过")
