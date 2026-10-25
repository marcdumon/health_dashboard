"""A stand-in for `uv run polar-h10 record` in the tests of polar_record, run as its own process: python
fake_polar_logger.py MODE PORT FOLDER. While it records it serves /status, /markers and /stop on PORT as the user's logger
does, and at the end it writes a session folder in FOLDER (3 minutes up to now, with the markers it got). Modes: 'ok'
records until POST /stop or SIGTERM; 'fail' says the strap was not found and ends with 1; 'no-api' runs without an API
until SIGTERM; 'ends' records 1 s, then stops with an error and ends with 1. No strap is used."""

import json
import signal
import sys
import threading
import time
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.polar_fixtures import logger_session

NOT_FOUND = 'No Polar H10 was found. Wear the strap with wet electrodes.'
LOST = 'The strap stopped sending.'


def main() -> int:
    """Run as the mode says; the exit code of the logger."""
    mode, port, folder = sys.argv[1], int(sys.argv[2]), Path(sys.argv[3])
    if mode == 'fail':
        print(NOT_FOUND, file=sys.stderr)
        return 1
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    if mode == 'no-api':
        stop.wait()
        return 0
    start = int(time.time()) - 180
    markers: list[tuple[float, str]] = []
    error = []

    class Handler(BaseHTTPRequestHandler):
        """The logger's routes that the app uses."""

        def log_message(self, *args: object) -> None:
            """No request log."""

        def send(self, code: int, body: dict) -> None:
            """A JSON answer."""
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            """GET /status."""
            if self.path != '/status':
                return self.send(404, {'detail': 'Not Found'})
            self.send(200, {'state': 'connected', 'device': {'name': 'Polar H10 E813952E', 'battery': 80},
                            'last_hr': {'hr_bpm': 128}, 'error': error[0] if error else None,
                            'session': {'id': 'fake', 'seconds': round(time.time() - start, 1), 'markers': len(markers)}})

        def do_POST(self) -> None:
            """POST /markers and POST /stop."""
            body = json.loads(self.rfile.read(int(self.headers.get('Content-Length') or 0)) or b'{}')
            if self.path == '/markers':
                markers.append((time.time() - start, body['label']))
                return self.send(201, {'label': body['label'], 'source': body.get('source', 'api')})
            if self.path == '/stop':
                stop.set()
                return self.send(202, {'stopping': True})
            return self.send(404, {'detail': 'Not Found'})

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    if mode == 'ends':
        time.sleep(1)
        error.append(LOST)
        time.sleep(0.3)
        stop.set()
    stop.wait()
    server.shutdown()
    seconds = int(time.time()) - start
    logger_session(folder, datetime.fromtimestamp(start, UTC), seconds, markers=markers)
    print(json.dumps({'ended_cleanly': mode == 'ok'}))
    return 1 if mode == 'ends' else 0


if __name__ == '__main__':
    sys.exit(main())
