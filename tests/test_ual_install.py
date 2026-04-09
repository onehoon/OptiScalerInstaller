"""Tests for UAL auto-detection install logic."""
from __future__ import annotations

from contextlib import contextmanager
import shutil
import zipfile
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest

from installer.install.components.ultimate_asi_loader import (
    OPTISCALER_ASI_NAME,
    ULTIMATE_ASI_LOADER_DLL_NAME,
    _resolve_ual_representative_name,
    install_ultimate_asi_loader,
)
from installer.install.workflow import build_install_context
from installer.install.services import OPTISCALER_BACKUP_DLL_NAMES, OPTISCALER_LEGACY_REMOVE_NAMES


_TEST_TMP_ROOT = Path(__file__).resolve().parents[1] / "codex_test_tmp"
_UAL_DETECTED_DLL_NAMES = (
    "dxgi.dll",
    "d3d12.dll",
    "d3d11.dll",
    "d3d10.dll",
    "d3d9.dll",
    "dinput8.dll",
    "version.dll",
    "winmm.dll",
)
_NON_DINPUT8_UAL_NAMES = tuple(name for name in _UAL_DETECTED_DLL_NAMES if name.lower() != "dinput8.dll")


@contextmanager
def _temp_dir():
    _TEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)
    temp_dir = _TEST_TMP_ROOT / f"tmp_{uuid4().hex}"
    temp_dir.mkdir()
    try:
        yield temp_dir
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


@contextmanager
def _component_tempdir():
    with _temp_dir() as temp_dir:
        yield str(temp_dir)


def _make_archive_with_dinput8(root: Path, content: bytes = b"UAL binary") -> Path:
    archive_path = root / "ual.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("dinput8.dll", content)
    return archive_path


def _game_data(target_path: Path, **extra) -> dict[str, str]:
    data = {
        "path": str(target_path),
        "game_name": "TestGame",
        "dll_name": "dxgi.dll",
    }
    data.update(extra)
    return data


class TestResolveUalRepresentativeName:
    @pytest.mark.parametrize("name", _UAL_DETECTED_DLL_NAMES)
    def test_single_name_returns_same_name(self, name):
        assert _resolve_ual_representative_name((name,)) == name

    @pytest.mark.parametrize("name", _NON_DINPUT8_UAL_NAMES)
    def test_dinput8_wins_over_any_other_detected_name(self, name):
        detected = (name, "dinput8.dll")
        assert _resolve_ual_representative_name(detected) == "dinput8.dll"

    @pytest.mark.parametrize(
        ("detected", "expected"),
        (
            (("winmm.dll", "version.dll"), "version.dll"),
            (("dxgi.dll", "d3d12.dll"), "d3d12.dll"),
            (("d3d11.dll", "d3d10.dll"), "d3d10.dll"),
        ),
    )
    def test_non_dinput8_pairs_use_sorted_representative(self, detected, expected):
        assert _resolve_ual_representative_name(detected) == expected

    def test_empty_falls_back_to_default_constant(self):
        assert _resolve_ual_representative_name(()) == ULTIMATE_ASI_LOADER_DLL_NAME


class TestInstallUltimateAsiLoaderAutoDetect:
    @pytest.mark.parametrize("detected_name", _UAL_DETECTED_DLL_NAMES)
    def test_single_detected_dll_name_is_updated_in_place(self, detected_name):
        with _temp_dir() as target_dir, _temp_dir() as archive_dir:
            archive_path = _make_archive_with_dinput8(archive_dir)
            existing_path = target_dir / detected_name
            existing_path.write_bytes(b"old content")

            with patch(
                "installer.install.components.ultimate_asi_loader.tempfile.TemporaryDirectory",
                side_effect=_component_tempdir,
            ):
                install_ultimate_asi_loader(
                    str(target_dir),
                    {},
                    ual_detected_names=(detected_name,),
                    cached_archive_path=str(archive_path),
                )

            representative_path = target_dir / detected_name
            assert representative_path.exists()
            assert representative_path.read_bytes() == b"UAL binary"
            if detected_name.lower() != "dinput8.dll":
                assert not (target_dir / "dinput8.dll").exists()

    @pytest.mark.parametrize("other_name", _NON_DINPUT8_UAL_NAMES)
    def test_dinput8_is_kept_as_representative_when_present_in_multi_detection(self, other_name):
        with _temp_dir() as target_dir, _temp_dir() as archive_dir:
            archive_path = _make_archive_with_dinput8(archive_dir)
            dinput8_path = target_dir / "dinput8.dll"
            other_path = target_dir / other_name
            dinput8_path.write_bytes(b"old dinput8")
            other_path.write_bytes(b"old other")

            with patch(
                "installer.install.components.ultimate_asi_loader.tempfile.TemporaryDirectory",
                side_effect=_component_tempdir,
            ):
                install_ultimate_asi_loader(
                    str(target_dir),
                    {},
                    ual_detected_names=("dinput8.dll", other_name),
                    cached_archive_path=str(archive_path),
                )

            assert dinput8_path.exists()
            assert dinput8_path.read_bytes() == b"UAL binary"
            assert not other_path.exists()

    def test_no_link_and_no_cache_skips_without_error(self):
        with _temp_dir() as target_dir:
            existing_path = target_dir / "dinput8.dll"
            existing_path.write_bytes(b"old ual")
            install_ultimate_asi_loader(
                str(target_dir),
                {},
                ual_detected_names=("dinput8.dll",),
            )
            assert existing_path.exists()
            assert existing_path.read_bytes() == b"old ual"


class TestBuildInstallContextUal:
    @pytest.mark.parametrize("detected_name", _UAL_DETECTED_DLL_NAMES)
    def test_ual_auto_detected_forces_optiscaler_asi_name_for_all_detected_names(self, detected_name):
        with _temp_dir() as target_dir:
            ctx = build_install_context(
                app=None,
                game_data=_game_data(target_dir),
                source_archive="archive.zip",
                resolved_dll_name="dxgi.dll",
                fsr4_source_archive="",
                fsr4_required=False,
                ual_detected_names=(detected_name,),
            )

        assert ctx.final_dll_name == OPTISCALER_ASI_NAME
        assert ctx.use_ultimate_asi_loader is True
        assert ctx.ual_detected_names == (detected_name,)

    def test_ual_sheet_flag_uses_resolved_dll_name(self):
        with _temp_dir() as target_dir:
            ctx = build_install_context(
                app=None,
                game_data=_game_data(target_dir, ultimate_asi_loader=True),
                source_archive="archive.zip",
                resolved_dll_name="OptiScaler.asi",
                fsr4_source_archive="",
                fsr4_required=False,
                ual_detected_names=(),
            )

        assert ctx.final_dll_name == "OptiScaler.asi"
        assert ctx.use_ultimate_asi_loader is True
        assert ctx.ual_detected_names == ()

    def test_no_ual_detected_and_no_flag_uses_proxy_resolution(self):
        with _temp_dir() as target_dir:
            ctx = build_install_context(
                app=None,
                game_data=_game_data(target_dir, dll_name="dxgi.dll"),
                source_archive="archive.zip",
                resolved_dll_name="",
                fsr4_source_archive="",
                fsr4_required=False,
                ual_detected_names=(),
            )

        assert ctx.use_ultimate_asi_loader is False
        assert ctx.final_dll_name == "dxgi.dll"


class TestOptiScalerAsiServiceConstants:
    def test_optiscaler_asi_in_backup_names(self):
        assert "OptiScaler.asi" in OPTISCALER_BACKUP_DLL_NAMES

    def test_optiscaler_asi_not_in_legacy_remove_names(self):
        assert "OptiScaler.asi" not in OPTISCALER_LEGACY_REMOVE_NAMES

    def test_nvapi64_still_in_legacy_remove(self):
        assert "nvapi64.dll" in OPTISCALER_LEGACY_REMOVE_NAMES

    def test_nvngx_still_in_legacy_remove(self):
        assert "nvngx.dll" in OPTISCALER_LEGACY_REMOVE_NAMES
