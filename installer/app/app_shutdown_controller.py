from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import logging


ShutdownCallback = Callable[[], None]


@dataclass(frozen=True)
class AppShutdownStep:
    name: str
    run: ShutdownCallback


@dataclass(frozen=True)
class AppShutdownCallbacks:
    best_effort_steps: tuple[AppShutdownStep, ...]
    destroy_root: ShutdownCallback


class AppShutdownController:
    def __init__(
        self,
        *,
        callbacks: AppShutdownCallbacks,
        logger=None,
    ) -> None:
        self._callbacks = callbacks
        self._logger = logger or logging.getLogger()

    def shutdown(self) -> None:
        for step in self._callbacks.best_effort_steps:
            try:
                step.run()
            except Exception:
                self._logger.debug("Failed during shutdown step: %s", step.name, exc_info=True)

        self._callbacks.destroy_root()


__all__ = [
    "AppShutdownCallbacks",
    "AppShutdownController",
    "AppShutdownStep",
]
