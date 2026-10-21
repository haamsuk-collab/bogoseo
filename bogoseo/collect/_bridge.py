"""Bridge executed by the insane-search interpreter (never imported by bogoseo).

Usage: python3.12 _bridge.py - ATTEMPT_TIMEOUT_S [MAX_ATTEMPTS]
The first argument must be ``-``: the URL is read from the first stdin line (keeps API keys
out of argv/ps). Any other value is rejected.
Runs insane-search's own fetch chain (engine.fetch_chain.fetch) and prints one JSON
object on stdout that also carries the raw response bytes (base64), which the stock CLI
cannot return for PDF/HWP/HWPX downloads. All network traffic is made by the engine.
The engine buffers the whole body (no streaming/byte-limit hook), so the size cap
(env BOGOSEO_MAX_DOWNLOAD_BYTES) is applied to Content-Length / body before base64.
"""
import base64
import json
import os
import re
import sys

_SECRET = r"key|servicekey|oc|apikey|api_key|authkey|auth_key"


def scrub(text):
    """Mask credential query values in any URL/error text leaving this process."""
    return re.sub(rf"(?i)((?:^|[?&;\s'\"])(?:{_SECRET})=)[^&\s'\"]*", r"\1***", str(text or ""))


def main() -> int:
    sys.path.insert(0, os.getcwd())  # cwd is the insane-search skill dir
    if sys.argv[1] != "-":
        sys.stderr.write("_bridge.py: first argument must be '-' (URL is read from stdin)\n")
        return 2
    timeout = int(sys.argv[2])
    url = sys.stdin.readline().strip()
    max_attempts = int(sys.argv[3]) if len(sys.argv) > 3 else None
    limit = int(os.environ.get("BOGOSEO_MAX_DOWNLOAD_BYTES") or 100 * 1024 * 1024)
    from engine import fetch_chain as fc

    seen = {}
    orig_build, orig_give = fc._build_result, fc._give_up

    def build(resp, *a, **k):
        seen["resp"] = resp
        return orig_build(resp, *a, **k)

    def give(trace, profile_used, last_resp, *a, **k):
        seen["resp"] = last_resp
        return orig_give(trace, profile_used, last_resp, *a, **k)

    fc._build_result, fc._give_up = build, give
    res = fc.fetch(url, timeout=timeout, max_attempts=max_attempts, enable_extraction=False,
                   enable_markdown=False, enable_playwright=False)
    resp = seen.get("resp")
    body = getattr(resp, "content", b"") if resp is not None else b""
    headers = {}
    try:
        headers = {str(k).lower(): str(v) for k, v in dict(resp.headers).items()}
    except Exception:
        pass
    trace = [{"executor": a.executor, "impersonate": a.impersonate, "status": a.status,
              "verdict": a.verdict, "error": scrub(a.error) if a.error else a.error} for a in res.trace]
    codes = [t["status"] for t in trace if t["status"]]
    out = {
        "ok": bool(res.ok),
        "status": getattr(resp, "status_code", None) if resp is not None else None,
        "last_code": codes[-1] if codes else None,
        "final_url": scrub(res.final_url),
        "content_type": headers.get("content-type", ""),
        "content_disposition": headers.get("content-disposition", ""),
        "stop_reason": scrub(res.stop_reason),
        "block_class": res.block_class,
        "trace": trace,
    }
    try:
        declared = int(headers.get("content-length", "0") or 0)
    except ValueError:
        declared = 0
    if max(declared, len(body or b"")) > limit:
        out.update({"ok": False, "error": "too_large", "body_b64": ""})
    else:
        out["body_b64"] = base64.b64encode(body or b"").decode("ascii")
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
