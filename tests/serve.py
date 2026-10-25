"""Serve dmrdash's page and API for browser tests, without the journal or BrandMeister threads.

    python3 tests/serve.py PORT
Reads its settings from DMRDASH_CONF like the real service. DMRDASH_LATEST=<version> plays a packaged
install with that version waiting in the apt repo.
"""

import importlib.util
import os
import sys
from http.server import ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("dmrdash", os.path.join(ROOT, "dmrdash.py"))
dmrdash = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dmrdash)
if os.environ.get("DMRDASH_LATEST"):
    dmrdash.DPKG_LIST = dmrdash.__file__  # any file that exists
    dmrdash.UPDATE.update(latest=os.environ["DMRDASH_LATEST"], checked=dmrdash.time.time())
ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), dmrdash.Handler).serve_forever()
