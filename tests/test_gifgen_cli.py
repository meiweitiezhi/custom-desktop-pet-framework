"""tools/local/gifgen.py 壳子的纯逻辑测试（tmpdir，不写任何真实素材目录）。

gifgen 本体放在 tools/local/（gitignore 区，仅本地存在），本测试文件随
tests/ 入库；环境里没有该壳子时整体 skip，保证别处 CI 依旧全绿。
"""
import importlib.util
import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

_GIFGEN = pathlib.Path(__file__).resolve().parents[1] / "tools" / "local" / "gifgen.py"
if _GIFGEN.exists():
    _spec = importlib.util.spec_from_file_location("gifgen_local", _GIFGEN)
    gifgen = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(gifgen)
else:  # pragma: no cover - 只有离开本机才会走到
    gifgen = None


@unittest.skipIf(gifgen is None, "tools/local/gifgen.py 仅本地存在")
class TestPathsAndBase(unittest.TestCase):
    def test_preview_and_install_paths(self):
        root = pathlib.Path(tempfile.mkdtemp())
        self.assertEqual(
            gifgen.preview_path(root, "laugh"),
            root / "assets" / "raw" / "drafts" / "laugh.gif")
        self.assertEqual(
            gifgen.raw_gif_path(root, "angry"),
            root / "assets" / "raw" / "angry.gif")

    def test_base_mapping_and_dance_guard(self):
        root = pathlib.Path("/nonexistent-root-xyz")
        self.assertEqual(gifgen.base_image_path(root, "laugh"),
                         root / "assets" / "states" / "laugh.png")
        # angry 没有真怒脸：用 idle 立绘做占位演出
        self.assertEqual(gifgen.base_image_path(root, "angry"),
                         root / "assets" / "states" / "idle.png")
        with self.assertRaises(SystemExit):
            gifgen.base_image_path(root, "dance")  # dance 已有真母带


@unittest.skipIf(gifgen is None, "tools/local/gifgen.py 仅本地存在")
class TestLoadRecipeOverrides(unittest.TestCase):
    def test_overrides_replace_and_keep_singleton_clean(self):
        steps_before = json.dumps(gifgen.RECIPES["laugh"].steps,
                                  ensure_ascii=False, sort_keys=True)
        r = gifgen.load_recipe(
            "laugh",
            fps=20, cycles=3,
            overrides_json=json.dumps({"steps": [{"op": "hold", "beats": 4}]}))
        self.assertEqual(r.steps, [{"op": "hold", "beats": 4}])
        self.assertEqual(r.fps, 20)
        self.assertEqual(r.cycles, 3)
        # 内置单例绝不能被批量生成过程污染
        steps_after = json.dumps(gifgen.RECIPES["laugh"].steps,
                                 ensure_ascii=False, sort_keys=True)
        self.assertEqual(steps_before, steps_after)

    def test_bad_json_and_unknown_op_exit(self):
        with self.assertRaises(SystemExit):
            gifgen.load_recipe("laugh", overrides_json="{not json")
        with self.assertRaises(SystemExit):
            gifgen.load_recipe("laugh", overrides_json='{"steps":[{"op":"fly"}]}')
        with self.assertRaises(SystemExit):
            gifgen.load_recipe("no-such-state")


if __name__ == "__main__":
    unittest.main()
