"""Read-only relocation planning and recoverable guitar metadata transactions."""
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import uuid

from midi_generator.core.instruments import classify_source_path
from midi_generator.storage.catalog import REFERENCE_LOCK, _assert_reference_unlocked
from midi_generator.storage.patterns import _tag_values
from midi_generator.storage.sources import _new_backup_dir, _sha256

VERSION = "6.1.1.1"
FEATURES = ("pitch", "polyphony", "rhythm", "note_duration", "chords", "repetition", "riff_characteristics")


def _metadata_path(value, root):
    path = Path(value)
    path = (path if path.is_absolute() else root / path).resolve()
    if not path.is_relative_to(root) or path.is_relative_to(root / "data/sources") or path.suffix.lower() != ".json":
        raise ValueError("Reference JSON paths must be inside project_root and outside data/sources")
    return path


def _source_path(value, root):
    path = (root / value).resolve()
    if not path.is_relative_to(root / "data/sources/guitar"):
        raise ValueError(f"Guitar source is outside data/sources/guitar: {value}")
    return path


def _json_bytes(data):
    return (json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _stage(path, data):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("wb", dir=path.parent, prefix=".guitar-reference-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        if path.exists():
            os.chmod(temporary, stat.S_IMODE(path.stat().st_mode))
        return temporary
    except BaseException:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise


def _install(staged, target):
    os.replace(staged, target)


def _write_atomic(path, data):
    temporary = _stage(path, data)
    try:
        _install(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _write_manifest(backup, manifest):
    _write_atomic(backup / "manifest.json", _json_bytes(manifest))


def _lock_owner(token, backup, *, active=True):
    return _json_bytes({"token": token, "pid": os.getpid(), "active": active, "backup_directory": str(backup)})


def _acquire_lock(lock, owner):
    temporary = _stage(lock, owner)
    try:
        os.link(temporary, lock)
    finally:
        temporary.unlink(missing_ok=True)


def _guitar_record(record):
    return (classify_source_path(record.get("path", "")).get("instrument") == "guitar"
            or record.get("instrument") == "guitar" or "guitar" in _tag_values(record)["instrument"])


def _plan(catalog_path, analysis_path, root, mappings):
    _assert_reference_unlocked(catalog_path)
    _assert_reference_unlocked(analysis_path)
    if not catalog_path.is_file():
        raise ValueError("Catalog not found; register sources before planning a relocation")
    catalog_raw = catalog_path.read_bytes()
    catalog = json.loads(catalog_raw)
    if not isinstance(catalog, dict) or not isinstance(catalog.get("files"), list):
        raise ValueError("Invalid source catalog")
    if any(not isinstance(r, dict) or not isinstance(r.get("path"), str)
           or not isinstance(r.get("file_id", r.get("path")), str) for r in catalog["files"]):
        raise ValueError("Catalog records require string paths and source IDs")
    analysis_raw = analysis_path.read_bytes() if analysis_path.is_file() else None
    analysis = json.loads(analysis_raw) if analysis_raw is not None else {"sources": []}
    if not isinstance(analysis, dict) or not isinstance(analysis.get("sources"), list):
        raise ValueError("Invalid guitar analysis report")
    records = [r for r in catalog["files"] if _guitar_record(r)]
    ids = [r.get("file_id", r.get("path")) for r in catalog["files"]]
    guitar_ids = {r.get("file_id", r.get("path")) for r in records}
    problems = []
    for identity, count in Counter(ids).items():
        if not isinstance(identity, str) or count != 1:
            problems.append({"source_id": identity, "reason": "duplicate_or_invalid_catalog_id"})
    if any(count > 1 for count in Counter(r.get("path") for r in catalog["files"]).values()):
        problems.append({"source_id": None, "reason": "duplicate_catalog_path"})
    entries = defaultdict(list)
    for entry in analysis["sources"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("source_id"), str):
            problems.append({"source_id": None, "reason": "invalid_analysis_reference"})
            continue
        entries[entry["source_id"]].append(entry)
        if entry["source_id"] not in guitar_ids:
            problems.append({"source_id": entry["source_id"], "reason": "orphan_analysis_reference"})
    if analysis_raw is not None and analysis.get("analysis_version") != "1.0":
        problems.append({"source_id": None, "reason": "unsupported_analysis_version"})
    if set(mappings) - guitar_ids:
        problems.append({"source_id": None, "reason": "unknown_mapping_source_id", "ids": sorted(set(mappings) - guitar_ids)})
    guitar_root = root / "data/sources/guitar"
    files = {}
    if guitar_root.is_dir():
        for path in sorted(guitar_root.rglob("*"), key=lambda p: p.as_posix()):
            if path.is_file() and path.suffix.lower() in {".mid", ".midi"}:
                if path.is_symlink() or not path.resolve().is_relative_to(guitar_root.resolve()):
                    problems.append({"source_id": None, "reason": "source_symlink_not_followed", "path": str(path)})
                else:
                    files[path.relative_to(root).as_posix()] = _sha256(path)
    owners = defaultdict(list)
    for record in catalog["files"]:
        owners[record.get("path")].append(record.get("file_id", record.get("path")))
    jobs, pending, observed = [], [], {}
    missing = defaultdict(list)
    reserved = set()
    contexts = {}

    def issue(identity, reason, **details):
        pending.append({"source_id": identity, "reason": reason, **details})

    for record in records:
        identity, old = record.get("file_id", record["path"]), record["path"]
        matched = entries.get(identity, [])
        if len(matched) != 1:
            issue(identity, "analysis_reference_missing_or_ambiguous", old_path=old)
            continue
        entry = matched[0]
        expected = entry.get("source_sha256")
        if (entry.get("status") != "ok" or not isinstance(expected, str) or len(expected) != 64
                or any(key not in entry for key in FEATURES)):
            issue(identity, "analysis_missing_hash_or_features", old_path=old)
            continue
        if record.get("source_sha256") and record["source_sha256"] != expected:
            issue(identity, "catalog_analysis_hash_conflict", old_path=old)
            continue
        if record.get("error") or not isinstance(record.get("analysis"), dict):
            issue(identity, "catalog_analysis_unavailable", old_path=old)
            continue
        if any(key in record and (not isinstance(record[key], list) or any(not isinstance(v, str) for v in record[key]))
               for key in ("path_history", "genre_directory_history")):
            issue(identity, "invalid_history_metadata", old_path=old)
            continue
        contexts[identity] = (record, entry, expected)
        if old in files:
            if files[old] != expected:
                issue(identity, "content_changed_reanalysis_required", old_path=old)
                continue
            if identity in mappings and mappings[identity] != old:
                issue(identity, "original_still_exists_copy_not_relocation", old_path=old)
                continue
            reserved.add(old); observed[old] = expected
            jobs.append((identity, old))
        elif identity in mappings:
            new = mappings[identity]
            try:
                _source_path(new, root)
            except ValueError as exc:
                issue(identity, "invalid_destination", message=str(exc)); continue
            if new not in files:
                issue(identity, "mapped_destination_missing", old_path=old, new_path=new)
            elif files[new] != expected:
                issue(identity, "mapped_hash_mismatch", old_path=old, new_path=new)
            elif owners.get(new) and owners[new] != [identity]:
                issue(identity, "destination_already_registered", new_path=new, owners=owners[new])
            elif new in reserved:
                issue(identity, "destination_claimed_by_multiple_ids", new_path=new)
            else:
                reserved.add(new); observed[new] = expected; jobs.append((identity, new))
        else:
            missing[expected].append(identity)
    candidates = defaultdict(list)
    for path, digest in files.items():
        if path not in reserved and not owners.get(path):
            candidates[digest].append(path)
    hash_identities = defaultdict(list)
    for identity, (_, _, digest) in contexts.items():
        hash_identities[digest].append(identity)
    for digest, identities in missing.items():
        targets = candidates.get(digest, [])
        if len(identities) == len(targets) == 1 and len(hash_identities[digest]) == 1:
            identity, target = identities[0], targets[0]
            jobs.append((identity, target)); reserved.add(target); observed[target] = digest
        else:
            already_registered = [p for p, h in files.items() if h == digest and owners.get(p)]
            reason = "ambiguous_hash_match" if len(identities) > 1 or len(targets) > 1 or len(hash_identities[digest]) > 1 else "destination_already_registered" if already_registered else "missing_source_or_hash_mismatch"
            for identity in identities:
                issue(identity, reason, old_path=contexts[identity][0]["path"], candidates=targets,
                      related_source_ids=hash_identities[digest], registered_candidates=already_registered)
    new_catalog, new_analysis = deepcopy(catalog), deepcopy(analysis)
    record_index = {r.get("file_id", r["path"]): r for r in new_catalog["files"]}
    analysis_index = {e["source_id"]: e for e in new_analysis["sources"] if isinstance(e, dict) and isinstance(e.get("source_id"), str)}
    updates, unchanged = [], 0
    for identity, new_path in jobs:
        record, entry, expected = contexts[identity]
        old = record["path"]
        classified = classify_source_path(new_path)
        if classified.get("instrument") != "guitar":
            issue(identity, "destination_not_guitar", new_path=new_path); continue
        role = classified.get("guitar_role")
        if old != new_path and role is None:
            issue(identity, "destination_not_formal_backing_or_lead", new_path=new_path); continue
        reference_changed = old != new_path or entry.get("file_path") != new_path or (record["analysis"].get("path") != new_path)
        role_changed = record.get("guitar_role") != role or entry.get("guitar_role") != role
        classification_changed = any(record.get(key) != value for key, value in classified.items())
        if not reference_changed and not role_changed and not classification_changed:
            unchanged += 1; continue
        updated, analyzed = record_index[identity], analysis_index[identity]
        legacy_genre = record.get("genre_directory") or classify_source_path(entry.get("file_path", "")).get("genre_directory")
        if legacy_genre and classified.get("genre_directory") is None:
            updated.setdefault("legacy_genre_directory", legacy_genre)
            history = list(updated.get("genre_directory_history", []))
            if legacy_genre not in history:
                history.append(legacy_genre)
            updated["genre_directory_history"] = history
        if old != new_path:
            history = list(updated.get("path_history", []))
            if old not in history: history.append(old)
            updated["path_history"] = history
        updated.update(path=new_path, file_id=identity, present=True, source_sha256=expected, **classified)
        updated["analysis"]["path"] = new_path
        analyzed.update(file_path=new_path, guitar_role=role)
        updates.append({"old_path": old, "new_path": new_path, "old_source_id": identity, "new_source_id": identity,
                        "sha256": expected, "guitar_role": role, "tags": deepcopy(record.get("tags")),
                        "genre_tags": _tag_values(record)["style"], "legacy_genre_directory": legacy_genre,
                        "analysis_old_path": entry.get("file_path"), "analysis_reused": True,
                        "catalog_fields": ["path", "file_id (unchanged)", "analysis.path", "guitar_role", "source_sha256", "path_history", "genre_directory", "legacy_genre_directory", "genre_directory_history"],
                        "catalog_changes": {key: {"before": record.get(key), "after": updated.get(key)}
                                            for key in ("path", "present", "guitar_role", "genre_directory", "source_sha256", "path_history", "legacy_genre_directory", "genre_directory_history")
                                            if record.get(key) != updated.get(key)},
                        "analysis_fields": ["file_path", "guitar_role"]})
    context_changes = {}
    if analysis_raw is not None:
        for key, value in {"project_root": str(root), "catalog_file": str(catalog_path)}.items():
            if analysis.get(key) != value:
                context_changes[key] = {"before": analysis.get(key), "after": value}
    if updates or context_changes:
        new_analysis.update(project_root=str(root), catalog_file=str(catalog_path))
        new_catalog["reference_updated_at"] = datetime.now(timezone.utc).isoformat()
    problems.extend(pending)
    for problem in problems:
        related = [r for r in records if r.get("file_id", r["path"]) == problem.get("source_id")]
        if len(related) == 1:
            problem["tags"] = deepcopy(related[0].get("tags"))
            matched_entries = entries.get(problem["source_id"], [])
            problem["analysis_reference"] = matched_entries[0].get("file_path") if len(matched_entries) == 1 else None
    warnings = []
    unclassified = sum(classify_source_path(r["path"]).get("guitar_role") is None for r in records)
    if unclassified:
        warnings.append(f"{unclassified} catalog guitar entries have no formal role in their current paths; no musical/name/tag inference was applied.")
    unregistered = [path for path in files if path not in reserved and not owners.get(path)]
    if unregistered:
        warnings.append("Unregistered files are not merged or registered by reconciliation; scan new assets after completing relocations.")
    if problems:
        warnings.append("User review is required. Apply is blocked until every identity/reference is resolved; use an explicit verified mapping where appropriate.")
    preview = {"dry_run": True, "migration_version": VERSION, "target_count": len(records),
               "confirmed_count": len(updates) + unchanged, "change_count": len(updates), "unchanged_count": unchanged,
               "unresolved_count": len(problems), "unclassified_count": unclassified,
               "updates": updates, "unresolved": problems, "unregistered_files": unregistered,
               "analysis_context_changes": context_changes,
               "warnings": warnings, "catalog_file": str(catalog_path), "analysis_file": str(analysis_path),
               "source_ids_changed": 0, "midi_files_moved": 0, "backup_directory": None}
    return preview, [(catalog_path, catalog_raw, new_catalog), (analysis_path, analysis_raw, new_analysis)], observed


def _lock_paths(targets):
    return sorted({p.parent / REFERENCE_LOCK for p, _, _ in targets}, key=str)


def _release_locks(paths, token):
    for path in paths:
        if path.exists() and json.loads(path.read_text())["token"] == token:
            path.unlink()


def _commit(targets, observed, root, backup_directory):
    for path, old, _ in targets:
        if old is None or path.read_bytes() != old:
            raise ValueError("Reference JSON changed during planning or is missing; rerun dry-run")
    payloads = [_json_bytes(new) for _, _, new in targets]
    base = (root / backup_directory).resolve()
    if base.is_relative_to(root / "data/sources"):
        raise ValueError("Backups must not be placed inside MIDI sources")
    backup = _new_backup_dir(root, base, "guitar-reference-update")
    token = uuid.uuid4().hex
    manifest = {"version": VERSION, "state": "prepared", "lock_token": token, "targets": []}
    for index, ((path, raw, _), payload) in enumerate(zip(targets, payloads)):
        name = f"{index}.before.json"
        with (backup / name).open("xb") as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        if _sha256(backup / name) != _digest(raw):
            raise OSError("Backup verification failed before metadata update")
        manifest["targets"].append({"path": path.relative_to(root).as_posix(), "backup_file": name,
                                    "before_sha256": _digest(raw), "after_sha256": _digest(payload)})
    _write_manifest(backup, manifest)
    locks, staged = [], []
    try:
        for lock in _lock_paths(targets):
            _acquire_lock(lock, _lock_owner(token, backup))
            locks.append(lock)
        if any(path.read_bytes() != raw for path, raw, _ in targets):
            raise ValueError("Reference JSON changed after backup; update cancelled")
        for relative, digest in observed.items():
            if not (root / relative).is_file() or _sha256(root / relative) != digest:
                raise ValueError(f"Source changed during planning: {relative}")
        for (path, _, _), payload in zip(targets, payloads):
            staged.append(_stage(path, payload))
        for index, ((path, _, _), temporary) in enumerate(zip(targets, staged)):
            _install(temporary, path)
            manifest["state"] = f"updated_{index + 1}"
            _write_manifest(backup, manifest)
        manifest["state"] = "complete"
        _write_manifest(backup, manifest)
    except Exception as exc:
        failures = []
        for index in reversed(range(len(targets))):
            try:
                path = targets[index][0]
                current = _sha256(path) if path.exists() else None
                detail = manifest["targets"][index]
                if current == detail["before_sha256"]:
                    continue
                if current is not None and current != detail["after_sha256"]:
                    raise ValueError(f"Metadata was edited externally; not overwritten: {path}")
                _write_atomic(path, (backup / detail["backup_file"]).read_bytes())
            except Exception as rollback:
                failures.append(str(rollback))
        manifest["state"] = "recovery_required" if failures else "rolled_back"
        manifest["error"] = str(exc)
        manifest["rollback_errors"] = failures
        try:
            _write_manifest(backup, manifest)
        except Exception as journal_error:
            failures.append(str(journal_error))
        if manifest["state"] == "recovery_required":
            for lock in locks:
                _write_atomic(lock, _lock_owner(token, backup, active=False))
        raise ValueError(f"Guitar reference update failed ({manifest['state']}); backup: {backup}. {exc}") from exc
    finally:
        for path in staged: path.unlink(missing_ok=True)
        if manifest["state"] in {"complete", "rolled_back"}:
            _release_locks(locks, token)
    return backup


def reconcile_guitar_sources(*, project_root=".", catalog_path="data/catalog/midi_catalog.json",
                             analysis_path="data/catalog/guitar_riff_analysis.json", mapping=None,
                             apply=False, backup_directory="data/backups"):
    """Preview by default. Update references only when every identity is unambiguous."""
    root = Path(project_root).resolve()
    catalog_file, analysis_file = _metadata_path(catalog_path, root), _metadata_path(analysis_path, root)
    if catalog_file == analysis_file:
        raise ValueError("Catalog and guitar analysis must be separate JSON files")
    if mapping is None: mapping = {}
    if not isinstance(mapping, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in mapping.items()):
        raise ValueError("mapping must be an object from existing source_id to new relative MIDI path")
    normalized = {}
    for identity, path in mapping.items():
        target = _source_path(path, root)
        normalized[identity] = target.relative_to(root).as_posix()
    report, targets, observed = _plan(catalog_file, analysis_file, root, normalized)
    if not apply:
        return report
    if report["unresolved"]:
        raise ValueError("Unresolved guitar references; nothing was written. Review dry-run and provide --mapping where appropriate: " + json.dumps(report["unresolved"], ensure_ascii=False))
    report["dry_run"] = False
    if not report["updates"] and not report["analysis_context_changes"]:
        return report
    backup = _commit(targets, observed, root, backup_directory)
    report["backup_directory"] = str(backup)
    return report


def _pid_active(pid):
    if not isinstance(pid, int) or pid <= 0: return False
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except (OSError, OverflowError):
        return False
    return True


def recover_guitar_references(backup_directory, *, project_root="."):
    """Restore verified before-images without moving MIDI or overwriting new edits."""
    root = Path(project_root).resolve()
    backup = (root / backup_directory).resolve()
    manifest = json.loads((backup / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("version") != VERSION or len(manifest.get("targets", [])) != 2:
        raise ValueError("Invalid guitar reference backup manifest")
    targets = []
    for target in manifest["targets"]:
        path = _metadata_path(target["path"], root)
        before = (backup / target["backup_file"]).resolve()
        if before.parent != backup or not before.is_file() or _sha256(before) != target["before_sha256"]:
            raise ValueError("Backup content/hash is invalid; recovery was cancelled")
        if path.exists() and _sha256(path) not in {target["before_sha256"], target["after_sha256"]}:
            raise ValueError(f"Refusing to overwrite metadata edited after the transaction: {path}")
        targets.append((path, before.read_bytes(), None))
    if targets[0][0] == targets[1][0]:
        raise ValueError("Recovery targets must be distinct")
    token = manifest["lock_token"]
    locks = _lock_paths(targets)
    for lock in locks:
        if lock.exists():
            owner = json.loads(lock.read_text())
            if owner.get("token") != token or (owner.get("active", True) and _pid_active(owner.get("pid"))):
                raise ValueError("Another/live reference transaction owns the lock; recovery refused")
    acquired = []
    try:
        for lock in locks:
            if lock.exists():
                _write_atomic(lock, _lock_owner(token, backup))
            else:
                _acquire_lock(lock, _lock_owner(token, backup))
            acquired.append(lock)
        manifest["state"] = "recovering"
        _write_manifest(backup, manifest)
        for path, before, _ in targets:
            _write_atomic(path, before)
        manifest["state"] = "rolled_back"
        _write_manifest(backup, manifest)
    except Exception as exc:
        manifest["state"] = "recovery_required"
        manifest["recovery_error"] = str(exc)
        _write_manifest(backup, manifest)
        for lock in acquired:
            _write_atomic(lock, _lock_owner(token, backup, active=False))
        raise ValueError(f"Recovery incomplete; verified backups and lock retained: {backup}. {exc}") from exc
    finally:
        if manifest["state"] == "rolled_back": _release_locks(acquired, token)
    return {"recovered": True, "backup_directory": str(backup), "metadata_files": [str(p) for p, _, _ in targets], "midi_files_moved": 0}
