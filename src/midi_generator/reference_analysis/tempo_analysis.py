"""Periodicity candidates, with measured relative support and no fake confidence."""
import math
from .audio_loader import audio_dependencies


def _refine_onset_period(bpm, onsets, np):
    """Fit the dominant recurring onset phase; do not force every onset onto a grid."""
    period = 60 / bpm
    times = np.array([event["relative_time_seconds"] for event in onsets])
    weights = np.array([event["strength"] ** 2 for event in onsets])
    phases = times % period
    histogram, _ = np.histogram(phases, bins=64, range=(0, period), weights=weights)
    smooth = sum(np.roll(histogram, shift) for shift in range(-3, 4))
    phase = (int(np.argmax(smooth)) + 0.5) / 64 * period
    distance = (phases - phase + period / 2) % period - period / 2
    selected = np.abs(distance) <= period * 0.12
    if np.count_nonzero(selected) < 8:
        return bpm
    x = np.rint((times[selected] - phase) / period)
    y, w = times[selected], weights[selected]
    if np.ptp(x) < 4 or np.sum(w) <= 0:
        return bpm
    mean_x, mean_y = np.average(x, weights=w), np.average(y, weights=w)
    denominator = np.sum(w * (x - mean_x) ** 2)
    if denominator <= 0:
        return bpm
    slope = float(np.sum(w * (x - mean_x) * (y - mean_y)) / denominator)
    if slope <= 0 or abs(slope / period - 1) > 0.03:
        return bpm
    residual = y - (mean_y + slope * (x - mean_x))
    if float(np.median(np.abs(residual))) > period * 0.06:
        return bpm
    return 60 / slope


def analyze_tempo(envelope, onsets, sample_rate, hop_length, duration_seconds):
    np, _, librosa = audio_dependencies()
    empty = {"estimated_bpm": None, "confidence": None, "candidates": [],
             "method": "tempogram_and_onset_periodicity", "status": "insufficient_evidence",
             "selection_basis": "librosa tempogram with a 120 BPM log-tempo prior, supported autocorrelation peak, then dominant onset-phase regression where supported.",
             "score_definition": "Relative positive autocorrelation support, not a probability."}
    if duration_seconds < 2 or len(onsets) < 4 or len(envelope) < 4:
        return empty
    centered = envelope - np.mean(envelope)
    max_lag = min(len(centered) - 1, math.ceil(60 * sample_rate / (hop_length * 30)))
    correlation = librosa.autocorrelate(centered, max_size=max_lag + 1)
    if not len(correlation) or correlation[0] <= 1e-12:
        return empty
    rate = sample_rate / hop_length
    peaks = []
    for lag in range(max(1, math.ceil(60 * rate / 240)), min(max_lag, math.floor(60 * rate / 40))):
        if correlation[lag] <= 0 or correlation[lag] < correlation[lag - 1] or correlation[lag] < correlation[lag + 1]:
            continue
        left, center, right = (float(correlation[lag + shift]) for shift in (-1, 0, 1))
        denominator = left - 2 * center + right
        shift = 0.5 * (left - right) / denominator if abs(denominator) > 1e-12 else 0.0
        lag_refined = lag + max(-0.5, min(0.5, shift))
        bpm = 60 * rate / lag_refined
        support = min(1.0, max(0.0, center / float(correlation[0])))
        peaks.append((support, bpm))
    if not peaks:
        return empty
    peaks.sort(key=lambda value: (-value[0], value[1]))
    strongest, primary = peaks[0]
    if strongest < 0.05:
        return empty
    estimate = float(np.asarray(librosa.feature.tempo(
        onset_envelope=envelope, sr=sample_rate, hop_length=hop_length,
        start_bpm=120, std_bpm=1.0, max_tempo=240,
    )).reshape(-1)[0])
    nearby = [(support, bpm) for support, bpm in peaks if abs(bpm / estimate - 1) <= 0.06]
    if nearby:
        _, primary = max(nearby)
    primary = _refine_onset_period(primary, onsets, np)
    choices = [(primary, "primary_periodicity")]
    for bpm, relationship in ((primary / 2, "half_tempo"), (primary * 2, "double_tempo")):
        if 30 <= bpm <= 360:
            choices.append((bpm, relationship))
    for _, bpm in peaks:
        if all(abs(bpm / existing - 1) > 0.03 for existing, _ in choices):
            choices.append((bpm, "alternative_periodicity"))
        if len(choices) >= 6:
            break
    candidates = []
    for bpm, relationship in choices:
        lag = 60 * rate / bpm
        measured = float(np.interp(lag, np.arange(len(correlation)), correlation)) if lag <= max_lag else None
        support = min(1.0, max(0.0, measured / float(correlation[0]))) if measured is not None else None
        candidates.append({"bpm": float(bpm), "confidence": None,
                           "relative_score": min(1.0, support / strongest) if support is not None else None,
                           "periodicity_score": support, "relationship": relationship})
    return {**empty, "estimated_bpm": float(primary), "candidates": candidates, "status": "estimated"}
