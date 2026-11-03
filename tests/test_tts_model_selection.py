#!/usr/bin/env python3
"""TTS model/voice routing regressions; no credentials or network required."""
from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
try:
    import edge_tts
except ImportError:
    sys.modules["edge_tts"] = types.SimpleNamespace(__version__="stub", Communicate=None)
import tts_ali
import tts_build
import tts_setup


class ModelSelectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name)
        self.write_project()
        (self.project / "narration.json").write_text(
            json.dumps([{"id": "intro", "text": "企业方案"}]), encoding="utf-8")
        self.env = patch.dict(os.environ, {"TTS_PROVIDER": ""})
        self.env.start()
        self.addCleanup(self.env.stop)

    def write_project(self, **overrides):
        self.pj = {"provider": "ali", "tts_model": tts_ali.MODEL,
                   "voice": tts_ali.DEFAULT_VOICE, "lang": "zh", "rate": "+8%"}
        self.pj.update(overrides)
        (self.project / "project.json").write_text(
            json.dumps(self.pj), encoding="utf-8")

    def read_project(self):
        return json.loads((self.project / "project.json").read_text(encoding="utf-8"))

    def setup_args(self, **overrides):
        values = dict(project=str(self.project), provider="ali", model="",
                      voice="", rate="", status=False)
        values.update(overrides)
        return argparse.Namespace(**values)

    def run_setup(self, args, tty=False, choices=None, key_ready=True):
        output = io.StringIO()
        status = {"status": "ok"} if key_ready else {"status": "missing", "detail": "test"}

        def checked(voice, **kwargs):
            return {"ok": True, "model": kwargs["model"], "voice": voice,
                    "key_masked": "***"}

        with patch.object(tts_setup.sys.stdin, "isatty", return_value=tty), \
             patch("builtins.input", side_effect=choices), \
             patch.object(tts_ali, "describe_env", return_value=status) as env, \
             patch.object(tts_ali, "check", side_effect=checked) as check, \
             contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            result = tts_setup.run(args)
        return result, output.getvalue(), env, check

    def test_default_and_supported_models(self):
        self.assertEqual(tts_ali.resolve_model_token(), "qwen-audio-3.1-tts-plus")
        self.assertEqual(tts_ali.resolve_model_token("2"), tts_ali.MODELS[1])
        for lang in ("zh", "en"):
            self.assertEqual(tts_ali.default_voice("plus", lang), "longanfengyue_v3.1")
        self.assertEqual(tts_ali.default_voice("flash", "en"), "Andy_v3.1")
        for model in ("qwen-audio-3.0-tts-plus", "qwen-audio-3.0-tts-flash",
                      "qwen-audio-2.0-tts", "qwen-audio-3.1-tts-next"):
            with self.subTest(model=model), self.assertRaises(tts_ali.AliTTSConfigError):
                tts_ali.resolve_model_token(model)

    def test_catalogs_and_previews_are_model_specific(self):
        plus = tts_ali.voices_for_model("plus")
        self.assertEqual({v[1] for v in plus}, {
            "longanfengyue_v3.1", "longanlingxin_v3.1",
            "longanhuan_v3.1", "xunanchuan_v3.1"})
        self.assertEqual(set(tts_ali.VOICE_CATALOG), set(tts_ali.MODELS))
        flash = tts_ali.voices_for_model("flash")
        self.assertGreater(len(flash), 100)
        for model in tts_ali.MODELS:
            entries = tts_ali.voices_for_model(model)
            self.assertEqual(len(entries), len({v[1] for v in entries}))
            for i, (_, voice, _) in enumerate(entries, 1):
                self.assertEqual(tts_ali.resolve_voice_token(str(i), model=model), voice)
                info = tts_ali.voice_info(voice, model)
                self.assertTrue(info["previews"][0].startswith("https://"))
                self.assertIn("timestamp", info["features"])
        with self.assertRaises(tts_ali.AliTTSConfigError):
            tts_ali.resolve_voice_token("5", model="plus")

    def test_incompatible_voice_fails_before_credentials(self):
        with patch.object(tts_ali, "load_credentials") as credentials:
            for voice in ("Andy_v3.1", "许玉远", "龙书_v3.1",
                          "qwen-audio-3.1-tts-flash-zora_bg"):
                with self.subTest(voice=voice), self.assertRaises(tts_ali.AliTTSConfigError):
                    tts_ali.synthesize("测试", voice=voice, model="plus")
            self.assertEqual(tts_ali.check("Andy_v3.1", model="plus")["stage"], "config")
            with self.assertRaises(tts_ali.AliTTSConfigError):
                tts_ali.synthesize("测试", model="qwen-audio-3.0-tts-plus")
            credentials.assert_not_called()

    def test_duplicate_name_requires_id_or_number(self):
        with self.assertRaises(tts_ali.AliTTSConfigError):
            tts_ali.resolve_voice_token("Ava", model="flash")
        for voice in ("Ava_v3.1", "qwen-audio-3.1-tts-flash-ava_fa"):
            self.assertEqual(tts_ali.resolve_voice_token(voice, model="flash"), voice)

    def test_synthesis_sends_selected_model_to_http(self):
        requests = []
        audio = b"ID3\x03\x00test-audio"
        event = {"output": {"type": "sentence-end", "finish_reason": "stop",
                            "audio": {"data": base64.b64encode(audio).decode()},
                            "sentence": {"words": [
                                {"text": "测", "begin_time": 0, "end_time": 100}]}},
                 "usage": {"characters": 2}}

        def response(request, **kwargs):
            requests.append(json.loads(request.data.decode()))
            return io.BytesIO(("data:" + json.dumps(event) + "\n\n").encode())

        with patch.object(tts_ali, "load_credentials", return_value={
                "api_key": "test-key", "base_url": "https://example.invalid/api",
                "path": "test.csv"}), \
             patch.object(tts_ali.urllib.request, "urlopen", side_effect=response):
            for model in tts_ali.MODELS:
                result = tts_ali.synthesize("测", model=model,
                                            voice="longanfengyue_v3.1")
                self.assertEqual(result["audio"], audio)
                self.assertEqual(len(result["boundaries"]), 1)
        self.assertEqual([r["model"] for r in requests], list(tts_ali.MODELS))
        self.assertTrue(all(r["input"]["word_timestamp_enabled"] for r in requests))

    def test_noninteractive_requires_model_before_voice_or_api(self):
        result, output, env, check = self.run_setup(self.setup_args())
        self.assertEqual(result, 1)
        self.assertIn("NEXT_ACTION=ask_user_model", output)
        self.assertNotIn("NEXT_ACTION=ask_user_voice", output)
        env.assert_not_called()
        check.assert_not_called()

    def test_model_selected_requires_voice_before_api(self):
        result, output, env, check = self.run_setup(self.setup_args(model="plus"))
        self.assertEqual(result, 1)
        self.assertIn("NEXT_ACTION=ask_user_voice", output)
        self.assertIn("longanfengyue_v3.1", output)
        self.assertNotIn("Andy_v3.1", output)
        env.assert_not_called()
        check.assert_not_called()

    def test_interactive_model_then_voice_saved_and_checked(self):
        flash = tts_ali.voices_for_model("flash")
        index = next(i for i, v in enumerate(flash, 1) if v[1] == "Andy_v3.1")
        self.write_project(lang="en")
        result, output, _, check = self.run_setup(
            self.setup_args(), tty=True, choices=["2", str(index)])
        self.assertEqual(result, 0)
        self.assertLess(output.index("阿里云百炼用哪个模型"), output.index("候选音色"))
        self.assertEqual(self.read_project()["tts_model"], tts_ali.MODELS[1])
        self.assertEqual(self.read_project()["voice"], "Andy_v3.1")
        check.assert_called_once_with("Andy_v3.1", model=tts_ali.MODELS[1], language="en")

    def test_plus_default_on_enter(self):
        result, _, _, check = self.run_setup(self.setup_args(), tty=True, choices=["", ""])
        self.assertEqual(result, 0)
        check.assert_called_once_with("longanfengyue_v3.1", model=tts_ali.MODELS[0], language="zh")

    def test_legacy_model_can_be_replaced_in_wizard(self):
        self.write_project(tts_model="qwen-audio-3.0-tts-plus", voice="longanlingxin")
        result, _, _, check = self.run_setup(self.setup_args(), tty=True, choices=["", ""])
        self.assertEqual(result, 0)
        self.assertEqual(self.read_project()["tts_model"], tts_ali.MODELS[0])
        check.assert_called_once_with("longanfengyue_v3.1", model=tts_ali.MODELS[0], language="zh")

    def test_selection_persists_when_key_is_missing(self):
        result, output, _, check = self.run_setup(
            self.setup_args(model="flash", voice="许玉远"), key_ready=False)
        self.assertEqual(result, 2)
        self.assertIn("NEXT_ACTION=fill_ali_key", output)
        self.assertEqual(self.read_project()["tts_model"], tts_ali.MODELS[1])
        self.assertEqual(self.read_project()["voice"], "xuyuyuan_v3.1")
        check.assert_not_called()

    def test_switching_model_clears_previous_voice_while_asking(self):
        self.write_project(tts_model=tts_ali.MODELS[1], voice="Andy_v3.1")
        result, _, _, check = self.run_setup(self.setup_args(model="plus"))
        self.assertEqual(result, 1)
        self.assertEqual(self.read_project()["tts_model"], tts_ali.MODELS[0])
        self.assertEqual(self.read_project()["voice"], "")
        check.assert_not_called()

    def test_cache_separates_models_even_for_shared_voice(self):
        args = ("企业方案", "longanfengyue_v3.1", "+8%", "+0%", "+0Hz",
                str(self.project / "cache"), True)
        plus = tts_build.cache_paths(*args, provider="ali", model="plus")
        flash = tts_build.cache_paths(*args, provider="ali", model="flash")
        self.assertNotEqual(plus, flash)
        self.assertEqual(plus, tts_build.cache_paths(*args, provider="ali", model=tts_ali.MODEL))
        self.assertEqual(tts_build.cache_paths(*args, provider="edge"),
                         tts_build.cache_paths(*args, provider="edge", model="flash"))

    def run_build(self, **overrides):
        values = dict(project=str(self.project), provider="", voice="", model="",
                      rate="", no_cache=False, no_trim=True)
        values.update(overrides)
        mock_result = {"bytes": 100, "container_duration_sec": 1.0,
                       "speech_end_sec": 1.0, "lead_cut_sec": 0, "boundary_count": 1,
                       "boundaries": [{"text": "企业", "offset_sec": 0, "duration_sec": 1.0}]}
        with patch.object(tts_build, "synth", new_callable=AsyncMock,
                          return_value=mock_result) as synth, \
             contextlib.redirect_stdout(io.StringIO()), \
             contextlib.redirect_stderr(io.StringIO()):
            result = asyncio.run(tts_build.run(argparse.Namespace(**values)))
        manifest = json.loads((self.project / "audio-manifest.json").read_text()) \
            if result == 0 else None
        return result, manifest, synth

    def test_build_uses_saved_model_and_manifest_records_it(self):
        self.write_project(tts_model=tts_ali.MODELS[1], voice="许玉远")
        result, manifest, synth = self.run_build()
        self.assertEqual(result, 0)
        self.assertEqual(manifest["model"], tts_ali.MODELS[1])
        self.assertIn(tts_ali.MODELS[1], manifest["engine"])
        self.assertEqual(synth.await_args.kwargs["voice"], "xuyuyuan_v3.1")
        self.assertEqual(synth.await_args.kwargs["model"], tts_ali.MODELS[1])

    def test_build_model_override_selects_compatible_default(self):
        self.write_project(tts_model=tts_ali.MODELS[1], voice="Andy_v3.1", lang="en")
        result, manifest, synth = self.run_build(model="plus")
        self.assertEqual(result, 0)
        self.assertEqual(manifest["model"], tts_ali.MODELS[0])
        self.assertEqual(synth.await_args.kwargs["voice"], "longanfengyue_v3.1")
        self.assertEqual(self.read_project()["tts_model"], tts_ali.MODELS[1])

    def test_build_rejects_incompatible_explicit_voice(self):
        result, _, synth = self.run_build(model="plus", voice="Andy_v3.1")
        self.assertEqual(result, 1)
        synth.assert_not_awaited()

    def test_build_can_override_legacy_model_without_reusing_its_voice(self):
        self.write_project(tts_model="qwen-audio-3.0-tts-plus", voice="longanlingxin")
        result, manifest, synth = self.run_build(model="plus")
        self.assertEqual(result, 0)
        self.assertEqual(manifest["model"], tts_ali.MODELS[0])
        self.assertEqual(synth.await_args.kwargs["voice"], "longanfengyue_v3.1")

    def test_new_projects_use_plus_and_fengyue_in_both_languages(self):
        for lang in ("zh", "en"):
            path = self.project / lang
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts/new_project.py"),
                 str(path), "test", "--lang", lang],
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            project = json.loads((path / "project.json").read_text())
            self.assertEqual(project["provider"], "ali")
            self.assertEqual(project["tts_model"], tts_ali.MODELS[0])
            self.assertEqual(project["voice"], "longanfengyue_v3.1")


if __name__ == "__main__":
    unittest.main()
