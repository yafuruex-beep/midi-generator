"""Public Python API."""
from pathlib import Path
from typing import Any, Mapping
from midi_generator.core.midi_io import copy_midi, convert_midi_channel, inspect_midi, load_midi, save_midi
from midi_generator.core.models import GenerationRequest, GenerationResult
from midi_generator.planning import (create_reference_generation_plan, validate_reference_generation_plan,
                                     save_reference_generation_plan, load_reference_generation_plan)
from midi_generator.storage.catalog import analyze_midi, scan_midi_catalog
from midi_generator.storage.drum_analysis import (
    analyze_catalog_drums,
    export_drum_analysis_csv,
    render_drum_analysis_report,
)
from midi_generator.storage.patterns import (
    PatternSearchQuery,
    add_tags,
    get_tags,
    remove_tags,
    search_patterns,
    update_tags,
)
from midi_generator.storage.sources import (
    correct_source_channels,
    inspect_sources,
    list_sources,
    organize_sources,
    validate_source_channels,
)
from midi_generator.modules.drums.generator import generate_drums
from midi_generator.modules.drums.groove import analyze_drum_groove
from midi_generator.storage.drum_grooves import analyze_catalog_grooves, get_drum_groove_analysis
from midi_generator.reference_analysis import (
    analyze_reference_audio, save_reference_analysis, analyze_reference_section,
    analyze_reference_pattern_map, build_reference_pattern_map,
    save_reference_pattern_map, load_reference_pattern_map, validate_reference_pattern_map,
)
from midi_generator.modules.guitar import analyze_guitar_midi
from midi_generator.modules.guitar.rule_backing import (
    RhythmBlueprint, BackingRules, BackingStep,
    extract_rhythm_blueprint, generate_backing_steps, generate_backing_phrase, render_backing_midi,
)
from midi_generator.storage.guitar_riffs import analyze_catalog_guitars, get_guitar_riff_analysis, search_guitar_riffs
from midi_generator.storage.guitar_migration import reconcile_guitar_sources, recover_guitar_references


def inspect(path: str | Path) -> dict[str, Any]:
    """Inspect a Standard MIDI File."""
    return inspect_midi(path)


def round_trip(source: str | Path, destination: str | Path) -> Path:
    """Copy a MIDI file through the MIDI parser/writer as a foundation test."""
    return copy_midi(source, destination)


def generate(
    request: GenerationRequest | Mapping[str, Any],
    *,
    output_path: str | Path | None = None,
    catalog_path: str | Path = "data/catalog/midi_catalog.json",
    project_root: str | Path = ".",
    groove_analysis_path: str | Path | None = None,
    guitar_analysis_path: str | Path | None = None,
    guitar_sync_config_path: str | Path | None = None,
) -> GenerationResult:
    """Generate a MIDI asset through the shared instrument implementations."""
    validated = request if isinstance(request, GenerationRequest) else GenerationRequest.from_dict(request)
    if validated.instrument.lower() == "bass":
        from midi_generator.modules.bass import generate_follow_bass
        return generate_follow_bass(validated, output_path=output_path, project_root=project_root)
    if validated.instrument.lower() == "guitar":
        from midi_generator.modules.guitar.backing import generate_backing_guitar
        return generate_backing_guitar(validated, output_path=output_path, catalog_path=catalog_path,
                                       project_root=project_root, guitar_analysis_path=guitar_analysis_path,
                                       sync_config_path=guitar_sync_config_path)
    if validated.instrument.lower() != "drums":
        raise ValueError("The current generator supports instrument='drums' only")
    if any(key in validated.options for key in ("guitar_role", "sync_to_drums")):
        raise ValueError("guitar_role and sync_to_drums are only supported for instrument='guitar'")
    return generate_drums(validated, output_path=output_path, catalog_path=catalog_path,
                          project_root=project_root, groove_analysis_path=groove_analysis_path)


__all__ = [
    "RhythmBlueprint", "BackingRules", "BackingStep",
    "extract_rhythm_blueprint", "generate_backing_steps", "generate_backing_phrase", "render_backing_midi",
    "GenerationRequest",
    "create_reference_generation_plan",
    "validate_reference_generation_plan",
    "save_reference_generation_plan",
    "load_reference_generation_plan",
    "GenerationResult",
    "reconcile_guitar_sources",
    "recover_guitar_references",
    "analyze_guitar_midi",
    "analyze_catalog_guitars",
    "get_guitar_riff_analysis",
    "search_guitar_riffs",
    "analyze_reference_audio",
    "analyze_reference_section",
    "analyze_reference_pattern_map",
    "build_reference_pattern_map",
    "save_reference_pattern_map",
    "load_reference_pattern_map",
    "validate_reference_pattern_map",
    "save_reference_analysis",
    "analyze_drum_groove",
    "analyze_catalog_grooves",
    "get_drum_groove_analysis",
    "PatternSearchQuery",
    "analyze_midi",
    "analyze_catalog_drums",
    "add_tags",
    "get_tags",
    "generate",
    "export_drum_analysis_csv",
    "convert_midi_channel",
    "correct_source_channels",
    "inspect",
    "load_midi",
    "list_sources",
    "inspect_sources",
    "organize_sources",
    "round_trip",
    "render_drum_analysis_report",
    "remove_tags",
    "search_patterns",
    "scan_midi_catalog",
    "save_midi",
    "update_tags",
    "validate_source_channels",
]
