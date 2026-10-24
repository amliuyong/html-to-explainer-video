/* B() 节拍匹配的回归测试（零依赖）。
 *
 * 守的是同一条不变式的一半：词里的点不是标点（左右都是数字或 ASCII 字母：2.4% / 1.2.3 / X.509，lessons #88、#143）。
 * 屏幕文本那边的规则由 tests/test_subs_text.py 守；这里守「用文本查节拍」这条路 ——
 * 帧作者写 B('营收增长了2.4%')，归一化时不能把小数点削掉：
 *   削掉之后 '2.4%' 变成 '24%'，会去撞另一个真的念 24% 的块（静默配错节拍），
 *   或者反过来匹配不上而 throw（画面直接崩）。'X.509' 撞 'X509'、'2.0.1' 撞 '2.01' 是同一个形状。
 *
 * 做法：从 scripts/subs.py 里取出 BEATS_TMPL 模板本体（脚本真源，不复制一份），
 * 注入假数据后在 node 里跑。模板是 Python 三引号字符串，文件里写的是 \\s / \\u0000，
 * 所以取出来要先还原一层转义 —— 还原错的话 canary 断言（逗号必须被削掉）会立刻炸。
 *
 * 失败即 process.exit(1)。
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)));

const py = fs.readFileSync(path.join(ROOT, 'scripts', 'subs.py'), 'utf8');
const m = py.match(/BEATS_TMPL = """([\s\S]*?)"""/);
if (!m) {
  console.error('✗ 没能从 scripts/subs.py 里取到 BEATS_TMPL');
  process.exit(1);
}
// 还原一层 Python 字符串转义（\\s → \s，\\u0000 → \u0000）
const tmpl = m[1].replace(/\\\\/g, '\\');

// 匹配器有两份模板（lessons #88：subs.py 生成正式的，new_project.py 生成 _template 的占位），改一份漏一份。
// 两份的 norm() / find() / B / Be 那段（IIFE）必须逐字相同，下面的行为测试才同时守住两份。
const iife = (src) => (src.match(/\(function \(\) \{[\s\S]*?\n\}\)\(\);/) || [])[0];
const stub = fs.readFileSync(path.join(ROOT, 'scripts', 'new_project.py'), 'utf8').match(/stub = """([\s\S]*?)"""/);

const beats = [
  // 关键样本：这一条归一化后是 '24%左右'，且**排在 2.4% 那条前面**。
  // 削掉小数点的年代，B('2.4%左右') 会先撞上它 → 静默返回错误的节拍秒。
  { text: '24%左右', start: 3.0, end: 4.0 },
  { text: '2.4%左右', start: 0.1, end: 2.0 },
  { text: '营收增长了2.4%，', start: 0.5, end: 2.0 },
  { text: '版本升到1.2.3之后，', start: 4.0, end: 6.0 },
  // 词里的点（lessons #143）：2.1.2 的 norm 只护「数字.数字」，'X.509' 削成 'X509'，先撞上这一条
  { text: 'X509，', start: 6.5, end: 7.0 },
  { text: 'X.509 证书，', start: 7.0, end: 8.0 },
  // 连着的点：2.1.2 的 (\d)\.(\d) 一次吃掉右边的数字，'2.0.1' 只护住第一个点，成了 '2.01'
  { text: '2.01 版太旧，', start: 8.0, end: 8.5 },
  { text: '2.0.1 版修好了，', start: 8.5, end: 9.0 },
  // 一模一样的块优先（lessons #153）：一个系列 08-6 eo4 的两块挨着。2.2.3 以前取第一个匹配的块，
  // 「提交」是「提交之后」的前缀、排在前面，B('提交之后') 落到 9.5 这一块，后一块用 B() 根本落不到。
  { text: '提交；', start: 9.5, end: 10.0 },
  { text: '提交之后，', start: 10.1, end: 11.0 },
];
const seg = { id: 't', duration: 6.2, speech_end: 6.0, tail: 0.2 };

const code = tmpl
  .replace('__BEATS_DATA__', JSON.stringify(beats))
  .replace('__SEG_DATA__', JSON.stringify(seg));

const sandbox = { window: {} };
const fn = new Function('window', code);
fn(sandbox.window);
const { B, Be } = sandbox.window;

let failed = 0;
function check(label, actual, want) {
  const ok = actual === want;
  if (!ok) failed++;
  console.log(`  ${ok ? 'OK ' : '✗  '} ${label}` + (ok ? '' : `\n      得到: ${actual}\n      应为: ${want}`));
}
function checkThrows(label, fn2) {
  let threw = false;
  try { fn2(); } catch { threw = true; }
  if (!threw) failed++;
  console.log(`  ${threw ? 'OK ' : '✗  '} ${label}` + (threw ? '' : '（本应 throw，却静默返回了）'));
}

console.log('B() 节拍匹配（JS 侧）');
// canary：证明模板转义还原正确 —— 逗号必须被削掉
check('逗号被归一化掉', B('营收增长了2.4%'), 0.5);
// ★ 证伪核心：'2.4%左右' 必须命中自己那条，不许撞上先出现的 '24%左右'
check('2.4%左右 命中自己（不撞 24%左右）', B('2.4%左右'), 0.1);
check('真的 24%左右 也能查到', B('24%左右'), 3.0);
check('版本号整条命中', B('版本升到1.2.3之后'), 4.0);
// ★ 前缀语义：只能从块文本**开头**取（写少一点可以）
check('块文本开头的前几字能查（前缀匹配）', B('版本升到'), 4.0);
// ★ 反过来：取**中间**一段必须抛错 —— 这条用例守住「不是 contains」这件事。
//   块文本是「版本升到1.2.3之后，」，'1.2.3之后' 不在开头，任何块都不以它开头。
checkThrows("取中间一段应报错（B() 不是 contains）", () => B('1.2.3之后'));
check('Be 同规则', Be('2.4%左右'), 2.0);
checkThrows('查不存在的 2.5% 应报错（别静默返回）', () => B('2.5%左右'));
check('X.509 命中自己（不撞 X509）', B('X.509 证书'), 7.0);
check('真的 X509 也能查到', B('X509'), 6.5);
check('2.0.1 命中自己（连着的点都护住，不撞 2.01）', B('2.0.1 版'), 8.5);
// ★ 一模一样的块优先于前面只是前缀匹配的块（lessons #153）
check('一模一样的块优先：B(提交之后) 落在后一块，不落在前面的「提交；」', B('提交之后'), 10.1);
check('Be 同规则', Be('提交之后'), 11.0);
check('带标点也算一模一样', B('提交之后，'), 10.1);
check('前一块照样查得到（它自己一模一样）', B('提交；'), 9.5);
// 没有一模一样的块、匹配到不止一块：照旧落在第一块（check_beats_refs.py 把这种报成歧义，退出码 1）
check('没有一模一样的块：照旧第一块', B('提交之'), 9.5);
check('块文本是它的前缀（多写了尾巴）照旧命中', B('2.01 版太旧了吧'), 8.0);

console.log('两份模板（subs.py / new_project.py）');
check('new_project.py 的占位 beats 和 subs.py 的匹配器逐字相同',
  stub && iife(stub[1]) === iife(m[1]) && iife(m[1]) !== undefined, true);

if (failed) {
  console.error(`\n失败 ${failed} 项`);
  process.exit(1);
}
console.log('\n全部通过');
