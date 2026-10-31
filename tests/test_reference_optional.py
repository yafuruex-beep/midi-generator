"""Existing CLI/API must import with every audio dependency unavailable."""
import subprocess
import sys
import json

import mido
import pytest


@pytest.mark.parametrize("arguments", [["--help"], ["generate", "--help"], ["reference", "analyze", "--help"]])
def test_cli_help_without_audio_dependencies(arguments):
    code = '''
import builtins, runpy, sys
original = builtins.__import__
def blocked(name, *args, **kwargs):
    if name.split('.')[0] in {'numpy','librosa','soundfile','scipy','numba'}:
        raise ModuleNotFoundError('blocked optional dependency: ' + name)
    return original(name, *args, **kwargs)
builtins.__import__ = blocked
sys.argv = ['midi-generator'] + ARGS
runpy.run_module('midi_generator.cli', run_name='__main__')
'''.replace('ARGS', repr(arguments))
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout


def test_audio_request_without_extras_has_installation_guidance(tmp_path):
    source = tmp_path / "exists.wav"
    source.write_bytes(b"not decoded until dependency check")
    code = '''
import builtins
original = builtins.__import__
def blocked(name, *args, **kwargs):
    if name.split('.')[0] in {'numpy','librosa','soundfile'}:
        raise ModuleNotFoundError(name)
    return original(name, *args, **kwargs)
builtins.__import__ = blocked
from midi_generator.reference_analysis import analyze_reference_audio, AudioAnalysisError
try:
    analyze_reference_audio(SOURCE)
except AudioAnalysisError as error:
    print(error)
else:
    raise AssertionError('Missing optional dependencies must be reported')
'''.replace('SOURCE', repr(str(source)))
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert ".[audio]" in result.stdout


def test_existing_generation_works_when_audio_imports_are_blocked(tmp_path):
    from midi_generator.storage.catalog import scan_midi_catalog
    from midi_generator.storage.patterns import add_tags

    source = tmp_path / "data/sources/drums/other/basic.mid"
    source.parent.mkdir(parents=True)
    midi = mido.MidiFile(type=0, ticks_per_beat=480)
    midi.tracks.append(mido.MidiTrack([
        mido.MetaMessage("time_signature", numerator=4, denominator=4),
        mido.Message("note_on", note=36, velocity=100, channel=9),
        mido.Message("note_off", note=36, channel=9, time=120),
        mido.MetaMessage("end_of_track", time=1800),
    ]))
    midi.save(source)
    scan_midi_catalog(project_root=tmp_path)
    add_tags("basic.mid", section="intro", catalog_path=tmp_path / "data/catalog/midi_catalog.json")
    code = '''
import builtins, runpy, sys
original = builtins.__import__
def blocked(name, *args, **kwargs):
    if name.split('.')[0] in {'numpy','librosa','soundfile','scipy','numba'}:
        raise ModuleNotFoundError(name)
    return original(name, *args, **kwargs)
builtins.__import__ = blocked
sys.argv = ['midi-generator','generate','--instrument','drums','--section','intro',
            '--tempo','160','--bars','1','--project-root',ROOT]
runpy.run_module('midi_generator.cli', run_name='__main__')
'''.replace('ROOT', repr(str(tmp_path)))
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["output_files"]
