"""Extract the owner's T8 text-only rules without importing ComfyUI.

Pinned source, attribution, and a reproducible extraction; run from the kit root.
"""
import ast
import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "research/h3-review-agent"
DEST = ROOT / "app/yue2_app/assistant_rules"
COMMIT = "b09412575ef726b4b7637f7279f1848108435607"


def main():
    assert subprocess.check_output(["git", "-C", str(SOURCE), "rev-parse", "HEAD"], text=True).strip() == COMMIT
    DEST.mkdir(parents=True, exist_ok=True)
    (DEST / "__init__.py").write_text('"""Pinned T8 YuE2 text rules; no ComfyUI imports."""\n', encoding="utf-8")
    for name in ("yue2_abc.py", "provider_capabilities.py", "provider_transport.py"):
        shutil.copy2(SOURCE / name, DEST / name)
    shutil.copytree(SOURCE / "official_skills/yue2-music", DEST / "official_skills/yue2-music", dirs_exist_ok=True)
    shutil.copy2(SOURCE / "LICENSE.txt", DEST / "T8-LICENSE.txt")
    # Keep only the file reader; model-directory discovery must never import ComfyUI.
    catalog = (SOURCE / "local_gguf_catalog.py").read_text(encoding="utf-8")
    metadata_names = {"GGUFMetadataError", "GGUFModelInfo", "_read_exact", "_unpack", "_read_string",
                      "_read_value", "_metadata_values", "_cached_model_info"}
    metadata = ["from __future__ import annotations\nimport functools\nimport os\nimport struct\nfrom dataclasses import asdict, dataclass\nfrom pathlib import Path\nfrom typing import Any, BinaryIO\n"]
    for node in ast.parse(catalog).body:
        selected = isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name in metadata_names
        selected |= isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in {
            "_MAX_METADATA_STRING_BYTES", "_MAX_ARRAY_ITEMS", "_SCALAR_LAYOUTS"} for t in node.targets)
        if selected:
            text = ast.get_source_segment(catalog, node)
            if getattr(node, "name", "") == "GGUFModelInfo":
                text = "@dataclass(frozen=True)\n" + text
            if getattr(node, "name", "") == "_cached_model_info":
                text = "@functools.lru_cache(maxsize=512)\n" + text
            if getattr(node, "name", "") == "_read_value":
                text = text.replace('        length = int(_unpack(handle, "<Q"))', '        if element_type == 9:\n            raise GGUFMetadataError("Nested GGUF arrays are unsupported.")\n        length = int(_unpack(handle, "<Q"))')
                text = text.replace('        values = [_read_value(handle, element_type, keep=keep) for _ in range(length)]', '        if not keep:\n            for _ in range(length):\n                _read_value(handle, element_type, keep=False)\n            return None\n        values = [_read_value(handle, element_type, keep=True) for _ in range(length)]')
            metadata.append(text)
    (DEST / "gguf_metadata.py").write_text("# Adapted from T8mars at " + COMMIT + "; metadata-only reader.\n" + "\n\n".join(metadata) + "\n", encoding="utf-8")
    original = (SOURCE / "yue2.py").read_text(encoding="utf-8")
    names = {"SOURCE_COMMIT", "SOURCE_ROOT", "AUTO", "GENERATE", "PRESERVE", "EDIT", "INSTRUMENTAL",
             "LYRIC_MODES", "COT_MODES", "STANDARD", "REVIEW", "ABC_KEEP", "ABC_STRIP", "ABC_GENERATE",
             "ABC_DOWNSTREAM", "SECTION_RE", "RUBRIC", "REVIEW_SCHEMA", "LYRIC_SYSTEM", "STYLE_SYSTEM", "ABC_SYSTEM"}
    pieces = ["from __future__ import annotations\nimport hashlib\nimport json\nimport math\nimport re\nfrom pathlib import Path\nfrom typing import Any\nfrom contextlib import nullcontext\nfrom . import yue2_abc\n",
              'API_KEY_PATTERN = re.compile(r"(?:sk-|sk_)[A-Za-z0-9_-]{16,}")\n']
    for node in ast.parse(original).body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in names for t in node.targets):
            text = ast.get_source_segment(original, node)
            if any(isinstance(t, ast.Name) and t.id == "STYLE_SYSTEM" for t in node.targets):
                text = text.replace('Return ONLY {"style":"..."}. Write a compact, coherent musical description in style_language:',
                    'Return ONLY a JSON object with one key, "style", whose value is your actual musical description.\n'
                    'Write the finished description, never a placeholder or ellipsis. Use the language specified in brief.style_language.\n'
                    'Describe the following as a compact, coherent paragraph:')
            pieces.append(text)
        elif isinstance(node, ast.ClassDef) and node.name == "YuE2PromptError":
            pieces.append(ast.get_source_segment(original, node))
        elif isinstance(node, ast.FunctionDef):
            text = ast.get_source_segment(original, node)
            if node.name == "enhance_yue2_prompt":
                text = text.replace("def enhance_yue2_prompt(*, session=None, **kwargs)", "def enhance_yue2_prompt(*, runner, **kwargs)")
                text = text.replace('    values = {**DEFAULTS, **kwargs}', '    values = {**DEFAULTS, **kwargs}\n    if "lyrics" in runner.fixed_fields:\n        values["lyrics"] = runner.fixed_fields["lyrics"]\n        values["lyrics_mode"] = INSTRUMENTAL if runner.fixed_fields.get("instrumental") and not values["lyrics"].strip() else PRESERVE')
                text = text.replace("with ExitStack() as stack:\n        runner = YuE2Runner(values, stack, session)", "with nullcontext():")
                text = text.replace('        style = _field(runner.complete("style",', '        runner.publish(lyrics=lyrics, instrumental=mode == INSTRUMENTAL)\n        style = _field(runner.complete("style",')
                text = text.replace("        review = None", '        if "###" in style or language_mismatch(style, values["style_language"]):\n            raise YuE2PromptError("曲风语言或格式不正确，请重试曲风创作。")\n        runner.publish(lyrics=lyrics, style=style)\n        review = None')
                text = text.replace('style = _field(runner.complete("style",', 'style = runner.fixed_fields["style"] if "style" in runner.fixed_fields else _field(runner.complete("style",')
                text = text.replace("except (YuE2PromptError, LocalQwenProviderError) as exc:", "except YuE2PromptError as exc:")
                text = text.replace('        if not abc and not str(values["abc"]).strip()', '        runner.publish(lyrics=lyrics, style=style, review=review)\n        if not abc and not str(values["abc"]).strip()')
                # Node-specific validation/provider wiring lives after the pure function.
            pieces.append(text)
    # Functions are bounded to the source's module-level text engine (node methods are not copied).
    defaults = {
        "music_idea": "", "lyrics_mode": "AUTO（有词保留，无词创作）", "lyrics_language": "中文", "lyrics": "",
        "cot": "full", "quality_mode": "标准 / Standard", "seed": 0, "style_language": "English",
        "structure": "", "genre": "", "vocal": "", "instruments": "", "bpm": 0, "meter": "AUTO", "key_scale": "",
        "constraints": "", "target_duration_seconds": 0, "creativity": "balanced", "edit_section": "Chorus",
        "edit_occurrence": 1, "edit_request": "", "abc": "", "abc_action": "保留 / Preserve",
        "abc_source": "交给下游 YuE2 规划（ABC 留空）/ Downstream", "yue2_seed": 831001, "song_id": "song", "cfg_scale": -1.0,
    }
    pieces.append("DEFAULTS = " + repr(defaults))
    (DEST / "engine.py").write_text("# Adapted from T8mars at " + COMMIT + "; owner-authorized local-kit integration.\n" + "\n\n".join(pieces) + "\n", encoding="utf-8")
    (DEST / "SOURCE.json").write_text(json.dumps({"repository": "https://github.com/T8mars/comfyui-minimax-h3-prompt-enhancer-T8", "commit": COMMIT, "adaptations": ["pure text rules only", "injected runner", "checkpoint validated text", "standalone defaults"], "authorization": "Project owner requested integration into the local YuE2 kit."}, indent=2) + "\n", encoding="utf-8")
    print(DEST)


if __name__ == "__main__":
    main()
