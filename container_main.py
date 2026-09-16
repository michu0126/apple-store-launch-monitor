import os
import threading
from http.server import ThreadingHTTPServer

from container_worker import Monitor
from web_server import Handler, initialize


def serve():
    ThreadingHTTPServer(('0.0.0.0', 8765), Handler).serve_forever()


if __name__ == '__main__':
    os.makedirs(os.path.dirname(os.environ.get('DB_PATH', '/data/dashboard.sqlite3')), exist_ok=True)
    initialize()
    threading.Thread(target=serve, daemon=True).start()
    Monitor().run()
