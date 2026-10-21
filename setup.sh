#!/usr/bin/env bash
# Idempotent setup: dependencies, kordoc OCR models check, systemd user units.
set -euo pipefail
HERE="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
cd "$HERE"

[ -x .venv/bin/python ] || python3.12 -m venv .venv
.venv/bin/python -m pip install -q -r requirements.txt
npm ci --no-audit --no-fund
KORDOC_MODEL_CACHE="$HERE/.cache/kordoc-models" node_modules/.bin/kordoc check-ocr-models \
  || echo "kordoc OCR models missing: see README" >&2

# keys template (never overwrites an existing file)
KEYS="$HOME/.config/bogoseo/keys.yaml"
if [ ! -e "$KEYS" ]; then
  mkdir -p "$(dirname "$KEYS")"
  cp keys.example.yaml "$KEYS"
  chmod 600 "$KEYS"
  echo "created $KEYS (fill in the keys)"
fi

# skill symlink
SKILL_LINK="$HOME/.agents/skills/bogoseo"
mkdir -p "$(dirname "$SKILL_LINK")"
if [ -L "$SKILL_LINK" ] && [ "$(readlink -f "$SKILL_LINK")" = "$HERE" ]; then
  :
elif [ -e "$SKILL_LINK" ] || [ -L "$SKILL_LINK" ]; then
  echo "WARNING: $SKILL_LINK exists and is not a link to $HERE; left unchanged" >&2
else
  ln -s "$HERE" "$SKILL_LINK"
fi

# insane-search dependency of the collector (skill dir + interpreter + engine.fetch_chain)
INSANE_DIR="${BOGOSEO_INSANE_DIR:-$HOME/.omp/agent/skills/insane-search}"
INSANE_PY="${BOGOSEO_INSANE_PYTHON:-/usr/bin/python3.12}"
INSANE_OK=1
if [ ! -d "$INSANE_DIR" ]; then
  INSANE_OK=0
elif [ ! -x "$INSANE_PY" ] || ! (cd "$INSANE_DIR" && "$INSANE_PY" -c 'from engine import fetch_chain' >/dev/null 2>&1); then
  INSANE_OK=0
fi

UNIT_DIR="$HOME/.config/systemd/user"
mkdir -p "$UNIT_DIR"
# literal (not sed) substitution; the repo path is %-escaped for systemd and quoted in ExecStart
for u in bogoseo-collect.service bogoseo-collect.timer; do
  HERE="$HERE" .venv/bin/python - "systemd/$u" "$UNIT_DIR/$u" <<'PY'
import os, sys
src, dst = sys.argv[1], sys.argv[2]
here = os.environ["HERE"].replace("%", "%%")
quoted = '"' + here.replace("\\", "\\\\").replace('"', '\\"') + '/bin/bogoseo"'
text = open(src, encoding="utf-8").read()
text = text.replace("%h/work/bogoseo/bin/bogoseo", quoted).replace("%h/work/bogoseo", here)
tmp = dst + ".tmp"
open(tmp, "w", encoding="utf-8").write(text)
os.replace(tmp, dst)
PY
done
# the timer-run service does not inherit this shell: pin the insane-search paths that passed the check
mkdir -p "$UNIT_DIR/bogoseo-collect.service.d"
printf '[Service]\nEnvironment="BOGOSEO_INSANE_DIR=%s"\nEnvironment="BOGOSEO_INSANE_PYTHON=%s"\n' \
  "${INSANE_DIR//%/%%}" "${INSANE_PY//%/%%}" > "$UNIT_DIR/bogoseo-collect.service.d/insane-search.conf"
systemctl --user daemon-reload
if [ "$INSANE_OK" = 1 ]; then
  systemctl --user enable --now bogoseo-collect.timer
else
  echo "WARNING: insane-search not usable (dir $INSANE_DIR, interpreter $INSANE_PY, 'from engine import fetch_chain')." >&2
  echo "  Install the insane-search skill into $INSANE_DIR and make $INSANE_PY (python3.12) able to import its engine," >&2
  echo "  or set BOGOSEO_INSANE_DIR / BOGOSEO_INSANE_PYTHON; then rerun ./setup.sh. Collector timer NOT enabled." >&2
fi
echo "bogoseo setup done"
