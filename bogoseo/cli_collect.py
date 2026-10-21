"""CLI subcommands of the collector: collect, keys, verify, collect-status."""
from __future__ import annotations

import sys

DEFINED_RESULTS = {"성공", "건너뜀", "차단됨", "실패", "0건"}


def cmd_collect(args) -> int:
    from . import warehouse
    from .collect import runner
    from .collect.sources import REGISTRY, adapters
    adapters()  # loads web adapters
    unknown = [s for s in (args.source or []) if s not in REGISTRY]
    if unknown:
        print(f"알 수 없는 소스: {', '.join(unknown)}", file=sys.stderr)
        return 2
    from . import config
    from .collect.lock import RunLocked, run_lock
    lock_path = config.refs_dir() / ".collect.lock"
    try:
        with run_lock(lock_path):
            conn = warehouse.connect()
            results = runner.run_all(conn, only=args.source, limit=args.limit)
    except RunLocked:
        print(f"수집이 이미 실행 중입니다(잠금: {lock_path})", file=sys.stderr)
        return 0
    # exit 0 whenever every source ended in a defined status
    return 0 if all(r.result in DEFINED_RESULTS for r in results) else 1


def cmd_keys(args) -> int:
    from . import keys
    print(keys.render())
    return 0


def cmd_verify(args) -> int:
    from . import keys as keys_mod, verify, warehouse
    from .collect.runner import NO_KEY_WARNING
    if args.sources != "api":
        print("지원하는 값: --sources api", file=sys.stderr)
        return 2
    conn = warehouse.connect()
    results = verify.verify_api(conn)
    if any(s == "미검증" for _, s, _ in results):
        print(NO_KEY_WARNING)
    for _, _, line in results:
        print(line)
    return 0


def cmd_status(args) -> int:
    from . import warehouse
    conn = warehouse.connect()
    for r in conn.execute("SELECT name, kind, status, stages_tried, last_response_code, cursor, key_present FROM sources ORDER BY kind, name"):
        extra = f" stages={r['stages_tried']} code={r['last_response_code']}" if r["status"] in ("차단됨", "실패") else ""
        print(f"{r['name']} [{r['kind']}]: {r['status']} cursor={r['cursor']}{extra}")
    for r in conn.execute("SELECT source, COUNT(*) n, SUM(report_like) rl FROM documents WHERE source IS NOT NULL GROUP BY source"):
        print(f"  documents {r['source']}: {r['n']} (report_like {r['rl'] or 0})")
    return 0


def register(subparsers) -> None:
    p = subparsers.add_parser("collect", help="bulk collection from the 15 sources")
    p.add_argument("--source", action="append", help="source name (repeatable)")
    p.add_argument("--limit", type=int, help="max items per source")
    p.set_defaults(func=cmd_collect)
    p = subparsers.add_parser("keys", help="show API key items, issuing URLs and presence")
    p.set_defaults(func=cmd_keys)
    p = subparsers.add_parser("verify", help="verify API keys with one live call per source")
    p.add_argument("--sources", default="api", help="api")
    p.set_defaults(func=cmd_verify)
    p = subparsers.add_parser("collect-status", help="per-source collection status and counts")
    p.set_defaults(func=cmd_status)
