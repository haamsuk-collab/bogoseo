"""insane-search wrapper: the only network path of the collector.

Every request runs the insane-search engine in a subprocess (``_bridge.py`` executed by
the insane-search interpreter), is spaced per source, retried, logged to
``web_access_log`` with ``via_insane_search=1`` and returns the raw bytes.
"""
from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .. import config

_BRIDGE = Path(__file__).with_name("_bridge.py")
# Query parameters whose value is a secret and must never reach logs or traces.
SECRET_PARAMS = {"key", "servicekey", "oc", "apikey", "api_key", "authkey", "auth_key"}
BLOCK_VERDICTS = {"challenge", "blocked", "waf_block", "captcha", "auth_required"}
# Per-attempt budget of the engine; keeps one request under the 60 s hard timeout.
ATTEMPT_TIMEOUT_S = 8
MAX_ATTEMPTS = 6
DEFAULT_TIMEOUT_S = 60
DEFAULT_DOWNLOAD_TIMEOUT_S = 300


_REPO = Path(__file__).resolve().parents[2]


def insane_dir() -> Path:
    env = os.environ.get("BOGOSEO_INSANE_DIR")
    if env:
        return Path(env).expanduser()
    vendor = _REPO / "vendor" / "insane-search"
    if vendor.is_dir():
        return vendor
    return Path("~/.omp/agent/skills/insane-search").expanduser()


def insane_python() -> str:
    return os.environ.get("BOGOSEO_INSANE_PYTHON") or str(_REPO / ".venv" / "bin" / "python")


def scrub_url(url: str) -> str:
    """Mask secret query parameters (API keys) before a URL is stored or shown."""
    parts = urlsplit(url)
    if not parts.query:
        return url
    q = [(k, "***" if k.lower() in SECRET_PARAMS else v) for k, v in parse_qsl(parts.query, keep_blank_values=True)]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(q, safe="*"), parts.fragment))


@dataclass
class Response:
    url: str
    ok: bool = False
    status: Optional[int] = None
    last_code: Optional[int] = None
    content_type: str = ""
    disposition: str = ""
    final_url: str = ""
    body: bytes = b""
    stages: list = field(default_factory=list)
    verdicts: list = field(default_factory=list)
    error: str = ""
    attempts: int = 1

    @property
    def code(self) -> Optional[int]:
        return self.status or self.last_code

    @property
    def blocked(self) -> bool:
        """CAPTCHA / login / 403 after every insane-search stage."""
        if self.ok:
            return False
        if self.code in (401, 403, 429):
            return True
        return any(v in BLOCK_VERDICTS for v in self.verdicts)

    def text(self) -> str:
        for enc in ("utf-8", "cp949"):
            try:
                return self.body.decode(enc)
            except UnicodeDecodeError:
                continue
        return self.body.decode("utf-8", "replace")

    def json(self):
        return json.loads(self.text())


def _response_from_bridge(url: str, d: dict) -> Response:
    trace = d.get("trace") or []
    stages, seen = [], set()
    for a in trace:
        s = f"{a.get('executor') or '?'}/{a.get('impersonate') or '-'}"
        if s not in seen:
            seen.add(s)
            stages.append(s)
    error = None
    if not d.get("ok") and not d.get("error"):
        # engine-internal timeouts surface only in the per-attempt trace errors
        errs = [str(a.get("error") or "").lower() for a in trace]
        if any("timeout" in e or "timed out" in e for e in errs) and not d.get("status"):
            error = "timeout"
    return Response(
        url=url,
        ok=bool(d.get("ok")),
        status=d.get("status"),
        last_code=d.get("last_code"),
        content_type=d.get("content_type") or "",
        disposition=d.get("content_disposition") or "",
        final_url=scrub_url(d.get("final_url") or url),
        body=base64.b64decode(d.get("body_b64") or ""),
        stages=stages,
        verdicts=[a.get("verdict") for a in trace if a.get("verdict")],
        error=error,
    )


def has_secret_param(url: str) -> bool:
    return any(k.lower() in SECRET_PARAMS for k, _ in parse_qsl(urlsplit(url).query, keep_blank_values=True))


def scrub_text(text: str) -> str:
    """Mask ``name=value`` secrets (and URLs) inside free text such as stderr or error strings."""
    names = "|".join(sorted(SECRET_PARAMS, key=len, reverse=True))
    return re.sub(rf"(?i)((?:^|[?&;\s'\"])(?:{names})=)[^&\s'\"]*", r"\1***", text)


def max_download_bytes(settings: Optional[dict] = None) -> int:
    s = settings if settings is not None else config.settings()
    return int(float(s.get("max_download_mb", 100)) * 1024 * 1024)


def insane_run(url: str, timeout_s: int) -> dict:
    """Run the insane-search engine for one URL; returns the bridge JSON (or an error dict).

    The URL (which may carry an API key) goes to the bridge on stdin, never in argv."""
    if urlsplit(url).scheme.lower() == "http" and has_secret_param(url):
        return {"ok": False, "error": "refused_insecure_credential_url", "trace": []}
    env = dict(os.environ)
    env.setdefault("INSANE_OBSERVATIONS_DIR", str(Path("~/.insane-search/observations").expanduser()))
    env["BOGOSEO_MAX_DOWNLOAD_BYTES"] = str(max_download_bytes())
    if timeout_s > DEFAULT_TIMEOUT_S:     # long budget = attachment download: one attempt may use nearly all of it
        attempt_s, attempts = timeout_s - 10, 1
    else:
        attempt_s, attempts = ATTEMPT_TIMEOUT_S, MAX_ATTEMPTS
    cmd = [insane_python(), str(_BRIDGE), "-", str(attempt_s), str(attempts)]
    try:
        p = subprocess.run(cmd, cwd=str(insane_dir()), env=env, input=url + "\n", capture_output=True,
                           text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout", "trace": []}
    lines = [ln for ln in (p.stdout or "").strip().splitlines() if ln.startswith("{")]
    if not lines:
        return {"ok": False, "error": "no_output:" + scrub_text((p.stderr or "")[-200:]), "trace": []}
    return json.loads(lines[-1])


class Fetcher:
    """Per-source fetcher with spacing, retries and web_access_log."""

    def __init__(self, source: str, *, conn=None, settings: Optional[dict] = None,
                 runner: Optional[Callable[[str, int], dict]] = None,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep,
                 wall: Callable[[], float] = time.time):
        self.source = source
        self.conn = conn
        self.settings = settings if settings is not None else config.settings()
        self.runner = runner or insane_run
        self.clock, self.sleep, self.wall = clock, sleep, wall
        self.last_start: Optional[float] = None
        self.starts: list = []          # clock value at the start of every request (tests)
        self.log: list = []             # in-memory mirror of web_access_log rows
        self.last_response: Optional[Response] = None

    @property
    def interval(self) -> float:
        per = (self.settings.get("source_intervals") or {}).get(self.source)
        return float(per if per is not None else self.settings.get("request_interval_s", 3))

    def _wait(self) -> None:
        if self.last_start is not None:
            gap = self.interval - (self.clock() - self.last_start)
            if gap > 0:
                self.sleep(gap)

    def _log(self, url: str, code: Optional[int]) -> None:
        ts = datetime.fromtimestamp(self.wall()).astimezone().isoformat(timespec="seconds")
        row = (self.source, scrub_url(url), 1, None if code is None else str(code), ts)
        self.log.append(row)
        if self.conn is not None:
            self.conn.execute(
                "INSERT INTO web_access_log(source, url, via_insane_search, status_code, ts) VALUES (?,?,?,?,?)", row)

    def get(self, url: str, *, download: bool = False) -> Response:
        """``download=True`` (attachment bodies) uses ``timeouts.download_s`` and at most one retry:
        a ~100 MB file needs far more than the 60 s page budget."""
        timeouts = self.settings.get("timeouts") or {}
        retries = int(self.settings.get("retries", 2))
        if download:
            timeout = int(timeouts.get("download_s", DEFAULT_DOWNLOAD_TIMEOUT_S))
            retries = min(retries, 1)
        else:
            timeout = int(timeouts.get("insane_search_s", DEFAULT_TIMEOUT_S))
        resp = Response(url=url, error="not_run")
        for attempt in range(retries + 1):
            self._wait()
            self.last_start = self.clock()
            self.starts.append(self.last_start)
            if urlsplit(url).scheme.lower() == "http" and has_secret_param(url):
                resp = Response(url=url, error="refused_insecure_credential_url", attempts=attempt + 1)
                self.last_response = resp
                return resp
            raw = self.runner(url, timeout)
            resp = _response_from_bridge(url, raw) if raw.get("trace") is not None and "body_b64" in raw else Response(
                url=url, error=scrub_text(str(raw.get("error") or "no_response")), verdicts=[])
            if not resp.error and raw.get("error"):
                resp.error = scrub_text(str(raw["error"]))
            if resp.ok and len(resp.body) > max_download_bytes(self.settings):
                resp = Response(url=url, error="too_large", status=resp.status, last_code=resp.last_code,
                                stages=resp.stages, verdicts=resp.verdicts)
            resp.attempts = attempt + 1
            self._log(url, resp.code)
            transient = ((not resp.ok) and not resp.blocked and resp.error != "too_large"
                         and (resp.code is None or resp.code >= 500 or resp.code == 408))
            if resp.ok or not transient:
                break
        self.last_response = resp
        return resp


def stages_summary(resp: Response) -> str:
    return ",".join(resp.stages) if resp.stages else "-"
