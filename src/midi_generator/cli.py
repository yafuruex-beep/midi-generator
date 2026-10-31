"""Command-line interface."""
import argparse
import json
import re
import sys
from pathlib import Path
from midi_generator.core.midi_io import convert_midi_channel, copy_midi, inspect_midi
from midi_generator.api import generate
from midi_generator.storage.catalog import analyze_midi, scan_midi_catalog
from midi_generator.storage.drum_analysis import (
    analyze_catalog_drums,
    export_drum_analysis_csv,
    render_drum_analysis_report,
)
from midi_generator.storage.patterns import add_tags, get_tags, remove_tags, search_patterns, update_tags
from midi_generator.modules.drums.groove import GROOVE_VALUES
from midi_generator.storage.drum_grooves import analyze_catalog_grooves
from midi_generator.storage.guitar_riffs import analyze_catalog_guitars
from midi_generator.storage.guitar_migration import reconcile_guitar_sources, recover_guitar_references
from midi_generator.storage.sources import (
    correct_source_channels,
    inspect_sources,
    list_sources,
    organize_sources,
    validate_source_channels,
)


def _parse_series_genres(value: str | None) -> dict[str, str] | None:
    if not value:
        return None
    result: dict[str, str] = {}
    for item in value.split(","):
        match = re.fullmatch(r"\s*S(\d+)(?:\s*-\s*S?(\d+))?\s*:\s*(metalcore|hardcore|other)\s*", item, re.IGNORECASE)
        if not match:
            raise ValueError("--series-genres format: S01-S05:metalcore,S06-S15:hardcore")
        first = int(match.group(1))
        last = int(match.group(2) or first)
        if first > last:
            raise ValueError("S-series range start must not exceed its end")
        for number in range(first, last + 1):
            key = f"S{number:02d}"
            genre = match.group(3).lower()
            if key in result and result[key] != genre:
                raise ValueError(f"Conflicting genre assignments for {key}")
            result[key] = genre
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="midi-generator",
                                     description="Utilities for the reusable MIDI generator project.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    reference_parser = subparsers.add_parser("reference", help="Read-only reference audio analysis")
    reference_subparsers = reference_parser.add_subparsers(dest="reference_command", required=True)
    reference_analyze = reference_subparsers.add_parser("analyze", help="Analyze WAV/MP3/FLAC rhythm features and save JSON")
    reference_analyze.add_argument("audio_path", help="Existing input audio file")
    reference_analyze.add_argument("--start", type=float, default=0.0, help="Start seconds (default 0)")
    reference_analyze.add_argument("--end", type=float, help="End seconds (default audio end; out-of-range is rejected)")
    reference_analyze.add_argument("--output", required=True, help="UTF-8 JSON output path")
    reference_analyze.add_argument("--overwrite", action="store_true", help="Explicitly replace an existing JSON report")
    reference_analyze.add_argument("--verbose", action="store_true", help="Print the complete analysis instead of a summary")
    inspect_parser = subparsers.add_parser("inspect", help="Inspect a MIDI file")
    inspect_parser.add_argument("midi_file")

    midi_parser = subparsers.add_parser("midi", help="Analyze or convert MIDI files")
    midi_subparsers = midi_parser.add_subparsers(dest="midi_command", required=True)
    midi_analyze_parser = midi_subparsers.add_parser("analyze", help="Analyze channels and note numbers")
    midi_analyze_parser.add_argument("midi_file")
    channel_parser = midi_subparsers.add_parser("convert-channel", help="Copy a MIDI file with channel remapping")
    channel_parser.add_argument("source")
    channel_parser.add_argument("--from", dest="source_channel", type=int, help="Only convert this one-based channel")
    channel_parser.add_argument("--to", dest="target_channel", type=int, default=10, help="Target channel (1–16, default 10)")
    channel_parser.add_argument("--output", help="Destination MIDI path (required unless --dry-run)")
    channel_parser.add_argument("--dry-run", action="store_true", help="Preview conversion counts without writing a file")
    copy_parser = subparsers.add_parser("round-trip", help="Read/write a MIDI file to a different path")
    copy_parser.add_argument("source")
    copy_parser.add_argument("destination")
    copy_parser.add_argument("--overwrite", action="store_true", help="Replace an existing destination")
    catalog_parser = subparsers.add_parser("catalog", help="Manage the MIDI metadata catalog")
    catalog_subparsers = catalog_parser.add_subparsers(dest="catalog_command", required=True)
    scan_parser = catalog_subparsers.add_parser("scan", help="Scan MIDI files and update the JSON catalog")
    scan_parser.add_argument("--source-dir", default="data/sources", help="Directory to scan recursively")
    scan_parser.add_argument("--output", default="data/catalog/midi_catalog.json", help="Catalog JSON path")
    scan_parser.add_argument("--project-root", default=".", help="Root used to store relative file paths")
    analyze_drums_parser = catalog_subparsers.add_parser(
        "analyze-drums", help="Classify catalog MIDI as drum candidates without changing tags",
    )
    analyze_drums_parser.add_argument("--file-id", help="Analyze one catalog file ID instead of the full catalog")
    analyze_drums_parser.add_argument("--catalog", default="data/catalog/midi_catalog.json")
    analyze_drums_parser.add_argument("--output", default="data/catalog/drum_analysis.json")
    analyze_drums_parser.add_argument("--project-root", default=".")
    analyze_drums_parser.add_argument("--report", action="store_true", help="Print the detailed human-readable report")
    analyze_drums_parser.add_argument("--csv", help="Optionally export a compact CSV report")
    grooves_parser = catalog_subparsers.add_parser("analyze-grooves", help="Analyze drum groove features without editing sources or tags")
    grooves_parser.add_argument("--catalog", default="data/catalog/midi_catalog.json")
    grooves_parser.add_argument("--output", default="data/catalog/drum_groove_analysis.json")
    grooves_parser.add_argument("--project-root", default=".")
    grooves_parser.add_argument("--config", help="Groove thresholds and drum-map JSON")
    guitars_parser = catalog_subparsers.add_parser("analyze-guitars", help="Read-only guitar MIDI features and riff classification")
    guitars_parser.add_argument("--catalog", default="data/catalog/midi_catalog.json")
    guitars_parser.add_argument("--output", default="data/catalog/guitar_riff_analysis.json")
    guitars_parser.add_argument("--project-root", default=".")
    guitars_parser.add_argument("--config", help="Guitar feature thresholds and explicitly excluded notes")

    source_parser = subparsers.add_parser("source", help="Manage instrument-organized MIDI sources")
    source_subparsers = source_parser.add_subparsers(dest="source_command", required=True)
    reconcile_parser = source_subparsers.add_parser("reconcile-guitars", help="Reconcile manually moved guitar references; never move MIDI")
    reconcile_mode = reconcile_parser.add_mutually_exclusive_group()
    reconcile_mode.add_argument("--dry-run", action="store_true", help="Read-only preview (default)")
    reconcile_mode.add_argument("--apply", action="store_true", help="Back up and update unambiguous JSON references")
    reconcile_mode.add_argument("--recover", metavar="BACKUP_DIRECTORY", help="Restore verified metadata before-images from a transaction backup")
    reconcile_parser.add_argument("--project-root", default=".")
    reconcile_parser.add_argument("--catalog", default="data/catalog/midi_catalog.json")
    reconcile_parser.add_argument("--analysis", default="data/catalog/guitar_riff_analysis.json")
    reconcile_parser.add_argument("--mapping", help="JSON object: existing source_id to manually verified new relative path")
    reconcile_parser.add_argument("--backup-directory", default="data/backups")
    source_list_parser = source_subparsers.add_parser("list", help="List source MIDI files and channel status")
    source_inspect_parser = source_subparsers.add_parser("inspect", help="Inspect sources, optionally by instrument")
    source_inspect_parser.add_argument("--instrument", choices=("drums", "guitar", "bass", "synth"))
    source_validate_parser = source_subparsers.add_parser("validate-channels", help="Find source channel mismatches")
    source_correct_parser = source_subparsers.add_parser("correct-channels", help="Preview or apply channel calibration")
    correction_mode = source_correct_parser.add_mutually_exclusive_group()
    correction_mode.add_argument("--dry-run", action="store_true", help="Preview only (the default)")
    correction_mode.add_argument("--apply", action="store_true", help="Apply corrections after making backups")
    source_correct_parser.add_argument("--copy-output", action="store_true", help="Write corrected copies instead of editing sources")
    source_migrate_parser = source_subparsers.add_parser("migrate", help="Back up and organize existing MIDI sources")
    for source_command_parser in (source_list_parser, source_inspect_parser, source_validate_parser,
                                  source_correct_parser, source_migrate_parser):
        source_command_parser.add_argument("--source-dir", default="data/sources")
        source_command_parser.add_argument("--project-root", default=".")
        source_command_parser.add_argument("--config", default="config/instrument_channels.json")
    source_migrate_parser.add_argument("--catalog", default="data/catalog/midi_catalog.json")
    source_migrate_parser.add_argument("--backup-directory", default="data/backups")
    source_migrate_parser.add_argument("--instrument", choices=("drums", "guitar", "bass", "synth"),
                                      help="User-verified classification for unclassified files")
    source_migrate_parser.add_argument("--genre", choices=("metalcore", "hardcore", "other"),
                                      help="Genre folder for files classified by --instrument")
    source_migrate_parser.add_argument("--series-genres", help="S-series mapping, e.g. S01-S05:metalcore,S06-S15:hardcore")

    tag_parser = subparsers.add_parser("tag", help="Manage manual MIDI pattern tags")
    tag_subparsers = tag_parser.add_subparsers(dest="tag_command", required=True)
    for operation in ("add", "update"):
        operation_parser = tag_subparsers.add_parser(operation, help=f"{operation.title()} tags on a catalog entry")
        operation_parser.add_argument("file_id", help="Catalog path, or an unambiguous filename")
        operation_parser.add_argument("--instrument", help="Comma-separated instrument tags")
        operation_parser.add_argument("--section", help="Comma-separated section tags")
        operation_parser.add_argument("--style", help="Comma-separated style tags")
        operation_parser.add_argument("--tags", help="Comma-separated free-form tags")
        operation_parser.add_argument("--confidence", type=float, help="Optional confidence from 0 to 1")
        operation_parser.add_argument("--catalog", default="data/catalog/midi_catalog.json")
    show_parser = tag_subparsers.add_parser("show", help="Show tags on a catalog entry")
    show_parser.add_argument("file_id")
    show_parser.add_argument("--catalog", default="data/catalog/midi_catalog.json")
    remove_parser = tag_subparsers.add_parser("remove", help="Remove tags from a catalog entry")
    remove_parser.add_argument("file_id")
    remove_parser.add_argument("--instrument", help="Instrument tag(s) to remove")
    remove_parser.add_argument("--section", help="Section tag(s) to remove")
    remove_parser.add_argument("--style", help="Style tag(s) to remove")
    remove_parser.add_argument("--tags", help="Free-form tag(s) to remove")
    remove_parser.add_argument("--catalog", default="data/catalog/midi_catalog.json")

    search_parser = subparsers.add_parser("search", help="Search and rank catalog patterns")
    search_parser.add_argument("--guitar-role", choices=("backing", "lead"), help="Formal guitar role from guitar/backing or guitar/lead only")
    search_parser.add_argument("--riff-characteristic", dest="riff_characteristics", action="append", metavar="LABEL",
                               help="Required guitar riff label; repeat or use commas for multiple labels (AND)")
    search_parser.add_argument("--guitar-analysis", help="Independent guitar riff report JSON")
    search_parser.add_argument("--instrument")
    search_parser.add_argument("--section")
    search_parser.add_argument("--style")
    search_parser.add_argument("--tags", help="Free-form tag preferences (comma-separated)")
    search_parser.add_argument("--required-tags", help="Tags every result must have (comma-separated)")
    search_parser.add_argument("--bpm-min", type=float)
    search_parser.add_argument("--bpm-max", type=float)
    search_parser.add_argument("--time-signature", help="Meter such as 4/4")
    search_parser.add_argument("--bars-min", type=float)
    search_parser.add_argument("--bars-max", type=float)
    search_parser.add_argument("--notes-min", type=int)
    search_parser.add_argument("--notes-max", type=int)
    search_parser.add_argument("--pitch-min", type=int, help="Minimum overlapping MIDI note number")
    search_parser.add_argument("--pitch-max", type=int, help="Maximum overlapping MIDI note number")
    search_parser.add_argument("--catalog", default="data/catalog/midi_catalog.json")

    generate_parser = subparsers.add_parser("generate", help="Generate an instrument MIDI from a catalog pattern")
    generate_parser.add_argument("--config", help="JSON generation request file")
    generate_parser.add_argument("--instrument")
    generate_parser.add_argument("--section")
    generate_parser.add_argument("--tempo", type=float)
    generate_parser.add_argument("--bars", type=int)
    generate_parser.add_argument("--time-signature", help="Meter such as 4/4")
    generate_parser.add_argument("--style", help="Comma-separated genre tags")
    generate_parser.add_argument("--tags", help="Comma-separated required pattern tags")
    generate_parser.add_argument("--seed", type=int)
    generate_parser.add_argument("--guitar-role", help="Guitar role: backing supported; lead is not implemented")
    generate_parser.add_argument("--sync-to-drums", help="Existing drum MIDI used as a guitar rhythm reference")
    generate_parser.add_argument("--guitar-analysis", help="Independent Phase 6.1 guitar analysis JSON")
    generate_parser.add_argument("--guitar-sync-config", help="Guitar synchronization weights/settings JSON")
    generate_parser.add_argument("--midi-channel", type=int, help="One-based MIDI channel; drums default 10, guitar backing default 1")
    generate_parser.add_argument("--output", help="Output MIDI path; defaults to a descriptive path in output/")
    generate_parser.add_argument("--catalog", default="data/catalog/midi_catalog.json")
    generate_parser.add_argument("--project-root", default=".")
    for groove_parser in (search_parser, generate_parser):
        for name, choices in GROOVE_VALUES.items():
            groove_parser.add_argument("--" + name.replace("_", "-"), choices=choices, help="Soft groove preference")
        groove_parser.add_argument("--tightness", type=float, help="Preferred minimum grid tightness, 0–1 (soft)")
        groove_parser.add_argument("--groove-analysis", help="Independent groove report JSON")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        if args.command == "reference":
            from midi_generator.reference_analysis import analyze_reference_audio, save_reference_analysis
            if Path(args.output).exists() and not args.overwrite:
                raise FileExistsError(f"Reference report already exists: {args.output}; use --overwrite to replace it")
            result = analyze_reference_audio(args.audio_path, start_seconds=args.start, end_seconds=args.end)
            saved = save_reference_analysis(result, args.output, overwrite=args.overwrite)
            display = result if args.verbose else {
                "output_file": str(saved), "tempo": result["tempo"], "onset_count": len(result["onsets"]),
                "onsets_per_second": result["rhythmic_activity"]["onsets_per_second"],
                "mean_rms": result["energy"]["mean_rms"],
                "sixteenth_alignment_score": result["grid_alignment"]["sixteenth_alignment_score"],
                "warnings": result["analysis_metadata"]["warnings"],
            }
            print(json.dumps(display, ensure_ascii=False, indent=2, allow_nan=False))
        elif args.command == "inspect":
            print(json.dumps(inspect_midi(args.midi_file), indent=2))
        elif args.command == "midi" and args.midi_command == "analyze":
            print(json.dumps(analyze_midi(args.midi_file), ensure_ascii=False, indent=2))
        elif args.command == "midi" and args.midi_command == "convert-channel":
            result = convert_midi_channel(
                args.source,
                args.output,
                source_channel=args.source_channel,
                target_channel=args.target_channel,
                dry_run=args.dry_run,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.command == "round-trip":
            destination = copy_midi(args.source, args.destination, overwrite=args.overwrite)
            print(json.dumps({"output_file": str(destination)}, indent=2))
        elif args.command == "catalog" and args.catalog_command == "scan":
            result = scan_midi_catalog(args.source_dir, args.output, project_root=args.project_root)
            print(json.dumps({key: value for key, value in result.items() if key != "catalog"}, indent=2))
        elif args.command == "catalog" and args.catalog_command == "analyze-grooves":
            result = analyze_catalog_grooves(catalog_path=args.catalog, output_path=args.output,
                                             project_root=args.project_root, config_path=args.config)
            print(json.dumps({key: value for key, value in result.items()
                              if key in {"target_count", "success_count", "error_count", "classification_counts"}}, indent=2))
        elif args.command == "catalog" and args.catalog_command == "analyze-guitars":
            report = analyze_catalog_guitars(catalog_path=args.catalog, output_path=args.output,
                                             project_root=args.project_root, config_path=args.config)
            print(json.dumps(report["summary"], ensure_ascii=False, indent=2, allow_nan=False))
        elif args.command == "catalog" and args.catalog_command == "analyze-drums":
            report = analyze_catalog_drums(
                catalog_path=args.catalog,
                output_path=args.output,
                project_root=args.project_root,
                file_id=args.file_id,
            )
            if args.csv:
                report["csv_file"] = str(export_drum_analysis_csv(report, args.csv))
            if args.report:
                print(render_drum_analysis_report(report))
                print(f"\nJSON saved to: {report['output_file']}")
                if report.get("csv_file"):
                    print(f"CSV saved to: {report['csv_file']}")
            else:
                print(json.dumps({
                    "catalog_entry_count": report["catalog_entry_count"],
                    "analyzed_entry_count": report["analyzed_entry_count"],
                    "successful_entry_count": report["successful_entry_count"],
                    "error_count": report["error_count"],
                    "classification_counts": report["classification_counts"],
                    "output_file": report["output_file"],
                    "csv_file": report.get("csv_file"),
                }, ensure_ascii=False, indent=2))
        elif args.command == "source" and args.source_command == "reconcile-guitars":
            if args.recover:
                result = recover_guitar_references(args.recover, project_root=args.project_root)
            else:
                mapping = None
                if args.mapping:
                    mapping = json.loads((Path(args.project_root) / args.mapping).read_text(encoding="utf-8"))
                result = reconcile_guitar_sources(project_root=args.project_root, catalog_path=args.catalog,
                    analysis_path=args.analysis, mapping=mapping, apply=args.apply, backup_directory=args.backup_directory)
            print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        elif args.command == "source":
            common = {"source_dir": args.source_dir, "project_root": args.project_root, "config_path": args.config}
            if args.source_command == "list":
                result = list_sources(**common)
            elif args.source_command == "inspect":
                result = inspect_sources(args.instrument, **common)
            elif args.source_command == "validate-channels":
                validation = validate_source_channels(**common)
                result = {
                    "source_count": validation["source_count"],
                    "instrument_summary": validation["instrument_summary"],
                    "status_counts": validation["status_counts"],
                    "correction_candidates": [
                        {"file_id": item["file_id"], "file_path": item["file_path"],
                         "actual_midi_channels": item["actual_midi_channels"],
                         "expected_midi_channel": item["expected_midi_channel"]}
                        for item in validation["correction_candidates"]
                    ],
                    "multi_channel_files": [
                        {"file_id": item["file_id"], "file_path": item["file_path"],
                         "actual_midi_channels": item["actual_midi_channels"],
                         "channel_status": item["channel_status"]}
                        for item in validation["multi_channel_sources"]
                    ],
                    "read_error_count": validation["read_error_count"],
                    "read_errors": validation["read_errors"],
                }
            elif args.source_command == "correct-channels":
                result = correct_source_channels(apply=args.apply, in_place=False if args.copy_output else None, **common)
            else:
                result = organize_sources(catalog_path=args.catalog, backup_directory=args.backup_directory,
                                          instrument=args.instrument, genre=args.genre,
                                          genre_by_series=_parse_series_genres(args.series_genres), **common)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.command == "tag":
            if args.tag_command == "add":
                result = add_tags(args.file_id, instrument=args.instrument, section=args.section, style=args.style,
                                  tags=args.tags, confidence=args.confidence, catalog_path=args.catalog)
            elif args.tag_command == "update":
                result = update_tags(args.file_id, instrument=args.instrument, section=args.section, style=args.style,
                                     tags=args.tags, confidence=args.confidence, catalog_path=args.catalog)
            elif args.tag_command == "remove":
                result = remove_tags(args.file_id, instrument=args.instrument, section=args.section, style=args.style,
                                     tags=args.tags, catalog_path=args.catalog)
            else:
                result = get_tags(args.file_id, catalog_path=args.catalog)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.command == "search":
            results = search_patterns(
                catalog_path=args.catalog, instrument=args.instrument, section=args.section, style=args.style,
                tags=args.tags, required_tags=args.required_tags, bpm_min=args.bpm_min, bpm_max=args.bpm_max,
                time_signature=args.time_signature, bars_min=args.bars_min, bars_max=args.bars_max,
                notes_min=args.notes_min, notes_max=args.notes_max, pitch_min=args.pitch_min, pitch_max=args.pitch_max,
                **{name: getattr(args, name) for name in (*GROOVE_VALUES, "tightness")},
                groove_analysis_path=args.groove_analysis,
                riff_characteristics=args.riff_characteristics,
                guitar_analysis_path=args.guitar_analysis,
                guitar_role=args.guitar_role,
            )
            print(json.dumps(results, ensure_ascii=False, indent=2))
        elif args.command == "generate":
            if args.config:
                with Path(args.config).open(encoding="utf-8") as config_file:
                    request_data = json.load(config_file)
                if not isinstance(request_data, dict):
                    raise ValueError("Generation config must contain a JSON object")
            else:
                request_data = {}
            for name in ("instrument", "section", "tempo", "bars", "seed", "guitar_role", "sync_to_drums"):
                value = getattr(args, name)
                if value is not None:
                    request_data[name] = value
            for name in ("style", "tags"):
                value = getattr(args, name)
                if value is not None:
                    request_data[name] = [item.strip() for item in value.split(",") if item.strip()]
            if args.time_signature:
                try:
                    request_data["time_signature"] = [int(item) for item in args.time_signature.split("/")]
                except ValueError as exc:
                    raise ValueError("--time-signature must look like 4/4") from exc
            if args.midi_channel is not None:
                generation_options = request_data.setdefault("generation_options", {})
                if not isinstance(generation_options, dict):
                    raise ValueError("config generation_options must be a JSON object")
                generation_options["midi_channel"] = args.midi_channel
            groove_preferences = {name: getattr(args, name) for name in (*GROOVE_VALUES, "tightness") if getattr(args, name) is not None}
            if groove_preferences:
                if not isinstance(request_data.get("groove", {}), dict):
                    raise ValueError("config groove must be an object")
                request_data["groove"] = {**request_data.get("groove", {}), **groove_preferences}
            result = generate(
                request_data,
                output_path=args.output,
                catalog_path=args.catalog,
                project_root=args.project_root,
                groove_analysis_path=args.groove_analysis,
                guitar_analysis_path=args.guitar_analysis,
                guitar_sync_config_path=args.guitar_sync_config,
            )
            print(json.dumps({
                "output_files": [str(path) for path in result.output_files],
                "warnings": result.warnings,
                "metadata": result.metadata,
            }, ensure_ascii=False, indent=2))
    except (OSError, ValueError, KeyError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
