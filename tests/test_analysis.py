import pytest

from coach_corrida.analysis import Analysis


def test_period_reconciles_distances_and_includes_end_date(history, workbook_file):
    history.import_file(workbook_file)
    stats = Analysis(history).period("2026-01-01", "2026-11-20")["by_sport"]["run"]
    assert stats["records"] == 3
    assert stats["distance_km"] == 28
    assert stats["moving_time_s"] == 2947+3605+2757
    assert stats["with_average_hr"] == 2
    assert stats["pace_s_per_km"] == pytest.approx((2947+3605+2757)/28)


def test_comparison_never_calls_pace_delta_fitness_loss(history, workbook_file):
    ids = history.import_file(workbook_file)["activity_ids"]
    result = Analysis(history).compare(ids[0], ids[1])
    assert result["delta_second_minus_first"]["pace_s_per_km"] == pytest.approx(65.8)
    assert result["conditioning_loss_percent"] is None
    assert any("FC média não disponível" in s for s in result["limitations"])


def test_candidates_keep_distance_and_duration_limits(history, workbook_file):
    ids = history.import_file(workbook_file)["activity_ids"]
    result = Analysis(history).candidates(ids[0])
    assert [x["id"] for x in result["candidates"]] == [ids[1]]


def test_week_missing_previous_is_not_zero_filled(history, workbook_file):
    history.import_file(workbook_file)
    result = Analysis(history).weekly("2026-11-16")
    assert result["changes"]["run"]["moving_time_s"]["previous"] is None
    assert result["changes"]["run"]["moving_time_s"]["change_percent"] is None
    with pytest.raises(ValueError):
        Analysis(history).weekly("2026-11-17")


def test_segment_requires_raw_samples(history, workbook_file):
    identifier = history.import_file(workbook_file)["activity_ids"][0]
    assert Analysis(history).segment(identifier, 0, 2)["available"] is False


def test_fit_segment_and_pagination_cover_original_records(history, real_fit):
    identifier = history.import_file(real_fit)["activity_ids"][0]
    segment = Analysis(history).segment(identifier, 2, 4)
    assert segment["available"] is True
    assert segment["records"] > 100
    assert 0 < segment["mean_sample_hr_bpm"] < 255
    assert segment["actual_distance_bounds_m"][0] >= 2000
    assert segment["actual_distance_bounds_m"][1] <= 4000
    assert Analysis(history).segment(identifier, 8, 11)["available"] is False
    seen = []
    offset = 0
    while True:
        page = history.samples(identifier, offset, 1000)
        seen.extend(x["sequence"] for x in page["items"])
        offset = page["next_offset"]
        if offset is None:
            break
    assert seen == list(range(3606))


def test_bad_ranges_fail_without_silent_empty_summary(history):
    with pytest.raises(ValueError):
        Analysis(history).period("2026-12-01", "2026-11-01")
    with pytest.raises(ValueError):
        history.list_activities(limit=10000)


def test_bike_has_speed_power_and_is_not_compared_as_running(history, workbook_file, cycling_fit):
    run_id = history.import_file(workbook_file)["activity_ids"][0]
    bike_id = history.import_file(cycling_fit)["activity_ids"][0]
    bike = history.detail(bike_id)
    assert bike["sport"] == "ride"
    assert bike["distance_m"] == 10000
    assert bike["average_speed_kmh"] == pytest.approx(20)
    assert bike["pace_s_per_km"] is None
    assert bike["average_power_w"] == 180
    assert bike["sample_sources"][0]["n"] == 3
    with pytest.raises(ValueError):
        Analysis(history).compare(run_id, bike_id)
