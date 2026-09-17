"""Exercise the real native request boundary for the two reported generation issues."""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path

from app.yue2_app.core_worker import generation_kwargs
from app.yue2_app.mulacover_core import normalize_request


ROOT = Path(__file__).resolve().parents[1]


class InstrumentalConditions(unittest.TestCase):
    def test_checkbox_and_json_intent_reach_native_prompt(self):
        request = {"style": "Mandarin pop, warm female vocal, piano, strings, 92 BPM",
                   "lyrics": "[Verse]\nDo not sing this lyric", "abc": "X:1\nK:C\nCDEF|",
                   "semantic_sampling": {"max_tokens": 100}, "seed": 44}
        for checked in (True, "on", "true", "1"):
            with self.subTest(checked=checked):
                result = generation_kwargs({**request, "instrumental": checked}, seed=45)
                self.assertEqual(result["lyrics"], "[instrumental]")
                self.assertIn("no singing, no vocals", result["style"])
                self.assertNotIn("warm female vocal", result["style"])
                for retained in ("Mandarin pop", "piano", "strings", "92 BPM"):
                    self.assertIn(retained, result["style"])
                self.assertEqual(result["abc"], request["abc"])
                self.assertEqual(result["semantic_sampling"], request["semantic_sampling"])
                self.assertEqual(result["seed"], 45)
                self.assertNotIn("instrumental", result)  # No unsupported native keyword.

    def test_false_or_absent_flag_keeps_singing_and_lyrics(self):
        request = {"style": "English soul, expressive lead vocal, Rhodes piano", "lyrics": "hello"}
        original = generation_kwargs(request)
        for unchecked in (False, "false", "off", "0", None):
            with self.subTest(unchecked=unchecked):
                self.assertEqual(generation_kwargs({**request, "instrumental": unchecked}), original)

    def test_existing_negative_conditions_are_retained(self):
        result = generation_kwargs({"style": "无需演唱, 无人声, guitar, no choir", "instrumental": True})
        self.assertIn("无需演唱", result["style"])
        self.assertIn("无人声", result["style"])
        self.assertIn("no choir", result["style"])
        self.assertIn("guitar", result["style"])


class RemixOptionalStyle(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        audio = self.root / "uploads" / "reference.wav"
        audio.parent.mkdir()
        audio.write_bytes(b"placeholder: admission does not require decoded audio")
        self.request = {"source_path": str(audio), "lyrics": "[Verse]\nhello"}

    def test_reference_mode_ignores_prior_style_and_accepts_blank_style(self):
        for extras in ({}, {"genre": "Mandarin pop", "tags": "prior country style",
                           "instrument": "piano", "mood": "hopeful"}):
            with self.subTest(extras=extras):
                normalized = normalize_request(self.root, {**self.request, **extras, "style_mode": "reference"})
                self.assertEqual(normalized["tags"], "")
                self.assertEqual(normalized["style_mode"], "reference")
                self.assertEqual(normalized, normalize_request(self.root, normalized))

    def test_legacy_and_custom_requests_keep_supplied_style(self):
        for mode in ({}, {"style_mode": "custom"}):
            with self.subTest(mode=mode):
                normalized = normalize_request(self.root, {**self.request, **mode, "genre": "country"})
                self.assertIn("genre:[country]", normalized["tags"])
                self.assertEqual(normalized, normalize_request(self.root, normalized))

    def test_custom_explicit_tags_do_not_require_genre(self):
        normalized = normalize_request(self.root, {**self.request, "style_mode": "custom", "tags": "jazz"})
        self.assertEqual(normalized["tags"], "jazz")

    def test_explicit_style_still_requires_a_condition_and_valid_mode(self):
        with self.assertRaisesRegex(ValueError, "至少填写"):
            normalize_request(self.root, {**self.request, "style_mode": "custom"})
        for mode in ("unknown", [], None):
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                normalize_request(self.root, {**self.request, "style_mode": mode})


class RemixUiIntent(unittest.TestCase):
    def test_default_mode_and_fields_do_not_force_a_genre(self):
        class Controls(HTMLParser):
            def __init__(self):
                super().__init__()
                self.in_remix = False
                self.select = ""
                self.defaults = []
                self.fields = {}

            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if tag == "form" and attrs.get("id") == "remix-form":
                    self.in_remix = True
                if not self.in_remix:
                    return
                if tag == "select":
                    self.select = attrs.get("name")
                if tag == "option" and self.select == "style_mode" and "selected" in attrs:
                    self.defaults.append(attrs.get("value"))
                if tag == "input" and attrs.get("name") in {"topic", "genre", "instrument", "mood"}:
                    self.fields[attrs["name"]] = attrs

            def handle_endtag(self, tag):
                if tag == "form":
                    self.in_remix = False
                if tag == "select":
                    self.select = ""

        controls = Controls()
        controls.feed((ROOT / "app/web/index.html").read_text(encoding="utf-8"))
        self.assertEqual(controls.defaults, ["reference"])
        self.assertEqual(set(controls.fields), {"topic", "genre", "instrument", "mood"})
        for field in controls.fields.values():
            self.assertNotIn("required", field)
            self.assertIn("disabled", field)

    @unittest.skipUnless(shutil.which("node"), "Node is required to execute the browser mode handlers")
    def test_real_style_handler_toggles_submission_and_legacy_drafts(self):
        source = (ROOT / "app/web/mulacover.js").read_text(encoding="utf-8")
        handlers = source.split("  function updateStyleMode() {", 1)[1].split("  function updateButtonState()", 1)[0]
        restore = source.split("  function restoreDraft() {", 1)[1].split("  function switchProjectDraft()", 1)[0]
        javascript = """
          const vm = require('node:vm'), assert = require('node:assert/strict');
          const fields=['topic','genre','instrument','mood'].map(name=>({name,type:'text',value:'country',disabled:true}));
          const labels=fields.map(input=>({hidden:true,classList:{toggle(key,value){input.hidden=value;}},querySelector(){return input;}}));
          const styleMode={value:'reference'}, hint={textContent:''};
          let draft={};
          const context={styleMode,form:{elements:[styleMode,...fields],querySelectorAll(){return labels;}},
            $(){return hint;},savedValue(){return JSON.stringify(draft);},draftKey(){return 'temporary';}};
          styleMode.name='style_mode'; styleMode.type='select-one';
          vm.createContext(context);
        """
        javascript += "vm.runInContext(" + json.dumps("function updateStyleMode() {" + handlers +
                                                           "function restoreDraft() {" + restore) + ",context);\n"
        javascript += """
          context.updateStyleMode(); assert(fields.every(field=>field.disabled&&field.hidden));
          styleMode.value='custom'; context.updateStyleMode(); assert(fields.every(field=>!field.disabled&&!field.hidden));
          styleMode.value='reference'; draft={genre:'rock'}; context.restoreDraft();
          assert.equal(styleMode.value,'custom'); assert.equal(fields[1].value,'rock');
          draft={genre:'rock',style_mode:'reference'}; context.restoreDraft(); context.updateStyleMode();
          assert.equal(styleMode.value,'reference'); assert(fields.every(field=>field.disabled));
        """
        completed = subprocess.run([shutil.which("node"), "-e", javascript], capture_output=True, text=True, timeout=15)
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
