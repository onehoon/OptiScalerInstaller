from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest


MODULE_PATH = Path(__file__).resolve().parents[1] / "installer" / "app" / "popup_markup.py"
MODULE_NAME = "popup_markup_under_test"
MODULE_SPEC = importlib.util.spec_from_file_location(MODULE_NAME, MODULE_PATH)
if MODULE_SPEC is None or MODULE_SPEC.loader is None:
    raise RuntimeError(f"Unable to load popup markup module from {MODULE_PATH}")

popup_markup = importlib.util.module_from_spec(MODULE_SPEC)
sys.modules[MODULE_NAME] = popup_markup
MODULE_SPEC.loader.exec_module(popup_markup)


class PopupMarkupTests(unittest.TestCase):
    def test_normalize_popup_markup_preserves_existing_newlines(self) -> None:
        self.assertEqual(
            popup_markup.normalize_popup_markup_text("Line 1\nLine 2\r\nLine 3\rLine 4"),
            "Line 1\nLine 2\nLine 3\nLine 4",
        )

    def test_normalize_popup_markup_expands_control_tokens(self) -> None:
        self.assertEqual(
            popup_markup.normalize_popup_markup_text("Title[P][DOT] First[BR][INDENT][dot] Second"),
            "Title\n\n\u2022 First\n   \u2022 Second",
        )

    def test_strip_markup_text_keeps_backward_compatibility(self) -> None:
        self.assertEqual(
            popup_markup.strip_markup_text("Alpha\n[RED]Beta[END][BR][DOT] Gamma"),
            "Alpha\nBeta\n\u2022 Gamma",
        )

    def test_indent_markup_uses_configured_width(self) -> None:
        self.assertEqual(
            popup_markup.strip_markup_text("Line 1[BR][INDENT]Line 2"),
            "Line 1\n   Line 2",
        )


if __name__ == "__main__":
    unittest.main()
