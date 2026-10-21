/* 渲染与 seek 历史无关的回归测试（lessons #138，issue #5）。
 *
 * GSAP 的补间在**第一次被渲染时**才记录起点（相对值 '-=26'、to() 的起点、函数值）。
 * render_video 的 --workers N 让每个浏览器只 seek 分给自己的帧 —— 若渲染器不补齐中间帧，
 * 同一个补间在不同页面里于不同帧、看到不同的中间状态时定起点，交错写盘的帧就会对不齐
 * （成片表现为元素来回跳，qc ⑥ 抓不到）。
 *
 * 做法：临时建一个 480×270 的单场景工程，帧里放「错峰入场 fromTo(y) + 同属性相对漂移 '-=26'」
 * 这一组会暴露问题的补间；分别用 --workers 1（顺序）与 --workers 3 渲染，逐帧比较 PNG 字节。
 * 有 numpy 时再用 --shutter 180 比较一次积分后的成帧。
 * peek_frame 也要与成片一致：有 Python + PIL 时，抽一帧与顺序渲染的同一帧逐像素比较。
 *
 * 找不到浏览器 / ffmpeg 时打印 SKIP 并以 0 退出；不一致即 process.exit(1)。
 */
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const FPS = 30, DUR = 2.0;

const FRAME = `<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
  html, body { margin: 0; width: 480px; height: 270px; overflow: hidden; background: #000; font-family: sans-serif; }
  .ev { position: absolute; left: 30px; width: 200px; height: 24px; background: #3cc; }
</style></head><body>
${[0, 1, 2, 3, 4, 5].map((i) => `<div class="ev" style="top:${30 + i * 34}px"></div>`).join('\n')}
<script src="../assets/gsap.min.js"></script>
<script>
  var tl = gsap.timeline({ paused: true });
  // 错峰入场：最后一张 1.05 s 开始、1.55 s 结束
  tl.fromTo('.ev', { y: -36, opacity: 0 }, { y: 0, opacity: 1, duration: 0.5, stagger: 0.16 }, 0.25);
  // 同一属性上的相对漂移，0.8 s 开始 —— 与入场重叠，起点在首次渲染时才记录
  tl.to('.ev', { y: '-=26', duration: 1.1, ease: 'none' }, 0.8);
  tl.to({}, { duration: 0.01 }, ${DUR});
  window.__tl = tl;
</script></body></html>
`;

function makeProject(dir) {
  fs.mkdirSync(path.join(dir, 'frames'), { recursive: true });
  fs.mkdirSync(path.join(dir, 'assets'), { recursive: true });
  fs.copyFileSync(path.join(ROOT, 'assets', 'gsap.min.js'), path.join(dir, 'assets', 'gsap.min.js'));
  fs.writeFileSync(path.join(dir, 'frames', 'drift.html'), FRAME);
  const frames = Math.round(DUR * FPS);
  fs.writeFileSync(path.join(dir, 'project.json'), JSON.stringify({
    slug: 'seek', width: 480, height: 270, fps: FPS, order: ['drift'], gap: 0, progress: false }));
  fs.writeFileSync(path.join(dir, 'layout.json'), JSON.stringify({
    drift: { start_sec: 0, duration_sec: DUR },
    _total: { audio_duration_sec: DUR, video_duration_sec: DUR, gap_sec: 0, fps: FPS, total_frames: frames } }));
  fs.writeFileSync(path.join(dir, 'subs.json'), JSON.stringify({ fps: FPS, segments: [{ id: 'drift', blocks: [] }] }));
}

function render(dir, extra) {
  const r = spawnSync(process.execPath, [path.join(ROOT, 'scripts', 'render_video.mjs'), dir,
    '--png-fast', '--keep-frames', ...extra], { encoding: 'utf8' });
  const out = (r.stdout || '') + (r.stderr || '');
  if (r.status !== 0) {
    if (/浏览器|browser|chrom|ffmpeg/i.test(out) && !/✓ 成片/.test(out)) {
      console.log('SKIP: 渲染器起不来（缺浏览器或 ffmpeg）\n' + out.split('\n').slice(-4).join('\n'));
      process.exit(0);
    }
    console.error(out); throw new Error('render failed: ' + extra.join(' '));
  }
  const framesDir = path.join(dir, 'render', 'frames');
  return fs.readdirSync(framesDir).filter((f) => f.endsWith('.png')).sort()
    .map((f) => [f, fs.readFileSync(path.join(framesDir, f))]);
}

function compare(label, a, b) {
  if (a.length !== b.length) return [`${label}: 帧数不同 ${a.length} vs ${b.length}`];
  const diff = a.filter(([f, buf], k) => b[k][0] !== f || !buf.equals(b[k][1])).map(([f]) => f);
  return diff.length ? [`${label}: ${diff.length}/${a.length} 帧与顺序渲染不同（首个 ${diff[0]}）`] : [];
}

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'hx-seek-'));
const failures = [];
try {
  const runs = {};
  for (const [name, extra] of [['seq', ['--workers', '1', '--shutter', '0']], ['par', ['--workers', '3', '--shutter', '0']]]) {
    const dir = path.join(tmp, name); makeProject(dir); runs[name] = render(dir, extra);
  }
  failures.push(...compare('--workers 3', runs.seq, runs.par));
  if (!failures.length) console.log(`✓ --workers 3 与 --workers 1 的 ${runs.seq.length} 帧逐字节相同`);

  // peek_frame 与成片同一帧：1.2 s = 第 36 帧（f_000037.png）
  const py = process.platform === 'win32' ? 'python' : 'python3';
  if (spawnSync(py, ['-c', 'import PIL, numpy'], { encoding: 'utf8' }).status === 0) {
    const dir = path.join(tmp, 'seq');
    const pk = spawnSync(process.execPath, [path.join(ROOT, 'scripts', 'peek_frame.mjs'), dir, 'drift', '--at-sec', '1.2'], { encoding: 'utf8' });
    if (pk.status !== 0) throw new Error('peek_frame failed: ' + pk.stdout + pk.stderr);
    const peek = fs.readdirSync(path.join(dir, 'render', 'peek')).find((f) => f.endsWith('.png'));
    const cmp = spawnSync(py, ['-c', [
      'import sys, numpy as np; from PIL import Image',
      'a = np.asarray(Image.open(sys.argv[1]).convert("RGB"), dtype=int)',
      'b = np.asarray(Image.open(sys.argv[2]).convert("RGB"), dtype=int)',
      'print(int(np.abs(a - b).max()))'].join('\n'),
      path.join(dir, 'render', 'peek', peek), path.join(dir, 'render', 'frames', 'f_000037.png')], { encoding: 'utf8' });
    const max = parseInt(cmp.stdout, 10);
    if (!(max <= 2)) failures.push(`peek_frame --at-sec 1.2 与成片第 36 帧不同（最大差 ${cmp.stdout.trim() || cmp.stderr.trim()}）`);
    else console.log(`✓ peek_frame --at-sec 1.2 与成片第 36 帧一致（最大差 ${max}）`);
  } else {
    console.log('SKIP: 没有 PIL / numpy，跳过 peek_frame 对照');
  }

  const numpy = spawnSync(process.platform === 'win32' ? 'python' : 'python3', ['-c', 'import numpy'], { encoding: 'utf8' });
  if (numpy.status === 0) {
    const shut = {};
    for (const [name, w] of [['seqs', '1'], ['pars', '3']]) {
      const dir = path.join(tmp, name); makeProject(dir); shut[name] = render(dir, ['--workers', w, '--shutter', '180']);
    }
    const f = compare('--workers 3 --shutter 180', shut.seqs, shut.pars);
    failures.push(...f);
    if (!f.length) console.log(`✓ 快门模式下 --workers 3 与 --workers 1 的 ${shut.seqs.length} 帧逐字节相同`);
  } else {
    console.log('SKIP: 没有 numpy，跳过快门模式');
  }
} finally {
  fs.rmSync(tmp, { recursive: true, force: true });
}

if (failures.length) { failures.forEach((m) => console.error('✗ ' + m)); process.exit(1); }
console.log('全部通过');
