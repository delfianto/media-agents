from dataclasses import dataclass


@dataclass(frozen=True)
class Workload:
    clips: int
    clip_duration: float
    ssimulacra_frames: int
    full: bool = False


WORKLOADS = {
    "quick": Workload(clips=4, clip_duration=2.0, ssimulacra_frames=8),
    "deep": Workload(clips=12, clip_duration=5.0, ssimulacra_frames=24),
    "full": Workload(clips=1, clip_duration=0.0, ssimulacra_frames=48, full=True),
}


def stratified_timestamps(duration: float, count: int, margin: float = 0.0) -> list[float]:
    if duration <= 0:
        raise ValueError("duration must be positive")
    if count <= 0:
        return []
    usable_start = min(max(0.0, margin), duration / 2)
    usable_end = max(usable_start, duration - margin)
    span = usable_end - usable_start
    if span == 0:
        return [usable_start] * count
    return [usable_start + span * ((index + 0.5) / count) for index in range(count)]


def clip_ranges(duration: float, count: int, clip_duration: float) -> list[tuple[float, float]]:
    if clip_duration <= 0 or clip_duration >= duration:
        return [(0.0, duration)]
    centers = stratified_timestamps(duration, count, margin=clip_duration / 2)
    return [
        (max(0.0, min(duration - clip_duration, center - clip_duration / 2)), clip_duration)
        for center in centers
    ]
