from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any


class VoiceTranscriber:
    """Lazily loads Whisper and serializes CPU-heavy transcription calls."""

    def __init__(
        self,
        *,
        model_name: str,
        device: str,
        compute_type: str,
        cache_dir: str,
        model_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.model_name = model_name
        self.device = device
        self.compute_type = compute_type
        self.cache_dir = cache_dir
        self._model_factory = model_factory
        self._model: Any = None
        self._lock = asyncio.Lock()

    async def transcribe(self, path: str | Path) -> str:
        async with self._lock:
            if self._model is None:
                self._model = await asyncio.to_thread(self._load_model)
            return await asyncio.to_thread(self._transcribe_sync, str(path))

    def _load_model(self) -> Any:
        factory = self._model_factory
        if factory is None:
            from faster_whisper import WhisperModel

            factory = WhisperModel
        return factory(
            self.model_name,
            device=self.device,
            compute_type=self.compute_type,
            download_root=self.cache_dir,
        )

    def _transcribe_sync(self, path: str) -> str:
        segments, _ = self._model.transcribe(path, beam_size=1, vad_filter=True)
        return " ".join(segment.text.strip() for segment in segments if segment.text.strip())
