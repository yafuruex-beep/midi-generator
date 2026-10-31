"""Catalog-backed rhythm-synchronized backing guitar selection and export."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import re

from midi_generator.core.models import GenerationResult
from midi_generator.modules.guitar.analysis import ANALYSIS_VERSION
from midi_generator.modules.guitar.timeline import read_timeline
from midi_generator.modules.guitar.render import phrase_fit, render_backing, source_length_bars
from midi_generator.modules.guitar.sync import (
    ALGORITHM_VERSION, analyze_drum_sync, attack_pattern, constant_context,
    load_sync_config, rhythm_sync_score,
)
from midi_generator.storage.catalog import _load_catalog
from midi_generator.storage.patterns import _tag_values, search_patterns

BACKING_LABELS = {"chug", "syncopated_riff", "gallop", "stop_and_go", "power_chord_riff",
                  "single_note_riff", "open_chord_riff"}


def _hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _rooted(path, root):
    path = Path(path)
    return path if path.is_absolute() else root / path


def classify_guitar_role(record, analysis):
    custom = _tag_values(record)["custom"]
    explicit = set()
    for role in ("backing", "lead"):
        if record.get("guitar_role") == role or any(tag in {role, "guitar_" + role} for tag in custom):
            explicit.add(role)
    parts = list(Path(record["path"]).parts[:-1])
    directory_roles = set()
    if "guitar" in parts:
        directory_roles = set(parts[parts.index("guitar") + 1:]) & {"backing", "lead"}
    explicit.update(directory_roles)
    if len(explicit) > 1:
        return {"role": "conflicting", "source": "explicit", "eligible": False,
                "evidence": "Conflicting backing/lead tags or role directories", "confidence": None}
    if explicit:
        role = next(iter(explicit))
        return {"role": role, "source": "directory" if directory_roles else "manual", "eligible": role == "backing",
                "evidence": "Explicit role directory/catalog role/free tag", "confidence": None}
    labels = sorted(set(analysis["riff_characteristics"]) & BACKING_LABELS)
    return {"role": "backing_candidate" if labels else "unknown", "source": "feature_inference",
            "eligible": True, "evidence": {"riff_labels": labels,
                "polyphony": analysis["polyphony"]["classification"],
                "pitch_range": [analysis["pitch"]["min_note"], analysis["pitch"]["max_note"]],
                "density": analysis["rhythm"]["density"],
                "possible_controls": bool(analysis["possible_control_notes"])}, "confidence": None}


def _save_output(generated, metadata, desired):
    desired.parent.mkdir(parents=True, exist_ok=True)
    for number in range(100000):
        path = desired if number == 0 else desired.with_name(f"{desired.stem}_{number}{desired.suffix}")
        sidecar = Path(str(path) + ".json")
        if path.exists() or sidecar.exists():
            continue
        metadata.update(output_file=str(path), metadata_file=str(sidecar))
        text = json.dumps(metadata, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        created = []
        try:
            with path.open("xb") as stream:
                created.append(path)
                generated.save(file=stream)
            with sidecar.open("x", encoding="utf-8") as stream:
                created.append(sidecar)
                stream.write(text)
        except Exception:
            for file in created:
                file.unlink(missing_ok=True)
            raise
        return path
    raise FileExistsError(f"No unused filename available for {desired}")


def generate_backing_guitar(request, *, output_path=None, catalog_path="data/catalog/midi_catalog.json",
                            project_root=".", guitar_analysis_path=None, sync_config_path=None):
    if request.instrument.lower() != "guitar":
        raise ValueError("Backing guitar generation requires instrument='guitar'")
    role = request.options.get("guitar_role")
    if role is None:
        raise ValueError("Without an explicit guitar_role, generation supports drums only; specify guitar_role='backing' and sync_to_drums")
    if role != "backing":
        raise ValueError(f"Guitar role {role!r} is not implemented; only guitar_role='backing' is supported")
    drum_path = request.options.get("sync_to_drums")
    if not isinstance(drum_path, str) or not drum_path.strip():
        raise ValueError("Backing guitar requires sync_to_drums pointing to an existing drum MIDI")
    if request.key is not None or request.chords:
        raise ValueError("Guitar transpose/key/chord adaptation is not implemented; omit key and chords")
    channel = request.options.get("midi_channel", 1)
    if type(channel) is not int or not 1 <= channel <= 16:
        raise ValueError("options.midi_channel must be an integer from 1 to 16")
    root = Path(project_root).resolve()
    catalog_file = _rooted(catalog_path, root)
    drum_file = _rooted(drum_path, root)
    if not drum_file.is_file():
        raise ValueError(f"Drum MIDI does not exist: {drum_file}")
    drum_hash = _hash(drum_file)
    config_file = _rooted(sync_config_path, root) if sync_config_path else root / "config/guitar_sync.json"
    config = load_sync_config(config_file if config_file.exists() else None)
    if sync_config_path is not None and not config_file.is_file():
        raise ValueError(f"Guitar sync config not found: {config_file}")
    drum = analyze_drum_sync(drum_file, tempo=request.tempo, bars=request.bars,
                             time_signature=request.time_signature, config=config)
    catalog = _load_catalog(catalog_file)
    styles = request.style
    if isinstance(styles, dict):
        styles = styles.get("genre", styles.get("genres", styles.get("style", [])))
    if isinstance(styles, str):
        styles = [styles]
    if styles is None:
        styles = []
    if not isinstance(styles, (tuple, list)):
        raise ValueError("style genre must be a string or list of genre names")
    found = {}
    analysis_file = _rooted(guitar_analysis_path, root) if guitar_analysis_path else catalog_file.parent / "guitar_riff_analysis.json"
    for style in styles or [None]:
        for candidate in search_patterns(catalog_path=catalog_file, instrument="guitar", section=request.section.lower(),
                style=style, required_tags=request.tags, time_signature=f"{request.time_signature[0]}/{request.time_signature[1]}",
                guitar_analysis_path=analysis_file, **request.groove):
            found[candidate["file_id"]] = candidate
    criteria = {"instrument": "guitar", "guitar_role": "backing", "section": request.section,
                "style_any": list(styles), "time_signature": list(request.time_signature), "required_tags": list(request.tags)}
    if not found:
        raise ValueError(f"No guitar candidate meets mandatory conditions {criteria}; verify section/style/tags/meter. No other section or style was substituted")
    if not analysis_file.is_file():
        raise ValueError("Guitar analysis is missing; run catalog analyze-guitars")
    report = json.loads(analysis_file.read_text(encoding="utf-8"))
    if report.get("analysis_version") != ANALYSIS_VERSION:
        raise ValueError("Unsupported guitar analysis version; run catalog analyze-guitars")
    analysis_index = {a["source_id"]: a for a in report["sources"]}
    records = {r.get("file_id", r["path"]): r for r in catalog["files"]}
    eligible, rejected = [], []
    excluded = set(report["analysis_config"].get("excluded_notes", []))
    for file_id, candidate in sorted(found.items()):
        try:
            record = records[file_id]
            if record.get("instrument") != "guitar":
                raise ValueError("Source directory is not classified as guitar")
            analysis = analysis_index.get(file_id)
            if not analysis or analysis["status"] != "ok":
                raise ValueError("Missing/failed Phase 6.1 analysis")
            source_file = _rooted(record["path"], root)
            if not source_file.is_file() and catalog.get("source_directory"):
                source_file = _rooted(catalog["source_directory"], root) / record["path"]
            if _hash(source_file) != analysis["source_sha256"]:
                raise ValueError("Stale guitar analysis; run catalog scan and catalog analyze-guitars")
            role_info = classify_guitar_role(record, analysis)
            if not role_info["eligible"]:
                raise ValueError(f"Excluded guitar role: {role_info['role']} ({role_info['source']})")
            data = read_timeline(source_file, set())
            constant_context(data, request.time_signature)
            if len(data["channels"]) > 1:
                raise ValueError("Multi-channel guitar generation is unsupported; no channel collapse was attempted")
            if data["pairing"]["unmatched_note_offs"] or data["pairing"]["inferred_note_ends"]:
                raise ValueError("Unmatched guitar Note On/Off events")
            source_bars = source_length_bars(data, request.time_signature, config["tail_tolerance_beats"])
            source_beats = source_bars * request.time_signature[0] * 4 / request.time_signature[1]
            notes = [n for n in data["notes"] if n["note"] not in excluded]
            attacks = attack_pattern(data, length_beats=drum["length_beats"], source_beats=source_beats,
                                     notes=notes, tolerance=config["onset_tolerance_beats"])
            if not attacks:
                raise ValueError("No guitar attacks in the output window")
            sync = rhythm_sync_score(drum, attacks, time_signature=request.time_signature, config=config)
            if sync["grid_alignment"] < config["minimum_grid_alignment"]:
                raise ValueError("Guitar source is not sufficiently aligned to the straight sixteenth grid; no notes were quantized")
            period = analysis["repetition"]["pitch_rhythm_period_bars"]
            phrase, phrase_reason, partial = phrase_fit(source_bars, request.bars, period)
            features = set(analysis["riff_characteristics"]) & BACKING_LABELS
            suitability = 1.0 if role_info["source"] in {"manual", "directory"} else .5 if analysis["possible_control_notes"] else .8 if features else .5
            riff_support = (min(len(features), 2) / 2 + (analysis["rhythm"]["subdivision"] == "sixteenth")) / 2
            values = {"rhythm_sync": sync["overall_score"], "phrase_fit": phrase,
                      "backing_suitability": suitability, "riff_support": riff_support}
            weights = config["selection_weights"]
            total = sum(weights.values())
            breakdown = {key: {"value": values[key], "weight": weight, "contribution": values[key] * weight / total} for key, weight in weights.items()}
            score = sum(part["contribution"] for part in breakdown.values())
            eligible.append({"source_id": file_id, "path": record["path"], "source_sha256": analysis["source_sha256"],
                             "score": score, "score_breakdown": breakdown, "rhythm_sync": sync, "role": role_info,
                             "source_bars": source_bars, "analyzed_period_bars": period, "phrase_fit_reason": phrase_reason,
                             "partial_phrase": partial, "riff_characteristics": analysis["riff_characteristics"],
                             "provenance": record.get("provenance", {}), "catalog_tags": record.get("tags", {}),
                             "source_file": source_file, "data": data, "analysis_warnings": analysis["warnings"],
                             "possible_control_notes": analysis["possible_control_notes"],
                             "guitar_attacks": attacks})
        except (OSError, ValueError, KeyError, EOFError) as exc:
            rejected.append({"source_id": file_id, "path": candidate["file_path"], "reason": str(exc)})
    if not eligible:
        raise ValueError("No usable backing guitar candidates: " + json.dumps(rejected, ensure_ascii=False))
    eligible.sort(key=lambda c: (-c["score"], c["path"].casefold()))
    shortlist = [c for c in eligible if c["score"] >= eligible[0]["score"] - config["seed_score_window"]]
    selected = random.Random(request.seed).choices(shortlist, weights=[max(c["score"], 1e-9) ** 3 for c in shortlist], k=1)[0]
    if _hash(drum_file) != drum_hash or _hash(selected["source_file"]) != selected["source_sha256"]:
        raise ValueError("An input MIDI changed during generation; no output was written")
    generated, operations, render_warnings = render_backing(selected["data"], request, selected["source_bars"], channel)
    warnings = list(dict.fromkeys([*drum["warnings"], *selected["analysis_warnings"], *render_warnings]))
    if selected["role"]["source"] not in {"manual", "directory"}:
        warnings.append("Backing role is inferred/unverified; no manual role or section tags were added.")
    if selected["partial_phrase"]:
        warnings.append("Requested end cuts a partial source phrase; boundary Note Offs were preserved/closed.")
    if selected["source_bars"] * request.time_signature[0] * 4 / request.time_signature[1] != selected["data"]["end_tick"] / selected["data"]["midi"].ticks_per_beat:
        warnings.append("Source export duration was rounded to whole bars; phrase selection records the resulting length.")
    if selected["rhythm_sync"]["kick_f1"] < .5:
        warnings.append("Kick/attack agreement is low; selection cannot create new rhythmic alignment without editing notes.")
    section = re.sub(r"[^A-Za-z0-9_-]+", "_", request.section) or "pattern"
    desired = _rooted(output_path, root) if output_path else root / "output" / f"guitar_backing_{section}_{request.tempo:g}bpm_{request.bars}bars.mid"
    if desired.suffix.lower() not in {".mid", ".midi"}:
        desired = desired.with_suffix(".mid")
    metadata = {"instrument": "guitar", "guitar_role": "backing", "algorithm_version": ALGORITHM_VERSION,
                "request": {"instrument": request.instrument, "guitar_role": role, "section": request.section,
                            "tempo": request.tempo, "bars": request.bars, "time_signature": list(request.time_signature),
                            "style": list(styles), "tags": list(request.tags), "groove": request.groove,
                            "options": request.options, "seed": request.seed},
                "drum_reference": {"path": str(drum_file), "sha256": drum_hash, "analysis": drum},
                "selected_source": {key: value for key, value in selected.items() if key not in {"source_file", "data", "analysis_warnings"}},
                "rhythm_sync": selected["rhythm_sync"], "processing": operations,
                "catalog_file": str(catalog_file), "analysis_file": str(analysis_file), "analysis_sha256": _hash(analysis_file),
                "effective_config": config, "seed": request.seed,
                "candidate_ranking": [{"source_id": c["source_id"], "path": c["path"], "score": c["score"],
                                       "sync_score": c["rhythm_sync"]["overall_score"], "phrase_fit_reason": c["phrase_fit_reason"], "role": c["role"]} for c in eligible],
                "rejected_candidates": rejected, "seed_shortlist_ids": [c["source_id"] for c in shortlist],
                "generated_at": datetime.now(timezone.utc).isoformat(), "warnings": warnings,
                "note_policy": "Original MIDI pitches, onset ticks, velocities and controls retained; loop/crop and boundary releases only. Analysis exclusions never remove output notes."}
    path = _save_output(generated, metadata, desired)
    return GenerationResult(output_files=[path], warnings=warnings, metadata=metadata)
