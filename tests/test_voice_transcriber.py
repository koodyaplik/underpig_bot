from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.voice.transcriber import VoiceTranscriber


class FakeWhisperModel:
    def __init__(self, model_name: str, **options: object) -> None:
        self.model_name = model_name
        self.options = options

    def transcribe(self, path: str, **options: object) -> tuple[list[object], object]:
        assert Path(path).name == "voice.ogg"
        assert options == {"beam_size": 1, "vad_filter": True}
        return [SimpleNamespace(text=" Привет "), SimpleNamespace(text="мир")], object()


@pytest.mark.asyncio
async def test_voice_transcriber_loads_model_lazily(tmp_path: Path) -> None:
    path = tmp_path / "voice.ogg"
    path.write_bytes(b"test")
    transcriber = VoiceTranscriber(
        model_name="small",
        device="cpu",
        compute_type="int8",
        cache_dir=str(tmp_path / "models"),
        model_factory=FakeWhisperModel,
    )

    assert await transcriber.transcribe(path) == "Привет мир"
    assert transcriber._model.model_name == "small"
    assert transcriber._model.options["device"] == "cpu"
