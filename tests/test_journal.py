import pytest

from coach_corrida.journal import Journal


def test_note_needs_explicit_confirmation_and_retry_is_idempotent(history):
    journal = Journal(history.store)
    draft = journal.propose_note("2026-11-23", "Pernas cansadas", rpe=4)
    assert history.context()["recent_journal"] == []
    with pytest.raises(ValueError):
        journal.confirm(draft["draft_id"], "sim")
    assert history.context()["recent_journal"] == []
    first = journal.confirm(draft["draft_id"], "APROVAR")
    second = journal.confirm(draft["draft_id"], "APROVAR")
    assert first["id"] == second["id"]
    assert len(history.context()["recent_journal"]) == 1


def test_plan_approval_preserves_versions(history):
    journal = Journal(history.store)
    sessions = [{"date": "2026-11-24", "sport": "run", "minutes": 30, "instruction": "Leve"}]
    first = journal.propose_plan("2026-11-23", sessions, "Retomada")
    assert history.context()["approved_plans"] == []
    journal.confirm(first["draft_id"], "APROVAR")
    sessions[0]["minutes"] = 25
    second = journal.propose_plan("2026-11-23", sessions, "Ajuste conforme relato")
    journal.confirm(second["draft_id"], "APROVAR")
    plans = history.context()["approved_plans"]
    assert [p["version"] for p in plans] == [2, 1]
    assert plans[1]["data"]["sessions"][0]["minutes"] == 30


def test_plan_outside_week_and_invalid_rpe_rejected(history):
    journal = Journal(history.store)
    with pytest.raises(ValueError):
        journal.propose_plan("2026-11-23", [{"date": "2026-12-01", "sport": "run", "minutes": 30, "instruction": "Leve"}], "Retomada")
    with pytest.raises(ValueError):
        journal.propose_note("2026-11-23", "Teste", rpe=11)
