from app.services.task_reconcile import infer_progress_signal, normalize_task_status


def test_completed_ratio_is_high_confidence_completion():
    signal = infer_progress_signal("已完成第一批上线表单内部测试，73/73+")
    assert signal is not None
    assert signal["status"] == "completed"
    assert signal["progress"] == 100
    assert signal["confidence"] >= 0.9


def test_incomplete_ratio_stays_in_progress():
    signal = infer_progress_signal("第一批表单已完成 73/75")
    assert signal is not None
    assert signal["status"] == "in_progress"
    assert signal["progress"] == 97


def test_planning_and_negative_language_do_not_complete_tasks():
    assert infer_progress_signal("计划完成第一批上线表单内部测试，73/73") is None
    assert infer_progress_signal("预计本周完成数据迁移，100%") is None
    assert infer_progress_signal("数据迁移尚未完成，当前 73/73") is None


def test_explicit_percentage_can_update_progress():
    assert infer_progress_signal("当前迁移进度为 68%") == {
        "status": "in_progress",
        "progress": 68,
        "confidence": 0.96,
        "reason": "识别到明确进度 68%",
    }
    assert infer_progress_signal("测试完成率 100%")["status"] == "completed"


def test_status_aliases_are_normalized():
    assert normalize_task_status("已完成") == "completed"
    assert normalize_task_status("进行中") == "in_progress"
    assert normalize_task_status("未知状态") == "not_started"
