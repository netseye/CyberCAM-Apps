import ast
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


def expected_model_sizes():
    tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if any(isinstance(target, ast.Name) and
               target.id == "MODEL_EXPECTED_BYTES" for target in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError("main.py 缺少 MODEL_EXPECTED_BYTES")


class ModelAssetTests(unittest.TestCase):
    def test_bundled_assets_have_the_exact_validated_sizes(self):
        for filename, expected_bytes in expected_model_sizes().items():
            path = ROOT / filename
            self.assertTrue(path.is_file(), filename)
            self.assertEqual(path.stat().st_size, expected_bytes, filename)


if __name__ == "__main__":
    unittest.main()
