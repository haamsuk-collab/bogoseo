# bogoseo collector

Bulk collection of reference documents from 15 sources (9 API, 6 web) into the warehouse (`references/index.sqlite`).
All HTTP goes through the insane-search engine; every request is logged in `web_access_log` (`via_insane_search=1`).

## Setup

```
./setup.sh      # venv deps, npm ci, kordoc OCR model check, keys template, skill symlink, systemd user units + timer
```

Keys live in `~/.config/bogoseo/keys.yaml` (never committed). `setup.sh` copies the flat template
`keys.example.yaml` there (mode 600) only if the file is absent. The item catalogue (names, issuing URLs,
sources) is package data in `bogoseo/keys_catalog.yaml`; `bogoseo keys` reads it and shows which items are filled in.

The collector requires the **insane-search** skill: directory `~/.omp/agent/skills/insane-search`
(override `BOGOSEO_INSANE_DIR`), interpreter `/usr/bin/python3.12` (override `BOGOSEO_INSANE_PYTHON`) that can
`from engine import fetch_chain`. If it is missing, `setup.sh` warns, skips enabling the collector timer and exits 0;
install it and rerun `./setup.sh`. Download size is checked after the response is received (`max_download_mb`); the insane-search engine buffers whole responses, so there is no streaming cap.

## API keys

| key item | sources | issuing URL |
|---|---|---|
| `data_go_kr` | 정책브리핑, 행정안전부_정부 공문서 AI 학습데이터, 재정경제부_보도자료, 과학기술정보통신부_보도자료, 국회입법조사처 | https://www.data.go.kr/iim/api/selectAPIAcountView.do |
| `nabo` | 국회예산정책처 | https://www.nabo.go.kr/ko/api/apply.do?key=2509230004 |
| `nanet` | 국회도서관 | https://www.data.go.kr/data/15098174/openapi.do |
| `law_oc` | 법제처 | https://open.law.go.kr/LSO/openApi/cuAskList.do |
| `assembly` | 열린국회정보 | https://open.assembly.go.kr/portal/openapi/openApiActKeyIssPage.do |

Without a key the source is reported `미검증`, the run result is `건너뜀`, and `⚠ API 키 미입력` is printed.

## Commands

```
bin/bogoseo keys                    # key items, issuing URLs, presence (no values)
bin/bogoseo verify --sources api    # one live call per API source: 통과 / 실패 (code) / 미검증 + API 키 미입력
bin/bogoseo collect [--source NAME] [--limit N]
bin/bogoseo collect-status
```

Statuses: API sources `통과|미검증|실패`; web sources `입고|차단됨|실패|0건`; run results `성공|건너뜀|차단됨|실패|0건`.
A blocked source (CAPTCHA, login, 403 after all insane-search stages) is reported with the stages tried and the last response code.
The cursor (newest document date) is committed with each ingest; an immediate second run adds 0. Collection stops when free disk is below `min_free_bytes`.

## Report workflow

`bin/bogoseo report <subcommand>`, in workflow order:

- `init <name> [--draft FILE] [--evidence FILE...]` — create the report folder.
- `status <dir>` — confirmed facts and remaining questions.
- `candidates <dir> [--all]` — list reference candidates.
- `add-candidate <dir> <name> <value> --source FILE` — add a candidate manually.
- `confirm <dir> <name> [--value V] [--alias A] [--source S] [--not-in-body]` — confirm a candidate.
- `dismiss <dir> [names] [--rest]` — dismiss named candidates, or all remaining with `--rest`.
- `recommend [--doc-type T] [--limit N]` — recommend reference documents.
- `set-reference <dir> <doc_id>` — set the reference document.
- `set-style <dir> --kind 개조식|서술식 [--ending b1|b2|b3]` — set the report style.
- `outline <dir>` — build the outline.
- `log <dir> --question Q [--answer A] [--source ...] [--before B --after A --approved yes|no]` — log an interview result.
- `regen-guard <dir> [--proposed FILE] [--mark-checked]` — guard against overwriting user edits.

Top-level (not under `report`): `bin/bogoseo check reports/<name>/draft.md` validates the draft.

## Source notes

- 국회입법조사처: uses the `data_go_kr` key (data.go.kr 국회입법조사처_발간물, PublicationService).
- 국회도서관: uses a data.go.kr key (`nanet` item); apply at https://www.data.go.kr/data/15098174/openapi.do.
- 열린국회정보: key from https://open.assembly.go.kr/portal/openapi/openApiActKeyIssPage.do.
- 국회예산정책처: `https://www.nabo.go.kr/api/v1/report.do?key=...`.
- 법제처: `http://www.law.go.kr/DRF/lawSearch.do?OC=...&target=admrul`.
