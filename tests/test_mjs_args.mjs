#!/usr/bin/env node
/**
 * 测试 .mjs 脚本的参数处理：--help 退出 0，未知标志退出非零，且都不创建文件。
 */
import assert from 'node:assert/strict';
import { execSync, spawnSync } from 'node:child_process';
import { mkdtempSync, existsSync, readdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';

const SCRIPT_DIR = join(import.meta.dirname, '..', 'scripts');
const MJS_SCRIPTS = [
  'render_video.mjs',
  'cover_build.mjs',
  'check_layout.mjs',
  'peek_frame.mjs',
  'check_cover.mjs',
];

function testScript(scriptName, { args = [], expectExit = 0, expectStderr = null, checkNoFiles = false } = {}) {
  const scriptPath = join(SCRIPT_DIR, scriptName);
  const tmpDir = checkNoFiles ? mkdtempSync(join(tmpdir(), 'mjs-test-')) : null;
  
  const result = spawnSync('node', [scriptPath, ...args], {
    cwd: tmpDir || SCRIPT_DIR,
    encoding: 'utf8',
    timeout: 5000,
  });
  
  if (checkNoFiles && tmpDir) {
    const files = readdirSync(tmpDir);
    assert.equal(files.length, 0, `${scriptName} ${args.join(' ')} 不应创建文件，但创建了: ${files.join(', ')}`);
  }
  
  if (result.status !== expectExit) {
    console.error(`${scriptName} ${args.join(' ')}\nstdout: ${result.stdout}\nstderr: ${result.stderr}`);
  }
  assert.equal(result.status, expectExit, 
    `${scriptName} ${args.join(' ')} 应退出 ${expectExit}，实际退出 ${result.status}`);
  
  if (expectStderr !== null) {
    assert.match(result.stderr || result.stdout, expectStderr,
      `${scriptName} ${args.join(' ')} 的输出应匹配 ${expectStderr}`);
  }
  
  return result;
}

test('render_video.mjs --help exits 0 and prints usage', () => {
  const r = testScript('render_video.mjs', { 
    args: ['--help'], 
    expectExit: 0, 
    expectStderr: /用法|usage/i,
    checkNoFiles: true 
  });
});

test('render_video.mjs -h exits 0', () => {
  testScript('render_video.mjs', { args: ['-h'], expectExit: 0, checkNoFiles: true });
});

test('render_video.mjs --bogus exits non-zero', () => {
  const r = testScript('render_video.mjs', { 
    args: ['--bogus'], 
    expectExit: 1,
    checkNoFiles: true 
  });
  const output = r.stderr || r.stdout;
  assert.match(output, /--bogus|unknown|未知/i, '--bogus 应报告未知标志');
});

test('cover_build.mjs --help exits 0', () => {
  testScript('cover_build.mjs', { args: ['--help'], expectExit: 0, checkNoFiles: true });
});

test('cover_build.mjs --unknown exits non-zero', () => {
  testScript('cover_build.mjs', { args: ['--unknown'], expectExit: 1, checkNoFiles: true });
});

test('check_layout.mjs --help exits 0', () => {
  testScript('check_layout.mjs', { args: ['--help'], expectExit: 0, checkNoFiles: true });
});

test('check_layout.mjs --unknown exits non-zero', () => {
  testScript('check_layout.mjs', { args: ['--unknown'], expectExit: 1, checkNoFiles: true });
});

test('peek_frame.mjs --help exits 0', () => {
  testScript('peek_frame.mjs', { args: ['--help'], expectExit: 0, checkNoFiles: true });
});

test('peek_frame.mjs --unknown exits non-zero', () => {
  testScript('peek_frame.mjs', { args: ['--unknown'], expectExit: 1, checkNoFiles: true });
});

test('check_cover.mjs --help exits 0', () => {
  testScript('check_cover.mjs', { args: ['--help'], expectExit: 0, checkNoFiles: true });
});

test('check_cover.mjs --unknown exits non-zero', () => {
  testScript('check_cover.mjs', { args: ['--unknown'], expectExit: 1, checkNoFiles: true });
});
