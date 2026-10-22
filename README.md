# bogoseo

An omp skill for drafting Korean military/government reports (보고서, 개선안) in the marker system and style of a library reference, plus a collector that fills the reference warehouse (`references/index.sqlite`) from 15 sources (9 API, 6 web). All collector HTTP goes through the insane-search engine; every request is logged in `web_access_log` (`via_insane_search=1`).

Contents: [For AI agents: installing bogoseo](#for-ai-agents-installing-bogoseo) · [Requirements](#requirements) · [Usage guide](#usage-guide) · [insane-search](#insane-search) · [API keys](#api-keys) · [Command reference](#command-reference) · [Source notes](#source-notes)

---

## For AI agents: installing bogoseo

Run the steps in order from a normal shell of the target user (not root). Stop at the first step whose output differs from "Expected" and look it up in [Troubleshooting](#troubleshooting). Do not skip the preflight.

### Rules for the agent

1. Never commit `~/.config/bogoseo/keys.yaml` (it lives outside the repo, mode 600) and never print its values. Use `bin/bogoseo keys`, which shows presence only.
2. Never edit tests (`tests/`) to make them pass. If a test fails, report it.
3. API keys are personal. Ask the user for them (table in [API keys](#api-keys)); never invent or copy keys from elsewhere.
4. Run `sudo loginctl enable-linger "$USER"` only if the user has sudo and agrees. Without it the install still works; the daily timer just does not fire while the user is logged out.
5. Do not run `bin/bogoseo collect` as part of installation. It makes many live requests and writes to the warehouse; the timer does it daily.
6. Do not touch `~/.omp/agent/` or other users' systemd units. `setup.sh` writes only the repo, `~/.config/bogoseo/`, `~/.agents/skills/bogoseo`, and `~/.config/systemd/user/bogoseo-collect.*`.

### 1. Preflight

| Check | Command | Expected |
|---|---|---|
| Linux, not root | `uname -s; id -un` | `Linux`, a non-root user name |
| systemd user manager | `systemctl --user is-system-running` | `running` or `degraded` (either is fine). `Failed to connect to bus` means no user session: see Troubleshooting |
| Python 3.12 | `python3.12 --version` | `Python 3.12.x` |
| Node 20+ and npm | `node --version; npm --version` | `v20.x` or higher (e.g. `v20.20.2`), any npm version |
| git, curl | `git --version; curl --version \| head -1` | both print a version |
| Disk | `df -h . \| tail -1` | at least 4 GiB available (see sizes below) |
| Network | `for u in api.github.com codeload.github.com pypi.org registry.npmjs.org; do curl -sS -o /dev/null -m 10 -w "$u %{http_code}\n" https://$u/; done` | each host prints `200` or `301` (any HTTP status means reachable; `curl: (6)` / `(7)` / `(28)` means blocked) |

Measured sizes on a working install (2026-10-21): `.venv` 354 MB, `node_modules` 762 MB, `.cache/kordoc-models` 18 MB (PP-OCRv5 Korean det+rec), `vendor/insane-search` 0.8 MB. That is about 1.1 GB for the install itself. The collector also needs `min_free_bytes` (2 GiB, `settings.yaml`) free to run, and the warehouse is capped at `cap_bytes` (5 GiB). Hence the 4 GiB minimum for install plus a first collection; allow up to about 7 GiB if the warehouse will grow to its cap.

Google Chrome is optional (insane-search browser lane only). Check with `command -v google-chrome chromium`.

### 2. Get the code

```
git clone <repo-url> ~/work/bogoseo
cd ~/work/bogoseo
```

`~/work/bogoseo` is the default because `systemd/*.service` is written with `%h/work/bogoseo` paths, and the docs assume it. It is not hard-wired: `setup.sh` resolves its own location and rewrites the unit paths, the skill symlink and the insane-search drop-in to the actual clone directory, so any path works. Prefer a path without spaces. omp finds the skill through the symlink `~/.agents/skills/bogoseo` -> clone directory, and `SKILL.md` runs `bin/bogoseo` relative to that target. If the symlink already exists and points elsewhere, `setup.sh` leaves it unchanged and warns; move your clone to the linked path or replace the link yourself (`ln -sfn "$PWD" ~/.agents/skills/bogoseo`) after asking the user.

### 3. Run setup

```
./setup.sh
```

Flags: `--no-update` skips the insane-search release check (use when GitHub is unreachable or you want a reproducible vendor copy); `--no-timer` stops after the dependency, keys, symlink and insane-search steps and writes no systemd units. Any other argument prints `usage: ./setup.sh [--no-update] [--no-timer]` and exits 2. The script is idempotent; rerunning is safe. It stops on the first hard failure (`set -e`).

What it does, in order, and what you see when each step is OK:

| Step | Success output |
|---|---|
| Create `.venv` with `python3.12`, `pip install -r requirements.txt` | silent (pip runs with `-q`) |
| `npm ci` (kordoc 4.22.16) | npm's `added N packages` summary |
| `kordoc check-ocr-models` with `KORDOC_MODEL_CACHE=.cache/kordoc-models` | `[kordoc-ocr] ... (skip)` lines (or download lines on first run) and `ok` |
| keys template | `created ~/.config/bogoseo/keys.yaml (fill in the keys)` on first run; silent afterwards. Never overwrites |
| skill symlink | silent when created or already correct |
| insane-search | `insane-search vX.Y.Z installed in <repo>/vendor/insane-search`, or `insane-search vX.Y.Z already installed` |
| systemd units (skipped by `--no-timer`) | silent, then the timer is enabled and started |
| end | `bogoseo setup done` (with `--no-timer`: `bogoseo setup done (--no-timer: systemd units not written)`) |

Any line starting with `WARNING:` or the line `kordoc OCR models missing: see README` means a partial install; fix it with the Troubleshooting table and rerun `./setup.sh`.

### 4. Ask the user for API keys

Open `~/.config/bogoseo/keys.yaml` (already mode 600) and have the user fill the five items; edit the file only with values the user gives you.

| key item | where to apply |
|---|---|
| `data_go_kr` | https://www.data.go.kr/iim/api/selectAPIAcountView.do (then 활용신청 for each dataset) |
| `nabo` | https://www.nabo.go.kr/ko/api/apply.do?key=2509230004 |
| `nanet` | https://www.data.go.kr/data/15098174/openapi.do (활용신청) |
| `law_oc` | https://open.law.go.kr/LSO/openApi/cuAskList.do |
| `assembly` | https://open.assembly.go.kr/portal/openapi/openApiActKeyIssPage.do |

Keys are optional for installation. Without a key the dependent source is reported `미검증` and skipped.

### 5. Verify

Run from the repo directory. Commands marked read-only change nothing.

| Command | Expected |
|---|---|
| `bin/bogoseo --help` | usage with subcommands `ingest,check,extract,star,score,recommend,cap,docs,repair-versions,repair-titles,collect,keys,verify,collect-status,report`; exit 0 |
| `bin/bogoseo keys` | first line `키 파일: /home/<user>/.config/bogoseo/keys.yaml`, then one block per item with `입력됨` or `미입력`; exit 0. After step 4 all five say `입력됨` |
| `bin/bogoseo verify --sources api` | 9 lines `<source>: 통과`; `실패 (401)` etc. means the key is wrong; `미검증` plus `⚠ API 키 미입력` means the key is empty; exit 0 in all cases. Makes one live call per source |
| `.venv/bin/python -m pytest -q tests/` | `N passed` with no failures (131 passed on 2026-10-21; tests that depend on collected data skip before the first collection). Takes about 50 s |
| `systemctl --user is-enabled bogoseo-collect.timer` | `enabled` (not applicable after `--no-timer`) |
| `systemctl --user list-timers bogoseo-collect.timer --no-pager` | a `NEXT` time around 03:30 local plus up to 10 minutes |
| `cat vendor/insane-search/.version` | a release tag such as `v0.17.3` (`main` if it was installed from the branch) |
| `ls -l ~/.agents/skills/bogoseo` | symlink to the clone directory |

Installation is complete when these pass. Tell the user which keys are still missing and whether lingering is off.

### Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `python3.12: command not found` (setup.sh exits 127 at the venv step) | Python 3.12 not installed or not under that name | Install it (e.g. `sudo dnf install python3.12` / `sudo apt install python3.12 python3.12-venv`), or ask the user. Other versions are not supported: the pins in `requirements.txt` and the insane-search interpreter assume 3.12 |
| `The virtual environment was not created successfully` / `ensurepip is not available` | Debian/Ubuntu without `python3.12-venv` | `sudo apt install python3.12-venv`; `rm -rf .venv`; rerun |
| pip error during `requirements.txt` install | no network to PyPI, or a wheel is unavailable for the platform (`curl_cffi`, `kiwipiepy`, `pillow`) | Check the network row of preflight; confirm x86_64/aarch64 Linux with glibc; rerun |
| `npm ci` fails (`EAI_AGAIN`, `ETIMEDOUT`, `ENOTFOUND`, engine errors) | no access to registry.npmjs.org, or Node older than 20 | Fix the network/proxy (`npm config get registry`), upgrade Node to 20+, rerun. `package-lock.json` must stay unchanged; do not use `npm install` |
| `kordoc OCR models missing: see README` | the OCR model download (about 18 MB) failed, usually network | Run `KORDOC_MODEL_CACHE=$PWD/.cache/kordoc-models node_modules/.bin/kordoc check-ocr-models` until it prints `ok`. Offline machines: sideload with `node_modules/.bin/kordoc models --help` (SHA-256 verified). Digital PDFs and Office files extract without the models; scanned PDFs and images need them |
| `WARNING: <path> exists and is not a link to <repo>; left unchanged` | `~/.agents/skills/bogoseo` is a directory or points to another clone | If it is an older clone of this repo, ask the user, then `ln -sfn "$PWD" ~/.agents/skills/bogoseo` |
| `WARNING: GitHub release lookup failed; keeping installed insane-search <tag>` | `api.github.com` unreachable, or the unauthenticated rate limit (60 requests/hour per IP) is used up | Nothing to do: the installed copy is kept. Retry later or use `--no-update` |
| `WARNING: could not download or validate insane-search (<url>); keeping existing copy if any` | download from codeload.github.com failed, the archive had no `skills/insane-search/SKILL.md`, or the staged copy failed `from engine import fetch_chain` | The existing copy is untouched. Check the network, rerun. If no copy exists the timer is not enabled (next row) |
| `WARNING: could not install insane-search <tag>; previous copy restored` | the final `mv` into `vendor/` failed (permissions, disk full) | Fix permissions or free disk space; rerun |
| `WARNING: insane-search not usable (dir ..., interpreter ...).` (with `--no-timer`) | no vendor copy and none at `~/.omp/agent/skills/insane-search`, or the interpreter cannot import `engine` | Rerun `./setup.sh` without `--no-update` on a networked host, or set `BOGOSEO_INSANE_DIR` / `BOGOSEO_INSANE_PYTHON` |
| `WARNING: insane-search not usable (dir ..., interpreter ..., 'from engine import fetch_chain').` followed by `Collector timer NOT enabled.` | same as above, without `--no-timer` | Fix the insane-search install as above and rerun `./setup.sh`; the timer is enabled only after the check passes |
| `WARNING: user lingering is off; the timer will not run while you are logged out.` | `loginctl` Linger is `no` | `sudo loginctl enable-linger "$USER"` (only with sudo and the user's agreement). Check with `loginctl show-user "$USER" -p Linger --value` -> `yes` |
| `Failed to connect to bus: No medium found` / `No such file or directory` at `systemctl --user` | no user session (ssh without PAM session, container, `su`) | `export XDG_RUNTIME_DIR=/run/user/$(id -u)` and retry; if the user manager is not running, use `./setup.sh --no-timer` and tell the user. (Not tested here.) |
| `bin/bogoseo keys` shows `미입력`; `verify` prints `미검증` and `⚠ API 키 미입력` | key item empty in `keys.yaml` | Ask the user for the key (step 4) |
| `verify` prints `실패 (401)` or `실패 (403)` | wrong key, or the key is not yet approved for that dataset (data.go.kr datasets need 활용신청 and some approval time) | Re-check the key and the application on the issuing site. |
| `collect` prints `건너뜀 ... 디스크 여유 부족으로 수집 중단` | free space under the warehouse root is below `min_free_bytes` (2 GiB) | Free disk space or run `bin/bogoseo cap` to prune the warehouse to `cap_bytes`; lower `min_free_bytes` only with the user's consent, via `~/.config/bogoseo/settings.yaml` |
| `수집이 이미 실행 중입니다(잠금: ...)` (exit 0) | another collect (e.g. the timer) holds `references/.collect.lock` | Wait for it to finish; see `journalctl --user -u bogoseo-collect` |
| pytest failures | missing dependency, wrong interpreter | Use `.venv/bin/python -m pytest`, not the system one; rerun `./setup.sh`. Report real failures; do not edit tests |

---

## Requirements

- omp (the skill is linked into `~/.agents/skills/bogoseo`)
- Linux with systemd user services (`systemctl --user`)
- Python 3.12 (`python3.12`), Node 20+ (kordoc extraction and OCR), git, curl
- Google Chrome (optional): only for the insane-search browser lane (`nodriver`); without it browser fallbacks fail
- Your own API keys (see below)

---

## Usage guide

All commands run from the repo directory as `bin/bogoseo ...` (a wrapper around `.venv/bin/python -m bogoseo.cli`), or from anywhere by absolute path.

### A. Draft a report with omp

1. Start omp in any directory and run `/skill:bogoseo` (or say "보고서 작성" / "개선안"). The agent works from the repo that `~/.agents/skills/bogoseo` points to.
2. **Resume or start.** If `reports/<name>/` exists the agent runs `bin/bogoseo report status reports/<name>` and continues; otherwise `bin/bogoseo report init <name> [--draft FILE] [--evidence FILE...]`. A draft you already wrote is the starting draft: its sentences and `[참고 N]` attachments are inherited into `draft.md`.
3. **Evidence first.** `report candidates reports/<name>` lists facts prefilled from the evidence and the starting draft. The agent reads the evidence itself, adds candidates (`report add-candidate`), and asks you only to confirm or correct (`report confirm`). When you are done, `report dismiss reports/<name> --rest` rejects the remainder. Only confirmed items enter `facts.yaml`.
4. **Choose the reference.** `bin/bogoseo report recommend [--doc-type 개선안]` lists the warehouse in order: starred, type match, score. You pick one; `report set-reference reports/<name> <doc_id>` copies its extracts and marker system into `reports/<name>/reference/`.
5. **Choose the style.** `report set-style reports/<name> --kind 개조식`, or `--kind 서술식 --ending b1|b2|b3` (b1 ~함./~임., b2 ~다., b3 ~습니다.).
6. **Approve the outline.** `report outline reports/<name>` shows reference vs draft headings side by side; you approve section order and flow.
7. **Interview.** The agent asks about what the evidence does not cover, using the Ouroboros interview (`ouroboros_interview`) when available, otherwise omp's `ask` tool. There is no question cap; you can say "draft now" at any time. Each answer is logged with `report log` into `interview_log.yaml`. Changes to inherited sentences are shown before/after and need your approval.
8. **Draft.** The agent writes `reports/<name>/draft.md` in the reference's marker system (□ → ㅇ → •/-, ☞ conclusion, * note, ※ caption). Unknowns are marked inline `[확인 필요 N: ...]` and listed in a final `□ 확인 필요 목록`; attachments start with `[참고 N] 제목`.
9. **Final polish (최종 윤문).** Once the content is settled, the agent follows `guides/polish-ko.md` to fix wording only. It first copies the draft to `reports/<name>/revision/draft_before_polish_<YYYYMMDD-HHMM>.md`, then shows a before → after list (line, before, after, rule id). Only lines you approve are changed, and if more than 30% of body lines would change it stops to ask. Facts, numbers, markers, `[참고 N]`, `[확인 필요 N: ...]`, line order and ending style are never touched.
10. **Guards.** Before the agent replaces an existing draft it runs `report regen-guard reports/<name> [--proposed NEW.md]`; exit 3 means you edited the file, and your text is kept unless you approve. Then the gate:

```
bin/bogoseo check reports/<name>/draft.md      # prints FAIL lines per rule (i) facts (ii) 확인 필요 marks (iii) numbers (iv) endings, then PASS/FAIL
bin/bogoseo report regen-guard reports/<name> --mark-checked
```

`check` must exit 0 before the draft is shown to you.

Where things land, under `reports/<name>/` (gitignored): `draft.md` (the only deliverable), `state.yaml`, `evidence.yaml`, `candidates.yaml`, `facts.yaml`, `interview_log.yaml`, `check_result.yaml`, `starting_draft/` (your original file plus `extract.md`), `reference/` (copied reference extracts and `marker_system.yaml`). The folder files are the source of truth; the Ouroboros session id in `state.yaml` is only a cache.

### B. Add reference PDFs and other documents

Supported: `.pdf .hwp .hwpx .docx .xlsx .md .txt .png .jpg .jpeg`. Ingest copies; originals in `inbox/` are never moved or deleted.

1. Put files in `inbox/` (gitignored; any path works).
2. Check what extraction yields: `bin/bogoseo extract inbox/file.pdf` prints Markdown; add `--json` for the full result and `--force-ocr` to force OCR. kordoc runs first, then falls back to OCR (models in `.cache/kordoc-models`). Low-confidence OCR lines print `[저신뢰 OCR 줄 N개: 비전 단계 필요]` on stderr; the agent then renders pages and reads them with vision (see `SKILL.md`, "Extraction and OCR"). Unreadable text is never guessed; you are asked to paste it.
3. Ingest into the warehouse:

```
bin/bogoseo ingest inbox/민원개선안_1015.pdf --institution ○○청 --doc-type 개선안 --institution-class 중앙부처
bin/bogoseo ingest inbox/a.pdf inbox/b.pdf          # several files; metadata guessed when omitted
```

Each file prints one JSON line with `status`: `added`, `version` (a new version of an existing doc id), `duplicate`, `draft`, `needs_user`, or `failed`. Exit 2 means `needs_user` or `failed`. Doc types: 보고, 개선안, 계획, 결과, 검토, 건의, 지침, 기타. Institution classes: 국방, 중앙부처, 국회, 지자체, 공공기관, 민간. Files land under `references/<type>/<class>/` with `원본/` for originals.
4. Files whose name contains `작성중` are treated as in-progress drafts, not references. In a terminal you are asked; non-interactively you get `needs_user` and must rerun with `--draft <report-name>` (copies it to `reports/<report-name>/starting_draft/`) or `--yes-warehouse` (ingest anyway). Use `--doc-id doc_0001` to force a file into an existing document, and `--source`, `--source-url`, `--document-date` to record provenance.
5. For a report's own evidence use `report init <name> --evidence FILE...` instead; evidence is only referenced by path in `evidence.yaml`, not added to the warehouse.

### C. Warehouse operations

```
bin/bogoseo docs                        # doc_id, type/class, institution, score, ★, roles, title (tab separated)
bin/bogoseo star doc_0001               # favour in recommendations; --off to remove
bin/bogoseo score --all                 # recompute quality scores (or: score doc_0001 doc_0002)
bin/bogoseo recommend --type 개선안     # order: starred, type match, score
bin/bogoseo cap --dry-run               # JSON: cap, before/after bytes, what would be deleted
bin/bogoseo cap                         # enforce cap_bytes (5 GiB): deletes original files (unmeasurable, then lowest score first) until under the cap; starred docs are never deleted
bin/bogoseo repair-titles --dry-run     # replace boilerplate titles ("보도참고자료") with the item title; DB only, file names unchanged
bin/bogoseo repair-titles
bin/bogoseo repair-versions --dry-run   # split collected docs wrongly grouped as versions of one doc
bin/bogoseo repair-versions
```

Run the `--dry-run` form first and read its output. `docs` marks files whose original was removed by `cap` as `(원본 삭제됨)`. Scores show `측정 불가` for documents whose OCR confidence cannot be measured.

### D. Collection

1. Fill in keys (see [API keys](#api-keys)); check with `bin/bogoseo keys`.
2. `bin/bogoseo verify --sources api` makes one live call per API source: `통과`, `실패 (code)`, or `미검증` with `⚠ API 키 미입력`.
3. Manual run (the timer does this daily; only when the user wants it now):

```
bin/bogoseo collect                              # all 15 sources, up to collect_limit_per_source (20) each
bin/bogoseo collect --source 국방부 --limit 5    # one source (repeat --source for several), 5 items
```

   Unknown source names exit 2. Exit 0 when every source ended in a defined status. A second concurrent run exits 0 immediately with a lock message.
4. `bin/bogoseo collect-status` prints each source's status, cursor (newest document date) and document counts.
5. Timer: `systemd/bogoseo-collect.timer` runs `OnCalendar=*-*-* 03:30:00` with `RandomizedDelaySec=600` and `Persistent=true` (a missed run fires at next boot/login). The service is `Type=oneshot` with `TimeoutStartSec=6h`.

```
systemctl --user list-timers bogoseo-collect.timer --no-pager
systemctl --user start bogoseo-collect.service        # run now through systemd
journalctl --user -u bogoseo-collect.service -n 50 --no-pager
journalctl --user -u bogoseo-collect.service --since today --no-pager
```

Collection stops (source result `건너뜀`) when free disk is below `min_free_bytes`. Settings are in `settings.yaml`; override per user in `~/.config/bogoseo/settings.yaml` (deep-merged).

Statuses: API sources `통과|미검증|실패`; web sources `입고|차단됨|실패|0건`; run results `성공|건너뜀|차단됨|실패|0건`. A blocked source (CAPTCHA, login, 403 after all insane-search stages) is reported with the stages tried and the last response code. The cursor is committed with each ingest; an immediate second run adds 0.

### E. Update and uninstall

Update:

```
cd ~/work/bogoseo
git pull
./setup.sh              # refreshes deps (pip, npm ci), rewrites units, updates insane-search to the newest release
./setup.sh --no-update  # same, without the insane-search check
```

Then rerun the verification table in step 5 above.

Uninstall (ask the user before deleting anything under `references/` or `reports/`):

```
systemctl --user disable --now bogoseo-collect.timer
rm -f ~/.config/systemd/user/bogoseo-collect.service ~/.config/systemd/user/bogoseo-collect.timer
rm -rf ~/.config/systemd/user/bogoseo-collect.service.d
systemctl --user daemon-reload
rm -f ~/.agents/skills/bogoseo                  # only the symlink
# optional, with the user's consent:
rm -rf ~/.config/bogoseo                        # keys.yaml and settings.yaml
rm -rf .venv node_modules .cache vendor         # regenerable
# references/ (warehouse) and reports/ (drafts) hold your data: keep them, or delete the clone to remove everything
```

---

## insane-search

All HTTP uses the **insane-search** skill (https://github.com/fivetaku/insane-search, MIT). `./setup.sh` downloads its latest GitHub release (`skills/insane-search/` only) into `vendor/insane-search` (gitignored) and records the tag in `vendor/insane-search/.version`. Re-running `./setup.sh` updates to the newest release; `./setup.sh --no-update` skips the check. Its Python dependencies are pinned in `requirements.txt` and installed into bogoseo's own `.venv`, which is also the interpreter used.

Update failure behaviour:

- The new copy is downloaded into a temporary staging directory under `vendor/` and must pass `from engine import fetch_chain` (run with the collector's interpreter) and contain `SKILL.md` before it replaces anything. Only then is the old copy moved aside and the new one moved in; if that move fails the old copy is restored.
- If the release lookup, the download, or the check fails, the existing copy is kept and a `WARNING` is printed.
- The `main` branch is used only when no copy exists and the release lookup fails. A `main` install records `main` in `.version` and is replaced by the next successful release install.
- If no usable copy exists after all this, the timer is not enabled.

Resolution (`bogoseo/collect/fetch.py`): directory = `BOGOSEO_INSANE_DIR`, else `vendor/insane-search` if present, else `~/.omp/agent/skills/insane-search`; interpreter = `BOGOSEO_INSANE_PYTHON`, else `.venv/bin/python`. Both must be able to `from engine import fetch_chain`. `setup.sh` pins the checked paths for the timer service in `~/.config/systemd/user/bogoseo-collect.service.d/insane-search.conf`. Download size is checked after the response is received (`max_download_mb`); the engine buffers whole responses, so there is no streaming cap.

## API keys

`setup.sh` copies the flat template `keys.example.yaml` to `~/.config/bogoseo/keys.yaml` (mode 600) only if the file is absent. The item catalogue (names, issuing URLs, sources) is package data in `bogoseo/keys_catalog.yaml`; `bogoseo keys` shows which items are filled in (never the values).

| key item | sources | issuing URL |
|---|---|---|
| `data_go_kr` | 정책브리핑, 행정안전부_정부 공문서 AI 학습데이터, 재정경제부_보도자료, 과학기술정보통신부_보도자료, 국회입법조사처 | https://www.data.go.kr/iim/api/selectAPIAcountView.do |
| `nabo` | 국회예산정책처 | https://www.nabo.go.kr/ko/api/apply.do?key=2509230004 |
| `nanet` | 국회도서관 | https://www.data.go.kr/data/15098174/openapi.do |
| `law_oc` | 법제처 | https://open.law.go.kr/LSO/openApi/cuAskList.do |
| `assembly` | 열린국회정보 | https://open.assembly.go.kr/portal/openapi/openApiActKeyIssPage.do |

Without a key the source is reported `미검증`, the run result is `건너뜀`, and `⚠ API 키 미입력` is printed.

## Command reference

Top level: `ingest`, `check`, `extract`, `star`, `score`, `recommend`, `cap`, `docs`, `repair-versions`, `repair-titles`, `collect`, `keys`, `verify`, `collect-status`, `report`. Use `bin/bogoseo <command> --help` for flags.

```
bin/bogoseo keys                    # key items, issuing URLs, presence (no values)
bin/bogoseo verify --sources api    # one live call per API source: 통과 / 실패 (code) / 미검증 + API 키 미입력
bin/bogoseo collect [--source NAME] [--limit N]
bin/bogoseo collect-status
bin/bogoseo check reports/<name>/draft.md
```

`bin/bogoseo report <subcommand>`, in workflow order:

- `init <name> [--draft FILE] [--evidence FILE...]` — create `reports/<name>/`.
- `status <dir>` — confirmed facts and remaining questions.
- `candidates <dir> [--all]` — list fact candidates (`--all` includes dismissed).
- `add-candidate <dir> <name> <value> --source FILE` — add a candidate manually.
- `confirm <dir> <name> [--value V] [--alias A] [--source S] [--not-in-body]` — confirm a candidate.
- `dismiss <dir> [names] [--rest]` — dismiss named candidates, or all remaining with `--rest`.
- `recommend [--doc-type T] [--limit N]` — recommend reference documents.
- `set-reference <dir> <doc_id>` — set the reference document.
- `set-style <dir> --kind 개조식|서술식 [--ending b1|b2|b3]` — set the report style.
- `outline <dir>` — reference vs draft headings.
- `log <dir> --question Q [--answer A] [--source user|code|research|refined] [--before B --after A --approved yes|no]` — log an interview result.
- `regen-guard <dir> [--proposed FILE] [--mark-checked]` — guard against overwriting user edits.

## Source notes

- 국회입법조사처: uses the `data_go_kr` key (data.go.kr 국회입법조사처_발간물, PublicationService).
- 국회도서관: uses a data.go.kr key (`nanet` item); apply at https://www.data.go.kr/data/15098174/openapi.do.
- 열린국회정보: key from https://open.assembly.go.kr/portal/openapi/openApiActKeyIssPage.do.
- 국회예산정책처: `https://www.nabo.go.kr/api/v1/report.do?key=...`.
- 법제처: `http://www.law.go.kr/DRF/lawSearch.do?OC=...&target=admrul`.
