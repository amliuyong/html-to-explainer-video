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
python <skill>/scripts/tts_ali.py --synth "一句话" --out a.mp3 --voice 许玉远 --rate +8%
python <skill>/scripts/tts_setup.py --project . --provider ali --voice 许玉远   # 写进 project.json
python <skill>/scripts/tts_build.py --project .                # provider 缺省 = ali
```

## 音色与语速

- 默认 `xuyuyuan_v3.1`（许玉远，女声）；`lang=en` 的项目默认 `Andy_v3.1`。
- 3.1 只认 `*_v3.1` 音色（区分大小写）。`project.json` 里如果留着 edge（`…Neural`）或
  火山（`…_bigtts`）的音色 ID，`tts_build.py` 会提示并回落到默认音色。
- 内置目录里的 18 个音色 2026-10-19 逐个实测过，都返回字级时间戳（中文按字，英文按词）。
  目录外的音色如果不返回时间戳，`tts_build.py` 会打警告，字幕退化成按字数插值。
- 语速沿用 `+N%` 写法（`new_project.py` 默认 `+8%`），`tts_ali.py` 换算成倍速 `1 + N/100`，
  夹在 [0.5, 2.0]。实测 `rate=1.2` 时长 7.656s → 6.384s，时间戳同比缩放。
- `project.json` 的 `lang` 会作为 `language_hints` 传给服务端，改善数字和符号的读法。

## 实测行为（2026-10-19，本机 → 北京业务空间域名）

| 现象 | 处理 |
|---|---|
| 多句文本的 `begin_time` 是**整段音频的绝对时间**（第 2 句从 21.92s 接着往后排） | 直接拼，不逐句平移 |
| 字级时间戳落在**展开读法后的文本**上：「2026」→「二零二六」、「2.4%」→「百分之二点四」、英文「2026」→「twenty twenty six」；同一会话里也会有保留原数字的时候 | `tts_ali.to_original()` 用 difflib 把每项映射回原文，展开段合并成一项。**不映射的后果**：`subs.py` 把「二」误配到后文「第二」上，后面几块字幕整体提前 4–7 秒，而且不报错 |
| 标点并进相邻字（「，同」「段。」） | `subs.py` 对齐前会剥掉标点，不用管 |
| narration 里的 `\|` 切块符不会读出来，服务端当成停顿，也不进时间戳 | 原样送原文 |
| 某些写法只返回**插值的**字级时间戳：offset 不在 1/32 秒网格上（真的都在），整段或从某个字起到段尾（2026-10-21：13 集 673 段里 19 段，不算片头；片头那一句 8 集都是插值的；同一段文字重配，返回逐字节相同的音频） | `tts_build.py` 打 ⚠ 并在 manifest 记 `even_timestamps`（lessons #140）。改写这一段（换个说法、调语序、拆句）再重配；不改的话这一段的字幕和 `B()` 节拍对不上吐字 |
| 数字不熔读：`4479.55` 读作「四千四百七十九点五五」 | 不需要 edge 那套「数字间补逗号」 |
| 业务空间域名一个 SSE 事件拆成多行 `data:`；出错时是 HTTP 400 + `event:result` | 按规范拼接；判错只认 `code`（dashscope SDK 1.27.7 的流式栽在这里，所以只用标准库） |
| 首字节约 1 秒，一句 7 秒的音频整个请求约 2 秒 | — |

## 排错

| 报错 | 原因 |
|---|---|
| `InvalidParameter … [411]` | 音色不存在或不是 3.1 的音色 |
| `InvalidApiKey` | `apiKey` 不对，或 Key 与 `dashScope` 域名不在同一地域 |
| `DataInspectionFailed` | 内容安全拦截，改写那句解说词 |
| `Throttling…` | 超过 180 RPM，自动退避重试 |
| `找不到阿里云百炼密钥文件` | `tts_setup.py` 会输出 `NEXT_ACTION=fill_ali_key` 并停下，让用户补文件或设 `ALI_TTS_KEY_FILE` |

回归测试：`python tests/test_tts_ali.py`、`python tests/test_tts_even_timestamps.py`（不联网、不需要密钥）。
