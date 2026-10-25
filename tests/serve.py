"""Serve dmrdash's page and API for browser tests, without the journal or BrandMeister threads.

    python3 tests/serve.py PORT
Reads its settings from DMRDASH_CONF like the real service; the editor reads MMDVM.ini through config.py. DMRDASH_LATEST=<version> plays a packaged
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
# no path unit here: answer the editor's reads with config.py at once (changes are left in the request file)
spec = importlib.util.spec_from_file_location("config", os.path.join(ROOT, "config.py"))
cfg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cfg)
cfg.REQUEST, cfg.STATUS, cfg.VIEW = dmrdash.CONFIG_PATH, dmrdash.CONFIG_STATUS, dmrdash.CONFIG_VIEW


def kick():
    with open(cfg.REQUEST) as f:
        if '"op": "read"' in f.read():
            cfg.read_request()
            cfg.view(dmrdash.CONF["mmdvm_ini"])


dmrdash.CONFIG_KICK[0] = kick
ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), dmrdash.Handler).serve_forever()
