#!/usr/bin/env bash
# Sync the repo to web01's test workspace and run pytest there.
# The Hermes host has no Flask/pytest; web01 is the test host.
#
#   tools/wsync.sh              -> sync + run the whole suite
#   tools/wsync.sh tests/x.py   -> sync + run one file
#   tools/wsync.sh tests/x.py::test_name -v
set -uo pipefail

REPO=/home/hermesog/projects/sport-data-analyzer
PVE="root@192.168.10.8"
KEY=/home/hermesog/.ssh/id_ed25519_pve02
REMOTE=/root/work/sda

tar czf - -C "$REPO" \
    --exclude=.git --exclude=.superpowers --exclude=__pycache__ \
    --exclude=.venv --exclude='*.pyc' . \
  | ssh -i "$KEY" -o BatchMode=yes -o StrictHostKeyChecking=no "$PVE" \
      "pct exec 101 -- bash -c 'mkdir -p $REMOTE && tar xzf - -C $REMOTE'" || exit 9

ARGS="$*"
# Run pytest to a file, print the tail, then exit with PYTEST's status.
#
# Previously this was `pytest -q $ARGS 2>&1 | tail -40`, which had two problems
# that both hid failures: the pipeline's exit status was tail's, so the command
# always reported success, and the duplicated -q (pyproject already sets it in
# addopts) put pytest at quiet level 2, which suppresses the "N passed" summary
# line. A caller could not tell a green run from a red one.
ssh -i "$KEY" -o BatchMode=yes -o StrictHostKeyChecking=no "$PVE" \
  "pct exec 101 -- bash -lc 'cd $REMOTE && .venv/bin/python -m pytest $ARGS > /tmp/pytest.out 2>&1; rc=\$?; tail -40 /tmp/pytest.out; exit \$rc'"
