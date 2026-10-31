"""Spectral-flux onset detection, without instrument assignment."""
from .audio_loader import audio_dependencies


def analyze_onsets(samples, sample_rate, start_seconds, duration_seconds, *, hop_length=256, n_fft=1024):
    np, _, librosa = audio_dependencies()
    if len(samples) < n_fft or float(np.max(np.abs(samples), initial=0)) < 1e-8:
        return [], np.zeros(max(1, len(samples) // hop_length + 1))
    envelope = librosa.onset.onset_strength(y=samples, sr=sample_rate, hop_length=hop_length,
                                           n_fft=n_fft, n_mels=64, aggregate=np.mean)
    frames = librosa.onset.onset_detect(onset_envelope=envelope, sr=sample_rate, hop_length=hop_length,
                                        normalize=True, units="frames", backtrack=False)
    peak = float(np.max(envelope, initial=0))
    onsets = []
    for frame in frames:
        relative = float(frame * hop_length / sample_rate)
        if relative >= duration_seconds:
            continue
        raw = float(max(0, envelope[int(frame)]))
        onsets.append({"time_seconds": start_seconds + relative,
                       "relative_time_seconds": relative,
                       "strength": min(1.0, raw / peak) if peak else 0.0,
                       "raw_strength": raw})
    return onsets, envelope
