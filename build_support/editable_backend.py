"""Setuptools builds with a PEP 660 source proxy that needs no startup .pth file.

Python 3.13 skips hidden .pth files on macOS. This workspace repeatedly acquired
that flag on setuptools' editable path file. A regular importable proxy package
loads the source package directly, even when installed files acquire hidden flags.
Ordinary wheels and source distributions still use setuptools unchanged.
"""
from __future__ import annotations

import base64
import csv
import hashlib
import io
from pathlib import Path
import zipfile


def __getattr__(name):
    from setuptools import build_meta
    return getattr(build_meta, name)


def get_requires_for_build_editable(config_settings=None):
    from setuptools import build_meta
    return build_meta.get_requires_for_build_wheel(config_settings)


def prepare_metadata_for_build_editable(metadata_directory, config_settings=None):
    from setuptools import build_meta
    return build_meta.prepare_metadata_for_build_wheel(metadata_directory, config_settings)


def _make_source_proxy(wheel_path: Path, source_init: Path) -> None:
    """Replace copied package files with a source loader and rebuild wheel RECORD."""
    source_init = source_init.resolve()
    if not source_init.is_file():
        raise FileNotFoundError(f"Source package initializer not found: {source_init}")
    proxy = f'''# Generated for editable installation; source files remain authoritative.
def _load_source():
    from importlib.util import spec_from_file_location
    from pathlib import Path
    import sys
    source = Path({str(source_init)!r})
    if not source.is_file():
        raise ImportError(f"Editable MIDI Generator source was moved or deleted: {{source}}. Reinstall from its current location.")
    spec = spec_from_file_location(__name__, source, submodule_search_locations=[str(source.parent)])
    module = sys.modules[__name__]
    module.__file__ = str(source)
    module.__path__ = list(spec.submodule_search_locations)
    module.__spec__ = spec
    module.__loader__ = spec.loader
    spec.loader.exec_module(module)
_load_source()
del _load_source
'''.encode("utf-8")
    with zipfile.ZipFile(wheel_path) as archive:
        contents = {name: archive.read(name) for name in archive.namelist()
                    if not name.startswith("midi_generator/") and not name.endswith("/")}
    record_name = next(name for name in contents if name.endswith(".dist-info/RECORD"))
    contents.pop(record_name)
    contents["midi_generator/__init__.py"] = proxy
    record = io.StringIO(newline="")
    writer = csv.writer(record, lineterminator="\n")
    for name, data in sorted(contents.items()):
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode("ascii")
        writer.writerow((name, f"sha256={digest}", len(data)))
    writer.writerow((record_name, "", ""))
    contents[record_name] = record.getvalue().encode("utf-8")
    with zipfile.ZipFile(wheel_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(contents.items()):
            archive.writestr(name, data)


def build_editable(wheel_directory, config_settings=None, metadata_directory=None):
    from setuptools import build_meta
    filename = build_meta.build_wheel(wheel_directory, config_settings, metadata_directory)
    source_init = Path(__file__).resolve().parents[1] / "src/midi_generator/__init__.py"
    _make_source_proxy(Path(wheel_directory) / filename, source_init)
    return filename
