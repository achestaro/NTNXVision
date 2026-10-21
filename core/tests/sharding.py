"""Deterministic split of the test files into CI shards (``pytest --shard I/N``).

Whole files go to a shard, so module and session fixtures are built in one place. Files are
placed longest first, each on the currently lightest shard (greedy LPT), using the measured
seconds in ``shard-weights.json``; a file without a weight (a new one) counts as the median.
The weights only steer the balance: a stale or missing entry can make one shard slower,
never drop a file. ``test_sharding.py`` proves every file lands in exactly one shard.

Refresh the weights after big test changes (from core/):

    pytest -p no:cacheprovider --durations=0 -q > durations.txt
    python -m tests.sharding durations.txt
"""

from __future__ import annotations

import json
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path

WEIGHTS_FILE = Path(__file__).with_name("shard-weights.json")


def parse_shard(value: str) -> tuple[int, int]:
    """``"2/4"`` -> ``(2, 4)``; index is 1-based."""
    match = re.fullmatch(r"\s*(\d+)\s*/\s*(\d+)\s*", value)
    if not match:
        raise ValueError(f"--shard expects I/N (e.g. 2/4), got {value!r}")
    index, total = int(match.group(1)), int(match.group(2))
    if total < 1 or not 1 <= index <= total:
        raise ValueError(f"--shard {value}: need 1 <= I <= N")
    return index, total


def load_weights() -> dict[str, float]:
    try:
        data = json.loads(WEIGHTS_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    return {str(k): float(v) for k, v in data.items()}


def assign(files: list[str], total: int, weights: dict[str, float]) -> dict[str, int]:
    """Map each file (``tests/test_x.py``) to a 1-based shard. Deterministic for a given
    file set and weights, whatever order the files arrive in."""
    known = [weights[f] for f in files if f in weights]
    default = statistics.median(known) if known else 1.0
    load = [0.0] * total
    out: dict[str, int] = {}
    for f in sorted(set(files), key=lambda f: (-weights.get(f, default), f)):
        lightest = min(range(total), key=lambda i: (load[i], i))
        load[lightest] += weights.get(f, default)
        out[f] = lightest + 1
    return out


def weights_from_durations(text: str) -> dict[str, float]:
    """Per-file seconds from the ``--durations=0`` table of a serial run."""
    per_file: dict[str, float] = defaultdict(float)
    for line in text.splitlines():
        match = re.match(r"^\s*([0-9.]+)s\s+(?:call|setup|teardown)\s+(\S+?)::", line)
        if match:
            per_file[match.group(2)] += float(match.group(1))
    return {f: round(s, 1) for f, s in sorted(per_file.items())}


if __name__ == "__main__":
    weights = weights_from_durations(Path(sys.argv[1]).read_text(encoding="utf-8"))
    # Files whose tests all ran under the 5 ms reporting floor are absent from the table:
    # record them as near-free rather than leave them to count as the median.
    for path in sorted(Path(__file__).parent.glob("test_*.py")):
        weights.setdefault(f"tests/{path.name}", 0.1)
    WEIGHTS_FILE.write_text(json.dumps(weights, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"{len(weights)} files, {sum(weights.values()):.0f} s -> {WEIGHTS_FILE.name}")
