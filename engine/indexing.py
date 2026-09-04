"""
Fast de-duplication of integer rows — Geekatplay Studio

Finding which edges are shared, which faces are duplicates and which corners can be
welded is all the same question: which rows of this integer array are equal?

`numpy.unique(..., axis=0)` answers it by viewing each row as an opaque block of
bytes and sorting those, which is correct but slow — on a five-million-face model the
row sorts inside one analysis pass cost about four seconds, and the vertex-buffer
build another five.

When the values fit, packing each row into a single 64-bit integer and sorting that
instead does the same job several times faster: one contiguous 8-byte key per row
rather than a byte-wise comparison across columns. The packing is exact, so the
answer is identical, and when the values do not fit the original path is used.
"""
import numpy as np

# Room for the sign bit, so the packed keys stay comfortably inside uint64.
MAX_PACKED_BITS = 63


def pack_rows(rows: np.ndarray) -> np.ndarray | None:
    """
    One uint64 per row, preserving equality exactly. None when it will not fit.

    Each column is shifted down by its own minimum and given only as many bits as
    its range needs, so what matters is how much the values vary, not how large
    they are.
    """
    rows = np.asarray(rows)
    if rows.ndim != 2 or rows.shape[1] == 0 or not np.issubdtype(rows.dtype, np.integer):
        return None
    if len(rows) == 0:
        return np.zeros(0, dtype=np.uint64)

    lows = rows.min(axis=0)
    spans = (rows.max(axis=0).astype(object) - lows.astype(object) + 1)
    widths = [max(1, int(span - 1).bit_length()) for span in spans]
    if sum(widths) > MAX_PACKED_BITS:
        return None

    key = np.zeros(len(rows), dtype=np.uint64)
    for column, width in enumerate(widths):
        shifted = (rows[:, column].astype(np.int64) - int(lows[column])).astype(np.uint64)
        key = (key << np.uint64(width)) | shifted
    return key


def quantise(values: np.ndarray, decimals: int) -> np.ndarray | None:
    """
    Float rows as integers, so equality-to-a-tolerance becomes exact equality.

    None when the scaled values would not survive the conversion, which keeps the
    caller honest about very large coordinates rather than wrapping them silently.
    """
    scale = 10.0 ** decimals
    scaled = np.round(np.asarray(values, dtype=np.float64) * scale)
    if not np.isfinite(scaled).all() or np.abs(scaled).max(initial=0.0) > 2 ** 62:
        return None
    return scaled.astype(np.int64)


def unique_rows(rows: np.ndarray):
    """
    Group identical rows. Returns (first_index, inverse, counts):

        first_index  row number of the first appearance of each distinct row
        inverse      for every input row, which distinct row it is
        counts       how many times each distinct row appears

    Groups are ordered by their packed key rather than by first appearance, exactly
    as `numpy.unique(axis=0)` orders them.
    """
    rows = np.asarray(rows)
    if len(rows) == 0:
        empty = np.zeros(0, dtype=np.int64)
        return empty, empty, empty

    key = pack_rows(rows)
    if key is None:
        _, first, inverse, counts = np.unique(
            rows, axis=0, return_index=True, return_inverse=True, return_counts=True)
        return first, inverse.reshape(-1), counts

    _, first, inverse, counts = np.unique(
        key, return_index=True, return_inverse=True, return_counts=True)
    return first, inverse.reshape(-1), counts


def duplicate_mask(rows: np.ndarray) -> np.ndarray:
    """True for every row that repeats one seen earlier."""
    rows = np.asarray(rows)
    mask = np.ones(len(rows), dtype=bool)
    if len(rows) == 0:
        return ~mask
    first, _, _ = unique_rows(rows)
    mask[first] = False
    return mask
