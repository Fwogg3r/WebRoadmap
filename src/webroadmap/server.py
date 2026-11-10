"""``roadmap serve``: static page + a JSON API over model.py.

GET  /api/roadmap          whole roadmap + version + check results
GET  /api/version          change token (the page polls it every second)
GET  /api/show?id=ID
POST /api/<op>             JSON body; ops: add, add-phase, add-group, set, link, unlink, mv, arrange, rm, renumber
"""

from __future__ import annotations

import json
import mimetypes
import socket
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import model

STATIC = files("webroadmap").joinpath("static")


def api_op(root: Path, op: str, a: dict) -> dict:
    """One place mapping API ops onto model functions (CLI arguments mirrored)."""
    if op == "add":
        fields = {k: a.get(k) for k in ("nickname", "status", "color", "bold", "links")}
        return {"id": model.add_item(root, a.get("phase"), a["title"], body=a.get("body", ""),
                                     group=a.get("group"), **fields)}
    if op == "add-group":
        return {"id": model.add_group(root, a.get("phase"), a["name"], a.get("nickname"), parent=a.get("in"),
                                      body=a.get("body", a.get("summary", "")),
                                      status=a.get("status"), color=a.get("color"))}
    if op == "add-phase":
        return {"id": model.add_phase(root, a["name"], a.get("nickname"), after=a.get("after"),
                                      body=a.get("body", a.get("summary", "")),
                                      status=a.get("status"), color=a.get("color"))}
    if op == "set":
        return {"id": model.set_entry(root, a["id"], a.get("fields", {}), a.get("body"))}
    if op == "link":
        return {"id": a["a"], "links": model.link(root, a["a"], a["b"])}
    if op == "unlink":
        return {"id": a["a"], "links": model.unlink(root, a["a"], a["b"])}
    if op == "mv":
        return {"id": model.move(root, a["id"], str(a["target"])), "from": a["id"]}
    if op == "arrange":
        return {"ids": model.arrange(root, a["container"], list(a["children"]))}
    if op == "rm":
        return {"removed": model.remove(root, a["id"], force=bool(a.get("force")))}
    if op == "renumber":
        return {"renamed": model.renumber(root)}
    raise model.RoadmapError(f"unknown op {op}")


def snapshot(root: Path) -> dict:
    with model.LOCK:
        v = model.version(root)
        data = model.to_json(model.load(root))
        data["check"] = model.check(root)
    data["version"] = v
    return data


def make_handler(root: Path):
    class Handler(BaseHTTPRequestHandler):
        server_version = "webroadmap"

        def log_message(self, fmt, *args):  # quiet; errors still reach stderr via send_error
            pass

        def _send(self, code: int, body: bytes, ctype: str):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, data):
            self._send(code, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def do_GET(self):
            u = urlparse(self.path)
            try:
                if u.path == "/api/roadmap":
                    return self._json(200, snapshot(root))
                if u.path == "/api/version":
                    return self._json(200, {"version": model.version(root)})
                if u.path == "/api/show":
                    eid = parse_qs(u.query).get("id", [""])[0]
                    return self._json(200, model.show(root, eid))
                if u.path == "/api/check":
                    return self._json(200, model.check(root))
            except model.RoadmapError as e:
                return self._json(404, {"error": str(e)})
            name = "index.html" if u.path in ("/", "") else u.path.lstrip("/")
            if ".." in name or name.startswith("api/"):
                return self._json(404, {"error": "not found"})
            res = STATIC.joinpath(name)
            if not res.is_file():
                return self._json(404, {"error": "not found"})
            ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype.endswith("javascript"):
                ctype += "; charset=utf-8"
            self._send(200, res.read_bytes(), ctype)

        def do_POST(self):
            u = urlparse(self.path)
            # Refuse cross-site writes (a page on another origin posting to localhost).
            origin = self.headers.get("Origin")
            if origin and urlparse(origin).netloc != self.headers.get("Host"):
                return self._json(403, {"error": "cross-origin request refused"})
            if not u.path.startswith("/api/"):
                return self._json(404, {"error": "not found"})
            try:
                n = int(self.headers.get("Content-Length") or 0)
                args = json.loads(self.rfile.read(n) or b"{}")
                res = api_op(root, u.path[5:], args)
                res["version"] = model.version(root)
                return self._json(200, res)
            except KeyError as e:
                return self._json(400, {"error": f"missing argument {e}"})
            except (model.RoadmapError, ValueError, OSError) as e:
                return self._json(400, {"error": str(e)})

    return Handler


def free_port(host: str, start: int = 8790) -> int:
    for port in range(start, start + 200):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((host, port))
                return port
            except OSError:
                continue
    raise OSError("no free port")


class Server(ThreadingHTTPServer):
    daemon_threads = True
    # On Windows SO_REUSEADDR lets a second server bind a port already in use.
    allow_reuse_address = sys.platform != "win32"


def make_server(root: Path, host: str = "127.0.0.1", port: int | None = None) -> Server:
    port = port if port is not None else free_port(host)
    return Server((host, port), make_handler(Path(root)))


def lan_ip() -> str | None:
    """The address of the interface with the default route (a UDP connect sends nothing)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))
            return s.getsockname()[0]
    except OSError:
        return None


def serve(root: Path, host: str = "127.0.0.1", port: int | None = None, open_browser: bool = False):
    srv = make_server(root, host, port)
    shown = "127.0.0.1" if host in ("0.0.0.0", "") else host
    url = f"http://{shown}:{srv.server_address[1]}"
    print(f"roadmap: serving {root}", file=sys.stderr)
    if host in ("0.0.0.0", "", "::"):
        print("WARNING: listening on all interfaces; anyone on the network can EDIT this roadmap "
              "(no authentication).", file=sys.stderr)
        lan = lan_ip()
        if lan:
            print(f"  on the LAN: http://{lan}:{srv.server_address[1]}", file=sys.stderr)
    print(url, flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
