from __future__ import annotations

import shutil
from contextlib import contextmanager
from itertools import combinations
from pathlib import Path
from uuid import uuid4
from unittest.mock import MagicMock, patch

import pytest

from installer.games.handlers.base_handler import BaseGameHandler
from installer.games.handlers.install_precheck import (
    RESHADE_COMPAT_DLL_NAME,
    RESHADE_INSTALL_MODE_ALREADY_MIGRATED,
    RESHADE_INSTALL_MODE_INVALID_MULTIPLE,
    RESHADE_INSTALL_MODE_MIGRATE,
)
from installer.install import services as installer_services
from installer.install.workflow import InstallContext, InstallWorkflowCallbacks, build_install_context, run_install_workflow

_TEST_TMP_ROOT = Path(__file__).resolve().parents[1] / "codex_test_tmp"
_RESHADE_SOURCE_DLL_NAMES = (
    "dxgi.dll",
    "d3d12.dll",
    "d3d11.dll",
    "d3d10.dll",
    "d3d9.dll",
    "dinput8.dll",
    "version.dll",
    "winmm.dll",
)
_PROXY_DLL_NAMES = tuple(sorted(installer_services.OPTISCALER_PROXY_DLL_NAMES, key=str.lower))
_UNREAL_NON_BLOCKING_DLL_NAMES = tuple(
    sorted(
        (
            set(_RESHADE_SOURCE_DLL_NAMES)
            | set(_PROXY_DLL_NAMES)
            | {RESHADE_COMPAT_DLL_NAME}
        )
        - {"dxgi.dll"},
        key=str.lower,
    )
)


@contextmanager
def _temp_game_dir():
    _TEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)
    temp_dir = _TEST_TMP_ROOT / f"tmp_{uuid4().hex}"
    temp_dir.mkdir()
    try:
        yield temp_dir
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


@contextmanager
def _service_tempdir():
    with _temp_game_dir() as temp_dir:
        yield str(temp_dir)


def _make_game_data(tmp_path: Path, **overrides) -> dict[str, str]:
    game_data = {
        "path": str(tmp_path),
        "game_name": "Test Game",
        "dll_name": "dxgi.dll",
        "ini_settings": {},
    }
    game_data.update(overrides)
    return game_data


class _NoOpHandler:
    def finalize_install(self, app, game_data, target_path, logger) -> None:
        return None


def _make_callbacks() -> InstallWorkflowCallbacks:
    def _install_base_payload(source_archive: str, target_path: str, final_dll_name: str, exclude_patterns: list[str], logger) -> None:
        Path(target_path).mkdir(parents=True, exist_ok=True)
        (Path(target_path) / "OptiScaler.ini").write_text("[Main]\n", encoding="utf-8")

    return InstallWorkflowCallbacks(
        install_base_payload=_install_base_payload,
        apply_optional_ingame_ini_settings=lambda *args, **kwargs: None,
        apply_optional_engine_ini_settings=lambda *args, **kwargs: None,
        install_fsr4_dll=lambda *args, **kwargs: None,
    )


class TestReShadePrecheck:
    @pytest.mark.parametrize("source_name", _RESHADE_SOURCE_DLL_NAMES)
    def test_single_detected_reshade_source_is_marked_for_migration(self, source_name):
        with _temp_game_dir() as tmp_path:
            (tmp_path / source_name).write_bytes(b"reshade")
            handler = BaseGameHandler()

            with patch(
                "installer.games.handlers.install_precheck.installer_services.read_windows_version_strings",
                return_value={"ProductName": "ReShade"},
            ):
                result = handler.run_install_precheck(
                    _make_game_data(tmp_path, dll_name=source_name),
                    False,
                    MagicMock(),
                )

        assert result.ok is True
        assert result.resolved_dll_name == source_name
        assert result.reshade_install_mode == RESHADE_INSTALL_MODE_MIGRATE
        assert result.reshade_source_dll_name == source_name

    @pytest.mark.parametrize(
        "source_name",
        tuple(name for name in _RESHADE_SOURCE_DLL_NAMES if name.lower() != "dxgi.dll"),
    )
    def test_non_dxgi_reshade_does_not_block_preferred_dxgi_when_dxgi_is_free(self, source_name):
        with _temp_game_dir() as tmp_path:
            (tmp_path / source_name).write_bytes(b"reshade")
            handler = BaseGameHandler()

            with patch(
                "installer.games.handlers.install_precheck.installer_services.read_windows_version_strings",
                return_value={"ProductName": "ReShade"},
            ):
                result = handler.run_install_precheck(
                    _make_game_data(tmp_path, dll_name="dxgi.dll"),
                    False,
                    MagicMock(),
                )

        assert result.ok is True
        assert result.resolved_dll_name == "dxgi.dll"
        assert result.reshade_install_mode == RESHADE_INSTALL_MODE_MIGRATE
        assert result.reshade_source_dll_name == source_name

    def test_already_migrated_reshade_is_detected(self):
        with _temp_game_dir() as tmp_path:
            (tmp_path / RESHADE_COMPAT_DLL_NAME).write_bytes(b"reshade")
            handler = BaseGameHandler()

            with patch(
                "installer.games.handlers.install_precheck.installer_services.read_windows_version_strings",
                return_value={"ProductName": "ReShade"},
            ):
                result = handler.run_install_precheck(_make_game_data(tmp_path), False, MagicMock())

        assert result.ok is True
        assert result.reshade_install_mode == RESHADE_INSTALL_MODE_ALREADY_MIGRATED
        assert result.reshade_source_dll_name == ""

    @pytest.mark.parametrize(
        ("first_name", "second_name"),
        tuple(combinations(_RESHADE_SOURCE_DLL_NAMES, 2))
        + tuple((RESHADE_COMPAT_DLL_NAME, name) for name in _RESHADE_SOURCE_DLL_NAMES),
    )
    def test_multiple_reshade_dlls_block_install(self, first_name, second_name):
        with _temp_game_dir() as tmp_path:
            (tmp_path / first_name).write_bytes(b"reshade")
            (tmp_path / second_name).write_bytes(b"reshade")
            handler = BaseGameHandler()

            with patch(
                "installer.games.handlers.install_precheck.installer_services.read_windows_version_strings",
                return_value={"ProductName": "ReShade"},
            ):
                result = handler.run_install_precheck(_make_game_data(tmp_path), False, MagicMock())

        assert result.ok is False
        assert result.error_code == "reshade_invalid_multiple"
        assert result.reshade_install_mode == RESHADE_INSTALL_MODE_INVALID_MULTIPLE


class TestReShadeProxyResolution:
    @pytest.mark.parametrize("preferred_name", _PROXY_DLL_NAMES)
    def test_build_install_context_keeps_proxy_name_when_matching_reshade_will_be_migrated(self, preferred_name):
        with _temp_game_dir() as tmp_path:
            (tmp_path / preferred_name).write_bytes(b"reshade")

            install_ctx = build_install_context(
                app=None,
                game_data=_make_game_data(tmp_path, dll_name=preferred_name),
                source_archive="archive.zip",
                resolved_dll_name=preferred_name,
                fsr4_source_archive="",
                fsr4_required=False,
                ual_detected_names=(),
                reshade_install_mode=RESHADE_INSTALL_MODE_MIGRATE,
                reshade_source_dll_name=preferred_name,
            )

        assert install_ctx.final_dll_name == preferred_name

    @pytest.mark.parametrize(
        ("preferred_name", "expected_name"),
        (
            ("dxgi.dll", "winmm.dll"),
            ("d3d12.dll", "winmm.dll"),
            ("dbghelp.dll", "winmm.dll"),
            ("wininet.dll", "winmm.dll"),
            ("winhttp.dll", "winmm.dll"),
            ("winmm.dll", "version.dll"),
            ("version.dll", "winmm.dll"),
        ),
    )
    def test_build_install_context_falls_back_when_preferred_proxy_is_occupied(self, preferred_name, expected_name):
        with _temp_game_dir() as tmp_path:
            (tmp_path / preferred_name).write_bytes(b"occupied")

            install_ctx = build_install_context(
                app=None,
                game_data=_make_game_data(tmp_path, dll_name=preferred_name),
                source_archive="archive.zip",
                resolved_dll_name=preferred_name,
                fsr4_source_archive="",
                fsr4_required=False,
                ual_detected_names=(),
            )

        assert install_ctx.final_dll_name == expected_name


class TestOptiScalerDllMatrix:
    @pytest.mark.parametrize("dll_name", installer_services.OPTISCALER_BACKUP_DLL_NAMES)
    def test_backup_existing_optiscaler_dlls_handles_all_managed_names(self, dll_name):
        with _temp_game_dir() as tmp_path:
            managed_path = tmp_path / dll_name
            managed_path.write_bytes(b"optiscaler managed payload")

            installer_services.backup_existing_optiscaler_dlls(str(tmp_path))

            backup_path = tmp_path / f"old_opti_{dll_name}"
            assert not managed_path.exists()
            assert backup_path.exists()
            assert backup_path.read_bytes() == b"optiscaler managed payload"


class TestReShadeWorkflow:
    @pytest.mark.parametrize("source_name", _RESHADE_SOURCE_DLL_NAMES)
    def test_prepare_reshade_for_optiscaler_renames_any_supported_source_dll(self, source_name):
        with _temp_game_dir() as tmp_path:
            source_path = tmp_path / source_name
            source_path.write_bytes(b"reshade")

            result = installer_services.prepare_reshade_for_optiscaler(
                str(tmp_path),
                install_mode=RESHADE_INSTALL_MODE_MIGRATE,
                source_dll_name=source_name,
            )

            assert result is True
            assert not source_path.exists()
            assert (tmp_path / RESHADE_COMPAT_DLL_NAME).read_bytes() == b"reshade"

    def test_run_install_workflow_upserts_load_reshade(self):
        with _temp_game_dir() as tmp_path:
            (tmp_path / "dxgi.dll").write_bytes(b"reshade")
            install_ctx = InstallContext(
                handler=_NoOpHandler(),
                game_data=_make_game_data(tmp_path),
                source_archive="archive.zip",
                target_path=str(tmp_path),
                use_ultimate_asi_loader=False,
                final_dll_name="dxgi.dll",
                fsr4_source_archive="",
                fsr4_required=False,
                reshade_install_mode=RESHADE_INSTALL_MODE_MIGRATE,
                reshade_source_dll_name="dxgi.dll",
            )

            with (
                patch("installer.install.workflow.install_reframework_dinput8", return_value=None),
                patch("installer.install.workflow.install_optipatcher", return_value={}),
                patch("installer.install.workflow.install_unreal5_patch", return_value=None),
                patch("installer.install.workflow.rtss_notice.apply_rtss_global_settings_if_needed", return_value=None),
            ):
                installed_game = run_install_workflow(
                    app=None,
                    install_ctx=install_ctx,
                    module_download_links={},
                    optipatcher_url="",
                    gpu_info="",
                    callbacks=_make_callbacks(),
                    logger=MagicMock(),
                )

            ini_text = (tmp_path / "OptiScaler.ini").read_text(encoding="utf-8")
            assert "LoadReshade=true" in ini_text
            assert (tmp_path / RESHADE_COMPAT_DLL_NAME).exists()
            assert installed_game["__installed_proxy_name__"] == "dxgi.dll"


class TestUnrealDllMatrix:
    def test_unreal_patch_is_blocked_when_dxgi_exists(self):
        with _temp_game_dir() as target_dir, _temp_game_dir() as archive_dir:
            archive_path = archive_dir / "unreal.zip"
            archive_path.write_bytes(b"cached archive")
            (target_dir / "dxgi.dll").write_bytes(b"existing dxgi")

            with (
                patch("installer.install.services.tempfile.TemporaryDirectory", side_effect=_service_tempdir),
                patch("installer.install.services.extract_archive") as extract_archive,
            ):
                result = installer_services.install_unreal5_from_url(
                    "http://example.com/unreal.zip",
                    str(target_dir),
                    cached_archive_path=str(archive_path),
                )

            assert result is False
            extract_archive.assert_not_called()

    @pytest.mark.parametrize("existing_name", _UNREAL_NON_BLOCKING_DLL_NAMES)
    def test_unreal_patch_is_not_blocked_by_other_dll_names(self, existing_name):
        with _temp_game_dir() as target_dir, _temp_game_dir() as archive_dir:
            archive_path = archive_dir / "unreal.zip"
            archive_path.write_bytes(b"cached archive")
            (target_dir / existing_name).write_bytes(b"existing other dll")

            def _fake_extract(_src: str, dst: str, logger=None):
                Path(dst, "unreal_patch.marker").write_text("installed", encoding="utf-8")

            with (
                patch("installer.install.services.tempfile.TemporaryDirectory", side_effect=_service_tempdir),
                patch("installer.install.services.extract_archive", side_effect=_fake_extract),
            ):
                result = installer_services.install_unreal5_from_url(
                    "http://example.com/unreal.zip",
                    str(target_dir),
                    cached_archive_path=str(archive_path),
                )

            assert result is True
            assert (target_dir / "unreal_patch.marker").exists()
