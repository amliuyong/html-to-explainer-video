# 场景帧契约（frame-contract）

一个场景 = 一个自包含 HTML 文件 `frames/<id>.html`，1920×1080，GSAP 时间轴驱动。
渲染器逐帧 `tl.pause(t, false)` 确定性截图 —— 与实时录制的根本区别：**任何「依赖墙钟」的东西都会失控**。

> **画面从哪来？** 先看 **`references/style-catalog.md`** —— html-video 的 23 个模板风格
> （8 个类别，含每种画布/字体/时间轴/配色规范）。改编三步法与挑风格建议见
> **`references/template-guide.md`**。从零设计画面是最后选项，不是第一选项。

模板：`assets/frame-template.html`（复制后改内容，契约注释在文件头）。

## 十条硬性契约

| # | 契约 | 违反后果 |
|---|------|----------|
| 1 | 画布 1920×1080（或 project.json 声明的尺寸），系统字体栈 | 渲染视口即画布 |
| 2 | 绝不引外部字体/Google Fonts（渲染器 5s 字体硬顶） | 整帧退化或首帧字体错 |
| 3 | 颜色全部取 `../theme.css` 的 CSS 变量，禁止字面量 | 换主题 = 半换皮 |
| 4 | 动画只用 GSAP：`var tl = gsap.timeline({paused:true}); window.__tl = tl;` | 渲染器找不到时间轴 → 报错 |
| 5 | 节拍用 `B('块文本')` 对齐吐字 | 画面与配音两张皮 |
| 6 | 底部 80–170px 是字幕带（**170px 是硬底线**；版式设计基准取 176px，留 6px 余量），内容底边必须 ≥ 170px | 字幕压字，两边都读不清 → `check_layout.mjs` 报 ERROR |
| 7 | 每帧一个视觉焦点：主角 ≥170px 或大字 ≥96px 带 accent 柔光；配角不发光；文字 ≥22px | QC 退回 |
| 8 | GSAP `<script src="../assets/gsap.min.js">`（本地内置），不引 CDN | 离线渲染失败 |
| 9 | **搭建时不读布局**：位置/尺寸不靠 `getBoundingClientRect` / `getBBox` / `offsetHeight` 现量，要量就生成期量好写成常量（见下「渲染期一致性」） | `--workers` 并行渲染下某个进程量歪 → 成片**每 N 帧整块跳一下**，lint/几何/抽帧全绿 |
| 10 | **出场过渡写在本场尾部** `[speech_end, duration + __SEG__.gap]`（见下「场间过渡」） | 渲染器不做转场 → 每次换场都是硬切，同章常驻的角标/页眉还会闪一下 |

## 关于第 4 条的细节（最容易踩）

- **不要用 CSS transition 做入场**。transition 只在属性变化时触发一次，渲染器逐帧 seek 时根本不生效。
- **`@keyframes` 循环装饰（呼吸光、流光）可以用**：渲染器会同步 seek 所有 `document.getAnimations()`（`currentTime = t×1000`），确定性的。
- GSAP 时间轴**注册后不要自己 play**。渲染器下（`window.__MG_RENDER__` 已置位）由渲染器逐帧 seek；浏览器直接打开预览时才自动起播：

```js
if (!window.__MG_RENDER__) {
  requestAnimationFrame(function(){ requestAnimationFrame(function(){ tl.play(0); }); });
}
```

- **★ 数字滚动不要用 `onUpdate` 写 DOM**。GSAP `pause(atTime, suppressEvents)` 第二参默认 `true`，
  渲染器 seek 时**会掐掉本次触发的 `onUpdate`/`onComplete`** → `textContent` 永远停在初值（不报错、成片数字不动）。
  渲染器已显式传 `pause(t, false)` 修好根因，但**帧里仍推荐用纯 transform 驱动的「数字卷轴」**——
  它不依赖任何回调，浏览器预览 / 任何 seek 方式都成立。**绝不**用 `setInterval`/`requestAnimationFrame` 计数
  （墙钟，必然失控）。

  ```html
  <!-- 数字卷轴：外层裁剪，内层按 ITEM 高位移，终值 = 0 时露出目标数字 -->
  <div class="strip"><span class="val">0<br>1<br>…<br>100</span></div>
  ```
  ```js
  // inline 元素不吃 transform → .strip>.val 必须 display:block
  tl.fromTo('.strip .val', { y: 0 }, { y: -(steps * ITEM), duration: 1.4, ease: 'power2.out' }, B('…'));
  ```
  参考实现见 `references/workflow-guide.md` 附的「数字卷轴」最小示例。

- 一个帧只注册一条主时间轴（`window.__tl`）。需要多段编排就放进同一条 timeline（用 label/position 参数）。

## 第 9 条：渲染期一致性 —— 搭建时不读布局

渲染器用 `--workers N` 起 N 个浏览器，第 k 个进程负责帧号 ≡ k (mod N) 的那些帧，**每个进程各自加载一遍帧**。
帧脚本在搭建时间轴时读布局（典型：量内容块高度做纵向居中、量文字宽度摆箭头），读到的是**那个进程那一刻**
的排版结果 —— 脚本同步执行时字体与排版未必就绪，各进程就绪的时刻又不同，于是某个进程量出来差十几像素，
它负责的帧整体错位：成片里**每 N 帧跳一下**。单进程预览、`peek_frame`、`check_layout`、抽帧 QC 都看不出来
（它们一次只开一个页面），只有连着播才露馅。

| 要的效果 | 做法 |
|---|---|
| 内容块整体居中 / 按内容高度排位置 | **生成期量一次**：写个 Node 脚本逐帧打开、`await document.fonts.ready` 后量好，存 JSON，生成帧时写成常量（`stageY: 87`） |
| 连线 / 箭头跟着节点走 | 节点坐标本来就由你写进规格 —— 从规格算，不从 DOM 量 |
| SVG 路径长度（描边动画） | `path.getTotalLength()` / `getPointAtLength()` 只依赖 `d` 属性、与字体无关，**可以用** |

`lint_frames.py` 第 11 条会报这些读法，**连帧引用的本地脚本一起扫**（共享搭建器 `kit.js` 里的测量只扫 HTML 是看不见的）；
`qc_check.py` ⑥ 逐帧比较成片，「前后两帧相同、中间一帧不同」即判单帧跳动（FAIL），并提示跳动周期是否等于进程数。

## 第 10 条：场间过渡 —— 出场写在本场尾部

渲染器**只做拼接**：场景 i 的帧区间是 `[start_i, start_{i+1})`，**段间 gap 算在上一场里**，所以本场帧一直播到
`duration + gap`；这段时间帧里什么都不写，画面就停在本场末态，下一场第 0 帧直接切进来 —— 硬切。
`__SEG__`（`subs.py` 生成）给了排出场所需的一切：

| 字段 | 含义 |
|---|---|
| `speech_end` | 语音真实结束 —— 出场最早从这里开始，不压解说 |
| `duration` / `gap` | 本场帧播到 `duration + gap`（末场 `gap = 0`：收在片尾，不出场） |
| `chapter_start` / `chapter_end` | 本场是一章第一场 / 下一场是新的一章 —— 换章用更重的过渡 |

```js
var SEG = window.__SEG__;
if (SEG.gap > 0) {
  var x1 = SEG.duration + SEG.gap - 0.05;                               // 本场最后一帧之前收完
  var x0 = Math.min(Math.max(SEG.speech_end, SEG.duration - 0.15), x1 - 0.3);
  tl.to('.content', { opacity: 0, y: -18, duration: x1 - x0, ease: 'power2.in' }, x0);
}
```

| 换场类型 | 推荐做法 |
|---|---|
| 同章换场 | 内容淡出 + 轻微上移；**页眉/章节角标不动**，下一场也别让它重新入场（只在 `chapter_start` 时入场） |
| 进章节卡 | 用一个**跨场元素衔接**：上一场尾部把章节卡的主色块滑到它在下一场的位置，下一场第 0 帧它已就位、只长出文字 |
| 换章但没有章节卡 | 更重的动作：扫描线擦除（一份幕底副本从左往右露出来、亮线贴着它的边走）连页眉一起抹掉 |

注意两点：① 对**已有入场补间的元素**写出场时，用 `tl.to(...)`，或 `fromTo` 加 `immediateRender: false` ——
`fromTo` 默认在搭建时就把元素设成 from 值，会覆盖入场的初始态，第 0 帧就露馅；② `check_layout.mjs` 量的是**轴末状态**，
出场后内容都淡没了 —— 几何体检要对**不带出场**的版本跑（生成器留一个开关，例如 `--no-trans`），渲染再用带出场的版本。
`qc_check.py` ⑥ 会在每个场景边界比较前后两帧，整体突变的报「场间硬切」（WARN）。

## 第 5 条：B() 节拍器

`frames/<id>.beats.js` 由 `subs.py` 生成（解说词改动 → 重跑 tts+subs → 时间自动刷新，帧代码零改动）：

```js
tl.fromTo('.card', {...}, {..., duration: 0.7}, B('右边卡片'));     // 该块起播秒
tl.fromTo('.verdict', {...}, {..., duration: 0.7}, B('结论句') + 0.2);
var tEnd = Be('证据');                                              // 该块收尾秒
```

- **★ 匹配规则是「从头开始的前缀匹配」，不是「包含」**。归一化后判的是
  `bt === t || bt.startsWith(t) || t.startsWith(bt)`，三个分支的含义：

  | 分支 | 含义 | 建议 |
  |---|---|---|
  | `bt === t` | 与块文本完全一致 | ✓ |
  | `bt.startsWith(t)` | **text 是块文本的开头**（可以少写尾巴） | ✓ **唯一推荐用法** |
  | `t.startsWith(bt)` | text 以块文本开头（多写了尾巴） | ⚠ 能命中，**别依赖**（块文本一改就废） |

  取**中间**一段会抛错：

  | 块文本 | `B('一位父亲写下')` | `B('被网游毁掉的孩子')` |
  |---|---|---|
  | `一位父亲写下《被网游毁掉的孩子》` | ✓ | ✗（不是从开头） |
  | `《被网游毁掉的孩子》，` | ✗ | ✗（`《` 也不在开头） |

  `《》「」` **不参与归一化**（只有 `，。、！？；：,.!?;:|` 与空白被去掉）。
  **拿不准就抄块文本的前 6–10 个字。**
- 参数是**字幕块的原文片段**（带标点也行，匹配时自动去标点；**数字里的小数点会保留**，
  所以 `B('2.4%左右')` 不会撞上另一块 `24%左右`）；找不到会抛错——宁可构建失败也不要静默错位。
- **★ 抛错发生在「渲染到那一帧」的时刻，不是构建期** —— 前面几百帧白渲，长片要等十几分钟才炸。
  所以**渲染前必须跑构建期校验**（秒级出结果，失配会打印该帧的可用块列表）：
  ```bash
  "$PY" <skill>/scripts/check_beats_refs.py --project .        # 退出码 1 = 有失配
  "$PY" <skill>/scripts/check_beats_refs.py --project . --only hook_q,r1_data
  ```
- 兜底写法 `B('X') || 3.2` **禁止**：beats 缺失时要在构建期暴露，不是悄悄用旧数字。
- `window.__SEG__`：`{id, duration, speech_end, tail}` —— 帧尾呼吸：主体动画压在 `speech_end` 前，`duration - speech_end`（约 0.1–0.3s）留给静止收尾。

## 渲染器注入的东西（帧作者零负担）

| 层 | 说明 |
|----|------|
| `#mg-subs` | 硬字幕层（44px、bottom 96px、单帧硬切）——按 subs.json 块表逐帧切换。**兜底是白字黑边**；肤色调 `--mg-sub-fg` / `--mg-sub-stroke`（亮底片必须在 theme.css 的 `:root` 覆写 —— 渲染器没有 `body.paper` 规则，`class="paper"` 是惰性标记） |
| `#mg-progress` | 全局进度条（bottom 0–12px、accent 填充、章节刻度）——按全局时间填充。肤色调 `--mg-track` / `--mg-tick` |

两者 z-index 900/901，`pointer-events:none`。**内容区别压进字幕带（80–170px），进度条带（0–12px）也别放东西。**

## 几何：两类不会报错的错，和它们的确定性判据

`lint_frames.py` 是**静态文本**检查（禁色值字面量、禁 transition、中文字体族…），**它看不见几何**。
画面里最常见的两类「静默出错」恰恰是几何问题，且两者都 lint 全绿、渲完几千帧才被人眼发现：

| 症状 | 典型现场 | 根因 |
|---|---|---|
| **遮挡** | 图表轴标签被底部证据条压住、两段文字打架 | 容器高度只算了主体、没算附注；或两个绝对定位件各自以为占了位置 |
| **越界** | 内容溢出画布、压进字幕带 | 元素底边越过安全线、或绝对定位坐标算错 |

### 判据一：`check_layout.mjs`（终态几何体检，渲染前跑）

```bash
node <skill>/scripts/check_layout.mjs .            # ERROR 非零则退出码 1
node <skill>/scripts/check_layout.mjs . --only 20_shape,22_holiday
```

它把「谁和谁重叠、谁越界」变成可枚举清单（`out/layout_report.md`）：

- **ERROR**：侵入字幕禁区 / 出画 / 文字被遮挡 / 文字重叠 —— 一定是 bug
- **WARN**：越安全边 / 文字压色块 / 色块重叠 —— 多半是 bug，看一眼确认
- **INFO**：疑似未对齐（一个该跟 SVG 图形共心的部件却差了 N 像素）—— 供人判断

三条量法上的纪律（都是踩过的坑，见 `lessons.md` #77/#78）：

1. **量字墨，不量元素框。** 左对齐的 96px 大标题，元素宽 1800、字形只占左边 600 ——
   拿元素框算重叠会把「印章贴在标题右侧空白处」误报成「印章压住了标题」。用 `Range` 的
   `getClientRects()`，它逐行贴合字形。
2. **纵向再收成 em 盒。** `Range` 给的是行盒（= 字体 em 盒 + 上下留白）。大字号紧排时
   （180px / line-height 1.18），上一行的行盒底会与下一行行盒顶擦边十几像素 ——
   肉眼毫无重叠。字体在行盒里垂直居中，所以取 `中心 ± em/2`。
3. **带底色的元素用元素框**（它靠底色遮挡），纯文本才用字墨。

### 判据二：一个几何体只准有一个坐标系

「蓝点没落在射线汇聚点上」属于这一类：**射线画在 SVG 的 viewBox 坐标系里，
圆点是 CSS 绝对定位、在它自己的包含块坐标系里** —— 只要这两个矩形不是同一个，圆心就会偏，
而且偏得毫无规律（取决于 padding / 兄弟元素 / viewBox 比例）。

规则：**一个几何体的所有部件，要么全在一个 `<svg>` 里**（圆点写成 `<circle>`，与箭头共用同一 `<g>`
或同一组 transform），**要么全 CSS**（用一个 `.origin` 定位块包住圆点/圆环/射线，共享
`left:50%;top:50%;transform:translate(-50%,-50%)`）。**禁止一个部件在 SVG、另一个在 HTML。**

自查：`node <skill>/scripts/peek_frame.mjs . <帧id> --at 100 --guides` ——
叠一层十字中线 + 字幕禁区线，圆点在不在正中一眼可见（没有基准线时肉眼判不了）。
若图形真的以 SVG 内容中心为锚，`check_layout.mjs` 会以 INFO 报出偏差像素数。

## 版式基因（来自 html-video 模板一系，可自由组合）

- **幕底**：`--bg-radial`（主题径向光）+ 96px 网格（`--line-soft`）；整片常驻不被盖
- **卡片**：24px 圆角、`--card-bg` 底、`--line` 描边；重点卡用 `--accent-soft` 底 + `--accent-border` 描边 + inset 柔光
- **大字**：74–130px / 900 字重；关键词用 `<em>` + `--accent` + 发光
- **章节角标**：`--accent` 小字 + 短横条（配角，不发光）
- **证据条**：`bottom:176px`、左侧 5px accent 竖条 + tag 胶囊（事实必须带调研出处）
- **动效词汇**：入场 `power3.out` 位移 0.6–0.7s / 强调 `back.out(1.7~2)` 缩放 / 结论光晕 `sine.out` 0.8s / 划线强调 `power3.inOut`
- 数字大特写（高光时刻）：140–180px 数字 + 计数动画（**必须用上面的 transform 数字卷轴，不用 onUpdate**）+ 全屏 `--accent` 柔光

## 反例（QC 会退回）

- 同屏 3+ 个都发光的元素（光只跟主角）
- 背景撒碎屑/小图标墙（背景只有幕底+网格）
- 字幕带里放 footnote/evidence（`bottom < 170px` 的元素一律抬走）
- 文字 < 22px（投影后不可读）
- 入场轨迹穿过字幕带（路径先抬后落）
- 换场硬切、同章换场时页眉/角标重新淡入（第 10 条）
- 帧里现量布局来摆位置（第 9 条）
