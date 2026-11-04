"""Shared request and result models."""
from dataclasses import dataclass, field
import math
from pathlib import Path
from typing import Any, Mapping


@dataclass(frozen=True)
class GenerationRequest:
    """Common generation context; instrument-specific options live in options."""
    instrument: str
    section: str
    tempo: float
    bars: int
    time_signature: tuple[int, int] = (4, 4)
    key: str | None = None
    chords: tuple[str, ...] = ()
    style: dict[str, Any] | tuple[str, ...] = field(default_factory=dict)
    options: dict[str, Any] = field(default_factory=dict)
    seed: int | None = None
    tags: tuple[str, ...] = ()
    groove: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.instrument, str) or not isinstance(self.section, str):
            raise ValueError("instrument and section must be strings")
        if not self.instrument.strip():
            raise ValueError("instrument must not be empty")
        if not self.section.strip():
            raise ValueError("section must not be empty")
        if not math.isfinite(self.tempo) or self.tempo <= 0:
            raise ValueError("tempo must be greater than zero")
        if isinstance(self.bars, bool) or not isinstance(self.bars, int) or self.bars < 1:
            raise ValueError("bars must be a positive integer")
        if isinstance(self.time_signature, list):
            object.__setattr__(self, "time_signature", tuple(self.time_signature))
        if len(self.time_signature) != 2:
            raise ValueError("time_signature must contain numerator and denominator")
        numerator, denominator = self.time_signature
        if any(isinstance(value, bool) or not isinstance(value, int) for value in (numerator, denominator)):
            raise ValueError("time_signature values must be integers")
        if numerator < 1 or denominator < 1:
            raise ValueError("time_signature values must be positive")
        if denominator & (denominator - 1):
            raise ValueError("time_signature denominator must be a power of two")
        if isinstance(self.style, list):
            object.__setattr__(self, "style", tuple(str(item) for item in self.style))
        elif not isinstance(self.style, (dict, tuple)):
            raise ValueError("style must be a list of genre names or an object")
        if isinstance(self.style, tuple) and any(not isinstance(item, str) or not item.strip() for item in self.style):
            raise ValueError("style values must not be empty")
        if isinstance(self.tags, list):
            object.__setattr__(self, "tags", tuple(str(item) for item in self.tags))
        elif not isinstance(self.tags, tuple):
            raise ValueError("tags must be a list of strings")
        if any(not isinstance(item, str) or not item.strip() for item in self.tags):
            raise ValueError("tags must not contain empty values")
        if not isinstance(self.options, dict):
            raise ValueError("options must be an object")
        if not isinstance(self.groove, dict):
            raise ValueError("groove must be an object")
        if self.seed is not None and (isinstance(self.seed, bool) or not isinstance(self.seed, int)):
            raise ValueError("seed must be an integer or null")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "GenerationRequest":
        """Build a generation request from JSON-compatible data with required-field validation."""
        if not isinstance(data, Mapping):
            raise ValueError("generation request must be a JSON object")
        required = {"instrument", "section", "tempo", "bars"}
        missing = sorted(required - data.keys())
        if missing:
            raise ValueError(f"Missing required generation field(s): {', '.join(missing)}")
        if not isinstance(data["instrument"], str) or not isinstance(data["section"], str):
            raise ValueError("instrument and section must be strings")
        accepted = {
            "version", "instrument", "section", "tempo", "bars", "time_signature", "key", "chords",
            "style", "tags", "options", "generation_options", "reference_audio", "reference_artists",
            "characteristics", "seed", "groove", "guitar_role", "sync_to_drums", "riff_style",
        }
        unknown = sorted(set(data) - accepted)
        if unknown:
            raise ValueError(f"Unknown generation field(s): {', '.join(unknown)}")
        options = data.get("options", {})
        generation_options = data.get("generation_options", {})
        if not isinstance(options, Mapping) or not isinstance(generation_options, Mapping):
            raise ValueError("options and generation_options must be JSON objects")
        merged_options = {**options, **generation_options}
        for name in ("reference_audio", "reference_artists", "characteristics", "guitar_role", "sync_to_drums", "riff_style"):
            if name in data:
                merged_options[name] = data[name]
        styles = data.get("style", {})
        if isinstance(styles, list):
            styles = tuple(str(item) for item in styles)
        tags = data.get("tags", ())
        if isinstance(tags, list):
            tags = tuple(str(item) for item in tags)
        chords = data.get("chords", ())
        if isinstance(chords, list):
            chords = tuple(str(item) for item in chords)
        time_signature = data.get("time_signature", (4, 4))
        if not isinstance(time_signature, (tuple, list)):
            raise ValueError("time_signature must be a two-number array")
        try:
            return cls(
                instrument=data["instrument"],
                section=data["section"],
                tempo=float(data["tempo"]),
                bars=data["bars"],
                time_signature=tuple(time_signature),
                key=data.get("key"),
                chords=tuple(chords),
                style=styles,
                options=dict(merged_options),
                seed=data.get("seed"),
                tags=tags,
                groove=data.get("groove", {}),
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid generation request: {exc}") from exc


@dataclass
class GenerationResult:
    """Result returned by a generation operation."""
    output_files: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
