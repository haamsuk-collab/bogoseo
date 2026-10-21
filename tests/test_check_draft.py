import subprocess
import textwrap

import pytest
import yaml

from bogoseo import check, markers, style
from conftest import REPO

REF = textwrap.dedent("""\
    사무실 보안 점검 개선 보고

    <보안과 소령 이영희(5678), '26. 3. 2.(월)>

    □ 개요
    ㅇ 점검 주기를 분기 1회로 조정 필요
    - 점검 대상 15개 부서 지정
    * 별도 지침 시행 중
    ※ 부서별 현황
    ☞ 조정 시행 필요
    """)


def make_report(tmp_path, draft, *, facts=None, style_=None, ref=REF, ms="auto"):
    folder = tmp_path / "rep"
    (folder / "reference").mkdir(parents=True, exist_ok=True)
    (folder / "reference" / "ref.md").write_text(ref, encoding="utf-8")
    msd = markers.extract_marker_system(ref) if ms == "auto" else ms
    (folder / "reference" / "marker_system.yaml").write_text(
        yaml.safe_dump(msd, allow_unicode=True), encoding="utf-8")
    (folder / "facts.yaml").write_text(
        yaml.safe_dump(facts or [], allow_unicode=True), encoding="utf-8")
    (folder / "state.yaml").write_text(
        yaml.safe_dump({"style": style_ or {"kind": "개조식", "ending": None}},
                       allow_unicode=True), encoding="utf-8")
    p = folder / "draft.md"
    p.write_text(textwrap.dedent(draft), encoding="utf-8")
    return p


def run(tmp_path, draft, **kw):
    return check.check_draft(make_report(tmp_path, draft, **kw))


GOOD = """\
    보안 점검 체계 개선 보고

    <보안과 소령 박민수(1111), '26. 4. 1.(수)>

    □ 현황
    ㅇ 점검 주기 분기 1회 조정 필요
    - 점검 대상 15개 부서 지정
    * 시행 중인 지침 준용
    ※ 부서별 현황
    ☞ 조정 시행 필요
    """


def test_marker_system_extracted_from_reference():
    ms = markers.extract_marker_system(REF)
    assert ms["levels"] == [["□"], ["ㅇ"], ["-"]]
    assert ms["conclusion"] == "☞" and ms["note"] == "*" and ms["caption"] == "※"
    assert markers.extract_marker_system("그냥 문장입니다.\n또 문장입니다.") is None


def test_parse_lines_kinds():
    ms = markers.extract_marker_system(REF)
    kinds = [ln["kind"] for ln in markers.parse_lines("#### " + REF, ms)]
    assert kinds[:4] == ["title", "blank", "author", "blank"] or kinds[0] in ("title", "heading")
    ks = {ln["kind"] for ln in markers.parse_lines(REF, ms)}
    assert {"title", "author", "heading", "item", "note", "caption", "conclusion"} <= ks


def test_clean_draft_passes(tmp_path):
    res = run(tmp_path, GOOD)
    assert res["ok"], res["failures"]
    saved = yaml.safe_load((tmp_path / "rep" / "check_result.yaml").read_text(encoding="utf-8"))
    assert saved["exit_code"] == 0 and saved["draft_sha256"] and saved["checked_at"]


def test_rule_i_facts(tmp_path):
    facts = [{"name": "인원", "value": "15개 부서", "aliases": ["열다섯 개 부서"], "used_in_body": True},
             {"name": "미사용", "value": "없는값", "used_in_body": False}]
    assert run(tmp_path, GOOD, facts=facts)["ok"]
    facts[0]["value"] = "16개 부서"
    res = run(tmp_path, GOOD, facts=facts)
    assert res["failures"]["i"] and not res["ok"]
    facts[0]["aliases"] = ["15개 부서"]  # alias exact substring is enough
    assert run(tmp_path, GOOD, facts=facts)["ok"]


def test_candidates_never_read(tmp_path):
    p = make_report(tmp_path, GOOD)
    (p.parent / "candidates.yaml").write_text(
        yaml.safe_dump([{"name": "x", "value": "없는값", "used_in_body": True}], allow_unicode=True),
        encoding="utf-8")
    assert check.check_draft(p)["ok"]
    (p.parent / "candidates.yaml").write_bytes(b"\xff\xfe not yaml: [")
    assert check.check_draft(p)["ok"]


@pytest.mark.parametrize("draft,expect_fail", [
    # inline mark 1:1 with list
    ("""\
     보안 점검 보고

     □ 현황
     ㅇ 점검 주기 조정 필요 [확인 필요 1: 주기 근거]

     □ 확인 필요 목록
     - [확인 필요 1] 주기 근거 확인 필요
     """, False),
    # inline without list entry
    ("""\
     보안 점검 보고

     □ 현황
     ㅇ 점검 주기 조정 필요 [확인 필요 1: 주기 근거]
     """, True),
    # list entry without inline mark
    ("""\
     보안 점검 보고

     □ 현황
     ㅇ 점검 주기 조정 필요

     □ 확인 필요 목록
     - [확인 필요 2] 주기 근거 확인 필요
     """, True),
    # duplicate inline N
    ("""\
     보안 점검 보고

     □ 현황
     ㅇ 점검 주기 조정 필요 [확인 필요 1: 가]
     ㅇ 점검 대상 확대 필요 [확인 필요 1: 나]

     □ 확인 필요 목록
     - [확인 필요 1] 가
     """, True),
    # duplicate list entry
    ("""\
     보안 점검 보고

     □ 현황
     ㅇ 점검 주기 조정 필요 [확인 필요 1: 가]

     □ 확인 필요 목록
     - [확인 필요 1] 가
     - [확인 필요 1] 가
     """, True),
])
def test_rule_ii_marks(tmp_path, draft, expect_fail):
    res = run(tmp_path, draft)
    assert bool(res["failures"]["ii"]) == expect_fail, res["failures"]


def test_rule_iii_numbers(tmp_path):
    base = """\
        보안 점검 보고

        □ 현황
        ㅇ 점검 대상 {n}개 부서 지정
        """
    # 15 appears in the reference extract -> supported; 99 is not
    assert run(tmp_path, base.format(n=15))["ok"]
    res = run(tmp_path, base.format(n=99))
    assert any("99" in f for f in res["failures"]["iii"])
    # marked sentence may carry an unsupported number
    marked = """\
        보안 점검 보고

        □ 현황
        ㅇ 점검 대상 99개 부서 지정 [확인 필요 1: 부서 수]

        □ 확인 필요 목록
        - [확인 필요 1] 부서 수 확인 필요
        """
    assert run(tmp_path, marked)["ok"]
    # a fact value supports its numbers
    facts = [{"name": "부서", "value": "99개 부서", "used_in_body": True}]
    assert run(tmp_path, base.format(n=99), facts=facts)["ok"]
    # numbers in [참고 N] table cells count
    table = """\
        보안 점검 보고

        □ 현황
        ㅇ 점검 대상 지정 필요 [참고 1]

        [참고 1] 현황
        | 구분 | 수 |
        | --- | --- |
        | 부서 | 77 |
        """
    res = run(tmp_path, table)
    assert any("77" in f for f in res["failures"]["iii"])


def test_rule_iv_gaejo(tmp_path):
    def draft(line):
        return f"보안 점검 보고\n\n□ 현황\nㅇ {line}\n"
    for ok_line in ["점검 주기 조정 필요", "시행 예정", "보고 요망", "점검 완료", "추진 계획 수립(안)",
                    "점검 주기 조정 필요 [참고 1]"]:
        assert run(tmp_path, draft(ok_line))["ok"], ok_line
    for bad in ["점검 주기를 조정함", "점검 주기를 조정한다.", "점검 주기 조정 필요.", "조정이 필요하다",
                "조정 필요합니다"]:
        assert run(tmp_path, draft(bad))["failures"]["iv"], bad


@pytest.mark.parametrize("ending,good,others", [
    ("b1", ["점검 주기를 분기 1회로 조정함.", "점검 결과 보완이 필요한 사항임.", "예산이 증액됨."],
     ["점검 주기를 분기 1회로 조정한다.", "점검 주기를 분기 1회로 조정합니다."]),
    ("b2", ["점검 주기를 분기 1회로 조정한다.", "점검 결과 보완이 필요하다."],
     ["점검 주기를 분기 1회로 조정함.", "점검 주기를 분기 1회로 조정합니다.",
      "점검 주기를 분기 1회로 조정하였습니다."]),
    ("b3", ["점검 주기를 분기 1회로 조정합니다.", "점검 결과 보완이 필요합니다."],
     ["점검 주기를 분기 1회로 조정함.", "점검 주기를 분기 1회로 조정한다."]),
])
def test_rule_iv_seosulsik(tmp_path, ending, good, others):
    st = {"kind": "서술식", "ending": ending}

    def draft(*lines):
        return "보안 점검 보고\n\n□ 현황\n" + "\n".join(f"ㅇ {ln}" for ln in lines) + "\n"
    assert run(tmp_path, draft(*good), style_=st)["ok"]
    for bad in others:
        assert run(tmp_path, draft(bad), style_=st)["failures"]["iv"], bad
    # missing period fails
    assert run(tmp_path, draft("점검 주기를 분기 1회로 조정하는 방안을 추진함"), style_=st)["failures"]["iv"]
    # mixing fails
    assert run(tmp_path, draft(good[0], others[0]), style_=st)["failures"]["iv"]


def test_short_noun_exception_all_endings(tmp_path):
    for ending in ("b1", "b2", "b3"):
        st = {"kind": "서술식", "ending": ending}
        assert style.judge_ending("점검 결과 보고 예정", st)[0], ending          # <= 20 chars, noun
        assert not style.judge_ending("점검 주기를 분기 1회로 조정하는 계획 수립 예정", st)[0], ending  # > 20


def test_rule_iv_exemptions(tmp_path):
    st = {"kind": "개조식", "ending": None}
    draft = """\
        보안 점검을 한다.

        <보안과 소령 박민수(1111), '26. 4. 1.(수)>

        □ 점검 계획을 수립한다.
        ㅇ 점검 주기 조정 필요
        - 시행시기 : 4월 중 시행한다.
        * 별도 지침을 시행한다.
        ※ 표를 참고한다.
        | 구분 | 내용 |
        | --- | --- |
        | 점검 | 점검한다. |
        ☞ 조정 시행 필요

        □ 확인 필요 목록
        - [확인 필요 1] 내용을 확인한다.
        """
    res = run(tmp_path, draft, style_=st, facts=[])
    assert not res["failures"]["iv"], res["failures"]
    # but a conclusion line is in scope
    res = run(tmp_path, draft.replace("☞ 조정 시행 필요", "☞ 조정을 시행한다."), style_=st)
    assert res["failures"]["iv"]


def test_driven_by_reference_marker_system(tmp_path):
    ref = "보고 제목\n\n○ 대분류\n· 세부 사항 필요\n· 추가 사항 필요\n○ 두번째 대분류\n· 또 다른 사항 필요\n"
    ms = markers.extract_marker_system(ref)
    # one-level circle + dot hierarchy: the circle group is top level here
    assert ms["levels"][0] == ["○"]
    ok = "보고 제목\n\n○ 대분류\n· 세부 사항 필요\n"
    bad = "보고 제목\n\n○ 대분류\n· 세부 사항을 시행한다.\n"
    assert run(tmp_path, ok, ref=ref, ms=ms)["ok"]
    assert run(tmp_path, bad, ref=ref, ms=ms)["failures"]["iv"]


def test_cli_check_exit_code(tmp_path):
    p = make_report(tmp_path, GOOD)
    r = subprocess.run([str(REPO / "bin" / "bogoseo"), "check", str(p)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    p.write_text(GOOD.replace("조정 필요", "조정한다."), encoding="utf-8")
    r = subprocess.run([str(REPO / "bin" / "bogoseo"), "check", str(p)], capture_output=True, text=True)
    assert r.returncode == 1 and "FAIL" in r.stdout
