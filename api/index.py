"""La función de Vercel. La ruta real llega en el query __route."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import server


class handler(server.Handler):
    def do_GET(self):
        server.Handler.do_GET(self)

    def do_POST(self):
        server.Handler.do_POST(self)
