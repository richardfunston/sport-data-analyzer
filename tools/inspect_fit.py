"""Dump the structure of a FIT file: message types, fields, and session data."""
import sys
import collections

import fitdecode

path = sys.argv[1]
msg_fields = collections.OrderedDict()
msg_count = collections.Counter()
sessions = []

with fitdecode.FitReader(path) as fr:
    for frame in fr:
        if frame.frame_type != fitdecode.FIT_FRAME_DATA:
            continue
        name = frame.name
        msg_count[name] += 1
        if name not in msg_fields:
            msg_fields[name] = collections.Counter()
        for f in frame.fields:
            msg_fields[name][f.name] += 1
        if name == "session":
            sessions.append({f.name: f.value for f in frame.fields})

print("=== MESSAGE TYPES ===")
for name, count in msg_count.items():
    print(f"{name:24s} frames={count:<6d} fields={len(msg_fields[name])}")
    if name in ("session", "activity", "lap", "file_id", "sport", "device_info"):
        for fn, cnt in msg_fields[name].items():
            print(f"      - {fn} ({cnt})")

print("\n=== SESSION MESSAGES ===")
for s in sessions:
    for k, v in s.items():
        print(f"  {k:30s} = {v!r}")
    print("  ---")
