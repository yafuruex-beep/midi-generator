"""Regression coverage for editable imports without startup .pth processing."""
import base64
import csv
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import zipfile


def test_editable_proxy_loads_live_source_even_when_installed_files_are_hidden(tmp_path):
    backend_path = Path(__file__).resolve().parents[1] / "build_support/editable_backend.py"
    spec = importlib.util.spec_from_file_location("test_editable_backend", backend_path)
    backend = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(backend)

    source = tmp_path / "src/midi_generator"
    source.mkdir(parents=True)
    initializer = source / "__init__.py"
    initializer.write_text("value = 'original'\n", encoding="utf-8")
    (source / "child.py").write_text("answer = 42\n", encoding="utf-8")
    wheel = tmp_path / "example.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("midi_generator/__init__.py", "raise RuntimeError('copied package')")
        archive.writestr("midi_generator/stale.py", "")
        archive.writestr("midi_generator-0.1.0.dist-info/METADATA", "Name: midi-generator\nVersion: 0.1.0\n")
        archive.writestr("midi_generator-0.1.0.dist-info/RECORD", "")
    backend._make_source_proxy(wheel, initializer)

    installed = tmp_path / "installed"
    with zipfile.ZipFile(wheel) as archive:
        assert not any(name.endswith(".pth") for name in archive.namelist())
        assert "midi_generator/stale.py" not in archive.namelist()
        metadata = archive.read("midi_generator-0.1.0.dist-info/METADATA")
        assert b"Name: midi-generator" in metadata
        records = csv.reader(io.StringIO(archive.read("midi_generator-0.1.0.dist-info/RECORD").decode()))
        for name, digest, size in records:
            if digest:
                data = archive.read(name)
                assert int(size) == len(data)
                expected = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
                assert digest == f"sha256={expected}"
        archive.extractall(installed)

    proxy = installed / "midi_generator/__init__.py"
    if hasattr(os, "chflags") and hasattr(stat, "UF_HIDDEN"):
        os.chflags(proxy, proxy.stat().st_flags | stat.UF_HIDDEN)
    command = (
        f"import sys; sys.path.insert(0, {str(installed)!r}); "
        "import midi_generator, midi_generator.child, json; "
        "print(json.dumps([midi_generator.__file__, midi_generator.value, midi_generator.child.answer]))"
    )
    def imported():
        result = subprocess.run([sys.executable, "-I", "-B", "-c", command],
                                cwd=tmp_path, check=True, capture_output=True, text=True)
        return json.loads(result.stdout)

    assert imported() == [str(initializer), "original", 42]
    initializer.write_text("value = 'edited without reinstall'\n", encoding="utf-8")
    assert imported() == [str(initializer), "edited without reinstall", 42]
