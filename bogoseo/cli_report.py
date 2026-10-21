"""`bogoseo report ...` subcommands."""
from __future__ import annotations

import sys
from pathlib import Path

from . import report as R


def _init(a):
    from . import config
    folder = config.reports_dir() / a.name
    R.init_report(a.name, folder, draft=a.draft, evidence=a.evidence or [])
    print(folder)
    return 0


def _status(a):
    print(R.format_summary(R.resume_summary(a.dir)))
    return 0


def _candidates(a):
    done = R._confirmed_keys(a.dir)
    labels = {"pending": "대기", "dismissed": "제외", "confirmed": "확정"}
    for c in R._load(Path(a.dir) / "candidates.yaml", []):
        st = c.get("status", "pending")
        if (c["name"], str(c["value"])) in done:
            st = "confirmed"
        elif st == "confirmed":
            st = "pending"
        if st == "dismissed" and not a.all:
            continue
        conf = f" 충돌:{','.join(c['conflict_with'])}" if c.get("conflict_with") else ""
        print(f"[{labels.get(st, st)}] {c['name']} = {c['value']} ({c['evidence_source']}){conf}")
    return 0


def _confirm(a):
    f = R.confirm_fact(a.dir, a.name, value=a.value, aliases=a.alias,
                       used_in_body=not a.not_in_body, source=a.source)
    print(f"확정: {f['name']} = {f['value']}")
    return 0


def _add_candidate(a):
    R.add_candidate(a.dir, a.name, a.value, a.source)
    print(f"후보 추가: {a.name} = {a.value} ({a.source})")
    return 0


def _dismiss(a):
    n = R.dismiss_candidates(a.dir, names=a.names or None, rest=a.rest)
    print(f"제외: {n}건")
    return 0


def _set_style(a):
    print(R.set_style(a.dir, a.kind, a.ending))
    return 0


def _set_reference(a):
    from . import config, warehouse
    conn = warehouse.connect()
    rebuilt = R.sync_reference(a.dir, a.doc_id, conn, config.root())
    print("rebuilt" if rebuilt else "unchanged")
    return 0


def _recommend(a):
    from . import warehouse
    for r in R.recommend(warehouse.connect(), doc_type=a.doc_type, limit=a.limit):
        print(r)
    return 0


def _outline(a):
    print(R.outline(a.dir))
    return 0


def _log(a):
    if a.before is not None or a.after is not None:
        R.record_approval(a.dir, a.question, a.before, a.after, a.approved == "yes")
    if a.answer is not None:
        R.log_answer(a.dir, a.question, a.answer, a.source)
    return 0


def _guard(a):
    if a.mark_checked:
        R.mark_checked(a.dir)
        print("marked")
        return 0
    g = R.regen_guard(a.dir, a.proposed)
    if g["diff"]:
        print(g["diff"])
    print("USER_EDITED: keep user text unless approved" if g["user_edited"] else "UNCHANGED since last check")
    return 3 if g["user_edited"] else 0


def register(subparsers):
    p = subparsers.add_parser("report", help="per-report workflow")
    sp = p.add_subparsers(dest="report_cmd", required=True)

    def add(name, fn, **kw):
        s = sp.add_parser(name, **kw)
        s.set_defaults(func=fn)
        return s

    s = add("init", _init)
    s.add_argument("name"); s.add_argument("--draft"); s.add_argument("--evidence", nargs="*")
    for n, fn in (("status", _status), ("outline", _outline)):
        add(n, fn).add_argument("dir")
    s = add("candidates", _candidates)
    s.add_argument("dir"); s.add_argument("--all", action="store_true")
    s = add("confirm", _confirm)
    s.add_argument("dir"); s.add_argument("name"); s.add_argument("--value")
    s.add_argument("--alias", action="append"); s.add_argument("--source")
    s.add_argument("--not-in-body", action="store_true")
    s = add("add-candidate", _add_candidate)
    s.add_argument("dir"); s.add_argument("name"); s.add_argument("value")
    s.add_argument("--source", required=True)
    s = add("dismiss", _dismiss)
    s.add_argument("dir"); s.add_argument("names", nargs="*"); s.add_argument("--rest", action="store_true")
    s = add("set-style", _set_style)
    s.add_argument("dir"); s.add_argument("--kind", required=True, choices=["개조식", "서술식"])
    s.add_argument("--ending", choices=["b1", "b2", "b3"])
    s = add("set-reference", _set_reference)
    s.add_argument("dir"); s.add_argument("doc_id")
    s = add("recommend", _recommend)
    s.add_argument("--doc-type"); s.add_argument("--limit", type=int, default=5)
    s = add("log", _log)
    s.add_argument("dir"); s.add_argument("--question", required=True)
    s.add_argument("--answer"); s.add_argument("--source", default="user",
                                                choices=["user", "code", "research", "refined"])
    s.add_argument("--before"); s.add_argument("--after")
    s.add_argument("--approved", choices=["yes", "no"], default="yes")
    s = add("regen-guard", _guard)
    s.add_argument("dir"); s.add_argument("--proposed"); s.add_argument("--mark-checked", action="store_true")
    p.set_defaults(func=lambda a: p.print_help() or 1)
