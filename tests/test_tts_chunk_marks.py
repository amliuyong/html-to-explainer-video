#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""切块符 `|` 不送 TTS 的回归测试（不联网、不需要密钥：python tests/test_tts_chunk_marks.py）。

守的错（lessons #142）：narration 里的 `|` 只给 subs.py 切字幕块用，2.1.1 及以前却原样送给 TTS。
阿里云百炼有时把它读成「竖线」（紧跟在 、：；后面最容易），或把两个 `|` 夹着的短块读成「绝对值」；
同一段文字重配返回逐字节相同的音频，只能改字。

  A. 送给 TTS 的文本（subs.strip_chunk_marks）：没有 `|`；中文直接连上、不补停顿；
     两边都是 ASCII 字母或数字时补一个空格；没有 `|` 的文本原样返回。
  B. 真的 tts_build.synth / run()，三个引擎各换一个假的（阿里是假的 SSE 服务端，连数字展开读法一起造；
     火山换 synthesize；edge 换 Communicate）：送出去的文本里没有 `|`，manifest 的 original_text 留着 `|`。
  C. 边界映射：假的引擎按去掉 `|` 的文本逐字给时间戳，跑真的 subs.py —— 每块的起点正好是该块首字的时间戳。
  D. 缓存：含 `|` 的段不再命中 2.1.1 的旧缓存（旧签名带 `|`），不含 `|` 的段照旧命中；只挪 `|` 的位置不重配。
  E. tts_ali.py / tts_volcano.py 的 --synth 和 tts_build 送一样的文本（试听才和正式配音对得上）。

tts_build 顶层 import edge_tts；没装时这里塞一个空模块。失败即以非零码退出。
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import importlib.util
import io
import json
import os
import sys
import tempfile
import types
import urllib.request
from contextlib import redirect_stderr, redirect_stdout

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))   # tts_build 会 import subs / tts_ali / tts_volcano

try:
    import edge_tts  # noqa: F401
except ImportError:
    sys.modules["edge_tts"] = types.SimpleNamespace(__version__="stub", Communicate=None)


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, rel))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


tb = _load("tts_build", "scripts/tts_build.py")
subs = _load("subs", "scripts/subs.py")
fails: list[str] = []


def check(label, got, want):
    if got != want:
        fails.append(f"{label}\n    得到: {got!r}\n    应为: {want!r}")
    else:
        print(f"  OK  {label}")


# ---------------- A. 送给 TTS 的文本 ----------------
print("A. subs.strip_chunk_marks")
strip = subs.strip_chunk_marks
DF4 = "它也能给所有行套函数、|过滤、分组汇总、|按键连接两张表，|pandas\xa0里叫合并\xa0merge。"   # 03-4 df4 首版：念了「竖线」
check("「、|」「，|」去掉 `|`，标点留着", strip(DF4), "它也能给所有行套函数、过滤、分组汇总、按键连接两张表，pandas\xa0里叫合并\xa0merge。")
check("「：|」「；|」", strip("范围查询：|B\xa0树往后读；|LSM\xa0树"), "范围查询：B\xa0树往后读；LSM\xa0树")
check("片头", strip("D D I A 精读，|第四章，|存储与检索"), "D D I A 精读，第四章，存储与检索")
check("中文夹中文：直接连上，不补停顿", strip("你也能判断|这套视频值不值得看"), "你也能判断这套视频值不值得看")
check("ASCII 字母夹字母：补空格，不连成一个词", strip("Data-Intensive|Applications"), "Data-Intensive Applications")
check("字母夹数字：补空格", strip("Barroso|2013 年"), "Barroso 2013 年")
check("数字夹数字：补空格，不连成一个数", strip("300|400"), "300 400")
check("字母夹中文：直接连上", strip("PostgreSQL|这样的软件"), "PostgreSQL这样的软件")
check("`|` 旁边本来有空白：收成一个空格", strip("In 2026, | AI grew."), "In 2026, AI grew.")
check("连着两个 `|` 当一个", strip("a||b，||丙"), "a b，丙")
check("段首段尾的 `|` 去掉", strip("|开头和结尾|"), "开头和结尾")
check("两个 `|` 夹着的短块（读成过「绝对值」）", strip("增长了|3 倍|。"), "增长了3 倍。")
check("没有 `|` 的文本原样返回", strip("没有切块符，2.4% 照旧。"), "没有切块符，2.4% 照旧。")
check("全角｜不是切块符，不动", strip("甲｜乙"), "甲｜乙")
check("`|` 旁边是不换行空格：同样收成一个空格", strip("Amazon\xa0S3\xa0|这样的"), "Amazon\xa0S3 这样的")
check("切块不变：split_chunks 照旧按 `|` 切", subs.split_chunks(DF4),
      ["它也能给所有行套函数、", "过滤、分组汇总、", "按键连接两张表，", "pandas\xa0里叫合并\xa0merge。"])
# subs.chunk_starts 拿时间戳项的文本（去掉标点和空白）在 tts_text（各块直接拼起来）里顺序查找。
# 送出去的文本去掉空白以后必须和它逐字相同 —— 补的空格只能落在空白上，不能多出或吞掉别的字。
nows = lambda s: "".join(ch for ch in s if not ch.isspace())   # noqa: E731
for t in (DF4, "D D I A 精读，|第四章，|存储与检索", "Data-Intensive|Applications", "In 2026, | AI grew.", "a||b，||丙",
          "|开头和结尾|", "Amazon\xa0S3\xa0|这样的", "增长了|3 倍|。"):
    check(f"去空白后 = subs 的 tts_text：{t[:12]}…", nows(strip(t)), nows("".join(subs.split_chunks(t))))

# ---------------- 假的引擎 ----------------
STEP = 0.4                                   # 每个时间戳项 0.4 s，块长都 ≥ 0.6 s（不触发 subs 的停留重分配）
PUNCT = set("，。、：；！？,.:;!?")
ZH_DIGIT = dict(zip("0123456789", "零一二三四五六七八九"))
FAKE_MP3 = b"ID3\x03\x00fake-audio"
SENT: dict[str, list[str]] = {"ali": [], "edge": [], "volcano": []}


def tokens(text):
    """按服务端的样子切项：中文一字一项，连续的 ASCII 字母/数字一项，空白和标点并进下一项（段尾的并进上一项）。
    第 k 项从 k × STEP 秒开始。"""
    out, buf, i = [], "", 0
    while i < len(text):
        ch = text[i]
        if ch.isspace() or ch in PUNCT:
            buf += ch
            i += 1
            continue
        j = i + 1
        if ch.isascii() and ch.isalnum():
            while j < len(text) and text[j].isascii() and text[j].isalnum():
                j += 1
        out.append(buf + text[i:j])
        buf, i = "", j
    if buf and out:
        out[-1] += buf
    return [(t, round(k * STEP, 4), round((k + 1) * STEP, 4)) for k, t in enumerate(out)]


def sse_event(n, obj):
    body = json.dumps(obj, ensure_ascii=False, indent=2)
    return [f"id:{n}", "event:result", ":HTTP_STATUS/200", *[f"data:{ln}" for ln in body.splitlines()], ""]


class FakeResp:
    def __init__(self, lines):
        self.lines = [(ln + "\n").encode("utf-8") for ln in lines]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        return iter(self.lines)


def fake_urlopen(req, timeout=None):
    """假的阿里业务空间：数字按读法展开（2026 → 二零二六），时间戳落在展开后的文本上，和真的服务端一样。"""
    text = json.loads(req.data.decode("utf-8"))["input"]["text"]
    SENT["ali"].append(text)
    norm = "".join(ZH_DIGIT.get(c, c) for c in text)
    words = [{"text": t, "begin_time": int(round(b * 1000)), "end_time": int(round(e * 1000))}
             for t, b, e in tokens(norm)]
    lines = sse_event(1, {"output": {"type": "sentence-synthesis", "audio": {"data": base64.b64encode(FAKE_MP3).decode()},
                                     "sentence": {"index": 0, "words": []}}, "request_id": "fake"})
    lines += sse_event(2, {"output": {"type": "sentence-end", "original_text": text, "normalized_text": norm,
                                      "audio": {"data": ""}, "sentence": {"index": 0, "words": words}},
                           "usage": {"output_tokens": 1}, "request_id": "fake"})
    return FakeResp(lines)


def fake_volcano(text, *args, **kw):
    SENT["volcano"].append(text)
    return {"audio": FAKE_MP3, "usage": {}, "requests": 1,
            "boundaries": [{"text": t.strip(), "offset_sec": b, "duration_sec": round(e - b, 4)} for t, b, e in tokens(text)]}


class FakeCommunicate:
    def __init__(self, text, voice, rate="+0%", volume="+0%", pitch="+0Hz", boundary="SentenceBoundary"):
        SENT["edge"].append(text)
        self.text = text

    async def stream(self):
        yield {"type": "audio", "data": FAKE_MP3}
        for t, b, e in tokens(self.text):
            yield {"type": "WordBoundary", "text": t.strip(), "offset": int(round(b * 1e7)),
                   "duration": int(round((e - b) * 1e7))}


real_urlopen = urllib.request.urlopen
urllib.request.urlopen = fake_urlopen
tb.tts_ali.load_credentials = lambda: {"path": "fake", "api_key": "sk-fake", "base_url": "https://fake.invalid/api/v1"}
tb.tts_volcano.synthesize = fake_volcano
tb.edge_tts = types.SimpleNamespace(__version__="fake", Communicate=FakeCommunicate)
tb.container_duration = lambda path: 15.0    # 假音频没法让 ffmpeg 量；本测试不裁静音（--no-trim）
tb.RETRY_SLEEP = 0

# ---------------- B + C. 三个引擎：送出去的文本 + 边界映射 ----------------
TEXT = "2026 年：|主键索引、|二级索引|和全文索引，|都出自 Kleppmann|2017 年的书。"
SPOKEN = "2026 年：主键索引、二级索引和全文索引，都出自 Kleppmann 2017 年的书。"
CHUNKS = ["2026 年：", "主键索引、", "二级索引", "和全文索引，", "都出自 Kleppmann", "2017 年的书。"]
PLAIN = "这一段没有切块符。"
BASE = 10.0
# 每块首字（2026 / 主 / 二 / 和 / 都 / 2017）所在的项 × 0.4 s。阿里的时间戳落在展开后的文本上：
# 「二零二六」占 4 项、「二零一七」占 4 项，所以后面的块更晚；to_original 把它们并回「2026」「2017」。
WANT = {"edge": [0.0, 0.8, 2.4, 4.0, 6.0, 7.6],
        "volcano": [0.0, 0.8, 2.4, 4.0, 6.0, 7.6],
        "ali": [0.0, 2.0, 3.6, 5.2, 7.2, 8.8]}

for provider in ("ali", "volcano", "edge"):
    print(f"B/C. provider={provider}")
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "project.json"), "w", encoding="utf-8") as f:
            json.dump({"provider": provider, "rate": "+28%", "lang": "zh", "fps": 30, "slug": "t"}, f)
        with open(os.path.join(d, "narration.json"), "w", encoding="utf-8") as f:
            json.dump([{"id": "s1", "text": TEXT}, {"id": "s2", "text": PLAIN}], f, ensure_ascii=False)
        with open(os.path.join(d, "layout.json"), "w", encoding="utf-8") as f:
            json.dump({"s1": {"start_sec": BASE}, "s2": {"start_sec": 30.0}, "_total": {"gap_sec": 0.35}}, f)
        SENT[provider].clear()
        args = argparse.Namespace(project=d, provider="", voice="", rate="", no_trim=True, no_cache=False)
        with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
            code = asyncio.run(tb.run(args))
        check("tts_build run() 退出码 0", code, 0)
        check("送出去的文本里没有 `|`", SENT[provider], [SPOKEN, PLAIN])
        with open(os.path.join(d, "audio-manifest.json"), encoding="utf-8") as f:
            items = {it["id"]: it for it in json.load(f)["items"]}
        check("manifest：spoken_text 没有 `|`，original_text 留着", (items["s1"]["spoken_text"], items["s1"]["original_text"]),
              (SPOKEN, TEXT))

        sys.argv = ["subs.py", "--project", d]
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            subs.main()
        with open(os.path.join(d, "subs.json"), encoding="utf-8") as f:
            seg = {s["id"]: s for s in json.load(f)["segments"]}["s1"]
        check("subs.json 照旧按 `|` 切块", [b["spoken"] for b in seg["blocks"]], CHUNKS)
        check("每块起点 = 该块首字的时间戳（本地秒）", [b["local_start_sec"] for b in seg["blocks"]], WANT[provider])
        check("global_start_sec = 段起点 + 本地秒", [b["global_start_sec"] for b in seg["blocks"]],
              [round(BASE + t, 3) for t in WANT[provider]])

        # 再跑一遍走缓存：一个字都不再送
        SENT[provider].clear()
        with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
            asyncio.run(tb.run(args))
        check("重跑全部命中缓存（不再合成）", SENT[provider], [])

# ---------------- D. 缓存签名 ----------------
print("D. 缓存签名")
calls = SENT["volcano"]


def synth(text, cache, out):
    with redirect_stderr(io.StringIO()):
        return asyncio.run(tb.synth(text, out, "v", "+0%", cache_dir=cache, trim=False, provider="volcano"))


def seed(text, cache, marker):
    """按 2.1.1 的签名（签名里是原文，带 `|`）往缓存里塞一条。"""
    mp3, js = tb.cache_paths(text, "v", "+0%", tb.DEFAULT_VOLUME, tb.DEFAULT_PITCH, cache, False, provider="volcano")
    with open(mp3, "wb") as f:
        f.write(FAKE_MP3)
    with open(js, "w", encoding="utf-8") as f:
        json.dump({"boundaries": [{"text": marker, "offset_sec": 0.0, "duration_sec": 1.0}], "lead_cut_sec": 0.0},
                  f, ensure_ascii=False)


with tempfile.TemporaryDirectory() as d:
    cache, out = os.path.join(d, "cache"), os.path.join(d, "a.mp3")
    calls.clear()
    seed("甲乙、|丙丁。", cache, "竖线")
    r = synth("甲乙、|丙丁。", cache, out)
    check("含 `|` 的段不命中旧缓存（重配）", (len(calls), "竖线" in [b["text"] for b in r["boundaries"]]), (1, False))
    synth("甲乙、|丙丁。", cache, out)
    check("重配过的段再跑命中新缓存", len(calls), 1)
    synth("甲乙、丙|丁。", cache, out)
    check("只挪 `|` 的位置：送的文本一样，命中缓存", len(calls), 1)
    seed("没有切块符。", cache, "旧缓存")
    r = synth("没有切块符。", cache, out)
    check("不含 `|` 的段照旧命中 2.1.1 的缓存（不重配）", (len(calls), r["boundaries"][0]["text"]), (1, "旧缓存"))

# ---------------- E. --synth 和 tts_build 送一样的文本 ----------------
print("E. tts_ali.py / tts_volcano.py --synth")
got: list[str] = []


def fake_synthesize(text, *a, **kw):
    got.append(text)
    return {"audio": FAKE_MP3, "boundaries": [], "usage": {}, "requests": 1}


with tempfile.TemporaryDirectory() as d:
    for mod in (tb.tts_ali, tb.tts_volcano):
        mod.synthesize = fake_synthesize
        sys.argv = [f"{mod.__name__}.py", "--synth", DF4, "--out", os.path.join(d, "x.mp3")]
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            code = mod.main()
        check(f"{mod.__name__} --synth 退出码 0", code, 0)
    check("两个 --synth 送的都是去掉 `|` 的文本", got, [strip(DF4)] * 2)

urllib.request.urlopen = real_urlopen
if fails:
    print(f"\n✗ {len(fails)} 项失败：")
    for x in fails:
        print("  " + x)
    sys.exit(1)
print("\n全部通过")
