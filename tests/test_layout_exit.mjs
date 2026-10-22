/* check_layout 量「出场之前」的回归测试（lessons #147）。
 *
 * 契约 10 要求每场把出场写在 [speech_end, duration + gap] 里：内容淡出、滑走。check_layout 原来 seek 到时间轴末尾再量，
 * 那时候东西都已经淡没了 —— 量到 0 个元素，ERROR / WARN 全是 0，看起来「全绿」，其实什么都没量。
 * 修法：帧导出了 __SEG__ 且 gap > 0（有出场）时，量 speech_end 那一刻；主体动画压在 speech_end 之前（契约 8）。
 *
 * 做法：临时建一个单场景工程，帧里一个正常的标题、一个压进字幕带的条（bottom 100px，应报「侵入字幕禁区」），
 * speech_end 之后全部淡出。断言：量到的元素不是 0，侵入字幕带被报出来；末场（gap = 0）与去掉 __SEG__ 的帧照旧量轴末，
 * 后者量到 0 个元素时报「没量到元素」（WARN），不再是静默的全绿。
 *
 * 找不到浏览器时打印 SKIP 并以 0 退出；设了 HX_NO_SKIP=1（CI）时缺浏览器算失败。
 */
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const NO_SKIP = process.env.HX_NO_SKIP === '1';

const frame = (seg) => `<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<style>
  html, body { margin: 0; width: 1920px; height: 1080px; overflow: hidden; background: #000; color: #fff; font-family: sans-serif; }
  .title { position: absolute; left: 200px; top: 300px; font-size: 96px; font-weight: 800; }
  .intrude { position: absolute; left: 200px; bottom: 100px; width: 900px; height: 60px; font-size: 40px; background: #333; }
</style></head><body>
<div class="title">Queryable in minutes</div>
<div class="intrude">this bar sits in the subtitle band</div>
<script src="../assets/gsap.min.js"></script>
<script>
  ${seg ? `window.__SEG__ = ${JSON.stringify(seg)};` : ''}
  var tl = gsap.timeline({ paused: true });
  tl.fromTo(['.title', '.intrude'], { opacity: 0 }, { opacity: 1, duration: 0.4 }, 0.1);
  // 出场：speech_end 之后淡出（契约 10）
  tl.to(['.title', '.intrude'], { opacity: 0, duration: 0.45 }, 1.05);
  window.__tl = tl;
</script></body></html>
`;

const SKIP = Symbol('skip');

function run(seg) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'hx-layout-exit-'));
  try {
    fs.mkdirSync(path.join(dir, 'frames'), { recursive: true });
    fs.mkdirSync(path.join(dir, 'assets'), { recursive: true });
    fs.copyFileSync(path.join(ROOT, 'assets', 'gsap.min.js'), path.join(dir, 'assets', 'gsap.min.js'));
    fs.writeFileSync(path.join(dir, 'frames', 'exit.html'), frame(seg));
    fs.writeFileSync(path.join(dir, 'project.json'), JSON.stringify({
      slug: 'exit', width: 1920, height: 1080, fps: 30, order: ['exit'], gap: 0.35 }));
    const r = spawnSync(process.execPath, [path.join(ROOT, 'scripts', 'check_layout.mjs'), dir, '--json'], { encoding: 'utf8' });
    const out = (r.stdout || '') + (r.stderr || '');
    const jp = path.join(dir, 'out', 'layout.json');
    if (!fs.existsSync(jp)) {
      if (!NO_SKIP && /浏览器|browser|chrom|playwright/i.test(out)) {
        console.log('SKIP: check_layout 起不来（缺浏览器或 playwright-core）\n' + out.split('\n').slice(-4).join('\n'));
        return SKIP;      // 在 finally 删掉临时目录之后再退出
      }
      console.error(out); throw new Error('check_layout 没有写出 layout.json');
    }
    return JSON.parse(fs.readFileSync(jp, 'utf8'))[0];
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

const fails = [];
const check = (label, cond, detail) => { if (cond) console.log(`  OK  ${label}`); else fails.push(`${label}\n    ${detail}`); };

const once = (seg) => {
  const r = run(seg);
  if (r === SKIP) process.exit(0);
  return r;
};

console.log('有出场（__SEG__.gap > 0）：量 speech_end 那一刻');
const a = once({ id: 'exit', duration: 1.2, speech_end: 1.0, gap: 0.35 });
check('量到的元素不是 0', (a.items || []).length > 0, `元素 ${(a.items || []).length}`);
check('侵入字幕带被报出来', (a.findings || []).some((f) => f.level === 'ERROR' && f.kind === '侵入字幕禁区'),
  JSON.stringify(a.findings));
check('量的时刻是 speech_end（1.0 s）', Math.abs((a.measuredAt || 0) - 1.0) < 1e-6, `量在 ${a.measuredAt}`);
check('时间轴总长照旧记在 duration 里', Math.abs((a.duration || 0) - 1.5) < 1e-6, `duration ${a.duration}`);

console.log('末场（gap = 0）：照旧量轴末');
const c = once({ id: 'exit', duration: 1.2, speech_end: 1.0, gap: 0 });
check('量的时刻是轴末（1.5 s）', Math.abs((c.measuredAt || 0) - 1.5) < 1e-6, `量在 ${c.measuredAt}`);

console.log('没有 __SEG__：照旧量轴末，量到 0 个元素时报出来');
const b = once(null);
check('量的时刻是轴末（1.5 s）', Math.abs((b.measuredAt || 0) - 1.5) < 1e-6, `量在 ${b.measuredAt}`);
check('量到 0 个元素 → WARN「没量到元素」', (b.findings || []).some((f) => f.level === 'WARN' && f.kind === '没量到元素'),
  JSON.stringify(b.findings));

if (fails.length) {
  console.error('\n✗ 失败：');
  for (const f of fails) console.error('  - ' + f);
  process.exit(1);
}
console.log('\n✓ check_layout 出场回归全部通过');
