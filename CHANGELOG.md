# 变更日志

本项目的所有重要变更都记录在这里。格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循[语义化版本](https://semver.org/lang/zh-CN/)。

---

## [2.0.10] — 2026-10-21

### 修复

- **英文封面的钩子认错**：`check_cover.mjs` 判「纯数字串」的正则写在 `PAGE_PROBE` 模板字符串里，`\d` 与 `\-`
  被求值成 `d` 与 `-`，页面拿到 `/^[d.,%$¥+-–—:：]+$/` —— `+-–` 成了 U+002B..U+2013 的区间，所有拉丁字母都在里面。
  英文钩子因此被当成纯数字跳过，23px 的小卡片顶替成钩子，「钩子字号 / 钩子是最大的文字 / 行宽 / 孤字」整条链量错元素
  （中文钩子不在区间内，不受影响）（#3，lessons #137）。
  - 改成双层反斜杠 `/^[\\d.,%$¥+\\-–—:：]+$/`。中文封面的实测输出修复前后逐字相同。
  - 新增回归测试 `tests/test_page_probe_escapes.mjs` 并接入 CI：按模板字符串求值 `PAGE_PROBE` 后断言 `pureNumber` / `norm` 的行为；
    并扫描 `scripts/*.mjs` 里所有交给 `page.evaluate` / `addInitScript` 的模板字符串，反斜杠只许 `\\`、`\u`、`\x`、`` \` ``、`\$`。

## [2.0.9] — 2026-10-21

### 变更

- 阿里引擎中文默认音色由 `anmingyuan_v3.1`（安明远，男声）改为 `xuyuyuan_v3.1`（许玉远，女声）；
  英文仍为 `Andy_v3.1`。已有项目 `project.json` 里写死的音色不受影响。

### 修复

- **长片字幕越来越落后于配音**：`layout.json` 按每段 MP3 的容器时长排，`narration-full.mp3` 却把解码后的段直接 concat；
  解码出来的采样比容器时长平均短约 36 ms（MP3 的 priming / padding），8 分钟、53 段的片子到片尾音轨提前约 1.9 s，
  画面、硬字幕、SRT/VTT 都比声音晚。抽帧 QC 和 0.5 s 的时长容差都看不出来（lessons #136）。
  - `timeline_build.py` 拼音轨时每段补齐 / 截到 `round(duration_sec × sr)` 个采样（`apad` + `atrim`），
    段间静音按采样数补到下一段的 `round(start_sec × sr)` —— 每段正好占住它在 layout 里的槽位。
  - 原来「音轨与视频差 > 0.5 s」的提醒改成硬检查：拼完解码量总长，和 layout 总长差超过 1 帧就非零退出。
  - `layout.json` 各段的 `start_sec` / `duration_sec` 与总帧数不变：已出片的项目重跑 `timeline_build.py`，
    只把新音轨换进成片即可，不用重渲帧。`_total.audio_duration_sec` 改记解码后的真实总长（修正后等于视频总长）。
  - 新增回归测试 `tests/test_timeline_build.py`（合成 beep 样本，并反向验证旧拼法过不了），并接入 CI。

## [2.0.8] — 2026-10-19

首个版本。

### 能力

- **流水线**：`new_project` → `tts_setup` / `tts_build` → `timeline_build` → `subs` → 场景构建 →
  `check_beats_refs` / `lint_frames` / `check_layout` → `gate_check` → `render_video` → `qc_check` →
  `cover_build` / `check_cover`。
- **配音**：默认阿里云百炼 `qwen-audio-3.1-tts-flash`（字级时间戳，数字展开读法映射回原文）；
  可切 edge-tts 或火山引擎语音合成 2.0，三者产出同一种 manifest。
- **字幕与节拍**：词/字级时间戳切块的硬字幕 + SRT/VTT；场景里用 `B('块文本')` 取吐字时刻排动画。
- **画面**：23 个风格模板（8 类）的设计规范、40 多个动效词汇（`assets/motion.js`）、
  按解说词编排风格的 `style_director.py`。
- **渲染**：确定性逐帧 seek；1080p / 2K / 4K、30 / 60 fps；快门运动模糊（可按场景点名开）；
  多浏览器进程并行、断点续渲；PNG-fast / JPEG 截图通道。
- **质检与封面**：契约 lint、几何体检、成片 QC、16:9 / 3:4 / 9:16 封面与几何实测；
  七项用户确认由 `gate_check.py` 把关。

### 画面连续性（本版重点）

- **搭建期读布局会让成片每 N 帧跳一下**：帧在搭建时用 `getBoundingClientRect` / `getBBox` 等量尺寸摆位置，
  `--workers` 并行渲染时各进程量到的值不同，某个进程负责的帧整体错位。单页预览、几何体检、抽帧 QC 都看不出来。
  - `lint_frames.py` 第 11 条报这些读法，并**顺着 `<script src>` 扫帧引用的本地脚本**（共享搭建器里的测量只扫 HTML 看不见）；
    `getTotalLength` / `getPointAtLength` 与字体无关，不报。
  - 契约第 9 条：要量就在生成期量好、写成常量。
- **渲染器不做转场，不写出场就是硬切**：段间 gap 算在上一场里，场景帧播到 `duration + gap`。
  - `subs.py` 在 `__SEG__` 里写入 `gap`、`chapter_start`、`chapter_end`。
  - 契约第 10 条与帧模板的「出场过渡」一节：出场写在 `[speech_end, duration + gap]`；页眉/章节角标只在一章第一场入场。
- `qc_check.py` 新增 ⑥ 画面连续性：逐帧比较成片，单帧跳动判 FAIL（并报跳动周期是否等于进程数），
  场景边界画面整体突变判「场间硬切」WARN（阈值按实片标定：硬切 4–47，带过渡 ≤ 0.25）。`--skip-continuity` 可跳过。
- 新增回归测试 `tests/test_lint_frames.py`、`tests/test_qc_continuity.py`，并接入 CI。

[2.0.10]: https://github.com/amliuyong/html-to-explainer-video/releases/tag/v2.0.10
[2.0.9]: https://github.com/amliuyong/html-to-explainer-video/releases/tag/v2.0.9
[2.0.8]: https://github.com/amliuyong/html-to-explainer-video/releases/tag/v2.0.8
