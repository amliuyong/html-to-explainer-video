#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
tts_ali.py —— 阿里云百炼语音合成（qwen-audio-3.1-tts-flash）接口包。

【为什么单独成一个包】与 tts_volcano.py 同一条纪律：密钥只在这一个文件里被读取，
调用方（含 AI agent）只传「文本 + 音色 + 语速」，永远拿不到也不需要密钥值。

  ★ 不打印、不记录、不回显密钥；异常信息一律脱敏（见 _redact）。
  ★ 只读密钥文件，从不写它；上游返回体、缓存与 manifest 里也不会出现密钥。
  → agent 只需要会调用本包，不需要（也不应该）读密钥文件。

【密钥文件】默认 ~/ali_tts/api_key.csv（两列「字段名,值」，权限 0400）；
  环境变量 ALI_TTS_KEY_FILE 可指向别处。只用到两个字段：
    apiKey     → Authorization: Bearer <apiKey>
    dashScope  → 业务空间专属域名的 base URL（https://llm-xxx.cn-beijing.maas.aliyuncs.com/api/v1）
  接口全貌与实测坑：~/ali_tts/docs/qwen-audio-tts-http-api.md。

【接口】与 tts_volcano.synthesize 同构，tts_build.py 的下游 timing 逻辑零改动：
  synthesize(text, voice, rate=0, ...) -> {
      "audio": bytes,                                        # mp3 字节
      "boundaries": [{"text","offset_sec","duration_sec"}],  # 原始坐标系，单位秒
      "usage": {"input_tokens","output_tokens",...} | {},
      "requests": 1,
  }

【接口规格】（2026-10-14 文档，2026-10-19 在本机复测）
  POST {dashScope}/services/audio/tts/SpeechSynthesizer
  Header: Authorization: Bearer <apiKey> / Content-Type: application/json / X-DashScope-SSE: enable
  Body:   {model, input:{text, voice, format, sample_rate, rate, word_timestamp_enabled, language_hints}}
  响应:   SSE。音频在 sentence-synthesis 帧的 output.audio.data（base64，逐块解码追加）；
          完整字级时间戳在 sentence-end 帧的 output.sentence.words（begin_time/end_time，单位 ms）。
  ★ 业务空间域名把**一个事件拆成多行 data:**，必须按 SSE 规范拼接后再 json.loads
    （dashscope SDK 1.27.7 的流式就栽在这里，所以本包只用标准库）。
  ★ 字级时间戳只在流式下返回，且必须显式传 word_timestamp_enabled=true。
  ★ 多句文本的 begin_time 是**整段音频的绝对时间**（实测第 2 句从 21.92s 接着往后排），
    不需要逐句平移。标点会和相邻字并成一项（如「尾。」），subs.py 对齐时会先剥标点。
  ★ words 落在**展开读法后的文本**上（「2026」→「二零二六」），本包映射回原文后才交给
    下游 —— 不映射会让字幕整体提前好几秒（见 to_original）。
  ★ 判错只认 data 里的 code：业务空间域名出错是 HTTP 400 + event:result，不是 event:error。
  ★ rate 是倍速 [0.5, 2.0]。本包把「快百分之几」换算成 1 + pct/100
    （实测 rate=1.2：7.656s → 6.384s，时间戳同比缩放）。

用法：
  python tts_ali.py --check                         # 连通性自检（用一句话试合成）
  python tts_ali.py --voices                        # 列出内置常用音色
  python tts_ali.py --synth "测试文本" --out a.mp3   # 手动合成一段
  python tts_ali.py --key-file <路径> ...            # 指定密钥文件（默认 ~/ali_tts/api_key.csv）
被 tts_build.py 作为库调用（provider=ali 时）。
"""
from __future__ import annotations

import argparse
import base64
import binascii
import csv
import difflib
import http.client
import json
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tts_volcano  # noqa: E402  —— 语速换算与 MP3 头判定只保留一份

MODEL = "qwen-audio-3.1-tts-flash"
API_PATH = "/services/audio/tts/SpeechSynthesizer"
# 进 tts_build 的缓存签名：boundaries 的产出口径（to_original）一改就换版本号，
# 否则旧缓存里的未映射边界会被原样复用，字幕照样错位（2026-10-19 踩过）
BOUNDARY_MODE = "char-timestamps+orig-v1"
DEFAULT_KEY_FILE = os.path.join(os.path.expanduser("~"), "ali_tts", "api_key.csv")
DEFAULT_TIMEOUT = 60.0
DEFAULT_RETRIES = 3
RETRY_SLEEP = 1.0
RATE_MIN, RATE_MAX = 0.5, 2.0

# 3.1 只认 *_v3.1 音色（区分大小写）；和 3.0 的音色混用会报 InvalidParameter [411]
DEFAULT_VOICE = "xuyuyuan_v3.1"        # 许玉远：女声，知性成熟
DEFAULT_VOICE_EN = "Andy_v3.1"

# 内置常用音色（名称, voice, 说明）。完整 68 个见 ~/ali_tts/docs/qwen-audio-tts-http-api.md §7。
VOICES: list[tuple[str, str, str]] = [
    # —— 中文男声 ——
    ("安明远",      "anmingyuan_v3.1",  "男声 · 清亮自然，旁白/有声书"),
    ("霍拙石",      "huozhuoshi_v3.1",  "男声 · 清亮，有声书/旁白"),
    ("龙三叔",      "longsanshu_v3.1",  "男声 · 沉稳质感，有声书"),
    ("龙安智",      "longanzhi_v3.1",   "男声 · 睿智轻熟"),
    ("龙安朗",      "longanlang_v3.1",  "男声 · 清爽利落"),
    ("许南川",      "xunanchuan_v3.1",  "男声 · 支持方言与多语种"),
    # —— 中文女声 ——
    ("许玉远",      "xuyuyuan_v3.1",    "女声 · 知性成熟，新闻播报/旁白（默认）"),
    ("闻怀之",      "wenhuaizhi_v3.1",  "女声 · 稳重成熟，有声书/新闻播报"),
    ("谢舒柔",      "xieshurou_v3.1",   "女声 · 柔和知性，有声书/旁白"),
    ("安小岚",      "anxiaolan_v3.1",   "女声 · 清甜纯净，旁白/新闻播报"),
    ("于小云",      "yuxiaoyun_v3.1",   "女声 · 元气亲切，广告/旁白"),
    ("白安然",      "baianran_v3.1",    "女声 · 低沉浑厚带气声，配音讲解"),
    ("许言初",      "xuyanchu_v3.1",    "女声 · 沉稳磁性，新闻播报"),
    # —— 英文（只支持英文文本）——
    ("Andy",        "Andy_v3.1",        "美式英语 · 男声"),
    ("David",       "David_v3.1",       "美式英语 · 男声"),
    ("Ava",         "Ava_v3.1",         "美式英语 · 女声"),
    ("Eric",        "Eric_v3.1",        "英式英语 · 男声"),
    ("Emily",       "Emily_v3.1",       "英式英语 · 女声"),
]

VOICE_BY_NAME = {name: vid for name, vid, _ in VOICES}
VOICE_IDS = {vid for _, vid, _ in VOICES}

# edge（…Neural）与火山（…_bigtts）的音色 ID 形态。旧项目的 project.json 里常留着这种 ID，
# 原样送给阿里只会换来一句看不出原因的 `Engine error [411]`。
_FOREIGN_VOICE = re.compile(r"(Neural|_bigtts)$")


def is_foreign_voice(token: str) -> bool:
    return bool(_FOREIGN_VOICE.search((token or "").strip()))


def resolve_voice_token(token: str, default: str = "") -> str:
    """把「名称 / 序号 / 原始 ID」统一成音色 ID。空 token 或别家引擎的 ID → default。"""
    token = (token or "").strip()
    if not token or is_foreign_voice(token):
        return default or DEFAULT_VOICE
    if token.isdigit() and 1 <= int(token) <= len(VOICES):
        return VOICES[int(token) - 1][1]
    if token in VOICE_BY_NAME:
        return VOICE_BY_NAME[token]
    if token in VOICE_IDS:
        return token
    for name, vid, _ in VOICES:              # 允许带括注的名字，如「安明远（默认）」
        if name.startswith(token) or token.startswith(name):
            return vid
    return token                             # 目录外的 3.1 音色 / 复刻音色原样返回


def rate_multiplier(value) -> float:
    """'+8%' / '8' / 0.08 / 8 → 1.08，夹在 [0.5, 2.0]。百分比解析与火山共用一份。"""
    pct = tts_volcano.parse_rate_percent(value)
    return round(min(RATE_MAX, max(RATE_MIN, 1.0 + pct / 100.0)), 3)


class AliTTSConfigError(RuntimeError):
    """缺密钥文件或字段不全 —— 由 tts_setup.py 负责引导用户补齐。"""

    retryable = False                        # tts_build 的外层重试看这个属性


class AliTTSError(RuntimeError):
    """调用失败（已脱敏，可安全打印）。

    retryable=False 表示**重试没有意义**（Key 错、音色错、内容拦截、欠费这类）。
    退避重试只对网络抖动、超时、5xx、限流有意义。
    """

    def __init__(self, message: str, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


# ---------------------------------------------------------------- 密钥读取
def key_file() -> str:
    return os.environ.get("ALI_TTS_KEY_FILE", "").strip() or DEFAULT_KEY_FILE


def load_credentials() -> dict[str, str]:
    """读取密钥。返回的 dict 里含密钥，**调用方不得打印或落盘**。"""
    path = key_file()
    if not os.path.isfile(path):
        raise AliTTSConfigError(
            f"找不到阿里云百炼密钥文件 {path}。\n"
            "  格式：两列「字段名,值」的 CSV，至少要有 apiKey 与 dashScope 两行；\n"
            "  放在别处时设环境变量 ALI_TTS_KEY_FILE=<路径>。")
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            vals = {r[0].strip(): r[1].strip() for r in csv.reader(f) if len(r) == 2}
    except OSError as e:
        raise AliTTSConfigError(f"读不了密钥文件 {path}：{e.strerror}")
    api_key = vals.get("apiKey", "")
    base_url = vals.get("dashScope", "").rstrip("/")
    missing = [n for n, v in (("apiKey", api_key), ("dashScope", base_url)) if not v]
    if missing:
        raise AliTTSConfigError(f"{path} 缺字段：{', '.join(missing)}（两列「字段名,值」）")
    # Bearer 密钥随每次请求发出：http:// 会明文外泄，file:// 等别的协议也不该交给 urlopen
    if not base_url.lower().startswith("https://"):
        raise AliTTSConfigError(f"{path} 的 dashScope 必须是 https:// 开头的 base URL")
    return {"path": path, "api_key": api_key, "base_url": base_url}


def describe_env() -> dict[str, str]:
    """给 agent/用户看的**脱敏**状态摘要 —— 这个函数的输出可以安全打印。"""
    try:
        c = load_credentials()
    except AliTTSConfigError as e:
        return {"status": "missing", "detail": str(e).splitlines()[0]}
    return {
        "status": "ok",
        "key_file": c["path"],
        "key_masked": _mask(c["api_key"]),
        "endpoint": _mask_endpoint(c["base_url"]),
    }


def _mask(secret: str) -> str:
    return tts_volcano._mask(secret)


def _mask_endpoint(url: str) -> str:
    """业务空间 ID 不是密钥，但也没必要整串打到日志里。"""
    return re.sub(r"llm-[A-Za-z0-9]+", "llm-***", url)


# 形如 sk-xxxx 的百炼 API Key —— 只认这种明确形态，避免把 request_id 也抹掉
_SECRET_PAT = re.compile(r"sk-[A-Za-z0-9]{16,}")


def _redact(text: str) -> str:
    """把可能混进错误信息的密钥抹掉。异常与日志一律过这一层。"""
    try:
        v = load_credentials()["api_key"]
        if len(v) >= 8 and v in text:
            text = text.replace(v, _mask(v))
    except Exception:
        pass
    return _SECRET_PAT.sub(lambda m: _mask(m.group(0)), text)


# ---------------------------------------------------------------- SSE 解析
def _iter_events(lines):
    """行流 → (event, data 文本)。同一事件的多行 data: 用 \\n 拼起来（SSE 规范）。"""
    event, data = None, []
    for raw in lines:
        line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
        line = line.rstrip("\r\n")
        if not line:                         # 空行 = 一个事件结束
            if data:
                yield event, "\n".join(data)
            event, data = None, []
        elif line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:])
        # `id:` 与 `:HTTP_STATUS/200` 注释行都不需要
    if data:                                 # 错误帧之后流末尾没有空行
        yield event, "\n".join(data)


_HINTS = {
    "InvalidApiKey": "核对密钥文件里的 apiKey；Key 与 dashScope 域名必须是同一地域",  # pragma: allowlist secret（错误码，不是密钥）
    "DataInspectionFailed": "内容安全拦截：改写这句解说词再试",
    "Arrearage": "账号欠费：到百炼控制台充值",
    "AllocationQuota.FreeTierOnly": "免费额度已用完且开启了「用完即停」",
    "ModelNotFound": f"模型名不对或没有 {MODEL} 的调用权限",
}


def _api_error(msg: dict, http_status: int | None = None) -> AliTTSError:
    code = str(msg.get("code") or "")
    message = str(msg.get("message") or "")
    retryable = (code.startswith("Throttling") or http_status == 429
                 or (http_status or 0) >= 500)
    hint = _HINTS.get(code, "")
    if code == "InvalidParameter" and "[411]" in message:
        hint = "音色不存在或与模型不匹配：3.1 只认 *_v3.1 音色（区分大小写）"
    head = f"HTTP {http_status} · " if http_status else ""
    tail = f"\n  → {hint}" if hint else ""
    return AliTTSError(
        _redact(f"{head}{code}: {message} (request_id={msg.get('request_id', '?')}){tail}"),
        retryable=retryable)


def to_original(words: list[dict], original: str, normalized: str) -> list[list]:
    """把规范化文本上的字级时间戳映射回原文。返回 [[原文片段, begin_ms, end_ms], ...]。

    服务端会先展开数字与符号的读法（「2026」→「二零二六」、「2.4%」→「百分之二点四」、
    英文「2026」→「twenty twenty six」），words 的 text 与 begin_index 都落在展开后的
    normalized_text 上；展不展开还因句而异（同一会话里两种都实测到过）。
    下游 subs.py 拿词文本在**原文**里顺序查找来对齐：展开出来的「二」会误配到后文
    「第二」的「二」上，把之后几块字幕整体提前好几秒（实测 4–7s，且不报错）。

    做法：difflib 对齐两份文本。equal 段逐字对应；replace 段（数字 ↔ 读法）整段
    归给第一个落进来的词，同段后续的词只延长它的结束时间；delete 段（展开文本多出、
    原文没有的字）丢弃。原文多出的字没有时间戳，下游本来就会跳过。
    这里的「原文」是送出去的文本：tts_build 与 --synth 送之前已去掉切块符 `|`（lessons #142）。
    """
    items = [[w.get("text") or "", float(w.get("begin_time") or 0), float(w.get("end_time") or 0)]
             for w in words]
    if not original or not normalized or original == normalized:
        return items
    nmap: list = [None] * len(normalized)
    sm = difflib.SequenceMatcher(None, normalized, original, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        for i in range(i1, i2):
            if tag == "equal":
                k = j1 + i - i1
                nmap[i] = (None, k, k + 1)
            elif tag == "replace":
                nmap[i] = (i1, j1, j2)       # i1 兼作这段 replace 的标识
    out: list[list] = []
    claimed: set[int] = set()
    cur = 0
    for text, begin, end in items:
        p = normalized.find(text, cur) if text else -1
        if p < 0:                            # 对不上就原样保留，不比不映射更差
            out.append([text, begin, end])
            continue
        cur = p + len(text)
        spans, merged = [], False
        for i in range(p, cur):
            m = nmap[i]
            if m is None:
                continue
            block, j1, j2 = m
            if block is not None:
                if block in claimed:
                    merged = True
                    continue
                claimed.add(block)
            spans.append((j1, j2))
        if spans:
            out.append([original[min(a for a, _ in spans):max(b for _, b in spans)], begin, end])
        elif merged and out:
            out[-1][2] = max(out[-1][2], end)
    return out


def parse_stream(lines) -> tuple[bytes, list[dict], dict]:
    """SSE 行流 → (音频字节, boundaries, usage)。遇到错误帧抛 AliTTSError。"""
    audio = bytearray()
    words_by_sentence: dict[int, list] = {}
    usage: dict = {}
    for _event, payload in _iter_events(lines):
        try:
            msg = json.loads(payload)
        except ValueError:
            raise AliTTSError("SSE 帧不是合法 JSON（流可能被截断）")
        if msg.get("code"):                  # ★ 判错只认 code（见模块头）
            raise _api_error(msg)
        out = msg.get("output") or {}
        b64 = (out.get("audio") or {}).get("data")
        if b64:
            try:
                audio.extend(base64.b64decode(b64))
            except (binascii.Error, ValueError):
                raise AliTTSError("音频块 base64 解码失败（流可能被截断）")
        # begin / synthesis 帧里的 words 是逐步增长的半成品，只认 sentence-end
        if out.get("type") == "sentence-end":
            sent = out.get("sentence") or {}
            idx = int(sent.get("index", len(words_by_sentence)))
            words_by_sentence[idx] = to_original(
                sent.get("words") or [], out.get("original_text") or "",
                out.get("normalized_text") or "")
        if msg.get("usage"):
            usage = msg["usage"]

    boundaries: list[dict] = []
    for idx in sorted(words_by_sentence):
        for text, begin_ms, end_ms in words_by_sentence[idx]:
            txt = text.strip()
            if not txt:
                continue
            st = begin_ms / 1000.0
            et = end_ms / 1000.0
            boundaries.append({
                "text": txt,
                "offset_sec": round(st, 4),
                "duration_sec": round(max(0.0, et - st), 4),
            })
    return bytes(audio), boundaries, usage


def _http_error(e: urllib.error.HTTPError) -> AliTTSError:
    """HTTP 层错误。流式出错时响应体是一帧 SSE，非流式/鉴权错误是普通 JSON。"""
    try:
        body = e.read().decode("utf-8", "replace")
    except Exception:
        body = ""
    msg = None
    try:
        for _event, payload in _iter_events(body.splitlines()):
            msg = json.loads(payload)
            break
        if msg is None:
            msg = json.loads(body)
    except ValueError:
        msg = None
    if isinstance(msg, dict) and msg.get("code"):
        return _api_error(msg, http_status=e.code)
    return AliTTSError(f"HTTP {e.code} {e.reason}\n  {_redact(body[:400])}",
                       retryable=e.code == 429 or e.code >= 500)


# ---------------------------------------------------------------- 请求
def _one_request(text: str, voice: str, cred: dict[str, str], rate: float,
                 fmt: str, sample_rate: int, timeout: float,
                 language: str = "", instruction: str = "") -> tuple[bytes, list[dict], dict]:
    inp: dict = {"text": text, "voice": voice, "format": fmt, "sample_rate": sample_rate,
                 # ★ 不传这个开关，sentence.words 恒为空 —— 字幕只能退回按字数插值
                 "word_timestamp_enabled": True}
    if abs(rate - 1.0) > 1e-6:
        inp["rate"] = rate
    if language:
        inp["language_hints"] = [language]   # 只处理第一个元素；改善数字/符号读法
    if instruction:
        inp["instruction"] = instruction
    req = urllib.request.Request(
        cred["base_url"] + API_PATH,
        data=json.dumps({"model": MODEL, "input": inp}, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={"Authorization": "Bearer " + cred["api_key"],
                 "Content-Type": "application/json",
                 "X-DashScope-SSE": "enable"})
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)  # nosec B310 - base_url 已在 load_credentials 限定为 https://
    except urllib.error.HTTPError as e:      # 必须在 URLError 之前（它是子类）
        raise _http_error(e) from None
    except urllib.error.URLError as e:
        raise AliTTSError(f"网络不可达：{_redact(str(e.reason))}") from None
    except (TimeoutError, socket.timeout):
        raise AliTTSError(f"请求超时（{timeout}s）") from None
    try:
        with resp:
            return parse_stream(resp)
    except (TimeoutError, socket.timeout):
        raise AliTTSError(f"读流超时（{timeout}s）") from None
    except (http.client.IncompleteRead, ConnectionError) as e:
        raise AliTTSError(f"读流中断：{type(e).__name__}") from None


def synthesize(text: str, voice: str = DEFAULT_VOICE, rate=0,
               fmt: str = "mp3", sample_rate: int = 24000,
               timeout: float = DEFAULT_TIMEOUT, retries: int = DEFAULT_RETRIES,
               language: str = "", instruction: str = "") -> dict:
    """合成一段文本 → {audio, boundaries, usage, requests}。失败抛 AliTTSError。

    rate 是「快百分之几」（'+8%' / 8 / 0.08 均可），内部换算成倍速。
    """
    text = (text or "").strip()
    if not text:
        raise AliTTSError("文本为空", retryable=False)
    voice = resolve_voice_token(voice, DEFAULT_VOICE)
    cred = load_credentials()
    mult = rate_multiplier(rate)

    last: Exception | None = None
    tries = max(1, retries)
    for attempt in range(tries):
        try:
            audio, bounds, usage = _one_request(
                text, voice, cred, mult, fmt, sample_rate, timeout, language, instruction)
            if not audio:
                raise AliTTSError("没有收到任何音频块（检查音色 ID 与文本是否为空）")
            if fmt == "mp3" and not tts_volcano._looks_like_mp3(audio):
                raise AliTTSError("返回的音频不是有效 MP3 头（可能被截断）")
            result = {"audio": audio, "boundaries": bounds, "usage": usage, "requests": 1}
            # ★ 要了时间戳却一个都没回来：静默放行会让下游字幕整段塌成按字数插值
            if not bounds:
                result["warning"] = (
                    f"音色 {voice} 未返回字级时间戳（sentence.words 为空），"
                    f"字幕将退化为按字数插值 —— 建议换回 {DEFAULT_VOICE}。")
            return result
        except AliTTSError as e:
            if not e.retryable:
                raise                        # Key/音色/内容错，重试是白等
            last = e
        except Exception as e:               # noqa: BLE001
            last = e
        if attempt + 1 < tries:
            time.sleep(RETRY_SLEEP * (attempt + 1))
    raise AliTTSError(_redact(f"连续 {tries} 次失败：{type(last).__name__}: {last}"))


def _check_text(voice: str) -> tuple[str, str]:
    # 英文音色只支持英文文本；ID 首字母大写的就是英文音色（Andy_v3.1 / Emily_v3.1 …）
    if voice[:1].isupper():
        return "Connection test. This sentence only checks that the API works.", "en"
    return "连接测试，这句话只用来验证接口是否可用。", "zh"


def check(voice: str = "", timeout: float = 30.0) -> dict:
    """连通性自检：用一句短文本试合成。返回可安全打印的状态 dict。"""
    try:
        cred = load_credentials()
    except AliTTSConfigError as e:
        return {"ok": False, "stage": "key", "detail": str(e)}
    v = resolve_voice_token(voice)
    text, lang = _check_text(v)
    try:
        r = synthesize(text, voice=v, timeout=timeout, retries=1, language=lang)
    except AliTTSError as e:
        return {"ok": False, "stage": "api", "voice": v, "key_file": cred["path"],
                "detail": str(e)}
    return {
        "ok": True, "stage": "api", "voice": v,
        "key_file": cred["path"],
        "key_masked": _mask(cred["api_key"]),
        "endpoint": _mask_endpoint(cred["base_url"]),
        "audio_bytes": len(r["audio"]),
        "boundaries": len(r["boundaries"]),
        "output_tokens": (r.get("usage") or {}).get("output_tokens", 0),
        "warning": r.get("warning", ""),
    }


# ---------------------------------------------------------------- CLI
def _print_voices() -> None:
    print(f"阿里云百炼 {MODEL} 内置常用音色（完整 68 个见 ~/ali_tts/docs）：")
    for i, (name, vid, note) in enumerate(VOICES, 1):
        print(f"  {i:>2}. {name:<12} {note}\n      {vid}")


def main() -> int:
    p = argparse.ArgumentParser(description=f"阿里云百炼语音合成接口包（{MODEL}）")
    p.add_argument("--key-file", default="", help="密钥文件（默认 ~/ali_tts/api_key.csv）")
    p.add_argument("--check", action="store_true", help="连通性自检")
    p.add_argument("--voices", action="store_true", help="列出内置音色")
    p.add_argument("--synth", default="", help="合成一段文本（可直接贴带 | 的解说词，送之前去掉 |）")
    p.add_argument("--out", default="ali_tts_test.mp3")
    p.add_argument("--voice", default="")
    p.add_argument("--rate", default="", help="快百分之几：+8%% / 8 / -10")
    p.add_argument("--lang", default="", help="language_hints，如 zh / en")
    args = p.parse_args()

    if args.key_file:
        os.environ["ALI_TTS_KEY_FILE"] = args.key_file

    if args.voices:
        _print_voices()
        return 0

    if args.check:
        st = check(args.voice)
        if st["ok"]:
            print(f"✓ 连接正常 · 音色 {st['voice']} · 音频 {st['audio_bytes']}B · "
                  f"时间戳 {st['boundaries']} 个 · 输出 {st['output_tokens']} token")
            print(f"  密钥文件: {st['key_file']} · 密钥 {st['key_masked']}")
            print(f"  端点: {st['endpoint']}")
            if st["warning"]:
                print(f"⚠ {st['warning']}", file=sys.stderr)
            return 0
        print(f"✗ 未通过（{st['stage']}）\n{st['detail']}", file=sys.stderr)
        return 1

    if args.synth:
        # 和 tts_build 送一样的文本（去掉切块符 `|`，lessons #142）：拿解说词原文试听，结果才和正式配音一致
        import subs
        text = subs.strip_chunk_marks(args.synth)
        if text != args.synth:
            print(f"  已去掉切块符 |（和 tts_build 一样），送出：{text}", file=sys.stderr)
        try:
            r = synthesize(text, voice=args.voice, rate=args.rate, language=args.lang)
        except (AliTTSError, AliTTSConfigError) as e:
            print(f"✗ {e}", file=sys.stderr)
            return 1
        with open(args.out, "wb") as f:
            f.write(r["audio"])
        print(f"✓ {args.out} · {len(r['audio'])}B · 时间戳 {len(r['boundaries'])} 个 · "
              f"倍速 {rate_multiplier(args.rate)} · usage {r['usage']}")
        if r.get("warning"):
            print(f"⚠ {r['warning']}", file=sys.stderr)
        return 0

    p.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
