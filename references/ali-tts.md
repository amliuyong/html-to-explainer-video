# 阿里云百炼配音（默认引擎 `ali`）

模型 `qwen-audio-3.1-tts-flash`，HTTP + SSE，字级时间戳。接口包 `scripts/tts_ali.py`，
`tts_build.py` 的 `provider` 不写时默认就是它。三个引擎产出的 manifest 结构一致，
timeline / subs / 渲染器零改动。

接口全貌（参数、音色全表、计费、错误码、文档的坑）在 `~/ali_tts/docs/`，
本篇只记**本技能怎么用**和**接进流水线时实测到的行为**。

## 密钥

- 文件：`~/ali_tts/api_key.csv`，两列「字段名,值」，权限 0400。环境变量 `ALI_TTS_KEY_FILE` 可指向别处。
- 只用两个字段：`apiKey`（Bearer 鉴权）和 `dashScope`（业务空间专属域名的 base URL，必须是 `https://`，否则 `tts_ali.py` 拒绝读取）。
- **agent 不读、不打印这个文件。** 只有 `tts_ali.py` 读它；状态用 `tts_setup.py --status`
  或 `tts_ali.py --check` 看（密钥和业务空间 ID 都已脱敏）。

## 命令

```bash
python <skill>/scripts/tts_ali.py --check                      # 连通性自检（真合成一句）
python <skill>/scripts/tts_ali.py --voices                     # 内置常用音色
python <skill>/scripts/tts_ali.py --synth "一句话" --out a.mp3 --voice 许玉远 --rate +8%   # 可直接贴带 | 的解说词：和 tts_build 一样先去掉 |
python <skill>/scripts/tts_setup.py --project . --provider ali --voice 许玉远   # 写进 project.json
python <skill>/scripts/tts_build.py --project .                # provider 缺省 = ali
```

## 音色与语速

- 默认 `xuyuyuan_v3.1`（许玉远，女声）；`lang=en` 的项目默认 `Andy_v3.1`。
- 3.1 只认 `*_v3.1` 音色（区分大小写）。`project.json` 里如果留着 edge（`…Neural`）或
  火山（`…_bigtts`）的音色 ID，`tts_build.py` 会提示并回落到默认音色。
- 内置目录里的 18 个音色 2026-10-19 逐个实测过，都返回字级时间戳（中文按字，英文按词）。
  目录外的音色如果不返回时间戳，`tts_build.py` 打警告并算合成失败（2.2.1 起，lessons #150；原来是字幕退化成按字数插值）。
- 语速沿用 `+N%` 写法（`new_project.py` 默认 `+8%`），`tts_ali.py` 换算成倍速 `1 + N/100`，
  夹在 [0.5, 2.0]。实测 `rate=1.2` 时长 7.656s → 6.384s，时间戳同比缩放。
- `project.json` 的 `lang` 会作为 `language_hints` 传给服务端，改善数字和符号的读法。

## 实测行为（2026-10-19，本机 → 北京业务空间域名）

| 现象 | 处理 |
|---|---|
| 多句文本的 `begin_time` 是**整段音频的绝对时间**（第 2 句从 21.92s 接着往后排） | 直接拼，不逐句平移 |
| 字级时间戳落在**展开读法后的文本**上：「2026」→「二零二六」、「2.4%」→「百分之二点四」、英文「2026」→「twenty twenty six」；同一会话里也会有保留原数字的时候 | `tts_ali.to_original()` 用 difflib 把每项映射回原文，展开段合并成一项。**不映射的后果**：`subs.py` 把「二」误配到后文「第二」上，后面几块字幕整体提前 4–7 秒，而且不报错 |
| 标点并进相邻字（「，同」「段。」） | `subs.py` 对齐前会剥掉标点，不用管 |
| 标点后面紧跟英文词时，停顿常并进**下一个词的首字母**，给成一项：「：\|p」0.375 s，后面一项「andas」（2.1.2 起送出去的文本没有 `\|`，就是「：p」）。块起点取这一项的开头会早 0.3–0.7 s（2026-10-22：一个系列 19 集里 11 处，8 处在块首） | 2.1.3 起 `subs.py` 遇到块首是这种项（只剩一个字母、≥ 0.3 s、紧接前一项、和前一项不一样长、后面是同一个词的小写后半截），块从这一项的结尾起（lessons #144）。2.2.2 起停顿并进 2–3 个字母的（「；\|Not」+「ion」「bl」+「ameless」，后两个字母小写）同样处理，起点从这一项的结尾再往前退 0.125 s —— 字母本身已经在这一项里开口了（lessons #152） |
| narration 里的 `\|` 切块符原样送过去，**有时会读出来**：读成「竖线」（紧跟在 、：；后面最容易），两个 `\|` 夹着的短块读成「绝对值」；同一段文字重配是逐字节相同的音频（2026-10-21：一个系列 16 集里 6 集碰上） | 2.1.2 起 `tts_build.py` 和 `tts_ali.py --synth` 送之前去掉 `\|`（lessons #142），不补停顿 —— 停顿归标点管；两边都是 ASCII 字母或数字时换成一个空格。字级时间戳落在去掉 `\|` 的文本上，`subs.py` 照旧按 `\|` 切块、对到各块首字 |
| 某些写法只返回**插值的**字级时间戳：offset 不在真时间戳的网格上（中文字级在 1/32 秒上，英文词级在 0.08 秒上；真的都在，lessons #146），整段或从某个字起到段尾（2026-10-21：13 集 673 段里 19 段，不算片头；片头那一句 8 集都是插值的；同一段文字重配，返回逐字节相同的音频） | `tts_build.py` 打 ⚠ 并在 manifest 记 `even_timestamps`（lessons #140）。改写这一段（换个说法、调语序、拆句）再重配；不改的话这一段的字幕和 `B()` 节拍对不上吐字 |
| 数字不熔读：`4479.55` 读作「四千四百七十九点五五」 | 不需要 edge 那套「数字间补逗号」 |
| 业务空间域名一个 SSE 事件拆成多行 `data:`；出错时是 HTTP 400 + `event:result` | 按规范拼接；判错只认 `code`（dashscope SDK 1.27.7 的流式栽在这里，所以只用标准库） |
| 读流会断（2026-10-22：一集 52 段的首配里好几次）。断在一块里面是「读流中断」，断在一帧中间是「SSE 帧不是合法 JSON」；**断在两块之间时标准库不报错**（`http.client` 吞掉 `IncompleteRead`，当成 EOF），拿到的是半截音频、没有 `sentence-end`，也就没有时间戳。那一集 11 段这样进了缓存（`boundary_count` 0，一整句只剩 3.77 秒） | 2.2.1 起读不到最终帧（`finish_reason: "stop"`）就是「读流提前结束」，三种都退避重试。`tts_build.py` 再查一遍结果：0 个字级时间戳、或音频短得不像这段文字（< 0.11 秒 × 可念单位数 ÷ 语速倍数），重试后非零退出，不进缓存、不覆盖上一次的音频；坏的缓存读时认出来重配（lessons #150） |
| 首字节约 1 秒，一句 7 秒的音频整个请求约 2 秒 | — |

## 排错

| 报错 | 原因 |
|---|---|
| `InvalidParameter … [411]` | 音色不存在或不是 3.1 的音色 |
| `InvalidApiKey` | `apiKey` 不对，或 Key 与 `dashScope` 域名不在同一地域 |
| `DataInspectionFailed` | 内容安全拦截，改写那句解说词 |
| `Throttling…` | 超过 180 RPM，自动退避重试 |
| `读流提前结束` / `读流中断` / `SSE 帧不是合法 JSON` | 读流断了，自动退避重试；重试完还断就稍后重跑（坏的结果没进缓存） |
| `BadSynthesis: 没有字级时间戳` / `音频只有 …s` | 引擎返回了，结果却是截断的或没有时间戳（lessons #150）：稍后重跑；同一段一直这样就改写这一段；每段都没有时间戳是音色不支持，换回内置目录里的音色 |
| `找不到阿里云百炼密钥文件` | `tts_setup.py` 会输出 `NEXT_ACTION=fill_ali_key` 并停下，让用户补文件或设 `ALI_TTS_KEY_FILE` |

回归测试：`python tests/test_tts_ali.py`、`python tests/test_tts_even_timestamps.py`、`python tests/test_tts_chunk_marks.py`、`python tests/test_tts_truncated.py`（不联网、不需要密钥）。
