from __future__ import annotations

import re
import threading
import uuid
from pathlib import Path
from typing import Any, BinaryIO

from app.database import DEFAULT_MONITOR_ROOT, get_connection, get_setting, now_iso, row_to_dict
from app.services.scanner import SUPPORTED_EXTENSIONS, index_document
from app.services.wiki import queue_wiki_refresh


MAX_UPLOAD_BYTES = 100 * 1024 * 1024
_upload_lock = threading.Lock()
_upload_jobs: dict[str, dict[str, Any]] = {}


def _set_job(job_id: str, **patch: Any) -> None:
    with _upload_lock:
        if job_id in _upload_jobs:
            _upload_jobs[job_id].update(patch)


def upload_targets() -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        default_dir = Path(get_setting(conn, "default_monitor_dir", str(DEFAULT_MONITOR_ROOT)))
        monitor_types = get_setting(conn, "monitor_types", {})
    finally:
        conn.close()

    targets: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    if isinstance(monitor_types, dict):
        for key, config in monitor_types.items():
            if not isinstance(config, dict) or not config.get("enabled", True):
                continue
            label = str(config.get("label") or key)
            for index, directory in enumerate(config.get("directories") or []):
                if not directory:
                    continue
                path = str(Path(directory).expanduser().resolve())
                identity = (key, path.lower())
                if identity in seen:
                    continue
                seen.add(identity)
                targets.append({
                    "monitorType": key,
                    "directoryIndex": index,
                    "label": label,
                    "directory": path,
                })
    if not targets:
        targets.append({
            "monitorType": "other",
            "directoryIndex": 0,
            "label": "其他项目资料",
            "directory": str((default_dir / "项目资料").resolve()),
        })
    return targets


def _resolve_target(monitor_type: str, directory_index: int) -> tuple[Path, str]:
    matches = [item for item in upload_targets() if item["monitorType"] == monitor_type]
    for item in matches:
        if item["directoryIndex"] == directory_index:
            return Path(item["directory"]), monitor_type
    raise ValueError("上传目录不在当前监控目录配置中")


def _safe_filename(filename: str) -> str:
    name = Path(filename or "").name.strip().rstrip(". ")
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    if not name or name in {".", ".."}:
        raise ValueError("文件名无效")
    suffix = Path(name).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"暂不支持 {suffix or '无扩展名'} 文件")
    if len(name) > 180:
        stem = Path(name).stem[: max(1, 180 - len(suffix))]
        name = f"{stem}{suffix}"
    return name


def _unique_destination(directory: Path, filename: str) -> Path:
    destination = directory / filename
    if not destination.exists():
        return destination
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    for index in range(1, 1000):
        candidate = directory / f"{stem} ({index}){suffix}"
        if not candidate.exists():
            return candidate
    raise ValueError("同名文件过多，请调整文件名后重试")


def _write_upload(stream: BinaryIO, destination: Path) -> int:
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.uploading")
    total = 0
    try:
        with temporary.open("xb") as output:
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    raise ValueError("单个文件不能超过 100 MB")
                output.write(chunk)
        temporary.replace(destination)
        return total
    finally:
        if temporary.exists():
            temporary.unlink(missing_ok=True)


def _process_upload(job_id: str) -> None:
    with _upload_lock:
        job = dict(_upload_jobs[job_id])
    try:
        _set_job(job_id, status="processing", stage="正在抽取并建立知识索引", progress=35)
        result = index_document(Path(job["path"]), job["monitorType"], force=True)
        if result.get("status") == "error":
            raise RuntimeError(result.get("error") or "文件抽取失败")

        conn = get_connection()
        try:
            document = row_to_dict(conn.execute("SELECT * FROM documents WHERE path = ?", (job["path"],)).fetchone())
            if not document:
                raise RuntimeError("文件已保存，但未生成资料台账记录")
            if job.get("deliverableId"):
                existing = conn.execute(
                    "SELECT id FROM deliverables WHERE id = ? AND COALESCE(is_archived, 0) = 0",
                    (job["deliverableId"],),
                ).fetchone()
                if not existing:
                    raise RuntimeError("交付物记录不存在或已归档")
                conn.execute(
                    "UPDATE deliverables SET document_id = ?, updated_at = ? WHERE id = ?",
                    (document["id"], now_iso(), job["deliverableId"]),
                )
                conn.commit()
        finally:
            conn.close()

        ocr_pending = document.get("ocr_status") in {"pending", "processing"}
        wiki_result = None
        if not ocr_pending:
            _set_job(job_id, stage="正在排队更新项目 Wiki", progress=80)
            wiki_result = queue_wiki_refresh(
                document.get("doc_category") or job["monitorType"],
                trigger=f"upload:{job_id}",
                include_deliverables=bool(job.get("deliverableId")),
            )
        _set_job(
            job_id,
            status="completed",
            stage="已完成，等待 OCR 后更新 Wiki" if ocr_pending else "已完成，Wiki 更新已排队",
            progress=100,
            documentId=document["id"],
            wikiRefresh=wiki_result,
            wikiDeferredForOcr=ocr_pending,
            finishedAt=now_iso(),
        )
    except Exception as exc:
        _set_job(job_id, status="failed", stage="处理失败", error=str(exc), finishedAt=now_iso())


def save_upload(
    stream: BinaryIO,
    filename: str,
    monitor_type: str,
    directory_index: int = 0,
    deliverable_id: int | None = None,
) -> dict[str, Any]:
    safe_name = _safe_filename(filename)
    directory, category = _resolve_target(monitor_type, directory_index)
    directory.mkdir(parents=True, exist_ok=True)
    destination = _unique_destination(directory, safe_name)
    size = _write_upload(stream, destination)
    job_id = uuid.uuid4().hex
    job = {
        "id": job_id,
        "fileName": destination.name,
        "path": str(destination.resolve()),
        "monitorType": category,
        "directoryIndex": directory_index,
        "deliverableId": deliverable_id,
        "sizeBytes": size,
        "status": "queued",
        "stage": "等待后台处理",
        "progress": 10,
        "error": "",
        "documentId": None,
        "wikiRefresh": None,
        "wikiDeferredForOcr": False,
        "startedAt": now_iso(),
        "finishedAt": "",
    }
    with _upload_lock:
        _upload_jobs[job_id] = job
        if len(_upload_jobs) > 50:
            for old_id in list(_upload_jobs)[:-50]:
                _upload_jobs.pop(old_id, None)
    threading.Thread(target=_process_upload, args=(job_id,), daemon=True).start()
    return {"ok": True, "job": dict(job)}


def upload_status() -> dict[str, Any]:
    with _upload_lock:
        jobs = [dict(item) for item in reversed(list(_upload_jobs.values()))]
    return {
        "running": any(item["status"] in {"queued", "processing"} for item in jobs),
        "queued": sum(item["status"] == "queued" for item in jobs),
        "processing": sum(item["status"] == "processing" for item in jobs),
        "completed": sum(item["status"] == "completed" for item in jobs),
        "failed": sum(item["status"] == "failed" for item in jobs),
        "jobs": jobs[:20],
    }
