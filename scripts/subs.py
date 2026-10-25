#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
subs.py —— 字幕三出口 + 节拍器 beats.js。

【血统】字幕方案移植自 anything2explainer（tts_build.py + make_srt.py），
经 html-video-workbuddy-driver 实战修订。核心不变式：

  ① 块用 | 切（中文 ≤16 字 / 英文 ≤48 字符），整句一次送 TTS（词边界最准）
  ② 每块首字对齐到 WordBoundary 给的秒 → 帧号（绝不按字数插值：
     中文同字数时长能差 3 倍，插值一定飘）
  ③ 两级时钟：段长用容器时长（音画同步），末块收尾用语音真实结束
     （speech_end_sec，裁后坐标系）—— 混用会「字幕过了语音还没过」
  ④ 停留重分配：按真实语速比例 + 每块 ≥0.6s（water-filling）
  ⑤ 屏幕文本去句读标点（标点只驱动 TTS 停顿，不上画面）；
     **唯独词里的点要留**（左右都是数字或 ASCII 字母）—— 2.4% 削成 24% 是差一个数量级的假数字，
     ASN.1 / X.509 削成 ASN1 / X509 是另一个名字
  ⑥ 块首那一项要是「停顿 + 下一个英文词的首字母」（阿里：「：|p」+「andas」），块从这一项的结尾起，
     不从停顿开头起（lessons #144）；停顿并进的是 2–3 个字母（「；|Not」+「ion」）时，从这一项的结尾
     往前退 0.125 s（lessons #152）
  ⑦ 英文片里块首那一项带着前一句的标点（「. Today」「, and」）时，读这一段的配音找开口，块从开口往前退
     0.125 s 起，不从这一项的开头起 —— 只看时间戳分不出这一项里有没有停顿（lessons #158）
  ⑧ project.json 的 hold（timeline_build 记进 layout 的 hold_sec）算进段时长与 __SEG__.duration，
     不挂字幕：末块照旧收在语音真实结束（lessons #157）

【本技能新增】beats.js 节拍器 —— 把每块的本地秒写进 frames/<id>.beats.js，
场景 HTML 里用 B('文本') 拿到该词的起播秒，画面节拍直接对齐吐字节拍。
解说词改动后重跑 tts_build → 本脚本，beats.js 全部自动刷新，
帧代码一行不用改（这比 anything2explainer 的「帧号硬编码」省一轮全片重对位）。

用法：
  python subs.py --project . [--fps 30]
产出：
  subs.json + frames/<id>.beats.js + out/<slug>.srt / .vtt
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
from array import array
from itertools import accumulate

FPS_DEFAULT = 30
SUB_SIZE = 44
SUB_MAX_W = 1160
SUB_MIN_SIZE = 34
SUB_BUDGET_ZH = 16
SUB_BUDGET_EN = 48
MIN_HOLD = 0.6

_EM = {'space': 0.227, 'upper': 0.668, 'digit': 0.59, 'lower': 0.566,
       'accent': 0.58, 'punct': 0.325}


def text_em(s: str) -> float:
    t = 0.0
    for ch in s:
        c = ord(ch)
        if c >= 0x2000:
            t += 1.0
        elif ch == ' ':
            t += _EM['space']
        elif 'A' <= ch <= 'Z':
            t += _EM['upper']
        elif '0' <= ch <= '9':
            t += _EM['digit']
        elif 'a' <= ch <= 'z':
            t += _EM['lower']
        elif 0xc0 <= c < 0x250:
            t += _EM['accent']
        else:
            t += _EM['punct']
    return t


def fit_size(text, max_w=SUB_MAX_W, size=SUB_SIZE, floor=SUB_MIN_SIZE):
    w = text_em(text) * size
    if w <= max_w:
        return size
    return max(floor, int(size * max_w / w))


def will_wrap(text):
    return text_em(text) * SUB_MIN_SIZE > SUB_MAX_W


def has_cjk(s: str) -> bool:
    return any('\u4e00' <= c <= '\u9fff' for c in s)


_SENT_END = '。！？；'
_PAUSE = '，、：'


def split_chunks(text: str):
    if '|' in text:
        return [c.strip() for c in text.split('|') if c.strip()]
    return auto_split(text)


# 切块符 `|` 的另一半：送 TTS 之前去掉（lessons #142）。和 split_chunks 放在一起，`|` 怎么用只在这里定义。
_CHUNK_MARKS = re.compile(r'\s*(?:\|\s*)+')
_ASCII_ALNUM = re.compile(r'[0-9A-Za-z]')
_LATIN_STOP = re.compile(r'[.,;:!?)\]"\'…]')   # 半角句读、右括号、引号、省略号：英文里后面跟空格再接下一个词
_LATIN_TAIL = re.compile(r'[0-9A-Za-z.,;:!?)\]"\']')   # 句读前面是英文（词尾、引号、括号），才算英文的句读


def strip_chunk_marks(text: str) -> str:
    """送给 TTS 的文本：去掉切块符 `|`。tts_build（三个引擎）与 tts_ali / tts_volcano 的 --synth 都用它。

    `|` 只管屏幕上怎么分块。原样送给阿里，它有时读成「竖线」（紧跟在 、：；后面最容易），
    两个 `|` 夹着的短块还会读成「绝对值」。停顿归标点管（「，|」「：|」的停顿本来就在标点上），
    所以去掉 `|` 不补停顿：
      - 两边是中文、标点，或在段首段尾 → 直接去掉（「判断|这套」照常连读，不再停一下）
      - 两边都是 ASCII 字母或数字 → 换成一个空格（「Intensive|Applications」不连成一个词，「300|400」不连成一个数）
      - 左边是英文后面的半角句读（. , ; : ! ?、右括号、引号或省略号），右边是字母或数字 → 换成一个空格：英文句读后面接下一个词
        （「minutes.|Today」「warehouse,|and」），不补空格就念成「minutes.Today」（lessons #145）。
        别的 ASCII 符号照旧直接连上（「TCP/|IP」「Data-|Intensive」「(|Kafka」「$|100」），全角标点、中文后面的半角标点也是
        （「：|p」「数据,|Kafka」）
      - `|` 旁边本来就有空白 → 收成一个空格
    没有 `|` 的文本原样返回。服务端的字级时间戳落在去掉 `|` 的文本上；chunk_starts 对的 tts_text
    是各块直接拼起来的，同样没有 `|`，所以每块照旧对到自己首字的时间戳。
    """
    if '|' not in text:
        return text

    def _join(m):
        a = text[m.start() - 1] if m.start() > 0 else ''
        b = text[m.end()] if m.end() < len(text) else ''
        if not a or not b:
            return ''
        spaced = any(ch != '|' for ch in m.group(0))
        words = _ASCII_ALNUM.match(a) and _ASCII_ALNUM.match(b)
        before = text[m.start() - 2] if m.start() >= 2 else ''
        after_stop = _LATIN_STOP.match(a) and _ASCII_ALNUM.match(b) and _LATIN_TAIL.match(before)
        return ' ' if spaced or words or after_stop else ''

    return _CHUNK_MARKS.sub(_join, text)


def auto_split(text: str, budget=None):
    zh = has_cjk(text)
    budget = budget or (SUB_BUDGET_ZH if zh else SUB_BUDGET_EN)
    sents, cur = [], ''
    for ch in text:
        cur += ch
        if ch in _SENT_END:
            sents.append(cur.strip()); cur = ''
    if cur.strip():
        sents.append(cur.strip())
    out = []
    for s in sents:
        if len(s) <= budget:
            out.append(s); continue
        parts, cur = [], ''
        for ch in s:
            cur += ch
            if ch in _PAUSE:
                parts.append(cur.strip()); cur = ''
        if cur.strip():
            parts.append(cur.strip())
        buf = ''
        for p in parts:
            if not buf:
                buf = p
            elif len(buf) + len(p) <= budget:
                buf += p
            else:
                out.append(buf); buf = p
        if buf:
            while len(buf) > budget:
                out.append(buf[:budget]); buf = buf[budget:]
            if buf:
                out.append(buf)
    return out or [text]


# 词里的字：数字（含全角）或 ASCII 字母。点的左右都是它，这个点就是词的一部分，不是句读。
_TOKEN_CH = re.compile(r'[\dA-Za-z]')


def is_token_dot(s: str, i: int) -> bool:
    """s[i] 是不是「词里的点」？左右各需一个数字或 ASCII 字母
    （2.4 / 1.2.3 / ASN.1 / X.509 / v1.5 / node.js / S3.Select）。

    去标点是为了 TTS 不被截断、画面不被句读打断；但词里的点不是句读，它是数字或名字的
    一部分 —— 删掉它「2.4%」会变成「24%」（数字差 10 倍），「X.509」会变成「X509」，
    屏幕文本就撒谎了（lessons #88、#143）。句末、缩写末尾（「e.g.」的第二个点）、前导「.5」照删。
    """
    if i <= 0 or i >= len(s) - 1 or s[i] != '.':
        return False
    return bool(_TOKEN_CH.match(s[i - 1]) and _TOKEN_CH.match(s[i + 1]))


# 词里的点（左右都是数字或 ASCII 字母）不能删；其余位置（句末、缩略、省略）照删。
# 注意 `.` 不能留在字符类里，否则一定被吃掉。
# 注意断言的**位置**：`X(?<!\d)` 断言的是 X 右边的字符，不是左边。要判「点的左边」
# 必须把断言写在点之前（写反了不会报错，只会静默失效）。
_PUNCT_RE = re.compile(r'[\s，。、！？：；“”（）,!?;:()\-—…·|]|(?<![\dA-Za-z])\.|\.(?![\dA-Za-z])')

# 停顿被标成下一个英文词的首字母（lessons #144）。标点后面紧跟英文词时，阿里常把停顿并进那个词的
# 首字母，给成一项：「：|p」0.375 s，后面一项「andas」（2.1.2 起送出去的文本没有 `|`，就是「：p」）。
# 块起点要是取这一项的开头，就落在停顿开头，比吐字早 0.3–0.7 s。
# 一个系列 19 集里这样的项有 11 个，都 ≥ 0.31 s；不盖停顿的首字母项最长 0.19 s（插值的段每字一样长，
# 到过 0.27 s / 0.31 s）。音频里开口比「andas」那一项的开头晚 0.12 s（中位数），和普通块起点比开口早的量
# （0.12 s，3148 块）一样 —— 字母本身的时长已经算进停顿那一项了，所以块起点就取这一项的结尾，不再往前减。
PAUSE_LETTER_MIN_SEC = 0.3
_ONE_LETTER = re.compile(r'[A-Za-z]')
_LOWER = re.compile(r'[a-z]')
# 前缀是 2–3 个字母时（「；|Not」+「ion」「bl」+「ameless」，lessons #152）字母本身已经在这一项里开口了：
# 一个系列 25 集里 5 个这样的块首，开口比后半截的开头早 0.15 s 到晚 0.12 s。块起点取这一项的结尾再往前退
# 一个普通块的提前量（0.125 s，5213 块的中位数 0.126），5 块里最晚的比开口晚 0.03 s（不到 30 fps 的一帧），
# 最早的提前 0.24 s（原来 0.28–0.68 s）。0.3 s 的门槛照用：25 集里紧接着小写后半截的 2–3 字母项 48 个，
# ≥ 0.3 s 的 11 个里都有 ≥ 0.2 s 的静音，不盖停顿的最长 0.27 s（「omb」+「stone」）。
# 后两个字母是大写的（「API」+「s」）是拼读，一个字母一个音节，不动。
_SHORT_PREFIX = re.compile(r'[A-Za-z][a-z]{1,2}')
PAUSE_PREFIX_BACKOFF_SEC = 0.125

# 英文片：前一句的标点并进下一个词（lessons #158）。阿里的英文词级时间戳给成「. Today」「. Colvenna」「, and」，
# 这一项的开头离开口还有一截静音：一部英文片里 3 个块首比开口早 0.24 / 0.33 / 0.35 s（普通块 0.12 s）。
# 只看时间戳分不出来 —— 同样带前置标点的「. Maintenance」「, MongoDB」开头就在开口前 0.10 / 0.13 s，
# 项的时长、前面空档的长短、句号还是逗号都分不开这两类。所以读这一段的配音：
#   在这一项开头附近（开头之后 CARRIED_PAUSE_REACH_SEC 以内开始）找第一段静音（≥ SILENCE_MIN_SEC、
#   比段内峰值低 SILENCE_DB 以上），静音之后第一次到峰值 ONSET_DB 算开口（#144、#152 量开口的口径），
#   块从开口往前退 PAUSE_PREFIX_BACKOFF_SEC（普通块的提前量）起 —— 只往后挪，不超过这一项的结尾。
# 两部英文片（Andy_v3.1，+0%）42 个块首里带前置标点的 28 个，插值的段（#140）里 4 个不看；24 个里 11 个挪了
# （早 0.13–0.35 s → 0.125 s），13 个不动（10 个本来只早 0.07–0.125 s，3 个开头附近没有停顿）。
# 中文片的标点是全角的，碰不到这条。
_CARRIED_PUNCT = re.compile(r'[\s|]*[.,;:!?…]')
ENV_SR = 16000              # 量能量包络的采样率
ENV_HOP_SEC = 0.005         # 包络一格 5 ms
ENV_WIN_SEC = 0.02          # 每格取 20 ms 窗的均方能量
SILENCE_DB = -45.0
SILENCE_MIN_SEC = 0.08
ONSET_DB = -20.0
CARRIED_PAUSE_REACH_SEC = 0.25


def _t_of(w):
    if 't' in w:
        return float(w['t'])
    return float(w.get('offset_sec', 0.0))


def _word_onset_after_pause(words, k, wts, at, i):
    """块首字 tts_text[i] 由第 k 项给出。这一项要是「停顿 + 英文词的首字母」，返回块该从哪一秒起
    （原始坐标系）；不是就返回 None（lessons #144、#152）。

    五个条件都要满足，缺一个就照旧用这一项的开头：
      - 这一项去掉标点后是一个 ASCII 字母，或 2–3 个字母且后面的是小写（「Not」「Ro」「bl」），就是块首字；
      - 下一项紧接在它后面（tts_text 里紧跟这几个字母），以小写字母开头 —— 同一个词的后半截。
        大写开头的是拼读的字母（「；|B」+「SO」、「O」+「L」），一个字母一个音节，本来就长；
      - 这一项 ≥ PAUSE_LETTER_MIN_SEC；
      - 它从前一项的结尾开始（空档 < 0.03 s）。停顿在前一项后面的空档里时，这一项从吐字开始；
      - 它和前一项不一样长。插值的段每字一样长（lessons #140），长不说明有停顿。
    一个字母：这一项的结尾（不晚于下一项的开头）。2–3 个字母：再往前退 PAUSE_PREFIX_BACKOFF_SEC。
    """
    if k is None or k == 0 or k + 1 >= len(words):
        return None
    one = _ONE_LETTER.fullmatch(wts[k]) is not None
    if not (one or _SHORT_PREFIX.fullmatch(wts[k])) or at.get(k) != i:
        return None
    if at.get(k + 1) != i + len(wts[k]) or not _LOWER.match(wts[k + 1]):
        return None
    w, prev = words[k], words[k - 1]
    dur, prev_dur = w.get('duration_sec'), prev.get('duration_sec')
    if dur is None or prev_dur is None or float(dur) < PAUSE_LETTER_MIN_SEC:
        return None
    off = _t_of(w)
    if off - (_t_of(prev) + float(prev_dur)) >= 0.03 or abs(float(dur) - float(prev_dur)) < 0.005:
        return None
    end = min(off + float(dur), _t_of(words[k + 1]))
    return end if one else end - PAUSE_PREFIX_BACKOFF_SEC


def ffmpeg_exe() -> str:
    from shutil import which
    p = which("ffmpeg")
    if p:
        return p
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def speech_envelope(path):
    """一段配音的能量包络：每 ENV_HOP_SEC 一格（第 i 格从 i × ENV_HOP_SEC 起），ENV_WIN_SEC 窗的均方能量，
    换成 dB、相对这一段的峰值（峰值那格是 0）。读不了（文件不在、没有 ffmpeg、解不出声音）返回 None 并提醒。"""
    pcm, why = array('h'), ''
    try:
        r = subprocess.run([ffmpeg_exe(), "-v", "error", "-i", path, "-f", "s16le", "-ac", "1",
                            "-ar", str(ENV_SR), "-"], capture_output=True)
        if r.returncode:
            why = r.stderr.decode("utf-8", "replace").strip()[-160:] or f"ffmpeg 退出码 {r.returncode}"
        else:
            pcm.frombytes(r.stdout[:len(r.stdout) // 2 * 2])
    except (OSError, ImportError) as e:
        why = str(e)
    if sys.byteorder == 'big':
        pcm.byteswap()
    hop, win = int(ENV_HOP_SEC * ENV_SR), int(ENV_WIN_SEC * ENV_SR)
    energy = []
    if len(pcm) >= win:
        c = list(accumulate((v * v for v in pcm), initial=0))
        energy = [c[i + win] - c[i] for i in range(0, len(pcm) - win + 1, hop)]
    peak = max(energy, default=0)
    if peak <= 0:
        print(f'  ⚠ 读不了 {path}（{why or "没有声音"}）：块首带前置标点的块照旧从时间戳起（lessons #158）',
              file=sys.stderr)
        return None
    return [10 * math.log10(e / peak) if e > 0 else -120.0 for e in energy]


def onset_after_pause(env, start, end):
    """[start, end)（裁后坐标系的秒）这一项里，开头之后 CARRIED_PAUSE_REACH_SEC 以内开始的第一段静音
    （≥ SILENCE_MIN_SEC、低于峰值 SILENCE_DB）之后，第一次到峰值 ONSET_DB 的时刻（lessons #158）。
    返回 None：找到静音之前已经到了 ONSET_DB（这一项开头就在念，后面的静音是词里的塞音闭塞）、
    没有这样的静音、静音没在这一项里结束、或者开口不在这一项里。"""
    i, n = max(0, int(round(start / ENV_HOP_SEC))), len(env)
    last = min(n, int(round(end / ENV_HOP_SEC)))
    reach = int(round((start + CARRIED_PAUSE_REACH_SEC) / ENV_HOP_SEC))
    need = int(round(SILENCE_MIN_SEC / ENV_HOP_SEC))
    while i < min(reach, last):
        if env[i] >= ONSET_DB:
            return None
        if env[i] >= SILENCE_DB:
            i += 1
            continue
        a = i
        while a > 0 and env[a - 1] < SILENCE_DB:      # 这段静音从 start 之前就开始了
            a -= 1
        b = i
        while b < n and env[b] < SILENCE_DB:
            b += 1
        if b - a < need:
            i = b
            continue
        while b < last and env[b] < ONSET_DB:
            b += 1
        return b * ENV_HOP_SEC if b < last else None
    return None


def _carried_punct_start(w, env, lead_cut):
    """块首那一项 w 带着前一句的标点（「. Today」，调用方已认过）时，块该从哪一秒起（裁后坐标系）：
    开口往前退 PAUSE_PREFIX_BACKOFF_SEC，不早于这一项的开头。这一项里找不到停顿就返回 None。"""
    if w.get('duration_sec') is None:
        return None
    start = _t_of(w) - lead_cut
    onset = onset_after_pause(env, start, start + float(w['duration_sec']))
    if onset is None:
        return None
    return max(start, onset - PAUSE_PREFIX_BACKOFF_SEC)


def chunk_starts(tts_text, chunks, words, lead_cut, audio=None):
    """每块首字对齐到词边界秒（词边界为原始坐标系 → 减 lead_cut 平移）。

    块首那一项要是盖住了停顿的英文首字母（「：|p」+「andas」），块从这一项的结尾起（lessons #144）；
    盖住停顿的是 2–3 个字母（「；|Not」+「ion」）时，从这一项的结尾往前退 0.125 s（lessons #152）。
    audio：这一段的配音（裁后坐标系，就是 audio/<id>.mp3）。给了的话，块首那一项带着前一句的标点
    （「. Today」）时从开口往前退 0.125 s 起（lessons #158）；只在要用时才解码，subs.main 只给英文片传。
    """
    char_t = [None] * len(tts_text)
    char_k = [None] * len(tts_text)      # 每个字的时间戳来自第几项
    wts = [_PUNCT_RE.sub('', w.get('text', '')) for w in words]
    at = {}                              # 第几项 → 它在 tts_text 里的起点
    cur = 0
    for k, w in enumerate(words):
        wt = wts[k]
        if not wt:
            continue
        p = tts_text.find(wt, cur)
        if p < 0:
            p = tts_text.find(wt[0], cur)
            if p < 0:
                continue
        t = _t_of(w) - lead_cut
        at[k] = p
        for i in range(p, min(len(tts_text), p + len(wt))):
            char_t[i] = t
            char_k[i] = k
        cur = p + len(wt)

    starts, pos, env = [], 0, None
    for c in chunks:
        st = None
        for i in range(pos, min(len(tts_text), pos + len(c))):
            if char_t[i] is not None:
                st = char_t[i]
                onset = _word_onset_after_pause(words, char_k[i], wts, at, i)
                if onset is not None:
                    st = onset - lead_cut
                elif audio and _CARRIED_PUNCT.match(words[char_k[i]].get('text', '')):
                    if env is None:
                        env = speech_envelope(audio) or []
                    carried = _carried_punct_start(words[char_k[i]], env, lead_cut) if env else None
                    if carried is not None:
                        st = carried
                break
        starts.append(st)
        pos += len(c)

    fixed = []
    for st in starts:
        if st is None:
            st = fixed[-1] if fixed else 0.0
        if fixed and st < fixed[-1]:
            st = fixed[-1]
        fixed.append(max(0.0, st))
    if fixed:
        fixed[0] = 0.0
    return fixed


_KEEP_CHARS = set('“”"\'‘’…、～·%（）《》〈〉【】[]()/*&+=-_#@$')
_TO_SPACE = set('  \u3000\t')
_DROP_PUNCT = set('，。！？；：,.!?;:、·|｜')


# 英文块尾的句读：块已经是一行字幕，句末的点、逗号、冒号、省略号不上屏；问号、感叹号留着（它们改变语气）。
_EN_TAIL_PUNCT = re.compile(r'[\s.,;:…、，。；：|｜]+$')


def clean_sub_text(s: str, lang: str = 'zh', keep_punct: bool = False) -> str:
    """屏幕文本：去句读标点（标点只驱动 TTS 停顿，不上画面）；英文片的英文块只去块尾的句读。

    唯一例外：词里的点（2.4% / ASN.1 / X.509，左右都是数字或 ASCII 字母）保留 —— 它属于数字或名字本身，
    删掉会让画面显示的数字与念出来的差一个数量级，或者写成另一个名字。逗号照删（lessons #88 用户拍板）。
    lang 不是 'zh' 时，空白留成一个空格（英文词之间要空格）。

    keep_punct=True（英文片里没有中文的块，subs.py 按 project.json 的 lang 判）：保留句内标点，只去块尾的
    . , ; : …，? ! 留着（2026-10-22 用户拍板，lessons #145）。原来这些块也照中文全删，「Kafka, a warehouse」
    上屏成「Kafka a warehouse」，「1,000」成「1000」，读不通。中文片不受影响，哪怕某一块全是英文。
    """
    if keep_punct:
        t = ''.join(' ' if ch in _TO_SPACE else ch for ch in s if ch not in '|｜')
        return _EN_TAIL_PUNCT.sub('', re.sub(r'\s{2,}', ' ', t).strip())
    out = []
    for i, ch in enumerate(s):
        if ch in _KEEP_CHARS:
            out.append(ch); continue
        if ch in _TO_SPACE:
            if lang != 'zh':
                out.append(' ')
            continue
        if ch in _DROP_PUNCT:
            if is_token_dot(s, i):
                out.append(ch)
            continue
        out.append(ch)
    t = re.sub(r'\s{2,}', ' ', ''.join(out)).strip()
    return t


def durations_proportional(secs, min_hold):
    n = len(secs)
    if n == 0:
        return []
    if n * min_hold >= sum(secs):
        return [min_hold] * n
    d = list(secs)
    fixed = [False] * n
    for _ in range(n + 2):
        free = [i for i in range(n) if not fixed[i]]
        if not free:
            break
        free_total = sum(secs[i] for i in free)
        budget = sum(secs) - min_hold * (n - len(free))
        changed = False
        for i in free:
            share = budget * (secs[i] / free_total) if free_total > 0 else budget / len(free)
            if share < min_hold:
                d[i] = min_hold; fixed[i] = True; changed = True
            else:
                d[i] = share
        if not changed:
            break
    return d


def frame_of(sec: float, fps: float) -> int:
    return int(round(sec * fps)) + 1


def tc(frame: int, fps: float, sep: str) -> str:
    ms = int(round((frame - 1) * 1000.0 / fps))
    h, rem = divmod(ms, 3600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f'{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}'


# ---- beats.js：给场景作者的对齐节拍器 ----
BEATS_TMPL = """/* 自动生成（subs.py）——解说词/配音改动后重跑即全量刷新，别手改。
 * B(text)  → 该字幕块起播秒（本地坐标系，0 = 本场景第 0 帧）
 * Be(text) → 该块收尾秒
 * ★ 匹配规则是「从头开始的前缀匹配」：bt === t || bt.startsWith(t) || t.startsWith(bt)
 *   也就是 text 必须是**块文本的开头**（唯一推荐用法），或与它整体一致。
 *   **一模一样的块优先**；没有一模一样的，取第一个匹配的块（匹配到不止一块时 check_beats_refs 报歧义）。
 *   **取中间一段会抛错**：
 *   块文本「一位父亲写下《被网游毁掉的孩子》」→ B('一位父亲写下') ✓ / B('被网游毁掉的孩子') ✗
 *   归一化会去 ，。、！？；：,.!?;:| 与空白（保留词里的点：2.4% / X.509），但**不去《》「」**。
 *   拿不准就抄块文本的前 6–10 个字。构建期先跑 scripts/check_beats_refs.py 全量校验。
 * __SEG_INFO__：{id, duration, speech_end, tail, gap, chapter_start, chapter_end} ——
 * duration 对帧长、speech_end 是语音真实结束（按它排主体动画）；
 * gap = 本场之后的段间静默（末场为 0）—— 渲染器把它算进本场，本场帧一直播到 duration + gap，
 *   出场过渡就排在 [speech_end, duration + gap] 里（frame-contract.md「场间过渡」）；
 * chapter_start / chapter_end = 本场是一章的第一场 / 下一场是新的一章（换章用更重的过渡）。
 * 用法：window.__SEG__.speech_end / window.__SEG__.duration / window.__SEG__.gap */
window.__BEATS__ = __BEATS_DATA__;
window.__SEG__ = __SEG_DATA__;
(function () {
  var B = window.__BEATS__;
  function norm(s) {
    // 词里的点不是标点：先护住左右都是数字或字母的点（2.4% / 1.2.3 / X.509），去完标点再还原，
    // 否则 B('2.4%') 会被削成 '24%'、B('X.509') 撞上 'X509'。用前瞻，连着的点（1.2.3）才都护得住。
    return String(s || '').replace(/([0-9A-Za-z])\\.(?=[0-9A-Za-z])/g, '$1\\u0000')
      .replace(/[\\s，。、！？；：,.!?;:|]/g, '')
      .replace(/\\u0000/g, '.');
  }
  function find(text) {
    // 一模一样的块优先：前面的「提交；」是「提交之后」的前缀，B('提交之后') 也要落在「提交之后，」那块
    var t = norm(text), hit = null;
    for (var i = 0; i < B.length; i++) {
      var bt = norm(B[i].text);
      if (bt === t) return B[i];
      if (!hit && (bt.indexOf(t) === 0 || t.indexOf(bt) === 0)) hit = B[i];
    }
    if (hit) return hit;
    throw new Error('B() 找不到节拍: ' + text);
  }
  window.B = function (text) { return find(text).start; };
  window.Be = function (text) { return find(text).end; };
})();
"""


def write_beats(path, seg, extra=None):
    data = [{'text': b['spoken'], 'start': b['local_start_sec'],
             'end': round(b['local_start_sec'] + (b['global_end_sec'] - b['global_start_sec']), 3)}
            for b in seg['blocks']]
    seginfo = {'id': seg['id'], 'duration': seg['duration_sec'],
               'speech_end': seg['speech_end_sec'], 'tail': seg['tail_silence_sec'], **(extra or {})}
    js = BEATS_TMPL.replace('__BEATS_DATA__', json.dumps(data, ensure_ascii=False, indent=2)) \
                   .replace('__SEG_DATA__', json.dumps(seginfo, ensure_ascii=False))
    open(path, 'w', encoding='utf-8', newline='\n').write(js)


def main() -> int:
    ap = argparse.ArgumentParser(description="html-to-explainer-video 字幕 + 节拍器")
    ap.add_argument("--project", default=".")
    ap.add_argument("--fps", type=float, default=0)
    args = ap.parse_args()

    proj = os.path.abspath(args.project)
    pj = json.load(open(os.path.join(proj, "project.json"), encoding="utf-8"))
    # 英文片（new_project.py --lang en）：英文块保留句内标点（lessons #145）；中文片照 #88 全删
    en_film = str(pj.get("lang") or "zh").lower().split("-")[0] == "en"
    fps = args.fps or float(pj.get("fps") or FPS_DEFAULT)
    narration = json.load(open(os.path.join(proj, "narration.json"), encoding="utf-8"))
    if isinstance(narration, dict):
        narration = narration.get("items") or []

    man = json.load(open(os.path.join(proj, "audio-manifest.json"), encoding="utf-8"))
    by_id = {it.get("id") or os.path.splitext(os.path.basename(it["path"]))[0]: it
             for it in man.get("items", [])}
    layout = json.load(open(os.path.join(proj, "layout.json"), encoding="utf-8"))

    out = {'fps': fps, 'size': SUB_SIZE, 'safe_width': SUB_MAX_W,
           'budget': {'zh': SUB_BUDGET_ZH, 'en': SUB_BUDGET_EN}, 'segments': []}
    warn_over, holds = [], {}

    for item in narration:
        sid, text = item['id'], item['text']
        chunks = split_chunks(text)
        m = by_id.get(sid) or {}
        words = m.get('boundaries') or []
        lead_cut = float(m.get('lead_cut_sec', 0.0))
        trimmed = bool(man.get('trimmed'))

        tts_text = ''.join(chunks)
        # 英文片才按音频校正带前置标点的块首（lessons #158）：中文片一块不变；插值的段（#140）时间戳本来就对不上音频
        audio = os.path.join(proj, 'audio', f'{sid}.mp3') if en_film and not m.get('even_timestamps') else None
        if words:
            starts_sec = chunk_starts(tts_text, chunks, words, lead_cut, audio=audio)
        else:
            total = float(m.get('container_duration_sec') or m.get('duration_sec') or 1.0)
            tot = max(1, sum(len(c) for c in chunks))
            acc, starts_sec = 0, []
            for c in chunks:
                starts_sec.append(total * acc / tot); acc += len(c)
            print(f'  ⚠ {sid}: 无词边界 → 按字数插值（字幕会飘）', file=sys.stderr)

        # 两级时钟：段长 = 容器；末块收尾 = 语音真实结束（均为裁后坐标系）
        seg_container = float(m.get('container_duration_sec') or m.get('duration_sec') or 0.0)
        # 停留（project.json 的 hold，timeline_build 记进 layout）算进段长、不挂字幕（lessons #157）
        hold = float(layout.get(sid, {}).get('hold_sec') or 0.0)
        if trimmed and 'speech_end_sec' in m:
            speech_end = float(m['speech_end_sec'])
        else:
            speech_end = float(m.get('duration_sec') or seg_container) - lead_cut
        span_end = min(speech_end, seg_container) if seg_container else speech_end

        n = len(chunks)
        natural = list(starts_sec) + [span_end]
        if len(natural) < n + 1:
            natural += [span_end] * (n + 1 - len(natural))

        raw_spans = [max(1.0 / fps, natural[i + 1] - natural[i]) for i in range(n)]
        spans = durations_proportional(raw_spans, MIN_HOLD) if n else []
        edges = [starts_sec[0] if starts_sec else 0.0]
        for d in spans:
            edges.append(edges[-1] + d)
        if edges[-1] > span_end and len(edges) > 1:
            k = span_end / edges[-1]
            edges = [starts_sec[0] + (e - starts_sec[0]) * k for e in edges]
        starts_sec = edges[:-1]
        bounds = edges

        base = float(layout.get(sid, {}).get('start_sec', 0.0))

        blocks = []
        for i, c in enumerate(chunks):
            a, b = bounds[i], bounds[i + 1]
            if b <= a:
                b = a + 1.0 / fps
            f0, f1 = frame_of(a, fps), max(frame_of(a, fps), frame_of(b, fps) - 1)
            screen = clean_sub_text(c, 'zh' if has_cjk(c) else 'en', keep_punct=en_film and not has_cjk(c)) or c
            size = fit_size(screen)
            blocks.append({
                'text': screen, 'spoken': c, 'from': f0, 'to': f1,
                'local_start_sec': round(a, 3),
                'global_start_sec': round(base + a, 3),
                'global_end_sec': round(base + b, 3),
                'size': size,
            })
            if will_wrap(screen) or len(screen) > (SUB_BUDGET_ZH if has_cjk(screen) else SUB_BUDGET_EN):
                warn_over.append((sid, screen))

        out['segments'].append({
            'id': sid, 'text': text, 'chunks': chunks, 'tts_text': tts_text,
            'start_sec': round(base, 3), 'duration_sec': round(seg_container + hold, 3),
            'speech_end_sec': round(speech_end, 3),
            'tail_silence_sec': round(max(0.0, seg_container + hold - speech_end), 3),
            'blocks': blocks,
        })
        if hold:
            holds[sid] = hold

    sp = os.path.join(proj, 'subs.json')
    json.dump(out, open(sp, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)

    # beats.js per scene
    frames_dir = os.path.join(proj, 'frames')
    os.makedirs(frames_dir, exist_ok=True)
    # 段间 gap 与渲染器同源（timeline_build 写进 layout 的 _total.gap_sec）；章节起点取 project.json
    gap = float(layout.get('_total', {}).get('gap_sec', pj.get('gap', 0)) or 0)
    ch_starts = {c.get('startSegment') for c in (pj.get('chapters') or [])}
    segs = out['segments']
    for i, s in enumerate(segs):
        last = i + 1 == len(segs)
        extra = {'gap': 0.0 if last else round(gap, 3),
                 'chapter_start': i == 0 or s['id'] in ch_starts,
                 'chapter_end': (not last) and segs[i + 1]['id'] in ch_starts}
        write_beats(os.path.join(frames_dir, f"{s['id']}.beats.js"), s, extra)

    # SRT / VTT
    items = []
    for s in out['segments']:
        for b in s['blocks']:
            gf0 = frame_of(b['global_start_sec'], fps)
            gf1 = max(gf0, frame_of(b['global_end_sec'], fps) - 1)
            items.append((gf0, gf1, b['text']))
    items.sort(key=lambda x: x[0])
    for i in range(len(items) - 1):
        if items[i][1] >= items[i + 1][0]:
            items[i] = (items[i][0], items[i + 1][0] - 1, items[i][2])

    srt, vtt = [], ['WEBVTT', f'NOTE {len(items)} 条 · fps {fps}', '']
    for i, (f0, f1, text) in enumerate(items, 1):
        srt.append(f'{i}\n{tc(f0, fps, ",")} --> {tc(f1 + 1, fps, ",")}\n{text}\n')
        vtt.append(f'{tc(f0, fps, ".")} --> {tc(f1 + 1, fps, ".")}\n{text}\n')

    slug = pj.get('slug') or 'video'
    out_dir = os.path.join(proj, 'out')
    os.makedirs(out_dir, exist_ok=True)
    srt_p = os.path.join(out_dir, f'{slug}.srt')
    open(srt_p, 'w', encoding='utf-8', newline='\n').write('\n'.join(srt))
    open(os.path.splitext(srt_p)[0] + '.vtt', 'w', encoding='utf-8', newline='\n').write('\n'.join(vtt))

    n_blocks = sum(len(s['blocks']) for s in out['segments'])
    print(f'[subs] {len(out["segments"])} 段 → {n_blocks} 块 · fps {fps}')
    print(f'[subs] → {sp} + frames/<id>.beats.js + {srt_p}(.vtt)')
    for s in out['segments']:
        hold = f'，含停留 {holds[s["id"]]:.2f}s' if s['id'] in holds else ''
        print(f'  {s["id"]:<12} {len(s["blocks"])} 块  语音止 {s["speech_end_sec"]:>6.2f}s '
              f'/ 容器 {s["duration_sec"]:>6.2f}s（尾静音 {s["tail_silence_sec"]:.2f}s 不挂字幕{hold}）')
    if warn_over:
        print(f'⚠ {len(warn_over)} 块超预算（自动缩字号，>1.3× 折两行压内容区，按缺陷处理）：')
        for sid, c in warn_over[:8]:
            print(f'    {sid}: "{c}"')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
