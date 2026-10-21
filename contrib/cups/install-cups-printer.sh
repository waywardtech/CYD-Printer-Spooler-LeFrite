#!/usr/bin/env bash
# Add LE SPOOLER as a real printer in CUPS, optionally shared over Bonjour.
#
#   sudo contrib/cups/install-cups-printer.sh            # local printer on this machine
#   sudo contrib/cups/install-cups-printer.sh --share    # ...and advertise it on the LAN (Bonjour)
#   sudo contrib/cups/install-cups-printer.sh --uninstall
#
# What it changes (all reversible with --uninstall, except installed packages):
#   * installs poppler-utils / ghostscript / tesseract-ocr if missing (PDF, PS, OCR)
#   * copies the backend to /usr/lib/cups/backend/lespooler (mode 0755: runs as user 'lp')
#   * creates a CUPS queue "LE_SPOOLER" -> lespooler://127.0.0.1:$PORT
#   * --share: installs avahi-daemon if missing, runs `cupsctl --share-printers`,
#     marks the queue shared. CUPS then listens on port 631 on your LAN
#     (not just localhost) and advertises the printer via mDNS/DNS-SD.
set -euo pipefail

QUEUE="${QUEUE:-LE_SPOOLER}"
PORT="${LE_SPOOLER_PORT:-8765}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(cups-config --serverbin 2>/dev/null || echo /usr/lib/cups)/backend"
LIBDIR=/usr/local/lib/le-spooler
say() { printf '\033[1m==>\033[0m %s\n' "$*"; }

[[ $EUID -eq 0 ]] || { echo "Run with sudo: sudo $0 $*"; exit 1; }

if [[ "${1:-}" == "--uninstall" ]]; then
  say "Removing queue $QUEUE and the lespooler backend"
  lpadmin -x "$QUEUE" 2>/dev/null || true
  rm -f "$BACKEND_DIR/lespooler"
  rm -rf "$LIBDIR"
  say "Done. (Printer sharing and avahi were left as they were: 'cupsctl --no-share-printers' turns sharing off.)"
  exit 0
fi

if ! command -v lpadmin >/dev/null && [[ ! -x /usr/sbin/lpadmin ]]; then
  say "Installing CUPS"
  apt-get update -qq && apt-get install -y cups
fi
need=()
command -v pdftotext >/dev/null || need+=(poppler-utils)   # PDF -> text
command -v gs        >/dev/null || need+=(ghostscript)     # PostScript -> text
command -v tesseract >/dev/null || need+=(tesseract-ocr)   # OCR for picture-of-a-page PDFs
if ((${#need[@]})); then
  say "Installing converters: ${need[*]}"
  apt-get update -qq && apt-get install -y -qq "${need[@]}"
fi

# The real backend lives in $LIBDIR, owned by the user who ran sudo, so it can be
# updated later without root (contrib/cups/update-backend.sh). CUPS runs a fixed
# two-line launcher from its own (root-owned) backend directory.
OWNER="${SUDO_USER:-root}"
say "Installing backend -> $LIBDIR/lespooler (updatable by $OWNER)"
install -d -o "$OWNER" -m 0755 "$LIBDIR"
install -o "$OWNER" -m 0755 "$HERE/lespooler" "$LIBDIR/lespooler"
say "Installing launcher -> $BACKEND_DIR/lespooler"
printf '#!/bin/sh\n# LE SPOOLER CUPS backend launcher (see %s)\nexec /usr/bin/python3 %s/lespooler "$@"\n' \
  "$LIBDIR" "$LIBDIR" > "$BACKEND_DIR/lespooler"
chmod 0755 "$BACKEND_DIR/lespooler"

say "Creating print queue $QUEUE"
/usr/sbin/lpadmin -p "$QUEUE" -E \
  -v "lespooler://127.0.0.1:$PORT" \
  -P "$HERE/le-spooler.ppd" \
  -D "LE SPOOLER fanfold printer" \
  -L "$(hostname)" \
  -o printer-error-policy=retry-job \
  -o media=9.5x11
/usr/sbin/cupsenable "$QUEUE"
/usr/sbin/cupsaccept "$QUEUE"

if [[ "${1:-}" == "--share" ]]; then
  if ! dpkg -s avahi-daemon >/dev/null 2>&1; then
    say "Installing avahi-daemon (Bonjour / mDNS)"
    apt-get update -qq && apt-get install -y avahi-daemon
  fi
  systemctl enable --now avahi-daemon
  say "Sharing printers on the local network (cupsctl --share-printers)"
  cupsctl --share-printers
  /usr/sbin/lpadmin -p "$QUEUE" -o printer-is-shared=true
  systemctl restart cups
fi

say "Done. Queue status:"
lpstat -p "$QUEUE" -v "$QUEUE" || true
cat <<EOF

Try it:
  echo "HELLO FROM CUPS" | lp -d $QUEUE
  lp -d $QUEUE some-document.pdf
Desktop apps now list "$QUEUE" in their Print dialog.
EOF
if [[ "${1:-}" == "--share" ]]; then
  echo "Other computers on the LAN should discover \"LE SPOOLER fanfold printer @ $(hostname)\"."
  echo "Check with:  avahi-browse -rt _ipp._tcp"
fi
