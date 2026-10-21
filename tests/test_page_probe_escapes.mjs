/* 「交给浏览器执行的 JS 源码字符串」的转义回归测试（零依赖）。
 *
 * check_cover.mjs / render_video.mjs 等把一段 JS 写在模板字符串里，再 page.evaluate(字符串)。
 * 在不带标签的模板字符串里，\d \s \- \. 这类不是合法转义，求值后只剩字符本身：
 *   源码写 /^[\d.,%$¥+\-–—:：]+$/ → 浏览器拿到 /^[d.,%$¥+-–—:：]+$/
 * `+-–` 成了 U+002B..U+2013 的字符区间，所有拉丁字母都落在里面 —— 英文钩子被当成「纯数字」跳过，
 * check_cover 转而把 23px 的小卡片认成钩子（lessons #137，issue #3）。不报错，只是量错了元素。
 *
 * 守两件事：
 *   A. check_cover 的 PAGE_PROBE 按模板字符串求值后，pureNumber / norm 的行为符合本意；
 *   B. scripts/*.mjs 里所有交给 page.evaluate / addInitScript 的模板字符串，
 *      反斜杠只允许 \\、\u、\x、\`、\$ —— 其余一律是会被吃掉的转义。
 *
 * 失败即 process.exit(1)。
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const SCRIPTS = path.join(ROOT, 'scripts');
let failed = 0;
const fail = (msg) => { failed++; console.error('✗ ' + msg); };
const ok = (msg) => console.log('✓ ' + msg);

// 从 src[start]（开头的反引号之后）读到配对的反引号，返回模板字符串的原文（未求值）。
function readTemplate(src, start) {
  let i = start, depth = 0;
  for (; i < src.length; i++) {
    const c = src[i];
    if (c === '\\') { i++; continue; }
    if (depth === 0 && c === '`') return src.slice(start, i);
    if (depth === 0 && c === '$' && src[i + 1] === '{') { depth = 1; i++; continue; }
    if (depth > 0 && c === '{') depth++;
    if (depth > 0 && c === '}') depth--;
  }
  throw new Error('模板字符串没有闭合');
}

// 找出一个文件里「交给浏览器执行」的模板字符串：
// evaluate(`…`) / addInitScript(`…`) 直接写的，以及 const NAME = `…` 且后文有 evaluate(NAME / addInitScript(NAME 的。
function pageTemplates(src) {
  const out = [];
  for (const m of src.matchAll(/(?:evaluate|addInitScript)\(\s*`/g)) {
    const start = m.index + m[0].length;
    out.push({ name: 'inline', start, body: readTemplate(src, start) });
  }
  for (const m of src.matchAll(/const ([A-Za-z_$][\w$]*) = `/g)) {
    const name = m[1];
    if (!new RegExp(`(?:evaluate|addInitScript)\\(\\s*${name}\\b`).test(src)) continue;
    const start = m.index + m[0].length;
    out.push({ name, start, body: readTemplate(src, start) });
  }
  return out;
}

// ── B. 同类守卫：页面源码字符串里不许有会被吃掉的转义 ──
let scanned = 0;
for (const file of fs.readdirSync(SCRIPTS).filter((f) => f.endsWith('.mjs')).sort()) {
  const src = fs.readFileSync(path.join(SCRIPTS, file), 'utf8');
  for (const t of pageTemplates(src)) {
    scanned++;
    for (let i = 0; i < t.body.length; i++) {
      if (t.body[i] !== '\\') continue;
      const next = t.body[i + 1];
      if (next === '\\') { i++; continue; }
      if ('ux`$'.includes(next)) { i++; continue; }
      const line = src.slice(0, t.start + i).split('\n').length;
      fail(`${file}:${line} ${t.name}：模板字符串里的「\\${next}」求值后会变成「${next}」—— 页面拿到的正则/字符串已经变了，写成「\\\\${next}」`);
    }
  }
}
if (scanned < 3) fail(`只扫到 ${scanned} 段页面源码字符串，提取规则可能失效了`);
else ok(`扫描 ${scanned} 段交给浏览器执行的模板字符串`);

// ── A. PAGE_PROBE 求值后的行为 ──
const coverSrc = fs.readFileSync(path.join(SCRIPTS, 'check_cover.mjs'), 'utf8');
const probeDef = pageTemplates(coverSrc).find((t) => t.name === 'PAGE_PROBE');
if (!probeDef) {
  fail('没能从 scripts/check_cover.mjs 里取到 PAGE_PROBE');
} else {
  // 按模板字符串求值一次 —— 和 page.evaluate 拿到的源码完全一样
  const probe = new Function('return `' + probeDef.body + '`;')();
  const pm = probe.match(/const pureNumber = (\/.+\/)\.test\(t\);/);
  const nm = probe.match(/const norm = (\(s\) => .+?);\r?\n/);
  if (!pm || !nm) {
    fail('PAGE_PROBE 里找不到 pureNumber / norm 的定义（改名了就同步改这个测试）');
  } else {
    const pureNumber = (0, eval)(pm[1]);
    const norm = (0, eval)(nm[1]);
    const cases = [
      // [文本, 期望是否「纯数字串」]
      ['6.02', true], ['4', true], ['1,234', true], ['-12%', true], ['+3.5', true], ['¥3.5', true], ['12:30', true],
      [norm('Events in. Answers out.'), false],   // 英文钩子（issue #3 的现场）
      [norm('Write through one API'), false],
      ['v1.0.0', false],
      ['{"order_id":1041}', false],
      ['家长不骂游戏了', false],
      ['2018→2026几乎消失', false],
    ];
    let bad = 0;
    for (const [t, want] of cases) {
      const got = pureNumber.test(t);
      if (got !== want) { bad++; fail(`pureNumber(${JSON.stringify(t)}) = ${got}，期望 ${want}（页面里的正则：${pm[1]}）`); }
    }
    if (!bad) ok(`pureNumber 判定 ${cases.length} 例全部符合本意`);
    if (norm('a b\tc\n') !== 'abc') fail(`norm('a b\\tc\\n') = ${JSON.stringify(norm('a b\tc\n'))}，期望 "abc"`);
    else ok('norm 去掉全部空白');
  }
}

if (failed) {
  console.error(`\n${failed} 项失败`);
  process.exit(1);
}
console.log('\n全部通过');
