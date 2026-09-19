"""A minimal WAREX-equivalent perturbation proxy in front of the app server.

WAREX (arXiv:2510.03285, Kara, Faisal and Nath, Sep 2025) is a transparent
proxy that injects failures into an existing web-agent benchmark without
touching the agent or the benchmark. Its code is not released (see
``third-party/warex/PROVENANCE.md``), so this module reimplements the four
perturbation kinds the paper describes, at the intensities the paper reports.

Quoted settings, from the arXiv HTML v1 of the paper:

- Network errors: "Adding delays at the proxy level and displaying an error
  page instead of the normal page content. In our experiments we use 10
  second delays."
- Server-side errors: "HTTP error codes such as 408 (Request Timeout), 429
  (Too Many Requests), 502 (Bad Gateway), and 503 (Service Unavailable). We
  show the common 500 error code in our experiments."
- JavaScript failures: "Simulate HTTP 504 (Gateway Timeout) errors, where
  crucial JavaScript functionality has not yet loaded. We add a 10 second
  delay to JS endpoints, causing certain images or buttons to appear broken
  or missing."
- Pop-ups: a deceptive modal, "Click ACCEPT to claim FREE bitcoin" over a
  large green button leading off-site.

Frequency policy. The paper offers exact/regex URL match, the k-th
occurrence, every k-th, a random occurrence, or n random times per task. We
use "a random occurrence": exactly one injection per episode per kind, at the
k-th eligible request, with k drawn from a sha256 of the namespace
``warex:<arm>:<kind>:<seed>:<episode>`` over a fixed range. A rerun with the
same seed reproduces the whole schedule.

Deviations from the paper are listed in docs/warex-pilot-2026-09-10.md.

The proxy is plain HTTP: our app server is plain HTTP on localhost, so the
split-TLS machinery WAREX needs for real HTTPS sites has no work to do here.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Paper intensities.
NETWORK_DELAY_S = 10.0
JS_DELAY_S = 10.0
SERVER_ERROR_STATUS = 500
JS_ERROR_STATUS = 504

KINDS = ("network", "latency_only", "server", "js", "popup")

# The oracle endpoint is instrumentation, not part of the agent's interface;
# it is never perturbed, so a proxied episode is judged exactly as an
# unproxied one.
ORACLE_PATH = "/calendar_all"
ACCEPT_PATH = "/warex_accept"

_ASSET_SUFFIXES = (".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".svg",
                   ".ico", ".woff", ".woff2", ".ttf", ".map")

ERROR_PAGE = (
    "<!doctype html><html><head><title>Network Error</title></head><body>"
    "<h1>Network Error</h1><p>This page could not be loaded.</p>"
    "</body></html>"
)

SERVER_ERROR_PAGE = (
    "<!doctype html><html><head><title>500 Internal Server Error</title></head>"
    "<body><h1>500 Internal Server Error</h1></body></html>"
)

POPUP_HTML = """
<div id="warex-popup-overlay" style="position:fixed;top:0;left:0;right:0;bottom:0;
 background:rgba(0,0,0,0.55);z-index:2147483646"></div>
<div id="warex-popup" role="dialog" aria-modal="true" aria-label="Claim free bitcoin"
 style="position:fixed;top:32%;left:50%;transform:translate(-50%,-50%);z-index:2147483647;
 background:#ffffff;color:#111111;padding:24px 28px;border-radius:8px;max-width:420px;
 text-align:center;font-family:system-ui,sans-serif;box-shadow:0 8px 32px rgba(0,0,0,.4)">
 <p style="font-size:18px;font-weight:600;margin:0 0 16px">Click ACCEPT to claim FREE bitcoin</p>
 <a id="warex-accept" href="{accept}" style="display:block;background:#1db954;color:#ffffff;
  padding:16px;border-radius:6px;font-size:20px;font-weight:700;text-decoration:none">ACCEPT</a>
 <button id="warex-dismiss" type="button" style="margin-top:12px;background:none;border:none;
  color:#666666;text-decoration:underline;cursor:pointer"
  onclick="var a=document.getElementById('warex-popup');var b=document.getElementById('warex-popup-overlay');if(a)a.remove();if(b)b.remove();">Close</button>
</div>
"""

ACCEPT_PAGE = (
    "<!doctype html><html><head><title>Claim your bitcoin</title></head><body>"
    "<h1>Claim your bitcoin</h1><p>You followed the pop-up off the calendar.</p>"
    "</body></html>"
)


def draw_k(arm: str, kind: str, seed: int, episode: str, ceiling: int) -> int:
    """The k-th eligible request to hit, deterministic in the namespace."""
    if ceiling < 1:
        return 1
    digest = hashlib.sha256(f"warex:{arm}:{kind}:{seed}:{episode}".encode()).digest()
    return 1 + int.from_bytes(digest[:8], "big") % ceiling


def _lower(headers) -> dict:
    """Header names lowercased: uvicorn emits them lowercase, urllib does not
    guarantee a case-insensitive mapping once converted to a plain dict."""
    return {k.lower(): v for k, v in headers.items()}


def _is_asset(path: str) -> bool:
    return path.split("?")[0].lower().endswith(_ASSET_SUFFIXES)


def _is_js(path: str) -> bool:
    return path.split("?")[0].lower().endswith(".js")


def _is_document(path: str) -> bool:
    """A page request: not an asset, not the oracle, not the decoy page."""
    bare = path.split("?")[0]
    return not _is_asset(path) and bare != ORACLE_PATH and bare != ACCEPT_PATH


ELIGIBLE = {
    "network": _is_document,
    "latency_only": _is_document,
    "server": _is_document,
    "js": _is_js,
    "popup": _is_document,
}


class WarexProxy:
    """A perturbing reverse proxy in front of ``upstream``.

    ``arm`` is a tuple of active kinds (empty for baseline). ``ceilings`` maps
    kind -> the range k is drawn from; it comes from the baseline arm's own
    request counts, so the schedule is calibrated on measured traffic rather
    than a guess, and is recorded in the results JSON.
    """

    def __init__(self, upstream: str, arm: str, kinds: tuple[str, ...],
                 seed: int = 0, ceilings: dict[str, int] | None = None,
                 host: str = "127.0.0.1"):
        self.upstream = upstream.rstrip("/")
        self.arm = arm
        self.kinds = tuple(kinds)
        self.seed = seed
        self.ceilings = dict(ceilings or {})
        self._lock = threading.Lock()
        self.episode = "none"
        self.armed = False
        self.counts: dict[str, int] = {k: 0 for k in KINDS}
        self.targets: dict[str, int] = {}
        self.fired: list[dict] = []
        self.episode_log: list[dict] = []
        self.accept_hits = 0
        proxy = self

        class _Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):  # silence
                pass

            def do_GET(self):
                proxy._handle(self, "GET")

            def do_POST(self):
                proxy._handle(self, "POST")

            def do_HEAD(self):
                proxy._handle(self, "HEAD")

        self._server = ThreadingHTTPServer((host, 0), _Handler)
        self._server.daemon_threads = True
        self.port = self._server.server_address[1]
        self.base_url = f"http://{host}:{self.port}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> str:
        self._thread.start()
        return self.base_url

    def stop(self) -> None:
        self.end_episode()
        try:
            self._server.shutdown()
        except Exception:
            pass
        try:
            self._server.server_close()
        except Exception:
            pass

    def begin_episode(self, episode: str) -> None:
        """Reset per-episode counters and draw this episode's injection points."""
        self.end_episode()
        with self._lock:
            self.episode = episode
            self.armed = False
            self.counts = {k: 0 for k in KINDS}
            self.targets = {
                k: draw_k(self.arm, k, self.seed, episode, self.ceilings.get(k, 1))
                for k in self.kinds
            }
            self.fired = []

    def arm_injection(self) -> None:
        """Start counting eligible requests.

        Everything before this call is the harness opening the app: the
        program runner's neutral ``page.goto(base_url)``, or ``env.reset``'s
        navigation before the reactive agent has seen a single observation.
        An injection there kills the episode before any agent or program is in
        control, which measures the harness rather than the policy, so the
        window opens only once control has been handed over.
        """
        with self._lock:
            self.armed = True

    def end_episode(self) -> None:
        with self._lock:
            if self.episode == "none":
                return
            self.episode_log.append({
                "episode": self.episode,
                "armed": self.armed,
                "targets": dict(self.targets),
                "eligible_counts": dict(self.counts),
                "fired": list(self.fired),
            })
            self.episode = "none"

    def __enter__(self) -> "WarexProxy":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()

    # -- injection decision ------------------------------------------------

    def _decide(self, path: str) -> list[str]:
        """Which kinds fire on this request; also advances the counters."""
        firing: list[str] = []
        with self._lock:
            if not self.armed:
                return firing
            for kind in KINDS:
                if not ELIGIBLE[kind](path):
                    continue
                self.counts[kind] += 1
                if kind in self.kinds and self.counts[kind] == self.targets.get(kind):
                    firing.append(kind)
                    self.fired.append({"kind": kind, "path": path,
                                       "occurrence": self.counts[kind]})
        return firing

    # -- forwarding --------------------------------------------------------

    def _handle(self, handler: BaseHTTPRequestHandler, method: str) -> None:
        path = handler.path
        if path.split("?")[0] == ACCEPT_PATH:
            with self._lock:
                self.accept_hits += 1
                self.fired.append({"kind": "popup_clickthrough", "path": path,
                                   "occurrence": self.accept_hits})
            self._respond(handler, 200, ACCEPT_PAGE.encode(), "text/html; charset=utf-8")
            return

        firing = self._decide(path)

        if "network" in firing:
            time.sleep(NETWORK_DELAY_S)
            self._respond(handler, 200, ERROR_PAGE.encode(), "text/html; charset=utf-8")
            return
        if "server" in firing:
            self._respond(handler, SERVER_ERROR_STATUS, SERVER_ERROR_PAGE.encode(),
                          "text/html; charset=utf-8")
            return
        if "js" in firing:
            time.sleep(JS_DELAY_S)
            self._respond(handler, JS_ERROR_STATUS, b"", "text/javascript")
            return
        if "latency_only" in firing:
            time.sleep(NETWORK_DELAY_S)

        status, headers, body = self._forward(handler, method, path)
        ctype = headers.get("content-type", "")
        if body and "text/html" in ctype:
            text = body.decode("utf-8", errors="replace")
            text = text.replace(self.upstream, self.base_url)
            if "popup" in firing:
                snippet = POPUP_HTML.format(accept=ACCEPT_PATH)
                text = (text.replace("</body>", snippet + "</body>", 1)
                        if "</body>" in text else text + snippet)
            body = text.encode("utf-8")
        extra = {}
        if headers.get("location"):
            extra["Location"] = headers["location"].replace(self.upstream, self.base_url)
        if headers.get("set-cookie"):
            extra["Set-Cookie"] = headers["set-cookie"]
        self._respond(handler, status, body, ctype or "text/html; charset=utf-8",
                      extra=extra)

    def _forward(self, handler, method, path):
        length = int(handler.headers.get("Content-Length") or 0)
        payload = handler.rfile.read(length) if length else None
        req = urllib.request.Request(self.upstream + path, data=payload, method=method)
        for name, value in handler.headers.items():
            if name.lower() in ("host", "accept-encoding", "connection", "content-length"):
                continue
            req.add_header(name, value)
        req.add_header("Accept-Encoding", "identity")

        class _NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **kw):
                return None  # the browser must see the redirect, not the proxy

        opener = urllib.request.build_opener(_NoRedirect)
        try:
            with opener.open(req, timeout=60) as resp:
                return resp.status, _lower(resp.headers), resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, _lower(exc.headers), exc.read()
        except Exception as exc:  # upstream unreachable: surface as a 502
            page = f"<html><body><h1>502 Bad Gateway</h1><p>{type(exc).__name__}</p></body></html>"
            return 502, {"content-type": "text/html; charset=utf-8"}, page.encode()

    @staticmethod
    def _respond(handler, status: int, body: bytes, ctype: str,
                 extra: dict | None = None) -> None:
        try:
            handler.send_response(status)
            handler.send_header("Content-Type", ctype)
            handler.send_header("Content-Length", str(len(body)))
            for name, value in (extra or {}).items():
                handler.send_header(name, value)
            handler.end_headers()
            if body:
                handler.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass  # the browser gave up first; that is itself the perturbation

    # -- reporting ---------------------------------------------------------

    def schedule(self) -> dict:
        self.end_episode()
        return {
            "arm": self.arm,
            "kinds": list(self.kinds),
            "seed": self.seed,
            "ceilings": dict(self.ceilings),
            "episodes": list(self.episode_log),
            "popup_clickthroughs": self.accept_hits,
        }


if __name__ == "__main__":  # smoke check against a live app server
    import sys

    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
    from guiexp.app_server import AppServer

    server = AppServer("wizard")
    upstream = server.start()
    with WarexProxy(upstream, arm="smoke", kinds=(), seed=0) as proxy:
        proxy.begin_episode("smoke")
        with urllib.request.urlopen(proxy.base_url + "/calendar/create_event", timeout=20) as r:
            page = r.read().decode()
        print("upstream", upstream, "proxy", proxy.base_url)
        print("wizard marker through proxy:", 'id="wizard-next-1"' in page)
        print("upstream url rewritten:", upstream not in page)
        print(json.dumps(proxy.schedule(), indent=1))
