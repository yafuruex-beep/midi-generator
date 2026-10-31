"""Linear-amplitude RMS measurements on the analysis downmix."""
from .audio_loader import audio_dependencies


def analyze_energy(samples, sample_rate, start_seconds, *, window_seconds=0.1):
    np, _, _ = audio_dependencies()
    size = max(1, round(sample_rate * window_seconds))
    frames = []
    for offset in range(0, len(samples), size):
        block = samples[offset:offset + size]
        rms = float(np.sqrt(np.mean(block ** 2)))
        frames.append({"time_seconds": start_seconds + offset / sample_rate,
                       "relative_time_seconds": offset / sample_rate,
                       "duration_seconds": len(block) / sample_rate, "rms": rms})
    mean_square = float(np.mean(samples ** 2)) if len(samples) else 0.0
    return {"mean_rms": sum(frame["rms"] for frame in frames) / len(frames) if frames else 0.0,
            "segment_rms": mean_square ** 0.5, "mean_square_energy": mean_square,
            "window_seconds": window_seconds, "rms_over_time": frames,
            "units": "Linear normalized PCM amplitude; not calibrated SPL or loudness."}
