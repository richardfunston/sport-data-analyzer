#!/usr/bin/env bash
# Helpers for driving the web01 container (CT 101 on pve02) from the Hermes host.
PVE_HOST="root@192.168.10.8"
PVE_KEY="/home/hermesog/.ssh/id_ed25519_pve02"
CTID=101

# Run a shell command inside web01.
w01() {
  ssh -i "$PVE_KEY" -o BatchMode=yes "$PVE_HOST" \
    "pct exec $CTID -- bash -lc $(printf '%q' "$1")"
}

# Copy a local file into web01 (base64 through the pve host, no extra creds).
w01push() {
  local src="$1" dst="$2"
  base64 -w0 "$src" | ssh -i "$PVE_KEY" -o BatchMode=yes "$PVE_HOST" \
    "pct exec $CTID -- bash -c $(printf '%q' "base64 -d > '$dst'")"
}

# Copy a local directory tree into web01 as a tar stream.
w01pushdir() {
  local src="$1" dst="$2"
  tar czf - -C "$src" . | ssh -i "$PVE_KEY" -o BatchMode=yes "$PVE_HOST" \
    "pct exec $CTID -- bash -c $(printf '%q' "mkdir -p '$dst' && tar xzf - -C '$dst'")"
}
