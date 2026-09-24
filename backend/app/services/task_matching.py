from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any, Iterable

from app.services.data_quality import normalize_title

ACTION_GROUPS = {
    "测试": ("测试", "验证", "测评", "联测", "回归", "uat"),
    "迁移": ("迁移", "导入", "转换", "同步"),
    "部署": ("部署", "安装", "上线", "发布", "切换"),
    "编制": ("编制", "输出", "形成", "制作", "整理", "撰写"),
    "确认": ("确认", "评审", "审批", "验收"),
    "对接": ("对接", "联调", "集成", "协调"),
    "开发": ("开发", "配置", "改造", "优化", "实现"),
    "梳理": ("梳理", "调研", "盘点", "分析"),
}
NOISE = ("已完成", "完成", "正在", "进行中", "下周", "本周", "计划", "预计", "持续", "推进", "工作", "任务")


def task_scope(text: dict[str, Any] | str) -> dict[str, tuple[str, ...]]:
    if isinstance(text, dict):
        text = f"{text.get('title') or ''} {text.get('description') or ''}"
    value = (text or "").lower()
    batches = sorted(set(re.findall(r"第[一二三四五六七八九十\d]+(?:批|期|阶段)", value)))
    phases = sorted(word for word in ("内部测试", "联调", "uat", "回归", "试运行", "验收测试", "生产环境", "测试环境") if word in value)
    return {"batches": tuple(batches), "phases": tuple(phases)}


def compatible_scope(left: dict[str, Any] | str, right: dict[str, Any] | str) -> bool:
    def text(value):
        return value if isinstance(value, str) else f"{value.get('title') or ''} {value.get('description') or ''}"
    a, b = task_scope(text(left)), task_scope(text(right))
    # Missing qualifiers are ambiguous, not equal to a specific batch/phase.
    return a == b


def action_signature(text: str) -> tuple[str, ...]:
    value = (text or "").lower()
    return tuple(sorted(name for name, words in ACTION_GROUPS.items() if any(word in value for word in words)))


def object_key(text: str) -> str:
    value = normalize_title(text or "")
    for word in NOISE:
        value = value.replace(normalize_title(word), "")
    for words in ACTION_GROUPS.values():
        for word in words:
            value = value.replace(normalize_title(word), "")
    return value


def _ngrams(value: str, size: int = 2) -> set[str]:
    return {value[i:i + size] for i in range(max(0, len(value) - size + 1))}


def task_similarity(left: dict[str, Any] | str, right: dict[str, Any] | str) -> float:
    def text(value):
        return value if isinstance(value, str) else str(value.get("title") or "")
    a, b = normalize_title(text(left)), normalize_title(text(right))
    if not a or not b:
        return 0.0
    seq = SequenceMatcher(None, a, b).ratio()
    aa, bb = _ngrams(a), _ngrams(b)
    grams = len(aa & bb) / max(1, len(aa | bb))
    ao, bo = object_key(a), object_key(b)
    objects = SequenceMatcher(None, ao, bo).ratio() if ao and bo else 0.0
    acts_a, acts_b = set(action_signature(a)), set(action_signature(b))
    actions = len(acts_a & acts_b) / max(1, len(acts_a | acts_b)) if acts_a or acts_b else 0.5
    score = max(seq, seq * .50 + grams * .20 + objects * .20 + actions * .10)
    if not compatible_scope(left, right):
        score = min(score, .79)
    return round(score, 4)


def ranked_matches(candidate: dict[str, Any], officials: Iterable[dict[str, Any]], floor: float = .65) -> list[dict[str, Any]]:
    result = []
    for target in officials:
        score = task_similarity(candidate, target)
        if score < floor:
            continue
        scope_ok = compatible_scope(candidate, target)
        result.append({**target, "similarity": score, "scopeCompatible": scope_ok,
                       "exact": normalize_title(candidate.get("title") or "") == normalize_title(target.get("title") or "") and scope_ok})
    return sorted(result, key=lambda item: (-item["similarity"], int(item.get("id") or 0)))


def safe_match(candidate: dict[str, Any], officials: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    matches = ranked_matches(candidate, officials)
    if not matches or matches[0]["similarity"] < .90 or not matches[0]["scopeCompatible"]:
        return None
    if len(matches) > 1 and matches[1]["similarity"] >= .80:
        return None
    return matches[0]
