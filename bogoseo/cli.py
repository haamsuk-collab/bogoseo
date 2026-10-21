"""bogoseo command line. Slices B/C add subcommands via register(subparsers)."""
from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path


def _tty_ask(question: str, options: list[str]) -> str:
    print(question, file=sys.stderr)
    for i, o in enumerate(options, 1):
        print(f"  {i}) {o}", file=sys.stderr)
    ans = input("> ").strip()
    if ans.isdigit() and 1 <= int(ans) <= len(options):
        return options[int(ans) - 1]
    return ans


def cmd_ingest(args) -> int:
    from . import warehouse
    conn = warehouse.connect()
    ask = _tty_ask if sys.stdin.isatty() else None
    rc = 0
    for f in args.files:
        r = warehouse.ingest_file(
            Path(f), conn=conn, institution=args.institution,
            institution_class=args.institution_class, doc_type=args.doc_type,
            doc_id=args.doc_id, draft=args.draft, ask=ask, confirm_warehouse=args.yes_warehouse,
            source=args.source, source_url=args.source_url, document_date=args.document_date)
        print(json.dumps(r, ensure_ascii=False))
        if r["status"] in ("needs_user", "failed"):
            rc = 2
            if r["status"] == "needs_user":
                print("사용자 확인 필요: --draft NAME, --yes-warehouse 또는 --doc-id ID 로 다시 실행",
                      file=sys.stderr)
    return rc


def cmd_check(args) -> int:
    from . import check
    res = check.check_draft(args.draft)
    labels = {"i": "(i) 사실", "ii": "(ii) 확인 필요 표시", "iii": "(iii) 숫자", "iv": "(iv) 종결어미"}
    for k, items in res["failures"].items():
        for it in items:
            print(f"FAIL {labels[k]}: {it}")
    print("PASS" if res["ok"] else "FAIL")
    return res["exit_code"]


def cmd_extract(args) -> int:
    from . import extract
    r = extract.extract(Path(args.file), force_ocr=args.force_ocr)
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=1))
    else:
        if not r["ok"]:
            print(f"extraction failed: {r['error']}", file=sys.stderr)
            return 1
        print(r["markdown"])
        if r["low_conf"]:
            print(f"[저신뢰 OCR 줄 {len(r['low_conf'])}개: 비전 단계 필요]", file=sys.stderr)
    return 0 if r["ok"] else 1


def cmd_star(args) -> int:
    from . import warehouse
    conn = warehouse.connect()
    if not warehouse.set_star(conn, args.doc_id, not args.off):
        print(f"unknown doc id {args.doc_id}", file=sys.stderr)
        return 1
    print(f"{args.doc_id} starred={not args.off}")
    return 0


def cmd_score(args) -> int:
    from . import quality, warehouse
    conn = warehouse.connect()
    ids = [r["doc_id"] for r in conn.execute("SELECT doc_id FROM documents ORDER BY doc_id")] \
        if args.all or not args.doc_ids else args.doc_ids
    for d in ids:
        r = quality.score_document(conn, d)
        print(f"{d}\t{'측정 불가' if r['unmeasurable'] else r['score']}\t{json.dumps(r['components'], ensure_ascii=False)}")
    return 0


def cmd_recommend(args) -> int:
    from . import quality, warehouse
    conn = warehouse.connect()
    for d in quality.recommend(conn, args.type):
        score = "측정 불가" if d["quality_score"] is None else d["quality_score"]
        print(f"{'★' if d['starred'] else ' '} {d['doc_id']}\t{d['doc_type']}\t{score}\t{d['title']}")
    return 0


def cmd_cap(args) -> int:
    from . import diskcap, warehouse
    conn = warehouse.connect()
    r = diskcap.enforce_cap(conn, dry_run=args.dry_run)
    print(json.dumps(r, ensure_ascii=False, indent=1))
    return 0


def cmd_docs(args) -> int:
    from . import warehouse
    conn = warehouse.connect()
    for d in warehouse.list_documents(conn):
        files = warehouse.list_files(conn, d["doc_id"])
        roles = ",".join(f"{f['role']}{'(원본 삭제됨)' if f['original_deleted'] else ''}" for f in files)
        score = "측정 불가" if d["quality_score"] is None else d["quality_score"]
        print(f"{d['doc_id']}\t{d['doc_type']}/{d['institution_class']}\t{d['institution']}\t"
              f"{score}\t{'★' if d['starred'] else '-'}\t{roles}\t{d['title']}")
    return 0


def cmd_repair_versions(args) -> int:
    from . import warehouse
    conn = warehouse.connect()
    for m in warehouse.split_misgrouped(conn, dry_run=args.dry_run):
        print(f"{'[dry-run] ' if args.dry_run else ''}{m['from_doc']} -> {m['to_doc']}\t"
              f"file {m['file_id']}\t{m['filename']}\t{m['title']}")
    return 0


def cmd_repair_titles(args) -> int:
    from . import warehouse
    conn = warehouse.connect()
    for c in warehouse.repair_titles(conn, dry_run=args.dry_run):
        print(f"{'[dry-run] ' if args.dry_run else ''}{c['doc_id']}: {c['old']} -> {c['new']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bogoseo", description="공문서 보고서 작성 도구")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("ingest", help="파일을 창고(또는 --draft 보고서 폴더)로 입고")
    s.add_argument("files", nargs="+")
    s.add_argument("--draft", metavar="NAME")
    s.add_argument("--doc-id")
    s.add_argument("--institution")
    s.add_argument("--institution-class")
    s.add_argument("--doc-type")
    s.add_argument("--yes-warehouse", action="store_true",
                   help="작성중 파일도 확인 없이 창고로 입고")
    s.add_argument("--source")
    s.add_argument("--source-url")
    s.add_argument("--document-date")
    s.set_defaults(func=cmd_ingest)

    s = sub.add_parser("check", help="초안 검사 (i)-(iv)")
    s.add_argument("draft")
    s.set_defaults(func=cmd_check)

    s = sub.add_parser("extract", help="문서 텍스트 추출")
    s.add_argument("file")
    s.add_argument("--force-ocr", action="store_true")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_extract)

    s = sub.add_parser("star", help="문서 ID 별표")
    s.add_argument("doc_id")
    s.add_argument("--off", action="store_true")
    s.set_defaults(func=cmd_star)

    s = sub.add_parser("score", help="품질 점수 재계산")
    s.add_argument("doc_ids", nargs="*")
    s.add_argument("--all", action="store_true")
    s.set_defaults(func=cmd_score)

    s = sub.add_parser("recommend", help="참고문서 추천 순서")
    s.add_argument("--type")
    s.set_defaults(func=cmd_recommend)

    s = sub.add_parser("cap", help="용량 상한 정리")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(func=cmd_cap)

    s = sub.add_parser("docs", help="문서 목록")
    s.set_defaults(func=cmd_docs)

    s = sub.add_parser("repair-versions", help="잘못 판본으로 묶인 수집 문서를 분리")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(func=cmd_repair_versions)

    s = sub.add_parser("repair-titles", help="수집 문서의 일반(boilerplate) 제목을 항목 제목으로 교체 "
                                             "(DB 제목·요약만 변경, 디스크 파일명은 바꾸지 않음)")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(func=cmd_repair_titles)

    for modname in ("bogoseo.cli_collect", "bogoseo.cli_report"):
        try:
            mod = importlib.import_module(modname)
        except ModuleNotFoundError as e:
            if e.name != modname:
                raise
            continue
        mod.register(sub)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    rc = args.func(args)
    return int(rc or 0)


if __name__ == "__main__":
    sys.exit(main())
