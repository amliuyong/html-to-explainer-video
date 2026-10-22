#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""截断的合成结果不算成功的回归测试（不联网、不需要密钥：python tests/test_tts_truncated.py）。

守的错（lessons #150）：一个系列的 06-5 首配时，阿里的读流断了好几次（「读流中断」「SSE 帧不是合法 JSON」），
其中 11 段拿回的是**半截音频、一个字级时间戳都没有**（boundary_count 0，一整句只有 3.8 秒），tts_build 当成功、
写进缓存，重跑拿回的还是它；下游也不报（插值检查不到 6 个字不判，0 个时间戳更不判）。
根因：分块传输的连接断在两块之间时，标准库逐行读把它当成正常 EOF（http.client 的 _peek_chunked 吞掉 IncompleteRead），
parse_stream 又不查最终帧。

  A. 读流：真的 http.client.HTTPResponse（假 socket 上的分块响应）。断在两块之间 → 没读到最终帧，抛可重试的错；
     断在一块里面 → 「读流中断」；断在一帧中间 → 「SSE 帧不是合法 JSON」；完整的流照常解析。火山没读到结束标记同样抛错。
  B. tts_build.synth（假的阿里服务端，走真的 tts_ali 与标准库读流）：读流截断 / 0 个时间戳 / 音频太短，
     每种都重试到 ALI_RETRIES 次后报错，缓存里没有文件，上一次好的 audio/<id>.mp3 不被覆盖；截断一次、第二次正常的照常成功。
  C. 坏的缓存（0 个时间戳 / 太短）读的时候认出来，重新合成并覆盖；引擎还是坏的就报错，坏的缓存原样不动（不写缓存）。
  D. run()：三个引擎各有一段坏结果 → 退出码非零（阿里、火山 return 1，edge SystemExit），不写 manifest，好的段照常进缓存。
  E. 下限的口径：speakable_units；真实最快的段（中文 +28% / +8%、英文 +0%）不报；语速越快下限越低。

假音频里写着自己的时长（「[d=3.000]」），container_duration 换成读这个标记 —— 不需要 ffmpeg；本测试不裁静音。
tts_build 顶层 import edge_tts；没装时这里塞一个空模块。失败即以非零码退出。
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import http.client
import importlib.util
import io
import json
import os
import re
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
tts_ali, tts_volcano = tb.tts_ali, tb.tts_volcano
fails: list[str] = []


def check(label, got, want):
    if got != want:
        fails.append(f"{label}\n    得到: {got!r}\n    应为: {want!r}")
    else:
        print(f"  OK  {label}")


# ---------------- 假的分块 HTTP 响应（真的 http.client.HTTPResponse） ----------------
class FakeSock:
    def __init__(self, data: bytes):
        self.data = data

    def makefile(self, mode, *a, **kw):
        return io.BufferedReader(io.BytesIO(self.data))

    def close(self):
        pass


def http_response(pieces: list[bytes], end="clean", ctype="text/event-stream") -> http.client.HTTPResponse:
    """每个 piece 一个 HTTP 分块。end="clean" 正常收尾（0 分块）；"between" 断在最后一块之后（没有 0 分块）；
    ("inside", k) 最后一块只到第 k 个字节就断了。"""
    body = b""
    for i, p in enumerate(pieces):
        if isinstance(end, tuple) and i == len(pieces) - 1:
            body += f"{len(p):x}\r\n".encode() + p[:end[1]]
            break
        body += f"{len(p):x}\r\n".encode() + p + b"\r\n"
    if end == "clean":
        body += b"0\r\n\r\n"
    head = f"HTTP/1.1 200 OK\r\nContent-Type: {ctype}\r\nTransfer-Encoding: chunked\r\n\r\n".encode()
    resp = http.client.HTTPResponse(FakeSock(head + body))
    resp.begin()
    return resp


def sse_frame(n: int, obj: dict) -> bytes:
    """业务空间域名的排版：带缩进的多行 JSON，每行一个 data:，空行收尾。"""
    body = json.dumps(obj, ensure_ascii=False, indent=2)
    lines = [f"id:{n}", "event:result", ":HTTP_STATUS/200", *[f"data:{ln}" for ln in body.splitlines()], "", ""]
    return "\n".join(lines).encode("utf-8")


def fake_audio(*secs: float) -> list[bytes]:
    """几块假 MP3（首块 ID3 头），每块写着自己的时长。"""
    heads = [b"ID3\x03\x00"] + [b"\xff\xfb"] * (len(secs) - 1)
    return [h + f"[d={s:.3f}]".encode() for h, s in zip(heads, secs)]


def fake_container_duration(path: str) -> float:
    with open(path, "rb") as f:
        return round(sum(float(x) for x in re.findall(rb"\[d=([0-9.]+)\]", f.read())), 3)


PUNCT = set("，。、：；！？,.:;!?")


def words_for(text: str, total: float) -> list[dict]:
    """一字一项（标点并进下一个字，段尾的并进上一个），均摊在 [0, total) 上 —— 时长不重要，只要有。"""
    items, buf = [], ""
    for ch in text:
        buf += ch
        if ch not in PUNCT:
            items.append(buf)
            buf = ""
    if buf and items:
        items[-1] += buf
    step = total / max(1, len(items))
    return [{"text": t, "begin_index": i, "end_index": i + 1,
             "begin_time": int(i * step * 1000), "end_time": int((i + 1) * step * 1000)}
            for i, t in enumerate(items)]


def ali_frames(text: str, secs=(3.0, 3.5), words=True) -> list[bytes]:
    """一句话的完整事件序列：sentence-begin → 两块 sentence-synthesis → sentence-end → 最终帧（finish_reason=stop）。"""
    a, b = fake_audio(*secs)
    w = words_for(text, sum(secs)) if words else []
    out = {"original_text": text, "normalized_text": text, "finish_reason": "null"}
    return [
        sse_frame(1, {"output": {**out, "type": "sentence-begin", "audio": {"data": ""},
                                 "sentence": {"index": 0, "words": []}}, "request_id": "fake"}),
        sse_frame(2, {"output": {"type": "sentence-synthesis", "finish_reason": "null",
                                 "audio": {"data": base64.b64encode(a).decode()},
                                 "sentence": {"index": 0, "words": []}}, "request_id": "fake"}),
        sse_frame(3, {"output": {"type": "sentence-synthesis", "finish_reason": "null",
                                 "audio": {"data": base64.b64encode(b).decode()},
                                 "sentence": {"index": 0, "words": w[:3]}}, "request_id": "fake"}),
        sse_frame(4, {"output": {**out, "type": "sentence-end", "audio": {"data": ""},
                                 "sentence": {"index": 0, "words": w}},
                      "usage": {"output_tokens": 40}, "request_id": "fake"}),
        sse_frame(5, {"output": {"audio": {"data": "", "url": "https://fake.invalid/a.mp3"}, "finish_reason": "stop"},
                      "usage": {"output_tokens": 44}, "request_id": "fake"}),
    ]


def ali_response(text: str, mode: str) -> http.client.HTTPResponse:
    """假的阿里服务端按 mode 回一个分块响应。每个 SSE 帧一个 HTTP 分块。
      ok        完整（6.5 s，有时间戳）
      cut       断在第一块音频之后、两块之间：标准库不报错，拿到 3.0 s 音频、没有 sentence-end（06-5 的样子）
      cut-chunk 断在第二块音频的分块里面：标准库抛 IncompleteRead
      cut-frame 第二块音频的 SSE 帧被拆成两个分块，断在两块之间：最后一帧只有半截 JSON
      nowords   完整的流（有最终帧），sentence-end 的 words 是空的
      short     完整的流、有时间戳，音频只有 1.2 s"""
    if mode == "short":
        return http_response(ali_frames(text, secs=(0.6, 0.6)))
    frames = ali_frames(text, words=(mode != "nowords"))
    if mode in ("ok", "nowords"):
        return http_response(frames)
    if mode == "cut":
        return http_response(frames[:2], end="between")
    if mode == "cut-chunk":
        return http_response(frames[:3], end=("inside", 40))
    if mode == "cut-frame":
        return http_response([*frames[:2], frames[2][:len(frames[2]) // 2]], end="between")
    raise ValueError(mode)


REQUESTS: list[str] = []
PLAN: dict[str, list[str]] = {}               # 文本 → 依次回哪种（用完了回 ok）


def fake_urlopen(req, timeout=None):
    text = json.loads(req.data.decode("utf-8"))["input"]["text"]
    REQUESTS.append(text)
    modes = PLAN.get(text) or []
    return ali_response(text, modes.pop(0) if modes else "ok")


real_urlopen = urllib.request.urlopen
urllib.request.urlopen = fake_urlopen
tts_ali.load_credentials = lambda: {"path": "fake", "api_key": "sk-fake", "base_url": "https://fake.invalid/api/v1"}
tb.container_duration = fake_container_duration
tb.RETRY_SLEEP = 0
tb.RETRY_BACKOFF = 0
tts_ali.RETRY_SLEEP = 0

# 31 个可念单位：+28% 时下限 0.11 × 31 ÷ 1.28 = 2.66 s。截断的那一截 3.0 s 在下限之上 —— 和 06-5 一样，只有 ① 抓得到
TEXT = "没有主节点，写入直接发给好几个副本：读的时候，你怎么知道哪个值是新的？"
RATE = "+28%"

# ---------------- A. 读流 ----------------
print("A. 读流：分块响应断在不同位置")
frames = ali_frames(TEXT)
a, b = fake_audio(3.0, 3.5)
audio, bounds, _ = tts_ali.parse_stream(http_response(frames))
check("完整的流照常解析（音频两块拼起来、31 个时间戳项）", (audio, len(bounds)), (a + b, 31))

lines = list(http_response(frames[:2], end="between"))
check("（根因）标准库：断在两块之间不报错，逐行读到的就是前两帧", len(lines) > 0, True)
CRED = {"api_key": "sk-fake", "base_url": "https://fake.invalid/api/v1"}


def one_request(mode):
    PLAN[TEXT] = [mode]
    try:
        tts_ali._one_request(TEXT, "xuyuyuan_v3.1", CRED, 1.28, "mp3", 24000, 5.0)
        return None
    except tts_ali.AliTTSError as e:
        return e


for mode, want in (("cut", "读流提前结束"), ("cut-chunk", "读流中断"), ("cut-frame", "不是合法 JSON")):
    e = one_request(mode)
    check(f"{mode}：抛可重试的 AliTTSError（{want}）",
          (e is not None, want in str(e or ""), getattr(e, "retryable", None)), (True, True, True))
e = one_request("nowords")
check("nowords：流是完整的，读流本身不报错（交给 tts_build 判）", e, None)

err = None
try:
    tts_ali.parse_stream([ln for f in frames[:4] for ln in f.decode("utf-8").split("\n")])
except tts_ali.AliTTSError as ex:
    err = ex
check("parse_stream：有 sentence-end、没有最终帧也算截断", (err is not None, "最终帧" in str(err or "")), (True, True))


class FakeOpener:
    def __init__(self, resp):
        self.resp = resp

    def open(self, req, timeout=None):
        return self.resp


def volcano_lines(end: bool) -> list[bytes]:
    a = base64.b64encode(fake_audio(2.0)[0]).decode()
    rows = [{"code": 0, "message": "OK", "data": a,
             "sentence": {"text": "你好", "words": [{"word": "你", "startTime": 0.1, "endTime": 0.3},
                                                    {"word": "好", "startTime": 0.3, "endTime": 0.6}]}}]
    if end:
        rows.append({"code": tts_volcano.END_CODE, "message": "OK", "data": None, "usage": {"text_words": 2}})
    return [(json.dumps(r, ensure_ascii=False) + "\n").encode("utf-8") for r in rows]


real_opener = tts_volcano._opener
VCRED = {"api_key": "fake", "resource_id": "seed-tts-2.0"}
tts_volcano._opener = lambda: FakeOpener(http_response(volcano_lines(True), ctype="application/json"))
audio, vb, usage = tts_volcano._one_request("你好", "v", VCRED, 0, "mp3", 24000, 160000, 5.0)
check("火山：读到结束标记照常解析", (len(vb), usage), (2, {"text_words": 2}))
tts_volcano._opener = lambda: FakeOpener(http_response(volcano_lines(False), end="between", ctype="application/json"))
err = None
try:
    tts_volcano._one_request("你好", "v", VCRED, 0, "mp3", 24000, 160000, 5.0)
except tts_volcano.VolcanoTTSError as ex:
    err = ex
check("火山：断在两块之间、没读到结束标记 → 可重试的错",
      (err is not None, "结束标记" in str(err or ""), getattr(err, "retryable", None)), (True, True, True))
tts_volcano._opener = real_opener


# ---------------- B. tts_build.synth ----------------
def synth(text, d, provider="ali", rate=RATE, cache=True):
    """跑真的 tts_build.synth。返回 (结果或异常, stderr)。"""
    err = io.StringIO()
    with redirect_stderr(err):
        try:
            r = asyncio.run(tb.synth(text, os.path.join(d, "audio", "s1.mp3"), "xuyuyuan_v3.1", rate,
                                     cache_dir=os.path.join(d, "cache") if cache else "", trim=False,
                                     provider=provider))
        except BaseException as ex:  # noqa: BLE001  —— edge 抛 SystemExit
            r = ex
    return r, err.getvalue()


def cache_files(d):
    root = os.path.join(d, "cache")
    return sorted(f for _, _, fs in os.walk(root) for f in fs) if os.path.isdir(root) else []


PREV = b"ID3\x03\x00previous-good-take[d=6.400]"


def seed_out(d):
    """上一次好的 audio/s1.mp3。"""
    os.makedirs(os.path.join(d, "audio"), exist_ok=True)
    with open(os.path.join(d, "audio", "s1.mp3"), "wb") as f:
        f.write(PREV)


def out_bytes(d):
    with open(os.path.join(d, "audio", "s1.mp3"), "rb") as f:
        return f.read()


print("B. tts_build.synth：坏结果重试、报错、不进缓存")
for label, plan, want_in_log in (
        ("读流截断（断在两块之间，06-5 的样子）", ["cut"] * 3, "读流提前结束"),
        ("读流中断 / 半截帧 / 截断轮着来", ["cut-chunk", "cut-frame", "cut"], "不是合法 JSON"),
        ("0 个时间戳（流是完整的）", ["nowords"] * 3, "没有字级时间戳"),
        ("音频短得不像这段文字（1.2 s / 31 个单位）", ["short"] * 3, "音频只有 1.20s")):
    with tempfile.TemporaryDirectory() as d:
        seed_out(d)
        REQUESTS.clear()
        PLAN[TEXT] = list(plan)
        r, log = synth(TEXT, d)
        check(f"{label}：报错", isinstance(r, tts_ali.AliTTSError), True)
        check(f"{label}：重试到 {tb.ALI_RETRIES} 次", (len(REQUESTS), f"[retry {tb.ALI_RETRIES}/{tb.ALI_RETRIES}]" in log),
              (tb.ALI_RETRIES, True))
        check(f"{label}：日志里有原因", want_in_log in log + str(r), True)
        check(f"{label}：缓存里没有文件", cache_files(d), [])
        check(f"{label}：上一次的 audio/s1.mp3 原样，没有 .part 残留",
              (out_bytes(d), sorted(os.listdir(os.path.join(d, "audio")))), (PREV, ["s1.mp3"]))

with tempfile.TemporaryDirectory() as d:
    REQUESTS.clear()
    PLAN[TEXT] = ["nowords"]
    r, log = synth(TEXT, d)
    check("0 个时间戳：阿里的「音色未返回字级时间戳」提醒照样打出来", "未返回字级时间戳" in log, True)

with tempfile.TemporaryDirectory() as d:
    REQUESTS.clear()
    PLAN[TEXT] = ["cut", "ok"]
    r, log = synth(TEXT, d)
    check("截断一次、第二次正常：成功，共 2 次请求", (isinstance(r, dict), len(REQUESTS)), (True, 2))
    check("成功的那次进缓存（1 对文件），时间戳 31 个", (len(cache_files(d)), r["boundary_count"] if isinstance(r, dict) else None),
          (2, 31))

with tempfile.TemporaryDirectory() as d:
    REQUESTS.clear()
    PLAN[TEXT] = []
    r, log = synth(TEXT, d)
    check("正常段：1 次请求、进缓存、容器 6.5 s", (len(REQUESTS), len(cache_files(d)), r["container_duration_sec"]),
          (1, 2, 6.5))
    check("正常段：没有 retry 日志", "[retry" in log, False)
    REQUESTS.clear()
    r, log = synth(TEXT, d)
    check("正常段重跑：命中缓存，不再请求", (len(REQUESTS), r["boundary_count"]), (0, 31))

# ---------------- C. 坏的缓存 ----------------
print("C. 坏的缓存：读的时候认出来")


def seed_cache(d, bounds, secs):
    """往 TEXT 的缓存位置塞一条（2.2.0 会写进去的那种）。"""
    mp3, js = tb.cache_paths(TEXT, "xuyuyuan_v3.1", RATE, tb.DEFAULT_VOLUME, tb.DEFAULT_PITCH,
                             os.path.join(d, "cache"), False, provider="ali")
    with open(mp3, "wb") as f:
        f.write(b"".join(fake_audio(*secs)))
    with open(js, "w", encoding="utf-8") as f:
        json.dump({"boundaries": bounds, "lead_cut_sec": 0.0}, f, ensure_ascii=False)
    return mp3, js


def snapshot(paths):
    out = []
    for p in paths:
        with open(p, "rb") as f:
            out.append(f.read())
    return out


GOOD_BOUNDS = [{"text": "没", "offset_sec": 0.0, "duration_sec": 0.2}]
for label, bounds, secs in (("0 个时间戳、3.77 s（06-5 qr1 的样子）", [], (3.77,)),
                            ("有时间戳、音频只有 1.0 s", GOOD_BOUNDS, (1.0,))):
    with tempfile.TemporaryDirectory() as d:
        paths = seed_cache(d, bounds, secs)
        REQUESTS.clear()
        PLAN[TEXT] = []
        r, log = synth(TEXT, d)
        check(f"{label}：不用它，重新合成（1 次请求）", (len(REQUESTS), "缓存里这一段是坏的" in log), (1, True))
        with open(paths[1], encoding="utf-8") as f:
            check(f"{label}：好的结果覆盖了它", (len(json.load(f)["boundaries"]), r["boundary_count"]), (31, 31))

    with tempfile.TemporaryDirectory() as d:
        paths = seed_cache(d, bounds, secs)
        before = snapshot(paths)
        REQUESTS.clear()
        PLAN[TEXT] = ["cut"] * 3
        r, log = synth(TEXT, d)
        check(f"{label}，引擎还是截断：重试 {tb.ALI_RETRIES} 次后报错",
              (isinstance(r, tts_ali.AliTTSError), len(REQUESTS)), (True, tb.ALI_RETRIES))
        check(f"{label}，引擎还是截断：缓存一个字节都没写", (snapshot(paths), len(cache_files(d))), (before, 2))

with tempfile.TemporaryDirectory() as d:
    seed_cache(d, GOOD_BOUNDS, (6.4,))
    REQUESTS.clear()
    r, log = synth(TEXT, d)
    check("好的缓存照旧命中（不请求、不打 ⚠）", (len(REQUESTS), "坏的" in log, r["boundary_count"]), (0, False, 1))

# ---------------- D. run() 的退出码 ----------------
print("D. run()：坏的段让整次配音非零退出")
BAD = "这一段每次都拿不到时间戳，写和读都要等两个副本的确认。"
FAKE_MP3 = b"".join(fake_audio(5.0))


def project(d, provider):
    with open(os.path.join(d, "project.json"), "w", encoding="utf-8") as f:
        json.dump({"provider": provider, "rate": RATE, "lang": "zh"}, f)
    with open(os.path.join(d, "narration.json"), "w", encoding="utf-8") as f:
        json.dump([{"id": "s1", "text": TEXT}, {"id": "s2", "text": BAD}], f, ensure_ascii=False)
    return argparse.Namespace(project=d, provider="", voice="", rate="", no_trim=True, no_cache=False)


def run_project(d, provider):
    with redirect_stderr(io.StringIO()) as err, redirect_stdout(io.StringIO()):
        try:
            code = asyncio.run(tb.run(project(d, provider)))
        except SystemExit as ex:
            code = ex.code
    return code, err.getvalue(), os.path.exists(os.path.join(d, "audio-manifest.json"))


def fake_volcano(text, *a, **kw):
    bounds = [] if text == BAD else [{"text": ch, "offset_sec": i * 0.2, "duration_sec": 0.2} for i, ch in enumerate(text)]
    return {"audio": FAKE_MP3, "boundaries": bounds, "usage": {}, "requests": 1}


class FakeCommunicate:
    def __init__(self, text, voice, rate="+0%", volume="+0%", pitch="+0Hz", boundary="SentenceBoundary"):
        self.text = text

    async def stream(self):
        yield {"type": "audio", "data": FAKE_MP3}
        if self.text != BAD:               # 坏的那段：websocket 提前关了，只有音频、没有 WordBoundary
            for i, ch in enumerate(self.text):
                yield {"type": "WordBoundary", "text": ch, "offset": i * 2_000_000, "duration": 2_000_000}


real_volcano, real_edge = tts_volcano.synthesize, tb.edge_tts
tts_volcano.synthesize = fake_volcano
tb.edge_tts = types.SimpleNamespace(__version__="fake", Communicate=FakeCommunicate)
for provider in ("ali", "volcano", "edge"):
    with tempfile.TemporaryDirectory() as d:
        PLAN[TEXT], PLAN[BAD] = [], ["nowords"] * 10
        code, log, wrote = run_project(d, provider)
        check(f"{provider}：退出码非零", code not in (0, None), True)
        check(f"{provider}：不写 audio-manifest.json", wrote, False)
        check(f"{provider}：报出是哪一段、为什么", "没有字级时间戳" in log + str(code), True)
        files = cache_files(os.path.join(d, "audio"))
        check(f"{provider}：只有好的 s1 进了缓存（1 对文件）", len(files), 2)
tts_volcano.synthesize, tb.edge_tts = real_volcano, real_edge

# ---------------- E. 下限的口径 ----------------
print("E. 下限：可念单位与真实最快的段")
U = tb.speakable_units
check("汉字一个一个算", U("你怎么知道哪个值是新的？"), 11)
check("英文词、数字一串算一个", U("都出自 Kleppmann 2017 年的书。"), 8)
check("词里的撇号不断开", U("don't stop, it's fine"), 4)
check("拼读的字母各算一个", U("D D I A 精读"), 6)
check("符号与标点不算", U("w + r > n，2.4%。"), 5)
check("没有可念的字：两条都不判", (U("……"), tb.synthesis_problem("……", [], 0.0, RATE)), (0, None))
B1 = [{"text": "x", "offset_sec": 0.0, "duration_sec": 0.1}]
# 真实最快的段（裁后容器时长、语速；字换成了同样单位数的占位字）：一个中文系列 1164 段里最快的（04-1 mem5），
# 另一部中文片（anmingyuan_v3.1，+8%）、英文片（Andy_v3.1，+0%）里最快的
check("中文系列最快的一段（21 个单位、3.46 s、+28%）不报",
      tb.synthesis_problem("写盘的时候，新来的写入进一个新的内存表，不用等。", B1, 3.46, "+28%"), None)
check("另一部中文片最快的一段（32 个单位、5.54 s、+8%）不报",
      tb.synthesis_problem("字" * 32, B1, 5.54, "+8%"), None)
check("英文片最快的一段（24 个词、8.11 s、+0%）不报",
      tb.synthesis_problem(" ".join(["word"] * 24) + ".", B1, 8.11, "+0%"), None)
check("同一段截到 1.5 s：报", tb.synthesis_problem("字" * 32, B1, 1.5, "+8%") is not None, True)
check("下限随语速变：同样 1.8 s，+0% 报（下限 3.52 s）、+100% 不报（下限 1.76 s）",
      (tb.synthesis_problem("字" * 32, B1, 1.8, "+0%") is not None, tb.synthesis_problem("字" * 32, B1, 1.8, "+100%")),
      (True, None))

urllib.request.urlopen = real_urlopen
if fails:
    print(f"\n✗ {len(fails)} 项失败：")
    for x in fails:
        print("  " + x)
    sys.exit(1)
print("\n全部通过")
