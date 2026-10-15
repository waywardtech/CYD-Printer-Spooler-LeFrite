#!/usr/bin/env bash
# LE SPOOLER installer for Debian / Armbian / Raspberry Pi OS (any Linux with Python 3.9+).
#
#   ./install.sh            check prerequisites, create spool folders, run the tests
#   ./install.sh --service  ...and also install + start a *user* systemd service
#
# It never uses sudo and never changes system settings. It only touches this
# folder and (with --service) ~/.config/systemd/user/le-spooler.service.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${LE_SPOOLER_PORT:-8765}"
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
die()  { printf '  \033[31m✗\033[0m %s\n' "$*"; exit 1; }

echo "LE SPOOLER installer"
echo

# --- Python -------------------------------------------------------------------
command -v python3 >/dev/null || die "python3 not found. Install it: sudo apt install python3"
python3 - <<'PY' || die "Python 3.9 or newer is required"
import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)
PY
ok "$(python3 --version) at $(command -v python3)"

# --- Folders ------------------------------------------------------------------
mkdir -p "$HERE/spool/incoming" "$HERE/spool/printed" "$HERE/notes"
touch "$HERE/spool/index.jsonl"
chmod +x "$HERE/server/"*.py "$HERE/cli/leprint.py" "$HERE/firmware/build.sh" 2>/dev/null || true
ok "spool folders ready in $HERE/spool"

# --- Self-test ----------------------------------------------------------------
if python3 -m unittest discover -s "$HERE/tests" >/tmp/le-spooler-tests.log 2>&1; then
  ok "self-test passed ($(grep -Eo 'Ran [0-9]+ tests' /tmp/le-spooler-tests.log))"
else
  tail -20 /tmp/le-spooler-tests.log
  die "self-test failed (full log: /tmp/le-spooler-tests.log)"
fi

# --- Port ---------------------------------------------------------------------
if command -v ss >/dev/null && ss -ltn "sport = :$PORT" | grep -q LISTEN; then
  warn "something is already listening on port $PORT (an older LE SPOOLER?)"
fi

# --- USB serial (for a CYD plugged into this machine) ---------------------------
if command -v lsusb >/dev/null && lsusb | grep -qiE '1a86:7523|10c4:ea60'; then
  ok "USB-serial chip found (CH340/CP210x): probably your CYD"
  if ! ls /dev/ttyUSB* >/dev/null 2>&1; then
    warn "...but no /dev/ttyUSB* exists. If brltty is installed it may be grabbing it:"
    warn "   see 'Troubleshooting' in README.md (needs sudo; not done automatically)"
  fi
  id -nG | grep -qw dialout || warn "add yourself to 'dialout' to use the serial port: sudo usermod -aG dialout $USER"
fi

# --- Optional user service ------------------------------------------------------
if [[ "${1:-}" == "--service" ]]; then
  command -v systemctl >/dev/null || die "systemctl not found"
  unit_dir="$HOME/.config/systemd/user"
  mkdir -p "$unit_dir"
  sed "s#@ROOT@#$HERE#g; s#@PORT@#$PORT#g" "$HERE/contrib/le-spooler.service" > "$unit_dir/le-spooler.service"
  systemctl --user daemon-reload
  systemctl --user enable --now le-spooler.service
  ok "user service installed and started (systemctl --user status le-spooler)"
  warn "to keep it running while you're logged out: sudo loginctl enable-linger $USER"
fi

ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
cat <<EOF

Next steps
  Start the server:   python3 $HERE/server/le_spooler_server.py
  Web UI:             http://${ip:-<this-machine>}:$PORT/
  Print something:    python3 $HERE/cli/leprint.py "HELLO WORLD"
  Watch it type:      python3 $HERE/server/le_spooler_preview.py
EOF
