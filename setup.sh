#!/usr/bin/env bash
# Idempotent setup: deps, vendored insane-search, kordoc OCR check, keys, skill link, systemd units (--no-update, --no-timer).
set -euo pipefail
HERE="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
cd "$HERE"

NO_UPDATE=0
NO_TIMER=0
for a in "$@"; do
  case "$a" in
    --no-update) NO_UPDATE=1 ;;
    --no-timer) NO_TIMER=1 ;;
    *) echo "usage: ./setup.sh [--no-update] [--no-timer]" >&2; exit 2 ;;
  esac
done

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


# insane-search: latest GitHub release, vendored (gitignored) into $HERE/vendor/insane-search
VENDOR="$HERE/vendor/insane-search"
update_insane() {
  local repo=fivetaku/insane-search tag tmp url
  tag="$(curl -fsSL --max-time 20 -H 'Accept: application/vnd.github+json' \
    "https://api.github.com/repos/$repo/releases/latest" 2>/dev/null \
    | .venv/bin/python -c 'import json,sys; print(json.load(sys.stdin)["tag_name"])' 2>/dev/null)" || tag=""
  if [ -n "$tag" ]; then
    url="https://codeload.github.com/$repo/tar.gz/refs/tags/$tag"
  elif [ -d "$VENDOR" ]; then
    echo "WARNING: GitHub release lookup failed; keeping installed insane-search $(cat "$VENDOR/.version" 2>/dev/null)" >&2
    return 0
  else
    tag=main
    url="https://codeload.github.com/$repo/tar.gz/refs/heads/main"
  fi
  if [ "$tag" != main ] && [ "$(cat "$VENDOR/.version" 2>/dev/null)" = "$tag" ]; then
    echo "insane-search $tag already installed"
    return 0
  fi
  mkdir -p "$HERE/vendor"
  tmp="$(mktemp -d "$HERE/vendor/.tmp.XXXXXX")"
  # stage, then prove the engine imports with the interpreter the collector will use, before replacing anything
  if curl -fsSL --max-time 120 "$url" -o "$tmp/src.tar.gz" \
     && mkdir "$tmp/out" \
     && tar -xzf "$tmp/src.tar.gz" -C "$tmp/out" --strip-components=3 --wildcards '*/skills/insane-search/' \
     && [ -f "$tmp/out/SKILL.md" ] \
     && (cd "$tmp/out" && "${BOGOSEO_INSANE_PYTHON:-$HERE/.venv/bin/python}" -c 'from engine import fetch_chain' >/dev/null 2>&1); then
    echo "$tag" > "$tmp/out/.version"
    if [ -d "$VENDOR" ]; then mv "$VENDOR" "$tmp/prev"; fi
    if mv "$tmp/out" "$VENDOR"; then
      echo "insane-search $tag installed in $VENDOR"
    else
      if [ -d "$tmp/prev" ]; then mv "$tmp/prev" "$VENDOR"; fi
      echo "WARNING: could not install insane-search $tag; previous copy restored" >&2
    fi
  else
    echo "WARNING: could not download or validate insane-search ($url); keeping existing copy if any" >&2
  fi
  rm -rf "$tmp"
}
if [ "$NO_UPDATE" = 0 ]; then update_insane; fi

# insane-search dependency of the collector (same resolution as bogoseo/collect/fetch.py)
if [ -n "${BOGOSEO_INSANE_DIR:-}" ]; then INSANE_DIR="$BOGOSEO_INSANE_DIR"
elif [ -d "$VENDOR" ]; then INSANE_DIR="$VENDOR"
else INSANE_DIR="$HOME/.omp/agent/skills/insane-search"; fi
INSANE_PY="${BOGOSEO_INSANE_PYTHON:-$HERE/.venv/bin/python}"
INSANE_OK=1
if [ ! -d "$INSANE_DIR" ]; then
  INSANE_OK=0
elif [ ! -x "$INSANE_PY" ] || ! (cd "$INSANE_DIR" && "$INSANE_PY" -c 'from engine import fetch_chain' >/dev/null 2>&1); then
  INSANE_OK=0
fi

if [ "$NO_TIMER" = 1 ]; then
  [ "$INSANE_OK" = 1 ] || echo "WARNING: insane-search not usable (dir $INSANE_DIR, interpreter $INSANE_PY)." >&2
  echo "bogoseo setup done (--no-timer: systemd units not written)"
  exit 0
fi

if [ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null)" != yes ]; then
  echo "WARNING: user lingering is off; the timer will not run while you are logged out. Run: sudo loginctl enable-linger $USER" >&2
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
