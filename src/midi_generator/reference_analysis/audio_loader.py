"""Optional audio dependencies and bounded, read-only decoding."""
from dataclasses import dataclass
import hashlib
import math
from pathlib import Path
from typing import Any


class AudioAnalysisError(ValueError):
    """An actionable input/dependency/analysis error for CLI and Python callers."""


def audio_dependencies():
    try:
        import numpy as np
        import soundfile as sf
        import librosa
    except (ImportError, OSError) as exc:
        raise AudioAnalysisError(
            "Audio analysis dependencies are unavailable. Install with "
            "python -m pip install -e '.[audio]'. " + str(exc)
        ) from exc
    return np, sf, librosa


@dataclass
class LoadedAudio:
    samples: Any
    sample_rate: int
    source: dict
    analysis_range: dict
    warnings: list[str]


def load_audio(audio_path, *, start_seconds=0.0, end_seconds=None, analysis_sample_rate=22050):
    path = Path(audio_path)
    if not path.is_file():
        raise FileNotFoundError(f"Reference audio not found: {path}")
    if path.suffix.lower() not in {".wav", ".wave", ".mp3", ".flac"}:
        raise AudioAnalysisError("Supported reference audio extensions: WAV, MP3, FLAC")
    for name, value in (("start_seconds", start_seconds), ("end_seconds", end_seconds)):
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)):
            raise AudioAnalysisError(f"{name} must be a finite number of seconds")
    if start_seconds is None or start_seconds < 0:
        raise AudioAnalysisError("start_seconds must be non-negative")
    np, sf, librosa = audio_dependencies()
    warnings = []
    try:
        with sf.SoundFile(path, mode="r") as audio:
            source_rate, channels, total_frames = audio.samplerate, audio.channels, audio.frames
            duration = total_frames / source_rate
            end = duration if end_seconds is None else end_seconds
            if start_seconds >= end:
                raise AudioAnalysisError("start_seconds must be less than end_seconds")
            if end > duration + 1e-9 or start_seconds >= duration:
                raise AudioAnalysisError(f"Analysis range exceeds audio duration ({duration:.6f} seconds)")
            start_frame = math.floor(start_seconds * source_rate)
            end_frame = min(total_frames, math.ceil(end * source_rate))
            if end_frame <= start_frame:
                raise AudioAnalysisError("Analysis range has no audio samples")
            audio.seek(start_frame)
            data = audio.read(end_frame - start_frame, dtype="float64", always_2d=True)
            audio_format, subtype = audio.format.lower(), audio.subtype
    except AudioAnalysisError:
        raise
    except (RuntimeError, OSError) as exc:
        hint = " MP3 decoding requires an MP3-capable libsndfile; install current soundfile wheels or convert to WAV." if path.suffix.lower() == ".mp3" else ""
        raise AudioAnalysisError(f"Cannot decode reference audio {path}: {exc}.{hint}") from exc
    if not len(data):
        raise AudioAnalysisError("Audio decoder returned no samples")
    if not np.all(np.isfinite(data)):
        raise AudioAnalysisError("Decoded audio contains NaN or Infinity samples")
    mono = np.mean(data, axis=1)
    if channels > 1 and np.max(np.abs(data)) > 1e-6 and np.max(np.abs(mono)) < 1e-8:
        warnings.append("Stereo downmix cancelled the signal; inspect channel polarity before interpreting silence.")
    if source_rate != analysis_sample_rate:
        mono = librosa.resample(mono, orig_sr=source_rate, target_sr=analysis_sample_rate, res_type="polyphase")
    digest = hashlib.sha256()
    with path.open("rb") as source_file:
        for block in iter(lambda: source_file.read(1024 * 1024), b""):
            digest.update(block)
    start, end = start_frame / source_rate, (start_frame + len(data)) / source_rate
    return LoadedAudio(
        samples=mono, sample_rate=analysis_sample_rate,
        source={"path": path.as_posix(), "absolute_path": str(path.resolve()), "format": audio_format,
                "duration_seconds": duration, "sample_rate": source_rate, "channels": channels,
                "subtype": subtype, "size_bytes": path.stat().st_size, "sha256": digest.hexdigest(),
                "source_id": "sha256:" + digest.hexdigest(), "license": None},
        analysis_range={"start_seconds": start, "end_seconds": end, "duration_seconds": end - start,
                        "requested_start_seconds": start_seconds, "requested_end_seconds": end_seconds},
        warnings=warnings,
    )
