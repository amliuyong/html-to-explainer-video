#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""测试密钥脱敏 —— 脱敏后的字符串不含密钥的任何片段。"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import tts_volcano  # noqa: E402


def test_mask_reveals_no_characters():
    """_mask() 不显示密钥的任何字符。"""
    # 假密钥：避开长度数字与内容重合（如 23 字符的 key 包含 "23"）
    keys = [
        "sk-abcdefghijklmnopqrstuvwx",  # 27 字符，典型百炼 key
        "FAKEabcdefghijklmnop",          # 20 字符，火山 Access Key（避开 "20"）
        "xyzABCDEF",                     # 9 字符，短密钥（避开 "9"）
        "xy",                            # 2 字符，极短（避开 "2"）
    ]

    for key in keys:
        masked = tts_volcano._mask(key)
        length_str = str(len(key))

        # 不含长度 ≥ 2 的子串（排除纯数字子串，可能与长度重合）
        for i in range(len(key) - 1):
            substr = key[i:i+2]
            # 跳过纯数字子串（可能与长度信息重合）
            if substr.isdigit():
                continue
            assert substr not in masked, \
                f"密钥的片段 {repr(substr)} 出现在脱敏结果 {repr(masked)} 中"

        # 不含首字符（除非是数字且在长度字符串中）
        if len(key) > 0:
            if not (key[0].isdigit() and key[0] in length_str):
                assert key[0] not in masked, \
                    f"密钥的首字符 {repr(key[0])} 出现在脱敏结果 {repr(masked)} 中"

        # 不含尾字符（除非是数字且在长度字符串中）
        if len(key) > 0:
            if not (key[-1].isdigit() and key[-1] in length_str):
                assert key[-1] not in masked, \
                    f"密钥的尾字符 {repr(key[-1])} 出现在脱敏结果 {repr(masked)} 中"

        # 应当包含长度信息
        assert length_str in masked, \
            f"脱敏结果 {repr(masked)} 未包含长度 {len(key)}"


def test_mask_empty():
    """空密钥或未设置密钥的情况。"""
    assert tts_volcano._mask("") == "(未设置)"
    assert tts_volcano._mask(None) == "(未设置)"


def test_tts_setup_masked_output(tmp_path, monkeypatch):
    """tts_setup.py 的输出不含密钥片段。"""
    import tts_ali
    import tts_setup

    # 创建假密钥文件（避开数字与长度冲突）
    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()
    ali_dir = fake_home / "ali_tts"
    ali_dir.mkdir()
    key_file = ali_dir / "api_key.csv"
    fake_key = "sk-fakekeyabcdefghijklmnopq"  # 27 字符
    key_file.write_text(f"api_key\n{fake_key}\n")

    monkeypatch.setenv("HOME", str(fake_home))

    # 调用 describe_env 获取状态
    status = tts_ali.describe_env()

    # 检查所有输出字段
    masked = status.get("key_masked", "")

    # 不含长度 ≥ 2 的子串（排除纯数字）
    for i in range(len(fake_key) - 1):
        substr = fake_key[i:i+2]
        if substr.isdigit():
            continue
        assert substr not in masked, \
            f"密钥片段 {repr(substr)} 出现在输出 {repr(masked)} 中"

    # 不含首尾字符（字母一定不会与长度数字冲突）
    assert fake_key[0] not in masked, \
        f"密钥首字符出现在输出 {repr(masked)} 中"
    assert fake_key[-1] not in masked, \
        f"密钥尾字符出现在输出 {repr(masked)} 中"


def test_volcano_masked_output(tmp_path, monkeypatch):
    """火山引擎的密钥脱敏同样不泄露字符。"""
    import tts_volcano

    # 创建假 tts.env（避开数字与长度冲突）
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    env_file = project_dir / "tts.env"
    fake_key = "FAKEabcdefghijklmnopXYZ"  # 22 字符（避开 "22"）
    env_file.write_text(f"VOLCANO_API_KEY={fake_key}\nVOLCANO_RESOURCE_ID=test-resource\n")

    # 调用 describe_env
    status = tts_volcano.describe_env(str(project_dir))
    masked = status.get("key_masked", "")

    # 不含密钥片段（排除纯数字）
    for i in range(len(fake_key) - 1):
        substr = fake_key[i:i+2]
        if substr.isdigit():
            continue
        assert substr not in masked, \
            f"密钥片段 {repr(substr)} 出现在输出 {repr(masked)} 中"

    assert fake_key[0] not in masked
    assert fake_key[-1] not in masked
