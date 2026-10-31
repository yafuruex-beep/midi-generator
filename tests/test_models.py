import pytest
from midi_generator.core.models import GenerationRequest


def test_valid_generation_request():
    request = GenerationRequest(instrument="drums", section="breakdown", tempo=105, bars=4)
    assert request.tempo == 105
    assert request.time_signature == (4, 4)


@pytest.mark.parametrize("kwargs", [
    {"instrument": "", "section": "verse", "tempo": 100, "bars": 4},
    {"instrument": "drums", "section": "", "tempo": 100, "bars": 4},
    {"instrument": "drums", "section": "verse", "tempo": 0, "bars": 4},
    {"instrument": "drums", "section": "verse", "tempo": 100, "bars": 0},
])
def test_invalid_generation_request(kwargs):
    with pytest.raises(ValueError):
        GenerationRequest(**kwargs)
