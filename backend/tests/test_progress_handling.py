from app.services.authority import question_scope
from app.services.progress import parse_progress_percent


def test_latest_progress_is_not_treated_as_schedule():
    assert question_scope("项目最新进度") == "progress"
    assert question_scope("截至8月底项目进展") == "progress"
    assert question_scope("合同工期和里程碑") == "schedule"


def test_progress_parser_accepts_completed_over_total_values():
    assert parse_progress_percent("73/73+") == 100
    assert parse_progress_percent("50%", "73/73+") == 50
