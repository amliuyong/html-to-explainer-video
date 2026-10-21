#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""阿里云百炼 TTS 接口包的回归测试（零依赖、不联网、不需要密钥：python tests/test_tts_ali.py）。

守四条不变式：

  1. SSE 解析：业务空间域名把一个事件拆成多行 data:，必须拼接后再解析；
     判错只认 data 里的 code（出错时是 HTTP 400 + event:result，流末尾没有空行）。
  2. 时间戳映射回原文：服务端把「2026」展开成「二零二六」再给时间戳，
     不映射回原文，subs.py 会把「二」误配到后文「第二」上，字幕整体提前好几秒。
  3. 语速换算：「快百分之几」→ 倍速 [0.5, 2.0]。
  4. 密钥文件：dashScope 必须是 https://（Bearer 密钥随每次请求发出，http 会明文外泄）。

用例里的帧结构与字级时间戳取自 2026-10-19 的真实响应（音频与 ID 已替换）。
失败即以非零码退出（CI 直接吃退出码）。
"""
from __future__ import annotations

import base64
import importlib.util
import io
import json
import os
import sys
import tempfile
import urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))   # tts_ali 会 import tts_volcano


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, rel))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


subs = _load("subs", "scripts/subs.py")
tts_ali = _load("tts_ali", "scripts/tts_ali.py")

fails: list[str] = []


def check(label, got, want):
    if got != want:
        fails.append(f"{label}\n    得到: {got!r}\n    应为: {want!r}")
    else:
        print(f"  OK  {label}")


def sse_event(n: int, obj: dict, multiline: bool = True) -> list[str]:
    """按业务空间域名的排版造一帧：带缩进的多行 JSON，每行一个 data:。"""
    body = json.dumps(obj, ensure_ascii=False, indent=2 if multiline else None)
    return [f"id:{n}", "event:result", ":HTTP_STATUS/200",
            *[f"data:{line}" for line in body.splitlines()], ""]


def words_from(normalized: str, starts_ms: list[int], ends_ms: list[int]) -> list[dict]:
    """把展开文本切成单字项（标点并进下一个字，和服务端一致）。"""
    items, buf = [], ""
    for ch in normalized:
        buf += ch
        if ch not in "，。！？、,.":
            items.append(buf)
            buf = ""
    if buf:
        items[-1] += buf
    assert len(items) == len(starts_ms) == len(ends_ms), (len(items), len(starts_ms))
    return [{"text": t, "begin_index": i, "end_index": i + 1,
             "begin_time": b, "end_time": e}
            for i, (t, b, e) in enumerate(zip(items, starts_ms, ends_ms))]


# ---- 1. SSE 解析 ----
print("SSE 解析 parse_stream")
ORIG = "2026年，|同比增长2.4%。|这是第二个阶段。"
NORM = "二零二六年，同比增长百分之二点四。这是第二个阶段。"
B = [0, 230, 370, 520, 670, 1260, 1480, 1630, 1850, 2080, 2370, 2590, 2890, 3110, 3260,
     4150, 4370, 4590, 4820, 4890, 5040, 5190]
E = B[1:] + [5410]
WORDS = words_from(NORM, B, E)
chunk_a, chunk_b = b"ID3\x03\x00audio-part-1", b"\xff\xfbaudio-part-2"
lines: list[str] = []
lines += sse_event(1, {"output": {"type": "sentence-begin", "original_text": ORIG,
                                  "normalized_text": NORM, "audio": {"data": ""},
                                  "sentence": {"index": 0, "words": WORDS[:2]},
                                  "finish_reason": "null"}, "request_id": "r1"})
lines += sse_event(2, {"output": {"type": "sentence-synthesis",
                                  "audio": {"data": base64.b64encode(chunk_a).decode()},
                                  "sentence": {"index": 0, "words": []},
                                  "finish_reason": "null"}, "request_id": "r1"})
lines += sse_event(3, {"output": {"type": "sentence-synthesis",
                                  "audio": {"data": base64.b64encode(chunk_b).decode()},
                                  "sentence": {"index": 0, "words": WORDS[:5]},
                                  "finish_reason": "null"}, "request_id": "r1"})
lines += sse_event(4, {"output": {"type": "sentence-end", "original_text": ORIG,
                                  "normalized_text": NORM, "audio": {"data": ""},
                                  "sentence": {"index": 0, "words": WORDS},
                                  "finish_reason": "null"},
                       "usage": {"output_tokens": 60}, "request_id": "r1"})
lines += sse_event(5, {"output": {"audio": {"data": "", "url": "http://x/y.mp3"},
                                  "finish_reason": "stop"},
                       "usage": {"output_tokens": 66}, "request_id": "r1"})
audio, bounds, usage = tts_ali.parse_stream(lines)
check("多行 data: 拼接后逐块解码音频", audio, chunk_a + chunk_b)
check("usage 取最终帧", usage, {"output_tokens": 66})
check("只认 sentence-end 的完整 words（不被半成品重复计入）",
      len([b for b in bounds if b["text"] == "年"]), 1)
check("字节流（bytes 行）同样能解析",
      tts_ali.parse_stream([(ln + "\n").encode() for ln in lines])[0], chunk_a + chunk_b)

print("错误帧")
err_lines = ["id:1", "event:result", ":HTTP_STATUS/400",
             'data:{"request_id":"r2","code":"InvalidParameter",'
             '"message":"[cosyvoice:]Engine error [411]: TTS speak operation failed"}']
try:
    tts_ali.parse_stream(err_lines)
    fails.append("错误帧没有抛异常")
except tts_ali.AliTTSError as e:
    check("错误帧（无结尾空行）被识别", "InvalidParameter" in str(e), True)
    check("音色错误不重试", e.retryable, False)
    check("音色错误带提示", "*_v3.1" in str(e), True)
try:
    tts_ali.parse_stream(sse_event(1, {"code": "Throttling.RateQuota", "message": "x"}, False))
    fails.append("限流帧没有抛异常")
except tts_ali.AliTTSError as e:
    check("限流可重试", e.retryable, True)

http_err = urllib.error.HTTPError(
    "https://example/api", 401, "Unauthorized", {},
    io.BytesIO(b'{"code":"InvalidApiKey","message":"Invalid API-key provided.","request_id":"r3"}'))
e = tts_ali._http_error(http_err)
check("HTTP 401 的 JSON 体被解析出 code", "InvalidApiKey" in str(e), True)
check("鉴权错误不重试", e.retryable, False)
e = tts_ali._http_error(urllib.error.HTTPError(
    "https://example/api", 503, "Busy", {}, io.BytesIO(b"<html>busy</html>")))
check("5xx 非 JSON 体可重试", e.retryable, True)

# ---- 2. 时间戳映射回原文 ----
print("映射回原文 to_original")
texts = [b["text"] for b in bounds]
check("「二零二六」合并回「2026」", texts[0], "2026")
check("「百分之二点四」合并回「2.4%」", "2.4%" in texts, True)
check("「2026」的结束时间延到「六」的末尾", bounds[0]["duration_sec"], round(E[3] / 1000, 4))
check("原文里的 | 落进相邻项（下游会剥掉）", "，|同" in texts, True)
check("后文的「二」不受影响", [b["offset_sec"] for b in bounds if b["text"] == "二"],
      [round(B[-4] / 1000, 4)])

# 真正要守的是下游效果：subs.chunk_starts 拿这些边界对出的块起点必须单调且准确
chunks = subs.split_chunks(ORIG)
starts = subs.chunk_starts("".join(chunks), chunks, bounds, 0.0)
check("块起点落在各块首字（同 → 1.26s，这 → 4.15s）", starts,
      [0.0, round(B[5] / 1000, 4), round(B[15] / 1000, 4)])
raw = [{"text": w["text"], "offset_sec": w["begin_time"] / 1000} for w in WORDS]
bad = subs.chunk_starts("".join(chunks), chunks, raw, 0.0)
check("对照：不映射时第二块会被拽到「第二」之后（证明用例有效）", bad[1] != starts[1], True)

check("保留数字的形态（| → ，、% 被吞）照样对得上",
      [t for t, _, _ in tts_ali.to_original(
          [{"text": t, "begin_time": 0, "end_time": 0} for t in ["2", ".", "4", "。，第", "二"]],
          "2.4%。|第二", "2.4。，第二")],
      ["2", ".", "4", "。|第", "二"])
check("英文读法 twenty twenty six → 2026,",
      [t.strip() for t, _, _ in tts_ali.to_original(
          [{"text": t, "begin_time": i, "end_time": i + 1}
           for i, t in enumerate(["In", " twenty", " twenty", " six", "， A", " I", " grew."])],
          "In 2026, AI grew.", "In twenty twenty six， A I grew.")],
      ["In", "2026,", "A", "I", "grew."])
check("原文与展开文本相同就原样返回",
      tts_ali.to_original([{"text": "好", "begin_time": 1, "end_time": 2}], "好", "好"),
      [["好", 1.0, 2.0]])

# ---- 3. 语速与音色 ----
print("语速 rate_multiplier / 音色 resolve_voice_token")
check("+8% → 1.08", tts_ali.rate_multiplier("+8%"), 1.08)
check("空值 → 1.0", tts_ali.rate_multiplier(""), 1.0)
check("上限 2.0", tts_ali.rate_multiplier("+200%"), 2.0)
check("下限 0.5", tts_ali.rate_multiplier("-80%"), 0.5)
check("edge 音色 ID 回落默认", tts_ali.resolve_voice_token("zh-CN-YunxiNeural"), tts_ali.DEFAULT_VOICE)
check("火山音色 ID 回落默认", tts_ali.resolve_voice_token("zh_male_m191_uranus_bigtts"),
      tts_ali.DEFAULT_VOICE)
check("显示名 → ID", tts_ali.resolve_voice_token("许玉远"), "xuyuyuan_v3.1")
check("序号 → ID", tts_ali.resolve_voice_token("1"), tts_ali.VOICES[0][1])
check("目录外的 3.1 音色原样返回", tts_ali.resolve_voice_token("longanhuan_v3.1"), "longanhuan_v3.1")

print("脱敏 _redact")
fake = "sk-" + "a1B2" * 10
check("sk- 形态被抹掉", fake in tts_ali._redact(f"boom {fake} boom"), False)
check("request_id 不被误抹", "r-1234567890abcdef" in tts_ali._redact("r-1234567890abcdef"), True)

print("密钥文件 load_credentials")


def creds_with(base_url: str):
    """临时密钥文件里写入给定的 dashScope，返回 load_credentials 的结果或它报的错。"""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "api_key.csv")
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"apiKey,{fake}\ndashScope,{base_url}\n")
        os.environ["ALI_TTS_KEY_FILE"] = path
        try:
            return tts_ali.load_credentials()
        except tts_ali.AliTTSConfigError as e:
            return e
        finally:
            del os.environ["ALI_TTS_KEY_FILE"]


ok = creds_with("https://llm-abc.cn-beijing.maas.aliyuncs.com/api/v1/")
check("https 读出 base_url（去掉末尾 /）",
      ok["base_url"] if isinstance(ok, dict) else ok, "https://llm-abc.cn-beijing.maas.aliyuncs.com/api/v1")
check("大写 HTTPS 也接受", isinstance(creds_with("HTTPS://llm-abc.example.com/api/v1"), dict), True)
for bad in ("http://llm-abc.cn-beijing.maas.aliyuncs.com/api/v1", "file:///etc/passwd", "llm-abc.example.com/api/v1"):
    err = creds_with(bad)
    check(f"拒绝 {bad.split(':')[0] if ':' in bad else '无协议'}",
          isinstance(err, tts_ali.AliTTSConfigError) and "https://" in str(err), True)

if fails:
    print(f"\n失败 {len(fails)} 项：\n")
    for f in fails:
        print("  ✗ " + f)
    sys.exit(1)
print("\n全部通过")
