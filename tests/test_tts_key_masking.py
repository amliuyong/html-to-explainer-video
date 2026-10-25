#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""密钥脱敏回归测试（python tests/test_tts_key_masking.py）。

守一件事（lessons #155）：

  tts_setup.py 打印的「脱敏密钥」不含密钥的任何字符。2.2.1 新增脱敏显示时，
  `tts_volcano._mask()` 显示首 4 个 + 末 4 个字符（`sk-1234******5678`），
  密钥片段会落进 transcript 与日志。改成只显示长度：`已设置（35 个字符）`。

用假密钥、在临时目录里创建假 key 文件与 tts.env；永不读取、打印任何真实密钥。
失败即以非零码退出。
"""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, rel))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


tts_volcano = _load("tts_volcano", "scripts/tts_volcano.py")
tts_ali = _load("tts_ali", "scripts/tts_ali.py")
fails: list[str] = []


def check(label, cond, msg=""):
    if not cond:
        fails.append(f"{label}{': ' + msg if msg else ''}")
    else:
        print(f"  OK  {label}")


print("A. _mask() 不显示密钥的任何字符")

# 假密钥：避开长度数字与内容重合
keys = [
    "sk-abcdefghijklmnopqrstuvwx",  # 27 字符，典型百炼 key
    "FAKEabcdefghijklmnop",          # 20 字符，火山 Access Key
    "xyzABCDEF",                     # 9 字符，短密钥
    "xy",                            # 2 字符，极短
]

for key in keys:
    masked = tts_volcano._mask(key)
    length_str = str(len(key))
    
    # 检查不含长度 ≥ 2 的子串（排除纯数字子串）
    for i in range(len(key) - 1):
        substr = key[i:i+2]
        if substr.isdigit():
            continue
        if substr in masked:
            fails.append(f"_mask({key!r})：密钥片段 {substr!r} 出现在 {masked!r} 中")
            break
    
    # 检查不含首尾字符（除非是数字且在长度字符串中）
    if len(key) > 0:
        if not (key[0].isdigit() and key[0] in length_str):
            if key[0] in masked:
                fails.append(f"_mask({key!r})：首字符 {key[0]!r} 出现在 {masked!r} 中")
        if not (key[-1].isdigit() and key[-1] in length_str):
            if key[-1] in masked:
                fails.append(f"_mask({key!r})：尾字符 {key[-1]!r} 出现在 {masked!r} 中")
    
    # 检查包含长度信息
    if length_str not in masked:
        fails.append(f"_mask({key!r})：未包含长度 {len(key)}")
    
    if not any(f.startswith(f"_mask({key!r})") for f in fails):
        print(f"  OK  _mask({len(key)} 字符) → {masked!r}")

check("_mask('') 显示未设置", tts_volcano._mask("") == "(未设置)")
check("_mask(None) 显示未设置", tts_volcano._mask(None) == "(未设置)")

print("\nB. tts_ali.describe_env() 的输出不含密钥片段")

with tempfile.TemporaryDirectory() as tmpdir:
    fake_home = os.path.join(tmpdir, "fake_home")
    os.makedirs(fake_home)
    ali_dir = os.path.join(fake_home, "ali_tts")
    os.makedirs(ali_dir)
    key_file = os.path.join(ali_dir, "api_key.csv")
    fake_key = "sk-fakekeyabcdefghijklmnopq"  # 27 字符
    with open(key_file, "w") as f:
        f.write(f"api_key\n{fake_key}\n")
    
    # 临时改 HOME
    old_home = os.environ.get("HOME")
    try:
        os.environ["HOME"] = fake_home
        status = tts_ali.describe_env()
        masked = status.get("key_masked", "")
        
        # 检查不含密钥片段（排除纯数字）
        found_leak = False
        for i in range(len(fake_key) - 1):
            substr = fake_key[i:i+2]
            if substr.isdigit():
                continue
            if substr in masked:
                fails.append(f"tts_ali.describe_env()：密钥片段 {substr!r} 出现在 {masked!r} 中")
                found_leak = True
                break
        
        if not found_leak:
            # 检查首尾字符
            if fake_key[0] in masked:
                fails.append(f"tts_ali.describe_env()：首字符 {fake_key[0]!r} 出现在 {masked!r} 中")
            elif fake_key[-1] in masked:
                fails.append(f"tts_ali.describe_env()：尾字符 {fake_key[-1]!r} 出现在 {masked!r} 中")
            else:
                print(f"  OK  tts_ali 输出不含密钥片段")
    finally:
        if old_home:
            os.environ["HOME"] = old_home
        else:
            os.environ.pop("HOME", None)

print("\nC. tts_volcano.describe_env() 的输出不含密钥片段")

with tempfile.TemporaryDirectory() as tmpdir:
    project_dir = os.path.join(tmpdir, "project")
    os.makedirs(project_dir)
    env_file = os.path.join(project_dir, "tts.env")
    fake_key = "FAKEabcdefghijklmnopXYZ"  # 22 字符
    with open(env_file, "w") as f:
        f.write(f"VOLCANO_API_KEY={fake_key}\nVOLCANO_RESOURCE_ID=test-resource\n")
    
    status = tts_volcano.describe_env(project_dir)
    masked = status.get("key_masked", "")
    
    # 检查不含密钥片段（排除纯数字）
    found_leak = False
    for i in range(len(fake_key) - 1):
        substr = fake_key[i:i+2]
        if substr.isdigit():
            continue
        if substr in masked:
            fails.append(f"tts_volcano.describe_env()：密钥片段 {substr!r} 出现在 {masked!r} 中")
            found_leak = True
            break
    
    if not found_leak:
        # 检查首尾字符
        if fake_key[0] in masked:
            fails.append(f"tts_volcano.describe_env()：首字符 {fake_key[0]!r} 出现在 {masked!r} 中")
        elif fake_key[-1] in masked:
            fails.append(f"tts_volcano.describe_env()：尾字符 {fake_key[-1]!r} 出现在 {masked!r} 中")
        else:
            print(f"  OK  tts_volcano 输出不含密钥片段")

if fails:
    print("\n✗ 失败：")
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("\n✓ 密钥脱敏回归全部通过")
