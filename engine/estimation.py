"""
Runtime and duration estimators for MeshService operations.
"""
import math


def estimate_seconds(operation: str, faces: int) -> float:
    """
    Rough wall-clock estimate, measured on a mid-range desktop. Used to warn
    the user before a long operation and to drive the progress bar; never exact.
    """
    f = max(1, faces) / 1e6
    return {
        "load": 8 * f + 1,
        "analyse": 4 * f + 0.2,
        "repair": 25 * f + 0.5,
        "fix_slivers": 30 * f + 0.3,
        "simplify": 6 * f + 0.3,
        "retopo": 45 * f + 3,
        "unwrap": 12 * f + 1.5,
        "pbr_gen": 3.0,
        "bake_glb": 6 * f + 2.0,
    }.get(operation, 5 * f + 0.5)


def describe_duration(seconds: float) -> str:
    """Human-friendly duration string."""
    if seconds < 3:
        return "a moment"
    if seconds < 60:
        return f"about {int(round(seconds / 5.0)) * 5} seconds"
    mins = seconds / 60.0
    lo = max(1, int(mins))
    hi = max(lo + 1, int(math.ceil(mins * 1.4)))
    return f"about {lo}–{hi} minutes"
