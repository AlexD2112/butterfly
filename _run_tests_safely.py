"""
The standing guard against a real incident this repo already had once - see NOTES.md, "Location/
Subspace rename..." (2026-08-22), for what happened.

server.py calls db.init_db() unconditionally at module import time. Several test modules `import
server`, and unittest's test COLLECTION (importing every test module) happens before any test's
setUp() gets a chance to point db.DB_FILE at a throwaway file - so the very first `import server`
during collection runs init_db() against whatever db.DB_FILE currently is, which by default is the
real production butterfly.db. This was always true and always harmless while init_db() was purely
additive (CREATE TABLE IF NOT EXISTS / idempotent ADD COLUMN) - the location/subspace rename
migration was the first genuinely destructive (ALTER TABLE RENAME) thing init_db() ever did, which is
what turned this long-standing quirk into a real production incident.

This wrapper sets db.DB_FILE to a harmless scratch path BEFORE importing/running anything else, so
even that earliest, setUp-less init_db() call lands somewhere disposable. Run tests through this
instead of `python -m unittest discover` directly. This is a workaround, not a fix - the underlying
cause (server.py's unconditional module-level init_db() call, combined with unittest's
import-before-setup ordering) is still there and will bite again on any future genuinely destructive
migration if this wrapper is bypassed. A real fix would restructure how/when init_db() runs relative
to the production entry point (waitress imports `server:app` directly - see
local-site-tunnel/start_local_site.ps1), which touches the deployment startup path and hasn't been
done here.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

# No real Wikidata lookups from tests (geodata.py) - tests that need them fake the HTTP layer.
os.environ.setdefault("GEOCODE", "0")

import db

db.DB_FILE = Path(tempfile.mktemp(suffix="_collection_side_effect.db"))

loader = unittest.TestLoader()
if len(sys.argv) > 1:
    suite = loader.loadTestsFromNames(sys.argv[1:])
else:
    suite = loader.discover(start_dir="tests")
runner = unittest.TextTestRunner(verbosity=1)
result = runner.run(suite)
sys.exit(0 if result.wasSuccessful() else 1)
