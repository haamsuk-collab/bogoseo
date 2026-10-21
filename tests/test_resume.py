import yaml

from bogoseo import report as R


def _folder(tmp_path):
    ev = tmp_path / "ev.md"
    ev.write_text("- 선발 인원: 30명\n- 지원 자격: 하사 3년\n", encoding="utf-8")
    d = tmp_path / "draft.md"
    d.write_text("□ 개요\n ㅇ 현재 20명 운영 [참고 1]\n ㅇ 기대효과 (작성 필요)\n", encoding="utf-8")
    f = tmp_path / "reports" / "r1"
    R.init_report("r1", f, draft=d, evidence=[ev])
    return f


def test_resume_shows_facts_and_questions(tmp_path):
    f = _folder(tmp_path)
    R.confirm_fact(f, "선발 인원")
    R.set_style(f, "개조식")
    # a "new process": only the folder path is known
    s = R.resume_summary(f)
    assert {"name": "선발 인원", "value": "30명"} in s["confirmed_facts"]
    assert any("지원 자격" in q for q in s["remaining_questions"])
    assert not any("선발 인원 =" in q for q in s["remaining_questions"])
    assert any("작성 필요" in q for q in s["remaining_questions"])
    facts = yaml.safe_load((f / "facts.yaml").read_text(encoding="utf-8"))
    assert [x["name"] for x in facts] == ["선발 인원"]  # candidates never leak in
    assert "[참고 1]" in (f / "draft.md").read_text(encoding="utf-8")


def test_latest_answer_wins_and_approval_logged(tmp_path):
    f = _folder(tmp_path)
    R.confirm_fact(f, "선발 인원", value="35명")
    assert R.load_facts(f)[0]["value"] == "35명"
    R.log_answer(f, "q", "a1"); R.log_answer(f, "q", "a2")
    e = R.record_approval(f, "q", "전", "후", True)
    assert e["answer"] == "a2" and e["approvals"] == [{"before": "전", "after": "후", "approved": True}]


class FakeMCP:
    def __init__(self, fail=False, bad_session=False):
        self.calls, self.fail, self.bad_session = [], fail, bad_session

    def __call__(self, args):
        self.calls.append(args)
        if self.fail:
            raise RuntimeError("mcp down")
        if "session_id" in args and self.bad_session:
            return {"error": "unknown session"}
        return {"session_id": "S-new"}


def test_missing_session_starts_new_seeded_interview(tmp_path):
    f = _folder(tmp_path)
    R.confirm_fact(f, "선발 인원")
    st = R.load_state(f)
    assert st["ouroboros_session_id"] is None
    mcp = FakeMCP()
    assert R.interview_backend(st, mcp, f) == "mcp"
    seed = mcp.calls[0]["initial_context"]
    assert "선발 인원: 30명" in seed and "지원 자격" in seed
    assert R.load_state(f)["ouroboros_session_id"] == "S-new"


def test_unreadable_session_reseeds(tmp_path):
    f = _folder(tmp_path)
    st = R.load_state(f); st["ouroboros_session_id"] = "gone"
    mcp = FakeMCP(bad_session=True)
    assert R.interview_backend(st, mcp, f) == "mcp"
    assert "session_id" in mcp.calls[0] and "initial_context" in mcp.calls[1]
    assert st["ouroboros_session_id"] == "S-new"


def test_failing_mcp_falls_back_to_ask(tmp_path):
    f = _folder(tmp_path)
    st = R.load_state(f); st["ouroboros_session_id"] = "x"
    assert R.interview_backend(st, FakeMCP(fail=True), f) == "ask"
    s = R.load_state(f)
    assert s["interview_backend"] == "ask" and s["ouroboros_session_id"] is None
    # workflow continues
    R.log_answer(f, "문체", "개조식")
    assert R.resume_summary(f)["remaining_questions"]


def test_regen_guard_keeps_user_edits(tmp_path):
    f = _folder(tmp_path)
    R.mark_checked(f)
    assert not R.regen_guard(f)["user_edited"]
    (f / "draft.md").write_text("사용자 수정\n", encoding="utf-8")
    prop = tmp_path / "new.md"; prop.write_text("재생성\n", encoding="utf-8")
    g = R.regen_guard(f, prop)
    assert g["user_edited"] and g["keep_user_text"] and "+재생성" in g["diff"]


def _two_values_folder(tmp_path):
    a = tmp_path / "a.md"; a.write_text("- 선발 인원: 30명\n", encoding="utf-8")
    b = tmp_path / "b.md"; b.write_text("- 선발 인원: 35명\n", encoding="utf-8")
    d = tmp_path / "draft.md"; d.write_text("□ 개요\n", encoding="utf-8")
    f = tmp_path / "reports" / "r2"
    R.init_report("r2", f, draft=d, evidence=[a, b])
    return f


def test_confirm_uses_matching_value_candidate(tmp_path):
    f = _two_values_folder(tmp_path)
    fact = R.confirm_fact(f, "선발 인원", value="35명")
    assert fact["evidence_source"] == "b.md"
    st = {c["value"]: c["status"] for c in R._load(f / "candidates.yaml", [])}
    assert st == {"35명": "confirmed", "30명": "dismissed"}


def test_later_candidate_with_other_value_stays_pending(tmp_path):
    f = _folder(tmp_path)
    R.confirm_fact(f, "선발 인원", value="30명")
    R.add_candidate(f, "선발 인원", "35명", "ev.md")
    assert "후보 확인: 선발 인원 = 35명" in R.remaining_questions(f)


def test_prefill_keeps_candidates_of_unreadable_evidence(tmp_path):
    f = _folder(tmp_path)
    (tmp_path / "ev.md").unlink()
    R.prefill_candidates(f)
    vals = {(c["name"], c["value"]) for c in R._load(f / "candidates.yaml", [])}
    assert ("선발 인원", "30명") in vals


def test_headings_whole_section_names_only():
    assert R._headings("결론적으로 현행 방식을 유지한다.\n") == []
    assert R._headings("개 선【안】\n향후추진\n") == ["개 선【안】", "향후추진"]
