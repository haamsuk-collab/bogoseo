"""`bogoseo verify --sources api`: one live call per API source, one status line each."""
from __future__ import annotations

from typing import Callable, Optional

from . import config, warehouse
from .collect.fetch import Fetcher
from .collect.sources import adapters

NO_KEY = "미검증 + API 키 미입력"


def verify_api(conn=None, *, keys: Optional[dict] = None, fetcher_factory: Optional[Callable] = None) -> list:
    """Returns [(source, status, line)]; status is 통과|실패|미검증. Updates the ``sources`` table when conn is given."""
    keys = config.load_keys() if keys is None else keys
    factory = fetcher_factory or Fetcher
    out = []
    for ad in adapters("api"):
        key = keys.get(ad.key_item)
        if not key:
            status, line, code = "미검증", f"{ad.name}: {NO_KEY}", ""
            key_present = False
        else:
            key_present = True
            fx = factory(f"verify:{ad.name}", conn=conn)
            try:
                res = ad.verify(fx, key)
            except Exception as e:      # unparsable body etc.: key present but the call failed
                last = fx.last_response
                code = str((last.code if last else None) or type(e).__name__)
                status, line = "실패", f"{ad.name}: 실패 ({code})"
            else:
                if res.ok:
                    status, line, code = "통과", f"{ad.name}: 통과", str(res.code or "")
                else:
                    code = str(res.code or "error")
                    status, line = "실패", f"{ad.name}: 실패 ({code})"
        if conn is not None:
            warehouse_status(conn, ad, status, code, key_present)
        out.append((ad.name, status, line))
    return out


def warehouse_status(conn, ad, status: str, code: str, key_present: bool) -> None:
    conn.execute(
        "INSERT INTO sources(name, kind, status, last_response_code, key_present, updated_at) VALUES(?,?,?,?,?,?) "
        "ON CONFLICT(name) DO UPDATE SET kind=excluded.kind, status=excluded.status, "
        "last_response_code=excluded.last_response_code, key_present=excluded.key_present, updated_at=excluded.updated_at",
        (ad.name, ad.kind, status, code, int(key_present), warehouse.now_iso()))
