# 变更日志

本项目的所有重要变更都记录在这里。格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循[语义化版本](https://semver.org/lang/zh-CN/)。

---

## [2.1.2] — 2026-10-21

### 修复

- **阿里把字幕切块符 `|` 读出来**（lessons #142）：narration 里的 `|` 只给 `subs.py` 切字幕块用，`tts_build.py` 却原样送给引擎。
  阿里云百炼有时把它读成「竖线」（紧跟在 、：；后面最容易），或把两个 `|` 夹着的短块读成「绝对值」；同一段文字重配返回
  逐字节相同的音频，只能改字。一个系列 16 集里 6 集碰上。`tts_build.py` 的注释和 `references/ali-tts.md` 里「服务端当停顿、
  不会读出来」的说法不成立。
  - 三个引擎（阿里 / 火山 / edge）送的都是去掉 `|` 的文本。规则在新增的 `subs.strip_chunk_marks`，和 `split_chunks` 放在一起。
    停顿归标点管（「，|」「：|」的停顿本来就在标点上），所以去掉 `|` 不补停顿：两边是中文、标点或在段首段尾就直接去掉
    （「判断|这套」照常连读）；两边都是 ASCII 字母或数字时换成一个空格（「Intensive|Applications」不连成一个词，
    「300|400」不连成一个数）；`|` 旁边本来有空白时收成一个空格。
  - `narration.json`、manifest 的 `original_text`、`subs.py` 的切块都不变；manifest 的 `spoken_text` 是去掉 `|` 的文本。
    字级时间戳落在去掉 `|` 的文本上；`subs.py` 拿来对齐的 `tts_text`（各块直接拼起来）同样没有 `|`，每块照旧对到自己首字的时间戳。
    在一个系列的 812 段上验证过：去掉空白后两份文本逐字相同；带 `|` 的 811 段已交付配音，把时间戳文本里的 `|` 去掉后，
    每块的起点一个都没变。
  - `tts_ali.py --synth` / `tts_volcano.py --synth` 同样先去掉 `|`（stderr 打一行实际送出的文本），拿解说词原文试听，
    结果和正式配音一致。
  - 实测（阿里 `xuyuyuan_v3.1`，+28%）：首版念了三处「竖线」的那一段（「它也能给所有行套函数、|过滤、分组汇总、|…」）
    去掉 `|` 重配，faster-whisper 的 zh / auto / en 三种模式都没有「竖线」（旧音频三种模式都有三处），字幕 4 块对到各自的首字。

### 缓存与已有项目

- 缓存签名改用去掉 `|` 的文本。**含 `|` 的段，下次跑 `tts_build.py` 时会重新合成**（这正是要的：旧音频可能念了「竖线」）。
  这些段的音频、时长、停顿、字级时间戳都会变，接着要重跑 `timeline_build.py` 和 `subs.py`，`B()` 节拍跟着刷新，成片要重审。
  不含 `|` 的段签名不变，照旧命中缓存。以后只挪 `|` 的位置（送出的文本没变）也不会重配。
- **已交付的项目不要再跑 `tts_build.py`，除非就是要重配**：几乎每段都带 `|`，一跑就是整片换音频。

### 测试

- 新增 `tests/test_tts_chunk_marks.py` 并接入 CI（不联网）：去 `|` 的规则；真的 `tts_build.synth` / `run()` 配三个假引擎
  （阿里是假的 SSE 服务端，连数字的展开读法一起造），送出去的文本里没有 `|`、`original_text` 留着；假引擎按去掉 `|` 的文本
  逐字给时间戳，跑真的 `subs.py`，每块起点正好是首字的时间戳；含 `|` 的段不命中 2.1.1 的旧缓存，不含的照旧命中，只挪 `|`
  也命中；两个 `--synth` 送的文本和 `tts_build` 一样。旧代码上 15 项失败。

## [2.1.1] — 2026-10-21

### 修复

- **`timeline_build.py` 拼音轨失败：`[libmp3lame] inadequate AVFrame plane padding`**（lessons #141）：2.0.9 起拼音轨时
  concat 完直接交给 libmp3lame。ffmpeg 6.1 起由命令行工具替编码器按 1152 个采样切帧：一帧的前一部分拷走，剩下的尾巴不拷贝，
  只挪数据指针，linesize 跟着减小。最后一帧碰上这样一截尾巴（末段补的静音帧）时，缓冲比 libmp3lame 要的
  `FFALIGN(nb_samples, 8)` 个采样少 4 个，编码器拒收，`narration-full.mp3` 和 `layout.json` 都不产出。
  只在 `av_cpu_max_align()` 为 16 字节的机器上出现（aarch64 / Apple Silicon、只有 SSE 的 x86）。AVX 的 x86 是 32 字节，
  切不出这种尾巴，所以 CI 一直是绿的。碰不碰上取决于总采样数和末段补了多少静音，一集约 1/64。
  实例：一集 48 段在 aarch64 上失败，后来重配了 4 段、总长变了，同一份脚本又能过。
  - concat 之后加 `asetnsamples=n=1152:p=0`：交给编码器的每帧正好 1152 个采样，ffmpeg 不再切帧。`p=0` 不补尾，采样一个不多、一个不少。
    三集真实配音（48 / 51 / 53 段）上，`layout.json` 和 `narration-full.mp3` 与 2.1.0 拼出来的逐字节相同，已出片的项目不用重跑。
  - 拼音轨的 ffmpeg 命令抽成 `concat_cmd()`。
  - `tests/test_timeline_build.py` 新增 C 段：合成两段的工程，让末段补的静音正好切出缓冲不够的尾巴。旧拼法加 `-cpuflags 0`
    （对齐降到 8 字节，x86 上也能复现）必须报这个错，新拼法同样加 `-cpuflags 0` 必须过；真的 `timeline_build.py` 要过；
    重新分帧前后 concat 出来的 PCM 逐字节相同。A 段加一条：concat 后面是 `asetnsamples=n=1152:p=0`。
    去掉这一处，aarch64 上 3 项失败，x86 上 2 项失败（x86 上真的脚本本来就能过）。

## [2.1.0] — 2026-10-21

### 新增

- **字级时间戳是插值的时提醒**（lessons #140）：阿里云百炼对某些写法不给真的字级时间戳，而是把一段（或从某个字起到段尾）
  的时长按字数分给每个字。音频正常，`subs.py` 照样切块、`B()` 照样能定位，只是字幕和画面节拍跟着插值的时刻走，对不上吐字，
  以前没有任何地方报。真的时间戳落在 1/32 秒的网格上（多数在 1/16 秒上），插值的不在：一个系列 13 集 673 段里，
  不在网格上的 offset 占比 648 段 ≤ 9 %、25 段 ≥ 57 %，界线很清楚。
  - `tts_build.py` 每合成（或从缓存取出）一段就判一次（至少 6 个字）：至少一半的 offset 不在 1/32 秒网格上（离格点 > 1 ms）
    或有连续 8 个不在；或每个字的时长 `max − min < 0.02 s`（插值碰巧落在网格上，比如每字正好 0.25 s）；或段尾 ≥ 8 个
    相邻字的步长相等（取整到毫秒后容差 2.5 ms）。插值从第 1、2 个字起是**整段**，从后面某个字起是**段尾**。
    被标出来的段在 stderr 打一行 ⚠（段 id、整段 / 后几个字、建议改写这一段再重配），全部合成完再汇总一行。只是提醒，退出码不变。
  - 只看「每字一样长」和「段尾步长相同」会漏掉一半：多字的词（「2025」「CrowdStrike」）分到整数倍的时长，
    步长取整到毫秒后差 2 ms（0.211 / 0.213）。
  - `audio-manifest.json` 每段新增字段 `"even_timestamps": "whole" | "tail" | null`（所以升次版本号）。下游脚本不读它，行为不变。
  - 同一段文字重配时服务端返回逐字节相同的音频，要换一条就得改字：换个说法、调语序、拆句（改成句号分开、把开头的词挪后）。
  - README 的质检表多了「配音时」一行。
  - 新增回归测试 `tests/test_tts_even_timestamps.py`（真实配音的 offset / 时长样本，含多字词、步长取整、段尾、正好在网格上的四种；
    把 `synth` 换成假的跑真的 `run()`，不联网），并接入 CI。

## [2.0.13] — 2026-10-21

### 修复

- **只装了 Chrome / Edge 的机器上渲染起不来**（lessons #139）：`render_video.mjs` 按 `--browser → BROWSER_PATH → Chrome → Edge`
  解析出浏览器、打了一行「浏览器：…」日志，却没有把它作为 `executablePath` 传给 `chromium.launch`，playwright 只认自带的 chromium，
  没下载过就报 `Executable doesn't exist`。`--browser` 与 `BROWSER_PATH` 也一直无效。自 2.0.8 起如此；`peek_frame` / `check_layout` /
  `cover_build` / `check_cover` 一直是对的，所以预览、体检、封面都正常，只有出片失败。现在与这四个工具一致。

### CI

- **渲染类回归测试在 CI 上从没真正跑过**：CI 不装 `node/` 的 `playwright-core`（seek history 直接失败），也不装 numpy / pillow / ffmpeg
  （快门、peek、qc 连续性、拼音轨的端到端段静默 SKIP，CI 照样全绿）。
  - 新增 `Install render dependencies`：`npm ci --prefix node`，pip 装 numpy / pillow / imageio-ffmpeg，并把 imageio-ffmpeg 的静态二进制以 `ffmpeg` 为名放上 PATH。
    浏览器用 runner 自带的 Chrome / Edge（依赖上面的修复）。
  - CI 设 `HX_NO_SKIP=1`：`test_seek_history.mjs`、`test_qc_continuity.py`、`test_timeline_build.py` 缺依赖时判失败而不是 SKIP。不设时本地行为不变。

## [2.0.12] — 2026-10-21

### 安全

按 ASH 3.5.4 扫描结果（容器模式，bandit / checkov / semgrep / opengrep / detect-secrets 等全部运行）修复：

- **`dashScope` 写成 `http://` 时 API Key 明文外泄**（bandit B310）：`tts_ali.py` 读密钥文件时要求 `dashScope` 以 `https://` 开头，
  否则报配置错误、不发请求（Bearer 密钥随每次请求发出；`file://` 等协议也不再交给 `urlopen`）。已有的 https 配置不受影响。
  `tests/test_tts_ali.py` 新增用例：https 正常读取，http / file / 无协议均被拒绝（旧代码上 3 项失败）。
- **CI 的 `GITHUB_TOKEN` 拿仓库默认权限**（checkov CKV2_GHA_1）：`ci.yml` 顶层声明 `permissions: contents: read`。
- **CI 引用可变 tag**（semgrep / opengrep mutable-action-tag）：`actions/checkout`、`setup-python`、`setup-node` 钉到 commit SHA，注释里标版本号。
- SHA1 只用于确定性选取与缓存文件名（bandit B324）：`style_director.py`、`tts_build.py` 加 `usedforsecurity=False`，哈希值不变，已有缓存照常命中。
- `tts_ali.py` 错误码提示表里的 `InvalidApiKey` 被 detect-secrets 误报为密钥：加 `pragma: allowlist secret`。

## [2.0.11] — 2026-10-21

### 修复

- **并行渲染的画面依赖 seek 历史，成片来回跳**（#5，lessons #138）：GSAP 补间在首次被渲染时才记录起点（相对值、`to()` 的起点、函数值）。
  `--workers N` 的每个浏览器只 seek 分给自己的帧，同一个补间在不同页面里于不同帧定起点，交错写盘的帧对不齐。
  实例：一列卡片错峰入场、同时用 `y: '-=26'` 整列上漂，第 4 张卡片相邻帧之间差到 22 px。`qc_check` ⑥ 只抓单帧跳动，抓不到。
  - `render_video.mjs`：每个页面记录已 seek 到的帧号，截第 i 帧前按顺序补齐跳过的帧（只推进 GSAP，不截图）。
    快门帧补首、末两个样本的时刻。换场、重试、`--recycle`、`--resume` 都从帧 0 补起。时间轴状态与 `--workers 1` 一致；补齐的代价是截图吞吐约降 9%（本机 1705 帧、6 进程：48.7 → 44.5 帧/秒）。
  - 新增 `--paint-history`：补齐时也完整绘制。移动元素边缘的抗锯齿取决于页面此前绘制过哪些帧，
    只补时间轴时个别像素仍差几十级，加此开关后实测 ≤ 1 级，并行截图吞吐约减半。
  - `peek_frame.mjs` / `check_layout.mjs`：按项目帧率从 0 逐帧走到目标时刻，看到的就是成片里那一帧（原来一步跳过去，是第三种状态）。
  - 修正日志：并行路径是共享队列取帧，不是 `f ≡ k (mod N)`。
  - `frame-contract.md`：说明 seek 历史，以及同一属性上时间重叠的补间即使顺序渲染也不对，应拆到父子元素或不同属性。
  - 新增回归测试 `tests/test_seek_history.mjs` 并接入 CI：480×270 的测试工程上，`--workers 3` 与 `--workers 1` 逐帧 PNG 字节比较（含快门模式），
    `peek_frame` 与成片同一帧逐像素比较。旧代码上 24/60 帧不一致、peek 最大差 204；新代码逐字节相同、peek 差 0。

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

[2.1.2]: https://github.com/amliuyong/html-to-explainer-video/releases/tag/v2.1.2
[2.1.1]: https://github.com/amliuyong/html-to-explainer-video/releases/tag/v2.1.1
[2.1.0]: https://github.com/amliuyong/html-to-explainer-video/releases/tag/v2.1.0
[2.0.13]: https://github.com/amliuyong/html-to-explainer-video/releases/tag/v2.0.13
[2.0.12]: https://github.com/amliuyong/html-to-explainer-video/releases/tag/v2.0.12
[2.0.11]: https://github.com/amliuyong/html-to-explainer-video/releases/tag/v2.0.11
[2.0.10]: https://github.com/amliuyong/html-to-explainer-video/releases/tag/v2.0.10
[2.0.9]: https://github.com/amliuyong/html-to-explainer-video/releases/tag/v2.0.9
[2.0.8]: https://github.com/amliuyong/html-to-explainer-video/releases/tag/v2.0.8
