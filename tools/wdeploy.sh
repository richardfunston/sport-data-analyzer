#!/usr/bin/env bash
# Deploy the repo to the running service on web01 (CT 101 on pve02).
#
# wsync.sh syncs to /root/work/sda and runs the tests there. That tree is NOT
# what the service serves, so a green run from wsync.sh says nothing about what
# users see. This script is the deploy step, and it exists because a fix once
# passed 300 tests and never reached production.
#
#   ./tools/wdeploy.sh            # deploy, restart, smoke-check
#   ./tools/wdeploy.sh --dry-run  # show what would change, touch nothing
set -uo pipefail

REPO=/home/hermesog/projects/sport-data-analyzer
PVE_HOST="root@192.168.10.8"
PVE_KEY=/home/hermesog/.ssh/id_ed25519_pve02
CTID=101
LIVE=/opt/sport-data-analyzer
UNIT=sport-data-analyzer
SERVICE_PORT=8090

ssh_pve() { ssh -i "$PVE_KEY" -o BatchMode=yes -o StrictHostKeyChecking=no "$PVE_HOST" "$@"; }
in_ct() { ssh_pve "pct exec $CTID -- bash -lc $(printf '%q' "$1")"; }

DRY_RUN=0
[ "${1:-}" = "--dry-run" ] && DRY_RUN=1

cd "$REPO" || exit 9

echo "== 1. tests must pass before anything is deployed =="
# wsync.sh now propagates pytest's own exit status, so this is trustworthy.
# It did not before: the pipeline's status was tail's, so a failing suite still
# exited 0 and a broken deploy could sail through a "green" check.
if ! bash tools/wsync.sh 2>&1 | tail -26; then
  echo "   REFUSING TO DEPLOY: the test suite did not pass" >&2
  exit 1
fi

echo "== 2. what differs between the repo and the live tree? =="
CHANGED=0
for f in $(find app wsgi.py -type f \( -name '*.py' -o -name '*.html' -o -name '*.js' -o -name '*.css' \) | grep -v static/vendor | sort); do
  local_sum=$(sha256sum "$f" | cut -c1-12)
  remote_sum=$(in_ct "sha256sum $LIVE/$f 2>/dev/null | cut -c1-12")
  if [ "$local_sum" != "$remote_sum" ]; then
    printf '   DIFF  %s\n' "$f"
    CHANGED=$((CHANGED + 1))
  fi
done
echo "   $CHANGED file(s) differ"

if [ "$DRY_RUN" = "1" ]; then
  echo "== dry run: nothing changed =="
  exit 0
fi

if [ "$CHANGED" = "0" ]; then
  echo "== live tree already matches the repo; nothing to do =="
  exit 0
fi

echo "== 3. back up the live application code =="
in_ct "cd $LIVE && tar czf /root/app-backup-\$(date +%Y%m%d-%H%M%S).tgz app wsgi.py && ls -1 /root/app-backup-*.tgz | tail -1"

echo "== 4. deploy (never touches .venv, data dir, or the database) =="
tar czf - --exclude=.git --exclude=.superpowers --exclude=__pycache__ \
    --exclude='*.pyc' --exclude=.venv . \
  | ssh_pve "pct exec $CTID -- bash -c 'tar xzf - -C $LIVE'"

echo "== 5. restart =="
in_ct "systemctl restart $UNIT && sleep 6 && systemctl is-active $UNIT"

echo "== 6. smoke check through the real socket =="
CODE=$(in_ct "curl -sS -o /dev/null -w '%{http_code}' --max-time 15 http://127.0.0.1:$SERVICE_PORT/login")
echo "   GET /login -> HTTP ${CODE:-none}"
if [ "$CODE" != "200" ]; then
  echo "   DEPLOY LOOKS BROKEN. Restore with:" >&2
  in_ct "ls -1t /root/app-backup-*.tgz | head -1" >&2
  exit 1
fi

echo "== deployed =="
