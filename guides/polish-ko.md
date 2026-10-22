# Korean final polish (최종 윤문)

## Purpose and scope
Wording only. Meaning, facts, and structure are fixed. The goal is a draft that reads like a careful human official wrote it: no translationese, no filler, consistent terms, clear logic order.

Adapted from im-not-ai (https://github.com/epoko77-ai/im-not-ai, MIT, epoko77-ai, b37e020); rules rewritten for 개조식/공문서.

## Invariants
Never change these. If a rule would touch one, skip the rule.
- facts.yaml values and aliases, verbatim.
- Numbers (amounts, dates, counts, percentages), including their units.
- Marker symbols: □ ㅇ • - * ※ ☞.
- `[참고 N]` lines and `[확인 필요 N: …]` text, plus the `□ 확인 필요 목록` entries.
- Line count and line order, unless the user approves a change.
- Ending style: 개조식 noun endings (~함, ~임, ~필요, ~추진) stay noun endings; a chosen 서술식 ending (b1/b2/b3) stays as chosen.

## Rules
Each rule: id, one-line rule, bad → good, and the closest im-not-ai category where one exists.

**P1. Organization subject takes 에서.** When the subject is a 단체 or 기관 (부서, 과, 팀, 위원회), use '에서', not '이/가'.
- `인재획득과(시험관리팀)가 … 확정` → `인재획득과(시험관리팀)에서 … 확정`
- Source: 표준국어대사전 '에서2' 「조사」 「7」 "((단체를 나타내는 명사 뒤에 붙어)) 앞말이 주어임을 나타내는 격 조사" (https://stdict.korean.go.kr).
- im-not-ai ref: none.

**P2. Translationese.** Replace ~에 있어서, ~에 대해(서), ~을 통해, ~와 관련하여, ~에 기반하여, 가지고 있다, 되어진다, ~에 의해 생성된 only when the result is natural.
- `선발 과정에 있어서 공정성을 가지고 있다` → `선발 과정이 공정하다`
- `담당자에 의해 생성된 문항` → `담당자가 만든 문항`
- im-not-ai ref: A.

**P3. Double particles.** Collapse ~에서의 / ~으로의 / ~과의 when a plain particle works.
- `현장에서의 대응` → `현장 대응`
- im-not-ai ref: A.

**P4. No comma after a connective ending.** Drop the comma right after -고/-며/-지만/-하여.
- `기준을 정비하고, 절차를 단순화함` → `기준을 정비하고 절차를 단순화함`
- im-not-ai ref: C.

**P5. Repetition.** The same content word twice in one line, or the same idea in two lines of one section: merge or cut.
- `시험 일정을 조정하여 시험 운영을 개선` → `일정을 조정하여 운영을 개선`

**P6. Stock and inflated phrases.** Cut 결론적으로, 종합적으로, 시사하는 바가 크다, 효율적으로, 다양한, 혁신적인, 발판/토대를 마련.
- `다양한 방안을 효율적으로 추진` → `방안 추진`
- im-not-ai ref: D.

**P7. Formal nouns and hedges.** ~하는 것이 가능하다 → ~할 수 있다; ~할 필요가 있다 → ~필요 (in 개조식). Keep policy and legal hedges (~할 수 있음, ~로 예상, ~할 계획); never turn them into flat assertions.
- `접수하는 것이 가능함` → `접수할 수 있음`
- im-not-ai ref: I, G (G-1 hedges are kept).

**P8. Term consistency.** One name per thing across the document (e.g. 출제도구, 문제은행, 문제지). Define a term before its first use, then reuse it unchanged.
- `문제은행` / `문항 DB` / `문제 저장소` → pick one.

**P9. Logic order.** A term or entity must not appear before the section that introduces it. Each ㅇ follows from the previous one, and 현황/문제점 maps one-to-one to 개선 items. If order must change, ask the user (line order is an invariant).

**P10. Excess English glosses.** Gloss a technical term once at most, at first use.
- `API(Application Programming Interface)` repeated → gloss in the first mention only.
- im-not-ai ref: B.

## Not applied (conflicts with 개조식)
- A-13 (noun-chain → sentence): noun chains are the 개조식 form.
- E-2 (ending variety): uniform noun endings are required.
- F-4 (-성/-적/-화 count): such nouns are normal 공문서 vocabulary.
- C-1/C-2/C-9 (reduce bullets and numbering): markers and numbering are the document structure.
- E-1 rhythm rules (sentence length and cadence variation): short parallel lines are intended.

## Procedure
1. Backup: copy `draft.md` to `reports/<name>/revision/draft_before_polish_<YYYYMMDD-HHMM>.md` (never overwrite an existing backup).
2. Read the whole draft once as a first-time reader.
3. Draft a before → after table: line, before, after, rule id (P1…P10).
4. Show it to the user and get approval. If more than 30% of body lines would change, stop and confirm. User-edited lines need explicit approval.
5. Apply only the approved lines.
6. Run `bin/bogoseo check reports/<name>/draft.md`.
7. On a fact or number failure, revert that line from the backup. Never edit facts.yaml to make the check pass.
