from __future__ import annotations

import hashlib
import io
import logging
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional
from urllib.parse import quote, urlparse

import requests
from PIL import Image, ImageOps
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .. import app_update
from .cover_utils import normalize_cover_filename


@dataclass(slots=True)
class PosterLoaderConfig:
    cache_dir: Path
    assets_dir: Path
    default_poster_candidates: tuple[Path, ...]
    target_width: int
    target_height: int
    repo_raw_base_url: str = ""
    bundled_cover_filename_map: Mapping[str, str] = field(default_factory=dict)
    timeout_seconds: int = 10
    max_retries: int = 3
    cache_version: int = 2
    enable_memory_cache: bool = True
    memory_cache_max: int = 100


@dataclass(frozen=True, slots=True)
class PosterLoadResult:
    image: Image.Image
    is_default: bool
    should_retry: bool


@contextmanager
def _temporary_logger_level(logger_names: tuple[str, ...], level: int):
    previous_levels: list[tuple[logging.Logger, int]] = []
    try:
        for logger_name in logger_names:
            logger = logging.getLogger(logger_name)
            previous_levels.append((logger, logger.level))
            logger.setLevel(level)
        yield
    finally:
        for logger, previous_level in reversed(previous_levels):
            logger.setLevel(previous_level)


def _make_default_poster_base(width: int, height: int) -> Image.Image:
    """Build a minimal fallback poster when no bundled asset exists."""
    return Image.new("RGB", (width, height), "#12161d")


def _load_default_poster_base(default_poster_path: Path, width: int, height: int) -> Image.Image:
    """Load the default poster from disk, creating it if it's missing or corrupt."""
    if default_poster_path.exists():
        try:
            return Image.open(default_poster_path).convert("RGB").resize((width, height), Image.Resampling.LANCZOS)
        except Exception:
            logging.warning("Failed to read/decode default poster at %s, will recreate it.", default_poster_path)

    try:
        default_poster_path.parent.mkdir(parents=True, exist_ok=True)
        img = _make_default_poster_base(width, height)
        suffix = default_poster_path.suffix.lower()
        if suffix == ".webp":
            img.save(default_poster_path, format="WEBP", quality=92)
        elif suffix in {".jpg", ".jpeg"}:
            img.save(default_poster_path, format="JPEG", quality=92)
        else:
            img.save(default_poster_path, format="PNG")
        return img
    except Exception as exc:
        logging.warning("Failed to create default poster asset at %s: %s", default_poster_path, exc)
        return _make_default_poster_base(width, height)


def _prepare_cover_image(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """Convert and fit/crop cover art for UI rendering."""
    if img.mode not in {"RGB", "RGBA"}:
        img = img.convert("RGBA")
    else:
        img = img.copy()

    prefit_limit = (max(1, target_w * 2), max(1, target_h * 2))
    if img.width > prefit_limit[0] or img.height > prefit_limit[1]:
        img.thumbnail(prefit_limit, Image.Resampling.LANCZOS)

    img = ImageOps.fit(
        img,
        (target_w, target_h),
        method=Image.Resampling.LANCZOS,
        centering=(0.5, 0.5),
    )
    return img.convert("RGBA")


def _encode_lossless_webp_bytes(image_bytes: bytes) -> Optional[bytes]:
    try:
        with Image.open(io.BytesIO(image_bytes)) as source_img:
            converted = source_img.convert("RGBA")
        output = io.BytesIO()
        converted.save(output, format="WEBP", lossless=True)
        return output.getvalue()
    except Exception:
        return None


class PosterImageLoader:
    def __init__(self, config: PosterLoaderConfig):
        self._config = config
        self._cache_dir = Path(config.cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._assets_dir = Path(config.assets_dir)
        self._default_poster_candidates = tuple(Path(path) for path in config.default_poster_candidates)
        if not self._default_poster_candidates:
            raise ValueError("Poster loader requires at least one default poster candidate path.")

        self._default_poster_path = next(
            (path for path in self._default_poster_candidates if path.exists()),
            self._default_poster_candidates[0],
        )
        self._bundled_cover_filename_map = {
            str(key).casefold(): str(value)
            for key, value in dict(config.bundled_cover_filename_map).items()
        }
        self._repo_raw_base_url = str(config.repo_raw_base_url or "").strip().rstrip("/")
        self._image_cache: dict[str, Image.Image] = {}
        self._image_cache_lock = threading.Lock()
        self._default_poster_base = _load_default_poster_base(
            self._default_poster_path,
            self._config.target_width,
            self._config.target_height,
        )
        self._image_session = self._build_retry_session()

    def close(self) -> None:
        try:
            self._image_session.close()
        except Exception:
            pass

    def make_placeholder_image(self) -> Image.Image:
        return self._default_poster_base.copy().convert("RGBA")

    def load(self, title: str, cover_filename: str, url: str) -> PosterLoadResult:
        normalized_cover_filename = normalize_cover_filename(cover_filename)
        repo_failed = False
        cover_cache_key = ""

        if normalized_cover_filename:
            cover_cache_key = self._poster_cache_key("cover_file", normalized_cover_filename, title=title)

            bundled_cover_path = self._find_bundled_cover_asset(normalized_cover_filename)
            if bundled_cover_path is not None:
                pil_img = self._load_prepared_image_from_path(bundled_cover_path, cover_cache_key)
                if pil_img is not None:
                    return PosterLoadResult(pil_img, False, False)

            disk_cache_path = self._get_cover_cache_path(normalized_cover_filename)
            if disk_cache_path is not None and disk_cache_path.exists():
                pil_img = self._load_prepared_image_from_path(disk_cache_path, cover_cache_key)
                if pil_img is not None:
                    return PosterLoadResult(pil_img, False, False)
                try:
                    disk_cache_path.unlink()
                except OSError:
                    pass

            repo_url = self._build_cover_repo_raw_url(normalized_cover_filename)
            if repo_url:
                try:
                    image_bytes = self._download_image_bytes(repo_url)
                    pil_img = self._load_prepared_image_from_bytes(image_bytes, cover_cache_key)
                    if pil_img is None:
                        raise RuntimeError("Downloaded cover image could not be decoded")
                    try:
                        self._store_cover_cache_bytes(normalized_cover_filename, image_bytes)
                    except Exception:
                        pass
                    return PosterLoadResult(pil_img, False, False)
                except Exception:
                    repo_failed = True

        cache_key = self._poster_cache_key("cover_url", url, title=title)
        cached_image = self._image_cache_get(cache_key) if self._config.enable_memory_cache else None
        if cached_image is not None:
            return PosterLoadResult(cached_image, False, False)

        if not url:
            return PosterLoadResult(self.make_placeholder_image(), True, repo_failed)

        try:
            image_bytes = self._download_image_bytes(url)
            pil_img = self._load_prepared_image_from_bytes(image_bytes, cache_key)
            if pil_img is None:
                raise RuntimeError("Downloaded cover image could not be decoded")
            if normalized_cover_filename:
                webp_lossless_bytes = _encode_lossless_webp_bytes(image_bytes)
                if webp_lossless_bytes:
                    try:
                        self._store_cover_cache_bytes(normalized_cover_filename, webp_lossless_bytes)
                        if self._config.enable_memory_cache and cover_cache_key:
                            self._image_cache_put(cover_cache_key, pil_img)
                    except Exception:
                        pass
            return PosterLoadResult(pil_img, False, False)
        except Exception:
            return PosterLoadResult(self.make_placeholder_image(), True, True)

    def _build_retry_session(self) -> requests.Session:
        session = requests.Session()
        retry = Retry(
            total=self._config.max_retries,
            connect=self._config.max_retries,
            read=self._config.max_retries,
            backoff_factor=0.6,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET", "HEAD"),
        )
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        return session

    def _find_bundled_cover_asset(self, cover_filename: str) -> Optional[Path]:
        normalized = normalize_cover_filename(cover_filename)
        if not normalized:
            return None

        bundled_name = self._bundled_cover_filename_map.get(normalized.casefold())
        if not bundled_name:
            return None

        candidate = self._assets_dir / bundled_name
        if candidate.exists() and candidate.is_file():
            return candidate
        return None

    def _get_cover_cache_path(self, cover_filename: str) -> Optional[Path]:
        normalized = normalize_cover_filename(cover_filename)
        if not normalized:
            return None
        return app_update.resolve_safe_child_path(self._cache_dir, normalized)

    def _build_cover_repo_raw_url(self, cover_filename: str) -> str:
        normalized = normalize_cover_filename(cover_filename)
        if not normalized or not self._repo_raw_base_url:
            return ""
        return f"{self._repo_raw_base_url}/{quote(normalized, safe='')}"

    def _poster_cache_key(self, source_type: str, source_value: str, title: str = "") -> str:
        normalized_source = ""
        raw_value = str(source_value or "").strip()
        if source_type == "cover_url" and raw_value:
            try:
                parsed = urlparse(raw_value)
                normalized_source = f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{parsed.path}".strip().lower()
            except Exception:
                normalized_source = raw_value.lower()
        else:
            normalized_source = raw_value.casefold()

        if not normalized_source:
            normalized_source = str(title or "").strip().casefold() or "unknown"

        cache_source = (
            f"poster|v{self._config.cache_version}|"
            f"{self._config.target_width}x{self._config.target_height}|"
            f"{source_type}|{normalized_source}"
        )
        return hashlib.sha256(cache_source.encode("utf-8")).hexdigest()

    def _load_prepared_image_from_path(self, image_path: Path, cache_key: str) -> Optional[Image.Image]:
        cached_image = self._image_cache_get(cache_key) if self._config.enable_memory_cache else None
        if cached_image is not None:
            return cached_image
        if not image_path.exists() or not image_path.is_file():
            return None

        try:
            with Image.open(image_path) as source_img:
                pil_img = _prepare_cover_image(source_img, self._config.target_width, self._config.target_height)
            if self._config.enable_memory_cache:
                self._image_cache_put(cache_key, pil_img)
            return pil_img
        except Exception:
            return None

    def _load_prepared_image_from_bytes(self, image_bytes: bytes, cache_key: str) -> Optional[Image.Image]:
        cached_image = self._image_cache_get(cache_key) if self._config.enable_memory_cache else None
        if cached_image is not None:
            return cached_image

        try:
            with Image.open(io.BytesIO(image_bytes)) as source_img:
                pil_img = _prepare_cover_image(source_img, self._config.target_width, self._config.target_height)
            if self._config.enable_memory_cache:
                self._image_cache_put(cache_key, pil_img)
            return pil_img
        except Exception:
            return None

    def _download_image_bytes(self, url: str) -> bytes:
        with _temporary_logger_level(("urllib3.connectionpool", "urllib3.util.retry"), logging.ERROR):
            with self._image_session.get(url, timeout=self._config.timeout_seconds, stream=True) as response:
                response.raise_for_status()
                return b"".join(response.iter_content(chunk_size=65536))

    def _store_cover_cache_bytes(self, cover_filename: str, image_bytes: bytes) -> Optional[Path]:
        cache_path = self._get_cover_cache_path(cover_filename)
        if cache_path is None:
            return None

        cache_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = cache_path.with_name(cache_path.name + ".tmp")
        with temp_path.open("wb") as cache_fp:
            cache_fp.write(image_bytes)
        temp_path.replace(cache_path)
        return cache_path

    def _image_cache_get(self, key: str) -> Optional[Image.Image]:
        try:
            with self._image_cache_lock:
                pil_img = self._image_cache.get(key)
                if pil_img is None:
                    return None
                self._image_cache.pop(key, None)
                self._image_cache[key] = pil_img
                return pil_img
        except Exception:
            return None

    def _image_cache_put(self, key: str, pil_img: Image.Image) -> None:
        try:
            with self._image_cache_lock:
                self._image_cache.pop(key, None)
                self._image_cache[key] = pil_img
                if len(self._image_cache) > self._config.memory_cache_max:
                    try:
                        first_key = next(iter(self._image_cache))
                        del self._image_cache[first_key]
                    except Exception:
                        try:
                            self._image_cache.popitem(last=False)
                        except Exception:
                            pass
        except Exception:
            pass


__all__ = [
    "PosterImageLoader",
    "PosterLoaderConfig",
    "PosterLoadResult",
]
