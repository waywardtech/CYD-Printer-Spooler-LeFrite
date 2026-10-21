#!/usr/bin/env bash
# Update the LE SPOOLER CUPS backend after a `git pull` - no sudo needed.
# (install-cups-printer.sh set /usr/local/lib/le-spooler up to be owned by you.)
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIBDIR=/usr/local/lib/le-spooler
if [[ ! -w "$LIBDIR" ]]; then
  echo "$LIBDIR isn't writable by $USER - run: sudo $HERE/install-cups-printer.sh" >&2
  exit 1
fi
python3 -m py_compile "$HERE/lespooler" 2>/dev/null || { echo "lespooler has a syntax error; not installing" >&2; exit 1; }
install -m 0755 "$HERE/lespooler" "$LIBDIR/lespooler"
echo "backend updated: $LIBDIR/lespooler"
