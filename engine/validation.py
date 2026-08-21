"""
Input sanitization shared by every API surface (desktop UI, MCP, Python).
Every public service method validates through these helpers so no caller
can hand the engine a bad path, an out-of-range number or a malformed list.
"""
import os
import math
import numpy as np

LOAD_EXTENSIONS = {".obj", ".fbx", ".glb", ".gltf", ".stl", ".ply", ".3ds", ".dae", ".3mf", ".off"}
MAX_FILE_MB = 2048


class ValidationError(ValueError):
    pass


def input_path(path, allowed=LOAD_EXTENSIONS) -> str:
    if not isinstance(path, str) or not path.strip():
        raise ValidationError("A file path is required.")
    p = os.path.abspath(os.path.expanduser(path.strip()))
    if not os.path.isfile(p):
        raise ValidationError(f"File not found: {p}")
    ext = os.path.splitext(p)[1].lower()
    if allowed and ext not in allowed:
        raise ValidationError(f"Unsupported file type '{ext}'. Supported: {', '.join(sorted(allowed))}")
    size_mb = os.path.getsize(p) / 1e6
    if size_mb > MAX_FILE_MB:
        raise ValidationError(f"File is {size_mb:.0f} MB; the limit is {MAX_FILE_MB} MB.")
    return p


def output_path(path, extension: str) -> str:
    if not isinstance(path, str) or not path.strip():
        raise ValidationError("An output path is required.")
    p = os.path.abspath(os.path.expanduser(path.strip()))
    if os.path.isdir(p):
        raise ValidationError(f"Output path is a directory: {p}")
    parent = os.path.dirname(p)
    if parent and not os.path.isdir(parent):
        raise ValidationError(f"Output folder does not exist: {parent}")
    if not p.lower().endswith(extension.lower()):
        p += extension
    return p


def number(value, name: str, lo: float, hi: float, default=None) -> float:
    if value is None:
        if default is None:
            raise ValidationError(f"{name} is required.")
        value = default
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise ValidationError(f"{name} must be a number.")
    if math.isnan(v) or math.isinf(v):
        raise ValidationError(f"{name} must be finite.")
    return min(hi, max(lo, v))


def boolean(value, default=False) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def choice(value, name: str, options, default=None) -> str:
    if value is None:
        value = default
    v = str(value).strip().lower()
    if v not in options:
        raise ValidationError(f"{name} must be one of: {', '.join(options)}")
    return v


def index_list(values, name: str, upper: int) -> list:
    if values is None:
        raise ValidationError(f"{name} is required.")
    if isinstance(values, (str, bytes)) or not hasattr(values, "__iter__"):
        raise ValidationError(f"{name} must be a list of integers.")
    out = []
    for v in values:
        try:
            i = int(v)
        except (TypeError, ValueError):
            raise ValidationError(f"{name} must contain only integers.")
        if i < 0 or i >= upper:
            raise ValidationError(f"{name}: index {i} is out of range 0..{upper - 1}.")
        out.append(i)
    return sorted(set(out))


def rotation_matrix(value) -> np.ndarray:
    try:
        r = np.asarray(value, dtype=float).reshape(3, 3)
    except Exception:
        raise ValidationError("Rotation must be a 3x3 matrix.")
    if not np.all(np.isfinite(r)):
        raise ValidationError("Rotation matrix contains non-finite values.")
    if not np.allclose(r @ r.T, np.eye(3), atol=1e-4) or np.linalg.det(r) < 0:
        raise ValidationError("Rotation matrix must be orthonormal with positive determinant.")
    return r
