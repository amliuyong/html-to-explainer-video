# html-to-explainer-video

[English](README.en.md) · 简体中文

一个 Agent Skill：把一个主题做成**带配音、词级字幕和动效画面的讲解视频**（MP4），并配好封面。

你给题目和时长，智能体按固定流水线推进：调研 → 解说词 → 配音 → 字幕与节拍 → HTML/GSAP 画面 → 逐帧渲染 → 质检 → 封面。
画面里每个元素的出场时刻都锚在解说词的吐字时刻上，改一句词、重跑配音，画面自动重新对位。

---

## 特性

| | |
|---|---|
| **配音** | 默认 **阿里云百炼 `qwen-audio-3.1-tts-flash`**（字级时间戳，18 个实测音色）；也可切 edge-tts（免密钥）或火山引擎语音合成 2.0。三种引擎产出同一种 manifest，下游零改动 |
| **字幕** | 按词/字级时间戳切块、硬字幕烧录，同时输出 SRT / VTT；数字展开读法（「2026」→「二零二六」）会映射回原文，不会错位 |
| **画面** | HTML + CSS + GSAP，1920×1080 或竖版；23 个风格模板（8 类）+ 40 多个动效词汇；`style_director.py` 读解说词给每场挑风格与动效强度 |
| **音画同步** | 场景里写 `B('块文本')` 取该字幕块的起播秒排动画；时长取 MP3 容器时长，拼音轨时每段按同一时长补齐到采样（长片不漂移），字幕收尾取语音真实结束 |
| **渲染** | 确定性逐帧 seek 截图（不是实时录屏）；1080p / 2K / 4K，30 / 60 fps；可选快门运动模糊；多浏览器进程并行、断点续渲 |
| **质检** | 渲染前：节拍引用校验、契约 lint、几何体检（遮挡 / 越界 / 侵入字幕带）；渲染后：流、时长、音量、抽帧速览、**逐帧连续性**（单帧跳动、场间硬切） |
| **封面** | 16:9 + 3:4（投竖版再加 9:16），每张独立排版，出图后做几何实测 |
| **确认闸门** | 配色、风格、形态、配音、时长、封面、渲染通道七项由用户拍板，`gate_check.py` 在构建和渲染前各挡一次 |

---

## 工作原理

```
narration.json ──tts_build──▶ audio/*.mp3 + manifest（词/字级时间戳）
                  │
                  ├─timeline_build─▶ layout.json（全局时间轴）+ narration-full.mp3
                  │
                  └─subs──────────▶ subs.json + frames/<id>.beats.js（B() 节拍器）+ SRT/VTT
                                              │
frames/<id>.html（GSAP 时间轴，B() 取节拍）◀─┘
        │
        └─render_video──▶ 逐帧 tl.pause(t) → 截图 → ffmpeg 合成 → out/<slug>.mp4
                              │
                              └─qc_check / cover_build / check_cover
```

- **一句话一个场景**。每个场景是一个自包含的 HTML 文件，注册一条暂停的 GSAP 时间轴 `window.__tl`。
- **渲染器逐帧 seek**：把时间轴停在第 *t* 秒，同步 CSS 动画，截图，再交给 ffmpeg。没有墙钟，不受机器快慢影响，
  同一份输入每次渲出的画面一致。
- **段间静默算在上一场里**：场景帧一直播到 `duration + gap`，出场过渡就写在这段静默里（`__SEG__.gap`）。
- 字幕层与进度条由渲染器注入并逐帧驱动，场景作者不用管。

---

## 安装

**依赖**：Python ≥ 3.9、Node ≥ 18、Chrome 或 Edge（都没有时自动下载 playwright chromium）、ffmpeg
（PATH 上没有就用 imageio-ffmpeg 自带的静态二进制）。

```bash
git clone https://github.com/amliuyong/html-to-explainer-video.git ~/.claude/skills/html-to-explainer-video
bash ~/.claude/skills/html-to-explainer-video/setup_env.sh --install   # 自检 + 补装缺项，可反复跑
```

仓库根目录就是技能目录。其它智能体把 `~/.claude/skills/` 换成对应目录即可：

| 智能体 | 个人级 | 项目级 |
|---|---|---|
| Claude Code | `~/.claude/skills/` | `.claude/skills/` |
| OpenAI Codex | `~/.codex/skills/` | `.codex/skills/` 或 `.agents/skills/` |
| Gemini CLI | `~/.gemini/skills/` | `.gemini/skills/` 或 `.agents/skills/` |
| Cursor | `~/.cursor/skills/` | `.cursor/skills/` |
| GitHub Copilot / VS Code | `~/.copilot/skills/` | `.github/skills/` |
| 通用约定 | `~/.agents/skills/` | `.agents/skills/` |

装完新开一个会话，让智能体重新扫描技能目录。

---

## 配置配音

### 阿里云百炼（默认）

1. 在百炼控制台创建 API Key，并记下业务空间的专属域名。
2. 写成两列 CSV，放到 `~/ali_tts/api_key.csv`（或用环境变量 `ALI_TTS_KEY_FILE` 指向别处），权限设为只读：

   ```csv
   apiKey,<你的 API Key>
   dashScope,<业务空间专属域名的 base URL>
   ```

3. 自检与选音色：

   ```bash
   python scripts/tts_ali.py --check                 # 真合成一句，验证连通（密钥脱敏显示）
   python scripts/tts_ali.py --voices                # 列出内置音色
   python scripts/tts_setup.py --project . --provider ali --voice xuyuyuan_v3.1   # 写进 project.json
   ```

默认音色 `xuyuyuan_v3.1`（许玉远，女声）；英文项目默认 `Andy_v3.1`。3.1 模型只认 `*_v3.1` 音色。
语速沿用 `+N%` 写法（新项目默认 `+8%`）。

> 智能体只调用 `tts_ali.py`，**不读取、不打印密钥文件**。缺文件时 `tts_setup.py` 输出 `NEXT_ACTION=fill_ali_key` 并停下，由你来补。

### 其它引擎

| 引擎 | 切换 | 说明 |
|---|---|---|
| edge-tts | `--provider edge` | 免费、免密钥，开箱可用 |
| 火山引擎语音合成 2.0 | `--provider volcano` | `cp tts.env.example tts.env` 后填 Key（`tts.env` 已 gitignore），`tts_setup.py --check` 测连接 |

也可以用环境变量 `TTS_PROVIDER=ali|edge|volcano` 切换。

---

## 用法

### 交给智能体

直接说要做什么，例如：

> 用 html-to-explainer-video 把「向量数据库是怎么检索的」做成一个 5 分钟的讲解视频，面向后端工程师。

智能体会在七个确认点停下来问你（配色、风格、形态、配音方案与音色、时长与画幅、封面张数、渲染通道），
你拍板后它才继续。

### 手动跑流水线

在视频项目目录里，按顺序：

```bash
SK=~/.claude/skills/html-to-explainer-video
python $SK/scripts/new_project.py my-video my-video --topic "向量检索"   # 建项目
cd my-video
python $SK/scripts/tts_setup.py      --project . --provider ali           # 配音方案与音色
python $SK/scripts/tts_build.py      --project .                          # 配音
python $SK/scripts/timeline_build.py --project .                          # 全局时间轴
python $SK/scripts/subs.py           --project .                          # 字幕 + 节拍器
python $SK/scripts/check_beats_refs.py --project .                        # B() 引用全部可解析
python $SK/scripts/lint_frames.py    --project .                          # 契约 lint
node   $SK/scripts/check_layout.mjs  .                                    # 几何体检
python $SK/scripts/gate_check.py     --project . --phase render           # 渲染前闸门
node   $SK/scripts/render_video.mjs  . --png-fast --audio audio/narration-full.mp3
python $SK/scripts/qc_check.py       --project .                          # 成片质检
node   $SK/scripts/cover_build.mjs   . && node $SK/scripts/check_cover.mjs .
```

项目目录结构：

```
my-video/
├── project.json        # 画布、fps、配音、场景顺序 order、章节
├── narration.json      # 解说词：一场一条，用 | 切字幕块（中文 ≤16 字/块）
├── theme.css           # 全部颜色变量（场景代码里不写色值）
├── frames/<id>.html    # 每个场景一个文件
├── audio/  layout.json  subs.json
└── out/                # 成片、封面、SRT/VTT、质检报告
```

---

## 渲染选项

```bash
node scripts/render_video.mjs . --list-profiles      # 看全部档位
```

| 档位 | 画质 | 帧率 | 快门 | 用途 |
|---|---|---|---|---|
| `draft` | 1080p | 30 | 关 | 打样、看节奏 |
| `balanced` | 1080p | 30 | 180° | 默认推荐 |
| `final` | 4K | 60 | 180° | 终稿 |
| `master` | 4K | 60 | 180° | 极限画质（PNG 精细 + 最慢编码） |

| 截图通道 | 参数 | 适用 |
|---|---|---|
| PNG-fast（推荐） | `--png-fast` | 纯 CSS / MG 图形帧：与 JPEG q95 同速、体积更小、逐像素无损 |
| JPEG q95 | `--jpeg --jpeg-quality 95` | 含满幅照片或 4K 终稿 |
| JPEG q82 | `--jpeg --jpeg-quality 82` | 只做打样 |

常用开关：`--preview 30`（只渲前 30 秒）、`--only <场景id>`、`--workers N`（多进程并行）、`--resume`（断点续渲）、
`--shutter 0`（关快门）、`--shutter-only <id,id>`（只给点名的场景开快门）。

---

## 质检

| 阶段 | 命令 | 抓什么 |
|---|---|---|
| 渲染前 | `check_beats_refs.py` | `B()` 引用不到字幕块（否则渲到那一帧才报错） |
| | `lint_frames.py` | 外链字体 / CDN、色值字面量、墙钟逻辑、CSS transition、字幕带压内容、缺中文字体、**搭建期读布局**（连帧引用的本地脚本一起扫） |
| | `check_layout.mjs` | 元素遮挡、出画、侵入字幕带、该对齐没对齐 |
| 渲染后 | `qc_check.py` | 流与时长对齐、音量、抽帧速览图、**单帧跳动**（FAIL）、**场间硬切**（WARN） |
| 封面 | `check_cover.mjs` | 边距、钩子字号、行宽、孤字、文字重叠 |

画面出了问题、知道是第几秒时：`python scripts/frame_at.py --project . --at 1:23` 直接定位到场景、帧号和源文件；
`node scripts/peek_frame.mjs . <场景id> --at-sec 3.5` 秒级出单帧图。

---

## 写场景时要守的规矩

完整版见 [`references/frame-contract.md`](references/frame-contract.md)，模板见 [`assets/frame-template.html`](assets/frame-template.html)。

1. 画布 1920×1080，只用系统字体，不引外部字体或 CDN。
2. 颜色只取 `theme.css` 的变量。
3. 动画只用 GSAP，一条暂停的时间轴注册在 `window.__tl`；入场不用 CSS transition。
4. 节拍用 `B('块文本')`，不写死秒数。
5. 底部 80–170px 留给字幕，0–12px 留给进度条。
6. 每帧一个视觉焦点。
7. **搭建时不读布局**：需要量的尺寸在生成期量好写成常量，否则多进程渲染时每 N 帧跳一下。
8. **出场过渡写在本场尾部** `[speech_end, duration + gap]`：渲染器不做转场，不写就是硬切。

---

## 参考文档

| 文档 | 内容 |
|---|---|
| [`SKILL.md`](SKILL.md) | 智能体读的完整流程与确认点 |
| [`references/frame-contract.md`](references/frame-contract.md) | 场景契约、节拍器、几何判据、场间过渡 |
| [`references/style-catalog.md`](references/style-catalog.md) | 23 个风格模板的设计规范 |
| [`references/motion-library.md`](references/motion-library.md) | 动效词汇（`assets/motion.js`） |
| [`references/render-profiles.md`](references/render-profiles.md) | 档位、截图通道、快门的速度与画质权衡 |
| [`references/ali-tts.md`](references/ali-tts.md) · [`references/volcano-tts.md`](references/volcano-tts.md) | 配音引擎细节与排错 |
| [`references/cover-guide.md`](references/cover-guide.md) | 封面排版规则 |
| [`references/lessons.md`](references/lessons.md) | 踩坑记录：每条静默失败的现象、根因与闸门 |

---

## 开发

```bash
python scripts/check_integrity.py          # 仓库自洽：版本号、风格目录、模板外链、密钥防线、frontmatter
python tests/test_subs_text.py             # 字幕文本规则
python tests/test_tts_ali.py               # 阿里 TTS（不联网、不需要密钥）
python tests/test_lint_frames.py           # lint：搭建期读布局
python tests/test_qc_continuity.py         # 逐帧连续性（缺 numpy / ffmpeg 时跳过端到端段）
node   tests/test_beats_norm.mjs           # 节拍匹配
```

CI（`.github/workflows/ci.yml`）在 Ubuntu 与 Windows、Python 3.9 与 3.13 上跑以上检查。

---

## 许可

[MIT](LICENSE)。随包附带的第三方组件（GSAP、playwright-core）与转写的风格目录各自适用其许可，
见 [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)；再分发时请连同该文件一起带上。
