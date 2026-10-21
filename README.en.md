# html-to-explainer-video

English · [简体中文](README.md)

An Agent Skill that turns a topic into a **narrated explainer video** (MP4) with word-level subtitles, motion
graphics and cover images.

You give it a topic and a target length; the agent follows a fixed pipeline: research → script → voice-over →
subtitles and beats → HTML/GSAP scenes → frame-by-frame render → QC → covers. Every on-screen entrance is anchored to
the moment the narrator says the matching words, so editing a line and re-running the voice-over re-times the
picture automatically.

---

## Features

| | |
|---|---|
| **Voice-over** | Defaults to **Alibaba Cloud Model Studio `qwen-audio-3.1-tts-flash`** (character-level timestamps, 18 tested voices). Switch to edge-tts (no key needed) or Volcengine TTS 2.0. All three produce the same manifest, so nothing downstream changes |
| **Subtitles** | Chunked on word/character timestamps, burned in, and exported as SRT / VTT. Spoken expansions ("2026" read as words) are mapped back to the original text, so subtitles do not drift |
| **Visuals** | HTML + CSS + GSAP at 1920×1080 or portrait; 23 style templates in 8 categories plus a 40+ motion vocabulary; `style_director.py` reads the script and picks a style and motion intensity per scene |
| **Sync** | Scenes call `B('subtitle chunk')` to get that chunk's start second; scene length is the MP3 container duration, the narration track pads every segment to that same length sample-exactly (no drift in long videos), and the last subtitle ends when speech really ends |
| **Rendering** | Deterministic per-frame seek and screenshot (not screen recording); 1080p / 2K / 4K at 30 / 60 fps; optional shutter motion blur; parallel browser workers; resumable |
| **QC** | Before rendering: beat-reference check, contract lint, geometry check (overlap / out of frame / subtitle band). After: streams, duration, loudness, contact sheet and **frame continuity** (single-frame jumps, hard cuts between scenes) |
| **Covers** | 16:9 + 3:4 (plus 9:16 for portrait feeds), each laid out separately and measured after rendering |
| **Approval gates** | Palette, style, format, voice, length, covers and render channel are decided by the user; `gate_check.py` blocks the build and the render until they are |

---

## How it works

```
narration.json ──tts_build──▶ audio/*.mp3 + manifest (word/char timestamps)
                  │
                  ├─timeline_build─▶ layout.json (global timeline) + narration-full.mp3
                  │
                  └─subs──────────▶ subs.json + frames/<id>.beats.js (B() beats) + SRT/VTT
                                              │
frames/<id>.html (GSAP timeline using B()) ◀─┘
        │
        └─render_video──▶ tl.pause(t) per frame → screenshot → ffmpeg → out/<slug>.mp4
                              │
                              └─qc_check / cover_build / check_cover
```

- **One sentence, one scene.** Each scene is a self-contained HTML file that registers one paused GSAP timeline as
  `window.__tl`.
- **The renderer seeks every frame**: it parks the timeline at second *t*, syncs CSS animations, takes a screenshot
  and hands the frames to ffmpeg. No wall clock is involved, so the same input renders the same picture every time.
- **The silence between scenes belongs to the earlier scene**: a scene plays until `duration + gap`, and its exit
  transition goes in that window (`__SEG__.gap`).
- The subtitle layer and progress bar are injected and driven by the renderer.

---

## Install

**Requirements**: Python ≥ 3.9, Node ≥ 18, Chrome or Edge (playwright chromium is downloaded if neither exists),
ffmpeg (falls back to the static binary bundled with imageio-ffmpeg).

```bash
git clone https://github.com/amliuyong/html-to-explainer-video.git ~/.claude/skills/html-to-explainer-video
bash ~/.claude/skills/html-to-explainer-video/setup_env.sh --install   # self-check and install what is missing
```

The repository root is the skill directory. For other agents, replace `~/.claude/skills/`:

| Agent | Personal | Project |
|---|---|---|
| Claude Code | `~/.claude/skills/` | `.claude/skills/` |
| OpenAI Codex | `~/.codex/skills/` | `.codex/skills/` or `.agents/skills/` |
| Gemini CLI | `~/.gemini/skills/` | `.gemini/skills/` or `.agents/skills/` |
| Cursor | `~/.cursor/skills/` | `.cursor/skills/` |
| GitHub Copilot / VS Code | `~/.copilot/skills/` | `.github/skills/` |
| Generic | `~/.agents/skills/` | `.agents/skills/` |

Start a new session afterwards so the agent rescans its skills.

---

## Voice-over setup

### Alibaba Cloud Model Studio (default)

1. Create an API key in the Model Studio console and note your workspace's dedicated endpoint.
2. Save them as a two-column CSV at `~/ali_tts/api_key.csv` (or point `ALI_TTS_KEY_FILE` elsewhere) and make it
   read-only:

   ```csv
   apiKey,<your API key>
   dashScope,<base URL of your workspace endpoint>
   ```

3. Check the connection and pick a voice:

   ```bash
   python scripts/tts_ali.py --check                 # synthesizes one line; secrets are masked
   python scripts/tts_ali.py --voices                # built-in voices
   python scripts/tts_setup.py --project . --provider ali --voice xuyuyuan_v3.1   # writes project.json
   ```

The default voice is `xuyuyuan_v3.1` (female, Mandarin); English projects default to `Andy_v3.1`. The 3.1 model only
accepts `*_v3.1` voices. Speaking rate uses the `+N%` form (new projects default to `+8%`).

> The agent only calls `tts_ali.py`; it **never reads or prints the key file**. If the file is missing,
> `tts_setup.py` prints `NEXT_ACTION=fill_ali_key` and stops so you can add it.

### Other engines

| Engine | Switch | Notes |
|---|---|---|
| edge-tts | `--provider edge` | Free, no key |
| Volcengine TTS 2.0 | `--provider volcano` | `cp tts.env.example tts.env`, fill in the key (`tts.env` is gitignored), then `tts_setup.py --check` |

`TTS_PROVIDER=ali|edge|volcano` also works.

---

## Usage

### Ask the agent

For example:

> Use html-to-explainer-video to make a 5-minute explainer on how vector databases search, for backend engineers.

The agent stops at seven decision points (palette, style, format, voice engine and voice, length and aspect ratio,
covers, render channel) and continues only after you decide.

### Run the pipeline by hand

Inside the video project, in this order:

```bash
SK=~/.claude/skills/html-to-explainer-video
python $SK/scripts/new_project.py my-video my-video --topic "vector search"
cd my-video
python $SK/scripts/tts_setup.py      --project . --provider ali
python $SK/scripts/tts_build.py      --project .
python $SK/scripts/timeline_build.py --project .
python $SK/scripts/subs.py           --project .
python $SK/scripts/check_beats_refs.py --project .
python $SK/scripts/lint_frames.py    --project .
node   $SK/scripts/check_layout.mjs  .
python $SK/scripts/gate_check.py     --project . --phase render
node   $SK/scripts/render_video.mjs  . --png-fast --audio audio/narration-full.mp3
python $SK/scripts/qc_check.py       --project .
node   $SK/scripts/cover_build.mjs   . && node $SK/scripts/check_cover.mjs .
```

Project layout:

```
my-video/
├── project.json        # canvas, fps, voice, scene order, chapters
├── narration.json      # one entry per scene; | splits subtitle chunks
├── theme.css           # every colour variable (scene code never hard-codes colours)
├── frames/<id>.html    # one file per scene
├── audio/  layout.json  subs.json
└── out/                # video, covers, SRT/VTT, QC report
```

---

## Render options

```bash
node scripts/render_video.mjs . --list-profiles
```

| Profile | Resolution | fps | Shutter | Use |
|---|---|---|---|---|
| `draft` | 1080p | 30 | off | Drafts and pacing |
| `balanced` | 1080p | 30 | 180° | Recommended default |
| `final` | 4K | 60 | 180° | Final delivery |
| `master` | 4K | 60 | 180° | Maximum quality (lossless PNG, slowest encode) |

| Capture channel | Flag | When |
|---|---|---|
| PNG-fast (recommended) | `--png-fast` | CSS / motion-graphics frames: as fast as JPEG q95, smaller, lossless |
| JPEG q95 | `--jpeg --jpeg-quality 95` | Full-frame photos or 4K finals |
| JPEG q82 | `--jpeg --jpeg-quality 82` | Drafts only |

Useful flags: `--preview 30`, `--only <scene>`, `--workers N`, `--resume`, `--shutter 0`, `--shutter-only <id,id>`.

---

## Quality checks

| Stage | Command | Catches |
|---|---|---|
| Before render | `check_beats_refs.py` | `B()` calls that match no subtitle chunk |
| | `lint_frames.py` | Remote fonts / CDNs, colour literals, wall-clock logic, CSS transitions, content in the subtitle band, missing CJK fonts, **layout reads while building** (also scans local scripts the frame loads) |
| | `check_layout.mjs` | Overlap, out of frame, subtitle-band intrusion, near-miss alignment |
| After render | `qc_check.py` | Streams and duration, loudness, contact sheet, **single-frame jumps** (FAIL), **hard cuts between scenes** (WARN) |
| Covers | `check_cover.mjs` | Margins, hook size, line width, orphans, overlapping text |

To find a problem reported at a timestamp: `python scripts/frame_at.py --project . --at 1:23` maps it to the scene,
frame and source file; `node scripts/peek_frame.mjs . <scene> --at-sec 3.5` renders that single frame in seconds.

---

## Scene rules

Full text in [`references/frame-contract.md`](references/frame-contract.md); template in
[`assets/frame-template.html`](assets/frame-template.html).

1. 1920×1080 canvas, system fonts only, no remote fonts or CDNs.
2. Colours come from `theme.css` variables only.
3. Animate with GSAP only: one paused timeline registered as `window.__tl`; no CSS transitions for entrances.
4. Time with `B('chunk')`, never hard-coded seconds.
5. The bottom 80–170px belong to subtitles and 0–12px to the progress bar.
6. One visual focus per frame.
7. **Do not read layout while building**: measure at generation time and bake constants, or parallel renders jump
   every N frames.
8. **Write the exit transition at the end of each scene** in `[speech_end, duration + gap]`: the renderer does not
   transition, so without it every scene change is a hard cut.

---

## Reference docs

| Doc | Contents |
|---|---|
| [`SKILL.md`](SKILL.md) | Full workflow and decision points the agent follows |
| [`references/frame-contract.md`](references/frame-contract.md) | Scene contract, beats, geometry, transitions |
| [`references/style-catalog.md`](references/style-catalog.md) | Design specs for the 23 style templates |
| [`references/motion-library.md`](references/motion-library.md) | Motion vocabulary (`assets/motion.js`) |
| [`references/render-profiles.md`](references/render-profiles.md) | Speed and quality trade-offs of profiles, channels and shutter |
| [`references/ali-tts.md`](references/ali-tts.md) · [`references/volcano-tts.md`](references/volcano-tts.md) | Voice engine details and troubleshooting |
| [`references/cover-guide.md`](references/cover-guide.md) | Cover layout rules |
| [`references/lessons.md`](references/lessons.md) | Silent failures: symptom, root cause and the gate that now catches each |

---

## Development

```bash
python scripts/check_integrity.py          # version, style catalog, template links, secret guard, frontmatter
python tests/test_subs_text.py
python tests/test_tts_ali.py               # offline, no key needed
python tests/test_lint_frames.py
python tests/test_qc_continuity.py         # end-to-end part skips without numpy / ffmpeg
node   tests/test_beats_norm.mjs
node   tests/test_page_probe_escapes.mjs   # page-evaluated source strings keep their regex escapes
node   tests/test_seek_history.mjs         # renders do not depend on seek history (skips without a browser)
```

CI (`.github/workflows/ci.yml`) runs these on Ubuntu and Windows with Python 3.9 and 3.13.

---

## License

[MIT](LICENSE). Bundled third-party components (GSAP, playwright-core) and the transcribed style catalog keep their
own licenses; see [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) and include it when you redistribute.
