from __future__ import annotations

import os
import unittest
from unittest import mock

from installer import i18n


class I18nTests(unittest.TestCase):
    def test_get_app_strings_returns_expected_language_objects(self) -> None:
        ko = i18n.get_app_strings("ko")
        en = i18n.get_app_strings("en")

        self.assertEqual(ko.common.ok, "확인")
        self.assertEqual(en.common.ok, "OK")
        self.assertNotEqual(ko.main.scan_section_title, en.main.scan_section_title)

    def test_pick_sheet_text_uses_sheet_language_suffix(self) -> None:
        row = {
            "popup_kr": "한국어 팝업",
            "popup_en": "English popup",
        }

        self.assertEqual(i18n.pick_sheet_text(row, "popup", "ko"), "한국어 팝업")
        self.assertEqual(i18n.pick_sheet_text(row, "popup", "en"), "English popup")

    def test_pick_module_message_uses_wrapped_sheet_keys(self) -> None:
        row = {
            "__warning_kr__": "한국어 경고",
            "__warning_en__": "English warning",
        }

        self.assertEqual(i18n.pick_module_message(row, "warning", "ko"), "한국어 경고")
        self.assertEqual(i18n.pick_module_message(row, "warning", "en"), "English warning")

    def test_translate_default_precheck_error_localizes_known_message(self) -> None:
        raw_error = (
            "No available OptiScaler DLL names for installation. "
            "Checked: dxgi.dll, d3d12.dll"
        )

        self.assertEqual(
            i18n.translate_default_precheck_error(raw_error, "ko"),
            "설치에 사용할 수 있는 OptiScaler DLL 이름이 없습니다. 확인한 이름: dxgi.dll, d3d12.dll",
        )
        self.assertEqual(i18n.translate_default_precheck_error(raw_error, "en"), raw_error)

    def test_build_mod_conflict_notice_text_uses_localized_templates(self) -> None:
        ko_notice = i18n.build_mod_conflict_notice_text(["ReShade 관련 파일이 감지되었습니다: dxgi.dll"], "ko")
        en_notice = i18n.build_mod_conflict_notice_text(["ReShade related files were detected: dxgi.dll"], "en")

        self.assertIn("기존 MOD 파일이 감지되었습니다.", ko_notice)
        self.assertIn("Existing MOD files were detected.", en_notice)

    def test_detect_ui_language_honors_forced_env(self) -> None:
        with mock.patch.dict(os.environ, {i18n.UI_LANGUAGE_ENV: "ko"}, clear=False):
            self.assertEqual(i18n.detect_ui_language(), "ko")
        with mock.patch.dict(os.environ, {i18n.UI_LANGUAGE_ENV: "en"}, clear=False):
            self.assertEqual(i18n.detect_ui_language(), "en")


if __name__ == "__main__":
    unittest.main()
