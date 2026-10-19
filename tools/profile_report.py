"""Time and profile the report build, to catch analysis slowdowns.

Usage: python tools/profile_report.py <file.fit>
"""

from __future__ import annotations

import cProfile
import io
import pstats
import sys
import time

from app.analysis import build_report
from app.fitio import parse_fit

path = sys.argv[1]
with open(path, "rb") as handle:
    raw = handle.read()

start = time.perf_counter()
activity = parse_fit(raw)
parsed = time.perf_counter()
print(f"parse_fit      {parsed - start:7.2f}s   samples={activity['meta']['sample_count']}")

profiler = cProfile.Profile()
profiler.enable()
report = build_report(activity)
profiler.disable()
built = time.perf_counter()
print(f"build_report   {built - parsed:7.2f}s   version={report['version']}")
print(f"total          {built - start:7.2f}s")

stream = io.StringIO()
pstats.Stats(profiler, stream=stream).sort_stats("tottime").print_stats(15)
print()
print("\n".join(stream.getvalue().splitlines()[4:26]))
