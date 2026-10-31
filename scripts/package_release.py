"""Build a source-only distribution without local assets or environments."""
import argparse
import hashlib
import json
from pathlib import Path
import tomllib
import zipfile

ROOT_FILES = ("README.md", "AGENTS.md", "pyproject.toml", "MANIFEST.in", ".gitignore", "LICENSE", "LICENSE.txt")
CONFIG_FILES = ("drum_groove.json", "guitar_analysis.json", "instrument_channels.json")
SOURCE_TREES = {"src/midi_generator": ".py", "tests": ".py", "build_support": ".py",
                "scripts": ".py", "docs": ".md", "schemas": ".json"}
EMPTY_DIRECTORIES = ("data/catalog", "data/cache", "data/references", "data/reports",
                     "data/backups", "data/archive", "data/sources/drums/metalcore",
                     "data/sources/drums/hardcore", "data/sources/drums/other",
                     "data/sources/guitar/metalcore", "data/sources/guitar/hardcore",
                     "data/sources/guitar/other", "data/sources/bass/metalcore",
                     "data/sources/bass/other", "data/sources/synth/metalcore",
                     "data/sources/synth/other", "data/sources/unclassified", "output")


def build_release(root, destination):
    root, destination = Path(root).resolve(), Path(destination).resolve()
    if destination.suffix.lower() != ".zip":
        raise ValueError("Release destination must end in .zip")
    metadata = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    files = {root / name for name in ROOT_FILES if (root / name).is_file()}
    for directory, suffix in SOURCE_TREES.items():
        files.update(p for p in (root / directory).rglob("*" + suffix)
                     if "__pycache__" not in p.parts)
    files.update(root / "config" / name for name in CONFIG_FILES)
    files.add(root / "examples/python_api_example.py")
    files.add(root / "examples/requests/drums_breakdown.json")
    contents = {}
    for path in sorted(files):
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError(f"Release source must be a regular project file: {path}")
        contents[path.relative_to(root).as_posix()] = path.read_bytes()
    for directory in EMPTY_DIRECTORIES:
        contents[directory + "/.gitkeep"] = b""
    manifest = {"project": metadata["project"]["name"], "version": metadata["project"]["version"],
                "policy": "Source only; user assets, catalogs, reports, backups, caches and environments are excluded.",
                "files": {name: hashlib.sha256(data).hexdigest() for name, data in sorted(contents.items())}}
    contents["release_manifest.json"] = (json.dumps(manifest, indent=2) + "\n").encode()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(contents.items()):
            info = zipfile.ZipInfo("midi-generator/" + name)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)
    return {"archive": str(destination), "file_count": len(contents),
            "sha256": hashlib.sha256(destination.read_bytes()).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="dist/midi-generator-0.1.0-source.zip")
    args = parser.parse_args()
    print(json.dumps(build_release(Path(__file__).resolve().parents[1], args.output), indent=2))


if __name__ == "__main__":
    main()
