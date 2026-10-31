"""Template-based drum MIDI generation."""

def __getattr__(name):
    # Analysis must be importable without eagerly importing catalog search/generation.
    if name == "generate_drums":
        from midi_generator.modules.drums.generator import generate_drums
        return generate_drums
    raise AttributeError(name)

__all__ = ["generate_drums"]
