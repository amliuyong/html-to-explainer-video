#!/usr/bin/env node
/**
 * .mjs 脚本参数处理的回归测试（零依赖）。
 *
 * 守一件事（lessons #156）：
 *
 *   --help / -h 打印用法并退出 0，任何未知标志报错并退出 1，且都不创建文件。
 *   2.2.4 以前 parseArgs 忽略未知标志，--help 被当成位置参数 → render_video.mjs --help
 *   启动了真的渲染（60 s、500 帧、不用锁、并发时混文件）。
 *
 * 在临时目录里跑每个脚本的 --help / -h / --bogus / --unknown，检查退出码和是否创建文件。
 * 失败即 process.exit(1)。
 */
import { spawnSync } from 'node:child_process';
import { mkdtempSync, readdirSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(fileURLToPath(import.meta.url), '..', '..');
const SCRIPT_DIR = join(ROOT, 'scripts');
const MJS_SCRIPTS = [
  'render_video.mjs',
  'cover_build.mjs',
  'check_layout.mjs',
  'peek_frame.mjs',
  'check_cover.mjs',
];

const fails = [];

function check(label, cond, msg = '') {
  if (!cond) {
    fails.push(`${label}${msg ? ': ' + msg : ''}`);
  } else {
    process.stdout.write(`  OK  ${label}\n`);
  }
}

function testScript(scriptName, args, expectExit, expectStderr = null, checkNoFiles = false) {
  const scriptPath = join(SCRIPT_DIR, scriptName);
  const tmpDir = checkNoFiles ? mkdtempSync(join(tmpdir(), 'mjs-test-')) : null;
  
  const result = spawnSync('node', [scriptPath, ...args], {
    cwd: tmpDir || SCRIPT_DIR,
    encoding: 'utf8',
    timeout: 5000,
  });
  
  if (checkNoFiles && tmpDir) {
    const files = readdirSync(tmpDir);
    if (files.length > 0) {
      fails.push(`${scriptName} ${args.join(' ')} 不应创建文件，但创建了: ${files.join(', ')}`);
    }
  }
  
  if (result.status !== expectExit) {
    fails.push(`${scriptName} ${args.join(' ')} 应退出 ${expectExit}，实际退出 ${result.status}`);
  }
  
  if (expectStderr !== null) {
    const output = result.stderr || result.stdout || '';
    if (!expectStderr.test(output)) {
      fails.push(`${scriptName} ${args.join(' ')} 的输出应匹配 ${expectStderr}，实际: ${output.slice(0, 100)}`);
    }
  }
  
  return result;
}

process.stdout.write('A. --help / -h 退出 0 并打印用法\n');

for (const script of MJS_SCRIPTS) {
  testScript(script, ['--help'], 0, /用法|usage/i, true);
  check(`${script} --help`, true);
  
  testScript(script, ['-h'], 0, null, true);
  check(`${script} -h`, true);
}

process.stdout.write('\nB. 未知标志退出 1 并报错\n');

for (const script of MJS_SCRIPTS) {
  const flag = script === 'render_video.mjs' ? '--bogus' : '--unknown';
  const result = testScript(script, [flag], 1, null, true);
  const output = result.stderr || result.stdout || '';
  const hasError = output.includes(flag) || /未知|unknown/i.test(output);
  check(`${script} ${flag}`, hasError, hasError ? '' : `未提及 ${flag} 或"未知"`);
}

if (fails.length > 0) {
  process.stdout.write('\n✗ 失败：\n');
  for (const f of fails) {
    process.stdout.write(`  - ${f}\n`);
  }
  process.exit(1);
}
process.stdout.write('\n✓ .mjs 参数处理回归全部通过\n');
