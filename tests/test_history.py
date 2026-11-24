import json
import shutil

import fitdecode
import pytest

from coach_corrida.history import History
from coach_corrida.storage import Store
from conftest import make_workbook, row


def test_workbook_missing_hr_and_zero_elevation_are_distinct(history, workbook_file):
    report = history.import_file(workbook_file)
    assert report["new_activities"] == 3
    first = history.detail(report["activity_ids"][0])
    assert first["average_hr"] is None
    assert first["facts"]["elevation_gain_m"] == 0
    assert first["pace_s_per_km"] == pytest.approx(294.7)
    assert first["time_basis"] == "local_unverified"


def test_same_file_renamed_is_not_imported_twice(history, workbook_file, tmp_path):
    history.import_file(workbook_file)
    copy = tmp_path / "another-name.xlsx"
    shutil.copyfile(workbook_file, copy)
    assert history.import_file(copy)["status"] == "already_imported"
    assert history.quality()["activities"] == 3


def test_fit_enriches_existing_workout_and_preserves_field_sources(history, workbook_file, real_fit):
    history.import_file(workbook_file)
    report = history.import_file(real_fit)
    assert report["new_activities"] == 0
    assert report["enriched_activities"] == 1
    assert history.quality()["activities"] == 3
    detail = history.detail(report["activity_ids"][0])
    assert detail["distance_m"] == 10002
    assert detail["average_hr"] == 156
    assert detail["sample_sources"][0]["n"] == 3606
    assert detail["sample_sources"][0]["hr"] == 3559
    assert detail["field_sources"]["average_hr"]["kind"] == "fit"
    assert detail["field_sources"]["name"]["kind"] == "workbook"
    assert len(detail["laps"]) == 11


def test_import_order_does_not_replace_fit_with_rounded_workbook(history, workbook_file, real_fit):
    fit = history.import_file(real_fit)
    history.import_file(workbook_file)
    detail = history.detail(fit["activity_ids"][0])
    assert detail["distance_m"] == 10002
    assert detail["average_hr"] == 156
    assert history.quality()["activities"] == 3


def test_corrupt_fit_is_rejected_without_database_rows(history, real_fit, tmp_path):
    damaged = bytearray(real_fit.read_bytes())
    damaged[-1] ^= 1
    path = tmp_path / "bad.fit"
    path.write_bytes(damaged)
    with pytest.raises(fitdecode.FitCRCError):
        history.import_file(path)
    assert history.quality()["sources"] == []
    assert history.quality()["activities"] == 0


def test_raw_daily_hr_is_never_linked_to_workout(history, workbook_file, tmp_path):
    history.import_file(workbook_file)
    path = tmp_path / "HEARTRATE_AUTO.csv"
    path.write_text("date,time,heartRate\n2026-05-29,12:27,180\n", encoding="utf-8")
    report = history.import_file(path)
    assert report["raw_rows"] == 1
    assert history.detail(1)["average_hr"] is None
    assert history.detail(1)["sample_sources"] == []


def test_malformed_csv_preserves_extras_and_missing_columns(history, tmp_path):
    path = tmp_path / "sleep.csv"
    path.write_text('date,naps\n2026-01-01,a,b\n2026-01-02\n', encoding="utf-8")
    report = history.import_file(path)
    assert report["raw_rows"] == report["malformed_raw_rows"] == 2
    with history.store.connect() as db:
        rows = [json.loads(r[0]) for r in db.execute("SELECT payload_json FROM source_rows ORDER BY sequence")]
    assert rows[0]["__extra_columns_unparsed"] == ["b"]
    assert rows[1]["naps"] is None


def test_different_start_times_on_same_day_are_not_merged(history, tmp_path):
    file = make_workbook(tmp_path / "double.xlsx", [row("2026-11-20", 5, 1800, hour=7), row("2026-11-20", 5, 1800, hour=18)])
    assert history.import_file(file)["new_activities"] == 2


def test_invalid_row_rolls_back_entire_source(history, tmp_path):
    file = make_workbook(tmp_path / "invalid.xlsx", [row("2026-11-20", 5, 1800), row("2026-11-21", -1, 1800)])
    with pytest.raises(ValueError):
        history.import_file(file)
    assert history.quality()["activities"] == 0
    assert history.quality()["sources"] == []


def test_original_copy_verified_and_backup_reopens(history, workbook_file, tmp_path):
    history.import_file(workbook_file)
    backup = tmp_path / "backup"
    history.store.backup(backup)
    restored = History(Store(backup))
    assert restored.quality()["activities"] == 3
    assert list((backup / "originals").rglob("*.xlsx"))[0].read_bytes() == workbook_file.read_bytes()
    with pytest.raises(ValueError):
        history.store.backup(backup)


def test_personal_evidence_matches_without_creating_new_workouts(history):
    from conftest import ROOT
    workbook = ROOT / "corridas_2026.xlsx"
    evidence = ROOT / "outputs/dados_consolidados_2026-11-23/dados_consolidados.json"
    if not workbook.exists() or not evidence.exists():
        pytest.skip("Fontes pessoais não distribuídas.")
    history.import_file(workbook)
    report = history.import_file(evidence)
    assert report["new_activities"] == 0
    assert report["enriched_activities"] == 6
    assert history.quality()["activities"] == 136
    half = history.list_activities("2026-06-28", "2026-06-28")["items"][0]
    assert half["average_hr"] == 167
    assert half["field_sources"]["average_hr"]["kind"] == "confirmed_evidence"
    tempo = history.list_activities("2026-07-28", "2026-07-28")["items"][0]
    assert tempo["rpe"] == 6
    assert len(history.detail(tempo["id"])["laps"]) == 9
