# 变更日志

本项目的所有重要变更都记录在这里。格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循[语义化版本](https://semver.org/lang/zh-CN/)。

---

## [2.2.4] — 2026-10-24

### 修复

- **`B()` 落到前面只是前缀匹配的块，一模一样的那块反而落不到**（lessons #153）：beats.js 的 `find()` 取第一个满足
  `bt === t ‖ bt.startsWith(t) ‖ t.startsWith(bt)` 的块。一个系列 08-6 eo4 的两块「提交；」「提交之后，」挨着，
  `B('提交之后')` 落在「提交；」，早了 0.6 秒；后一块的任何开头、连它的全文都先撞上前一块，用 `B()` 根本落不到。
  `check_beats_refs.py` 只问「有没有块匹配」，照样 OK。
  - 改成**一模一样的块优先**，没有一模一样的取第一个匹配的块。三处同一条规则：`subs.py` 生成的 beats.js、`new_project.py` 的占位 beats
    （#88 说的两份模板，测试钉住两份逐字相同）、`check_beats_refs.py` 新的 `resolve()`（测试在 node 里跑真的 beats.js 逐条对）。
  - `check_beats_refs.py` 匹配到不止一块、又没有一模一样的那块时报**歧义**，算错（退出码 1）：写明所在行、落到哪块、每块为什么匹配，
    和要那一块最短怎么写（`B('提交')` / `B('提交之后')`）。一模一样的块不止一块时只提醒（`⚠`，不算错）：`B()` 只落得到第一块。
    每行多一栏「歧义」，「失配」那一栏格式不变。
  - 算错不算提醒：一个系列 38 集交付的 7402 个 `B()`/`Be()` 里歧义是 0 个，在做的 10-1、10-3 也是 0 个；落在哪块只看块的先后，解说词改了
    切块就会换一块，而且不报错；除了完全相同的两块，总有写法落得到想要的那块。
  - 交付的帧：落点变的 0 个。匹配到不止一块的只有 08-6 eo4 的两处 `B('提交；')` / `Be('提交；')`，本来就一模一样、落在第 7 块。
    38 集的 `check_beats_refs` 输出除了新加的「歧义」一栏逐行相同，退出码全是 0。
- **`qc_check` 的场景边界和渲染器差一帧**（lessons #154）：qc 拿 layout 的 `start_sec`（舍入到 3 位）乘 fps 再 Python `round`（银行家舍入），
  渲染器累加 `duration_sec + gap`（不舍入）再 `Math.round`（.5 往上入）。一个系列 38 集 1932 个边界里 93 个差一帧：40 个 qc 晚一帧，
  「场间硬切」量的是下一场第 1 帧 − 第 0 帧（09-2 tmo7：87.45 × 30 = 2623.5 → 2624，渲染器是 87.44999999999999 × 30 → 2623）；
  53 个 qc 早一帧，量的是上一场停在 gap 里的两帧，真的硬切也看不见。
  - 新的 `scene_frame_starts()` 照抄渲染器的公式（gap 取 `project.json`，同渲染器的 `pj.gap || 0`），`js_round()` 就是 `Math.round`；
    `scene_boundaries()` 多一个 `gap` 参数。测试从 `render_video.mjs` 取出那几行在 node 里跑 403 组小数时长逐场对，另钉住 .5 帧、
    累加略大于 / 略小于 .5 三种边界。
  - `frame_at.py` 也用它：`--list` 的起帧 / 止帧，和 `--at` 的「场景终态帧」（原来在这些边界上会取到下一场的第 0 帧）。
  - 只影响 ⑥ 的「场间硬切」（WARN）和单帧跳动归到哪一场；FAIL 不受边界影响。重跑交付的 6 集见 lessons #154。

不改渲染器的出帧、字幕和配音；交付的成片不用重渲。

## [2.2.3] — 2026-10-23

### 修复

- 通用讲解流程不再要求每章固定次数的运镜、每句画面变化或超过 3 秒自动推镜（#23）。
  文字、图表与客户演示可以保留稳定的阅读停留；有叙事目的的运镜与宣传片动作语言继续支持。
- QC 指导增加实际成片的连续区间复核，并明确静态抽帧与单帧跳动检查无法判断平滑漂移是否影响阅读。
  抖动诊断先对照时间轴、源 PNG 与 MP4，区分作者加入的位移和渲染不一致。

本次只修订指导，不改变渲染器、动效 API、配音或字幕行为；已有成片需要移除不必要动画后重新渲染。

## [2.2.2] — 2026-10-22

### 修复

- **`lint_frames` 把脚本字符串里的「#301–」、id 选择器「#c13」当成色值字面量**（lessons #151）：第 3 条原来把 `#[0-9a-fA-F]{3,8}`
  套在整份源码上，脚本字符串、注释、画面文字、选择器全算。一个系列 07-1 的 split1 写了格名 `'#301–'` 和 `var CUR = '#c13'`，
  lint 报 4 条，只好拆成 `'#' + n + '–'`；lessons #108 也只能叫人改 id。
  - 改成只在颜色上下文里找 `#hex` / `rgb()` / `rgba()`：CSS 声明的值（`<style>`、`style="…"`、本地样式表；注释、引号里的字符串、
    `url(#…)` 不算，选择器不看）；颜色类的标签属性（`fill` / `stroke` / `stop-color` / `color` …）；脚本里赋给颜色类属性的值
    （`{ fill: … }`、`attr: { 'stop-color': … }`、`el.style.color = …`、`ctx.fillStyle = …`、`setAttribute('fill', …)`、
    `style.setProperty('--x', …)`）；整串就是 `#rrggbb` / `#rrggbbaa` 的字符串（调色板）；字符串里的 `rgb()` / `rgba()`。
    脚本拼的 `innerHTML` 与 `cssText` 照样查。
  - 不再报：`'#301–'`、`'#c13'`、`querySelector('#c13')`、`gsap.to('#bd1', …)`、`#bd1 { … }`、注释、画面文字、`url(#fade)`。
  - 查不到的：3 / 4 位色值先存进变量再用（`var C = '#c13'`），它和 id 分不开。
- **停顿并进英文词的前 2–3 个字母时，块还是提前 0.3–0.7 秒**（lessons #152）：2.1.3 只管了一个字母的（「：|p」+「andas」）。
  「；|Not」+「ion」、「bl」+「ameless」、「；|Ro」+「aring」、「ir」+「reversibility」、「，eg」+「gs」同样盖住了停顿，
  一个系列 25 集里块首有这 5 处，比开口早 0.28–0.68 s。
  - 判据沿用 2.1.3 的五条，「一个字母」放宽成「一个字母，或 2–3 个字母且后面的是小写」；后两个字母大写的（「API」+「s」）是拼读，不动。
  - 字母本身已经在这一项里开口了（开口比后半截的开头早 0.15 s 到晚 0.12 s），所以起点是这一项的结尾再往前退 0.125 s
    （普通块的提前量，5213 块的中位数 0.126 s）。改后 5 块比开口早 0.07–0.24 s，最晚的晚 0.03 s（不到一帧）。
  - 没管：「。Sl」+「ack」0.5 s 和前一项一样长，被插值那条护栏挡下（一个字母的「，R」+「iak」0.313 s 同理）；0.25 s 的项照旧不动。

### 已有项目

- `lint_frames`：只会少报，不会多报。那个系列 25 集 1346 帧新旧都是 0 条；另外 5501 帧（别的工程、模板、几何夹具）里旧代码报的
  171 处色值一处不少。为了绕 lint 拆开拼的 id、格名（07-1 split1–7 的 `'#' + n + '–'`）可以写回原样，不改也没事。
- `subs.py`：重跑后只有块首是「停顿 + 2–3 个字母」的块起点往后挪（和前一块的收尾）。那个系列是 4 集 5 块（02-2、02-3 ×2、04-3、06-5），
  晚 0.25–0.50 s；挂在上面的 `B()` 入场有 2 个（02-3 pr2「Notion 的一台」、04-3 bmp4「Roaring位图」），重跑后要重渲那两场。不用重配。

### 测试

- `tests/test_lint_frames.py`：12 种不该报的写法（07-1 那两行原样、id 选择器、三种注释、画面文字、`<style>` 选择器、`url(#…)`、
  `content`、非颜色属性、「Color: #1」）、17 种该报的（`<style>`、`style=`、SVG 属性、gsap 各颜色属性、`attr`、`setAttribute` /
  `setProperty`、赋值、三元、调色板、存进变量的 `rgba()`、`innerHTML`、`cssText`）、本地样式表一项。旧代码上 15 项失败；
  12 条判据各去掉一条都有测试变红。
- `tests/test_subs_pause_letter.py`：5 段真实时间戳（加上不带 `|` 的「；Not」）该改，「。Sl」「Le」「。|Fa」「，API」+「s」、
  四个字母的前缀不动。旧代码上 6 项失败；7 种改坏的写法各有测试变红。25 集重跑 `subs.py`：6680 块里只有这 5 块的起点和前一块的收尾变。

## [2.2.1] — 2026-10-22

### 修复

- **`tts_build` 把截断的配音当成功、写进缓存**（lessons #150）：一个系列的 06-5 首配时，阿里的读流断了好几次
  （日志里是「读流中断」「SSE 帧不是合法 JSON」），其中 11 段拿回的是半截音频、一个字级时间戳都没有
  （`boundary_count` 0，qr1 一整句只有 3.77 秒）。`tts_build` 照样写进缓存，重跑拿回的还是它；插值检查不到 6 个字不判，
  0 个时间戳更不判，下游一句不报。那一集是靠耳朵和 ASR 找出来、每段改一点措辞逼出新的一次合成的。
  - 根因：分块传输的连接断在**两块之间**时，标准库逐行读把它当成正常 EOF（`http.client` 的 `_peek_chunked` 吞掉
    `IncompleteRead`），`parse_stream` 又不查最终帧，半截流就成了一次成功的合成。断在一块里面才是「读流中断」，
    断在一帧中间才是「SSE 帧不是合法 JSON」—— 这两种本来就报错重试，06-5 漏过去的是第三种。
  - `tts_ali.parse_stream` 读不到最终帧（`finish_reason` 是 `stop` 的那一帧）就抛可重试的错「读流提前结束」；
    `tts_volcano` 同一个读法，读不到结束标记 `code=20000000` 同样抛错。
  - `tts_build` 对三个引擎的结果做两条体检，不过就算合成失败：照原有的退避重试，重试完还不过就非零退出（阿里、火山
    `return 1`，edge `SystemExit`），坏的结果不写缓存，也不覆盖上一次的 `audio/<id>.mp3`（先写到 `.part` 旁边，过了才换上去）：
    ① 有可念的字，却一个字级时间戳都没有；② 音频短得不像这段文字：裁后容器时长 < 0.11 秒 × 可念单位数 ÷ 语速倍数
    （汉字一个算一个，字母、数字连成的一串算一个）。读缓存时同样过这两条，坏的缓存不用、重新合成，好的结果覆盖它。
  - 下限的依据（折回 +0% 的每单位秒数）：一个中文系列 23 集 1164 段（`xuyuyuan_v3.1`，+28%）中位数 0.280、最快 0.211；
    另一部中文片 54 段（`anmingyuan_v3.1`，+8%）最快 0.187；两部英文片 15 段（`Andy_v3.1`）最快 0.338。0.11 是中位数的 39%，
    这 1233 段、加上在做的两集和 60 份试配的 3227 段，没有一段被误报，最紧的一段是下限的 1.70 倍。按字母数会让英文片每段都撞线，所以英文按词数。
    ② 只抓「半截以下」的粗截断：06-5 那 11 段截剩 3.70–8.95 秒，多在下限之上，靠的是 ① 和查最终帧。
  - 目录外不返回时间戳的音色，原来打一条警告、字幕退化成按字数插值，现在算失败（警告照打，说明为什么）。

### 已有项目

- 重跑 `tts_build`：好的缓存照旧命中，一段不重配。2.2.0 及以前写进缓存的截断结果（0 个时间戳、或短得不像）
  读的时候认出来，打一行 ⚠「缓存里这一段是坏的」，重新合成；那一段的音频、时长、时间戳都会变，接着重跑 `timeline_build.py` 和 `subs.py`。
- 已交付的项目不用重跑：那个系列 23 集的 manifest 里没有一段是 0 个时间戳或短得不像的。06-5 那 11 段已经改字重配过，
  `audio/cache/` 里留着的 11 条坏缓存签名对不上现在的文字，不会再被读到。

### 测试

- 新增 `tests/test_tts_truncated.py` 并接进 CI（不联网）：真的 `http.client.HTTPResponse` 造分块响应，断在两块之间 / 一块里面 /
  一帧中间，火山没有结束标记；真的 `tts_build.synth` 配假的阿里服务端：读流截断、0 个时间戳、音频太短，每种都重试 3 次后报错、
  缓存里没有文件、上一次的音频不被覆盖，截断一次第二次正常的照常成功；坏的缓存（0 个时间戳 / 太短）重新合成并覆盖，
  引擎还是坏的就报错、缓存一个字节不写；`run()` 三个引擎各有一段坏结果都非零退出、不写 manifest；下限的口径与三类真实最快的段。
  旧代码上 A–D 段 42 项失败（E 段测的函数旧代码没有）。
- `tests/test_tts_chunk_marks.py` 的假阿里服务端补上最终帧（真的服务端每次都发）。

## [2.2.0] — 2026-10-22

第一次用本技能做英文片（cove-lake 的 72 秒宣传片）碰到的五个问题。英文片的字幕口径变了（保留句内标点），所以升次版本号。

### 变更

- **英文片的字幕保留句内标点**（lessons #145，2026-10-22 用户拍板）：`project.json` 的 `lang` 是 `en` 时，没有中文的块在屏幕文本
  （字幕、SRT / VTT、`subs.json`）里保留逗号、冒号、分号与千分位，只去块尾的 `. , ; : …`，`? !` 留着。原来与中文同一条规则全删，
  「Kafka, a warehouse」上屏成「Kafka a warehouse」，「1,000」成「1000」；GIF 这类没有声音的投放里读不通。
  中文片一块都不变，哪怕某一块全是英文（lessons #88）。

### 修复

- **切块符去掉后英文词连在一起**（lessons #145）：`strip_chunk_marks` 在 `|` 左边是英文后面的半角句读（`. , ; : ! ?` 或右括号）、
  右边是字母或数字时补一个空格（引号、省略号后面也补）。原来只在两边都是字母或数字时补，「minutes.|Today」送给 TTS 成了「minutes.Today」。
  别的 ASCII 符号（「TCP/|IP」「Data-|Intensive」「(|Kafka」）、全角标点、中文后面的半角标点照旧直接连上。
- **英文段被误报「字级时间戳是插值的」**（lessons #146）：阿里给英文的是词级时间戳，真的落在 0.08 秒网格上，不在 1/32 秒上；
  一部 8 段的英文片 8 段全打 ⚠，其实只有 3 段是插值的。`tts_build` 认两个网格，每段取落点多的那个再判。
- **`check_layout` 对有出场的场景什么都没量**（lessons #147）：它量时间轴末尾，那时出场已经把东西淡没了，量到 0 个元素、0 个问题。
  帧有出场（`__SEG__.gap > 0`）时改量 `speech_end` 那一刻（报告的 `measuredAt`）；量到 0 个元素时报 WARN「没量到元素」。
- **`lint_frames` 不看帧引用的本地样式表；英文片也要求中文字体**（lessons #148）：顺着 `<link rel="stylesheet">` 扫本地样式表
  （`theme.css` 除外），画布、外链 `@import`、色值、transition、字体栈都查，出自样式表的问题记在样式表名下、只报一次；
  字幕带的 `bottom` 启发式只看帧自己的 CSS。中文字体族只在画面上有中文（注释、`<title>`、属性不算）或 `lang` 是 `zh` 时要求。
- **QC 跳过帧数校验**（lessons #149）：有的 ffmpeg 构建（Ubuntu 24.04 的 6.1.1）流复制时不数帧；`qc_check` 改为流复制没给帧数时解码一遍再数。

### 已有项目

- **中文片**：字幕不变。重跑 `tts_build`，只有「英文词 + 半角句读 + `|` + 英文词」的段（例如「PostgreSQL,|MySQL」）换缓存签名、重配一次，
  送出去的文本多一个空格；别的段照旧命中缓存。
- **英文片**：重跑 `subs.py`，屏幕文本补回句内标点，块的起止时刻不变，`B()` 查到的还是同一块（`norm()` 本来就去标点）。
  `audio-manifest.json` 的 `even_timestamps` 要重跑 `tts_build`（命中缓存，不重配）才会刷新。
- 有出场的帧，`check_layout` 现在真的量到东西了：原来全绿的工程可能冒出 ERROR，那是一直存在、以前没量到的问题。
- 帧引用了本地样式表的工程，`lint_frames` 可能多出样式表里的色值、transition、外链 `@import`（以前没扫），每个样式表每条只报一次。

### 测试

- 新增 `tests/test_qc_frame_count.py`、`tests/test_layout_exit.mjs`，并接进 CI；`test_subs_text.py`、`test_tts_chunk_marks.py`、
  `test_tts_even_timestamps.py`、`test_lint_frames.py` 加了英文与本地样式表的用例。每个新用例都在旧代码上跑过，必须变红：
  `test_subs_text.py` 6 项、`test_tts_chunk_marks.py` 6 项、`test_tts_even_timestamps.py` 1 项、`test_lint_frames.py` 8 项
  （这两个要先去掉旧代码不认的新参数）、`test_qc_frame_count.py` 3 项、`test_layout_exit.mjs` 6 项。

## [2.1.3] — 2026-10-22

### 修复

- **字幕把「ASN.1」「X.509」里的点删掉**（lessons #143）：`subs.py` 去句读标点时，只有左右都是数字的点才算词的一部分（`2.4%`），
  字母旁边的点照删。一个系列的 05-2 写了「A S N.1」，字幕上成了「ASN1」，只好改写解说词，ASN.1 只上批注。
  - 点的左右都是数字或 ASCII 字母就留着：`ASN.1`、`X.509`、`v1.5`、`3.14`、`node.js`、`S3.Select`、`1.2.3`。
    句末的点、「e.g.」最后那个点、中文旁边的点、前导的「.5」照删。
  - 逗号照删（lessons #88 定的口径）。数据也不支持留：19 集交付的字幕里没有一处「1,000」，大数都写成 1000 / 5800 / 32768。
  - 五个出口一起改（lessons #88）：屏幕文本 `clean_sub_text`（SRT / VTT / `subs.json` 跟着它）、对齐用的 `_PUNCT_RE`、
    `B()` 的归一化（`subs.py` 和 `new_project.py` 两份模板，加上 `check_beats_refs.py` 的 Python 版）、火山 `split_long_text` 的切句。
    `test_subs_text.py` 新加一条：两份模板的 `norm()` 逐字相同。
  - `B()` 的归一化改用前瞻。原来的 `(\d)\.(\d)` 匹配时吃掉右边的数字，「1.2.3」只护住第一个点，归一成「1.23」，
    会撞上真念「1.23」的块；两边一样地错，所以一直查得到，没人发现。
- **块起点落在停顿上，比吐字早 0.3–0.7 秒**（lessons #144）：标点后面紧跟英文词时，阿里常把停顿并进下一个词的首字母，
  给成一项：「：|p」0.375 s，后面一项「andas」（2.1.2 起送出去的文本没有 `|`，就是「：p」）。`subs.py` 拿这一项的开头当块起点，
  这一块在停顿一开始就上屏。2.1.2 的实测是 0.39 s。
  - 块首那一项同时满足这几条，块就从这一项的结尾起（不晚于下一项的开头）：去掉标点只剩一个 ASCII 字母；≥ 0.3 s；
    从前一项的结尾开始（空档 < 0.03 s）；和前一项不一样长；下一项紧接在它后面、小写开头，是同一个词的后半截。
    拼读的字母（「；|B」+「SO」、「K」0.344 s +「B」）、停顿在前一项后面空档里的（「F」+「aa」、「；|D」+「atomic」）、
    插值的等长项（lessons #140）都不动。
  - 依据（一个系列 19 集，5049 块）：这样的项有 11 个，0.31–0.69 s，8 个在块首；不盖停顿的首字母项最长 0.19 s
    （插值的段每字一样长，到过 0.27 s / 0.31 s）。音频里，这 11 处的开口比后半截的开头晚 0.02–0.18 s（中位数 0.12 s），
    和普通块（块首是停顿后的中文字）起点早于开口的量一样（中位数 0.12 s，3148 块）：字母本身的时长已经算进停顿那一项了，
    所以不再往前减一个字母的时长。
    改后这 8 块比开口早 0.02–0.16 s（改前 0.40–0.80 s）。
  - 没管：0.19–0.25 s、盖住一部分停顿的首字母项（块首 2 处，多早 0.16–0.19 s，和不盖停顿的「H」+「dr」0.188 s 分不开）；
    停顿并进 2–3 个字母的（「；|Not」+「ion」，块首 6 处），这几个字母本身要占时长，要另外定起点（lessons #144）。

### 已有项目

- 重跑 `subs.py`：只有块首是这种项的块变（起点往后挪，前一块的收尾跟着挪），别的块逐项不变。`beats.js` 里 `norm()` 那几行换了，
  `B()` 查到的还是同一块。一个系列 19 集上核过：5049 块里只有这 8 块的起点（和它们前一块的收尾）变了，往后挪 0.38–0.69 s；
  3830 个 `B()` / `Be()` 查到的块全部不变。挂在这 8 块上的 `B()` 入场也跟着往后挪，重跑后这几场要重渲、重审。
- 不用重配：音频和时间戳都没变，变的只是 `subs.py` 怎么读它们。

### 测试

- 新增 `tests/test_subs_pause_letter.py` 并接入 CI（不联网）：照抄真实配音的时间戳（不带音频），断言「：|p」「，|C」「。|K」
  和单个字母的块从这一项的结尾起，2.1.2 以后的「：p」也一样；拼读的字母、空档里的停顿、不够长的、插值的等长项不动；
  再跑真的 `subs.py`，只有受影响的块起点和前一块的收尾变，`beats.js` 跟着变。旧代码上 8 项失败。
- `tests/test_subs_text.py` 加词里的点（屏幕文本、`_PUNCT_RE`、火山切句、`check_beats_refs.norm`、两份 `norm()` 模板一致），
  旧代码上 15 项失败；`tests/test_beats_norm.mjs` 加「X.509」撞「X509」、「2.0.1」撞「2.01」，旧代码上 2 项失败。

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
