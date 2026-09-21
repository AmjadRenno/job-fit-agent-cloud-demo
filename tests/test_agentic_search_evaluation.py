from evaluation.agentic_search_runner import evaluate_offline, load_cases, save_result


def test_dataset_is_synthetic_complete_and_has_ambiguous_exclusion():
    cases = load_cases()
    assert 10 <= len(cases) <= 15 and len({case["id"] for case in cases}) == len(cases)
    assert any(case["expected_deterministic"] == "HUMAN_REVIEW" for case in cases)
    assert all("Amjad" not in str(case) for case in cases)


def test_offline_metrics_are_reproducible_and_reject_hallucinations():
    cases = load_cases()
    def deterministic(query): return [{"id": "exact", "title": ".NET Backend Developer"}] if query == ".NET" else []
    def fallback(query): return {"items": [{"id": "real", "title": ".NET Backend Developer"}], "job_ids": ["invented"], "trace": {"tool_call_count": 5}}
    first = evaluate_offline(cases, deterministic, fallback); second = evaluate_offline(cases, deterministic, fallback)
    assert first == second and first["live_quality"] == "NOT_MEASURED"
    assert first["ambiguous_excluded"] == 1 and first["persisted_result_grounding"]["hallucinated_id_rate"] > 0 and first["tool_call_compliance"]["violations"] > 0


def test_result_artifacts_are_append_safe(tmp_path, monkeypatch):
    import evaluation.agentic_search_runner as runner
    monkeypatch.setattr(runner, "RESULTS_DIR", tmp_path)
    assert save_result({"status": "NOT_MEASURED"}) != save_result({"status": "NOT_MEASURED"})
