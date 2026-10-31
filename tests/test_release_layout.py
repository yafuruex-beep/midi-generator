import hashlib
import importlib.util
import json
from pathlib import Path
import zipfile

import pytest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("package_release", ROOT / "scripts/package_release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


def test_release_contains_installable_sources_and_no_local_assets(tmp_path):
    destination = tmp_path / "release.zip"
    release.build_release(ROOT, destination)
    with zipfile.ZipFile(destination) as archive:
        names = {name.removeprefix("midi-generator/") for name in archive.namelist()}
        assert {"pyproject.toml", "build_support/editable_backend.py", "src/midi_generator/__init__.py",
                "scripts/package_release.py", "tests/test_release_layout.py",
                "examples/requests/drums_breakdown.json", "config/guitar_analysis.json",
                "data/sources/drums/metalcore/.gitkeep", "data/references/.gitkeep"} <= names
        assert not any(name.endswith((".mid", ".midi", ".mp3", ".wav", ".flac")) for name in names)
        assert not any(name.startswith((".venv", "build/", "configs/")) or "egg-info" in name for name in names)
        assert not any(name.startswith(("data/", "output/")) and not name.endswith("/.gitkeep") for name in names)
        manifest = json.loads(archive.read("midi-generator/release_manifest.json"))
        for name, digest in manifest["files"].items():
            assert hashlib.sha256(archive.read("midi-generator/" + name)).hexdigest() == digest
    assert json.loads((ROOT / "examples/requests/drums_breakdown.json").read_text())["instrument"] == "drums"


def test_release_is_reproducible_and_never_overwrites(tmp_path):
    first, second = tmp_path / "one.zip", tmp_path / "two.zip"
    release.build_release(ROOT, first)
    release.build_release(ROOT, second)
    assert first.read_bytes() == second.read_bytes()
    with pytest.raises(FileExistsError):
        release.build_release(ROOT, first)
    with pytest.raises(ValueError):
        release.build_release(ROOT, tmp_path / "source.mid")
