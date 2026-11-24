"""口径差异核查、归因交班路由。

与通用资源路由分开注册，避免 /api/{module}/{entry_id} 把
handovers / verifications 这类固定路径当成 entry_id 解析。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.services import calibration

router = APIRouter(prefix="/api/calibration", tags=["口径核查与归因交班"])


class SamplePayload(BaseModel):
    metric_id: int
    version: int
    business_date: str
    value: float
    sample_no: str | None = None
    verified: bool = True
    snapshot_time: str | None = None


class VerificationPayload(BaseModel):
    metric_id: int
    version_a: int
    version_b: int
    start_date: str
    end_date: str
    title: str = ""
    requested_by: str = ""


class HandoverPayload(BaseModel):
    scope: str = "week"
    business_date: str | None = None
    from_shift: str = ""
    to_shift: str = ""
    note: str = ""


class ClaimPayload(BaseModel):
    claimed_by: str = ""
    shift: str = ""


def _validate_versions(version_a: int, version_b: int) -> None:
    if version_a == version_b:
        raise HTTPException(status_code=400, detail="两个口径版本不能相同")


def _validate_range(start_date: str, end_date: str) -> None:
    if start_date > end_date:
        raise HTTPException(status_code=400, detail="开始日期不能晚于结束日期")


@router.get("/metrics")
def calibration_metrics() -> dict[str, Any]:
    """可作为核查对象的指标及已有口径版本。"""
    from app.database import db

    metrics = db.fetchall("SELECT * FROM metrics ORDER BY id ASC")
    result = []
    for metric in metrics:
        versions = db.fetchall(
            "SELECT calibration_version AS version, COUNT(*) AS sample_count, "
            "MIN(business_date) AS first_date, MAX(business_date) AS last_date "
            "FROM metric_values WHERE metric_id=? GROUP BY calibration_version "
            "ORDER BY calibration_version ASC",
            [metric["id"]],
        )
        result.append({
            "metric_id": metric["id"],
            "metric_name": metric["metric_name"],
            "metric_type": metric["metric_type"],
            "unit": metric.get("unit", ""),
            "versions": versions,
        })
    return {"items": result, "total": len(result)}


@router.get("/diff")
def calibration_diff(
    metric_id: int = Query(...),
    version_a: int = Query(...),
    version_b: int = Query(...),
    start_date: str = Query(...),
    end_date: str = Query(...),
) -> dict[str, Any]:
    """口径差异视图：按业务日期逐日配对两版已验证采样。"""
    _validate_versions(version_a, version_b)
    _validate_range(start_date, end_date)
    result = calibration.build_calibration_diff(
        metric_id, version_a, version_b, start_date, end_date
    )
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result.get("message", "指标不存在"))
    return result


@router.get("/compare")
def calibration_compare(
    metric_id: int = Query(...),
    version_a: int = Query(...),
    version_b: int = Query(...),
) -> dict[str, Any]:
    """指标存储口径对比：保留两版均值，并附上按业务日期的配对明细。"""
    from app.services import service_for

    _validate_versions(version_a, version_b)
    service = service_for("metric_store")
    return service.compare_calibrations(metric_id, version_a, version_b)


@router.get("/samples")
def calibration_samples(
    metric_id: int | None = Query(default=None),
    version: int | None = Query(default=None),
    start_date: str | None = Query(default=None),
    end_date: str | None = Query(default=None),
    limit: int = Query(default=500, ge=1, le=5000),
) -> dict[str, Any]:
    items = calibration.list_samples(
        metric_id, version, start_date, end_date, limit
    )
    return {"items": items, "total": len(items)}


@router.post("/samples")
def create_calibration_sample(payload: SamplePayload) -> dict[str, Any]:
    result = calibration.create_sample(
        payload.metric_id, payload.version, payload.business_date, payload.value,
        sample_no=payload.sample_no, verified=payload.verified,
        snapshot_time=payload.snapshot_time,
    )
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result.get("message"))
    return result


@router.get("/verifications")
def list_verifications(limit: int = Query(default=100, ge=1, le=500)) -> dict[str, Any]:
    items = calibration.list_verifications(limit)
    return {"items": items, "total": len(items)}


@router.post("/verifications")
def create_verification(payload: VerificationPayload) -> dict[str, Any]:
    _validate_versions(payload.version_a, payload.version_b)
    _validate_range(payload.start_date, payload.end_date)
    result = calibration.create_verification(
        payload.metric_id, payload.version_a, payload.version_b,
        payload.start_date, payload.end_date,
        title=payload.title, requested_by=payload.requested_by,
    )
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result.get("message"))
    return result


@router.get("/verifications/{verification_id}")
def get_verification(verification_id: int) -> dict[str, Any]:
    result = calibration.verification_detail(verification_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"核查记录 {verification_id} 不存在")
    return result


@router.post("/verifications/{verification_id}/refresh")
def refresh_verification(verification_id: int) -> dict[str, Any]:
    result = calibration.refresh_verification(verification_id)
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result.get("message"))
    return result


@router.post("/verifications/{verification_id}/handovers")
def create_handover(verification_id: int, payload: HandoverPayload) -> dict[str, Any]:
    result = calibration.create_handover(
        verification_id, payload.scope, payload.business_date,
        from_shift=payload.from_shift, to_shift=payload.to_shift, note=payload.note,
    )
    if not result.get("ok"):
        status_code = 404 if "不存在" in str(result.get("message")) else 400
        raise HTTPException(status_code=status_code, detail=result.get("message"))
    return result


@router.get("/handovers")
def list_handovers(
    verification_id: int | None = Query(default=None),
    shift: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    items = calibration.list_handovers(verification_id, shift, limit)
    return {"items": items, "total": len(items)}


@router.get("/handovers/{handover_id}")
def get_handover(handover_id: int) -> dict[str, Any]:
    result = calibration.handover_detail(handover_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"交班记录 {handover_id} 不存在")
    return result


@router.post("/handovers/{handover_id}/claim")
def claim_handover(handover_id: int, payload: ClaimPayload) -> dict[str, Any]:
    result = calibration.claim_handover(
        handover_id, claimed_by=payload.claimed_by, shift=payload.shift
    )
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("message"))
    return result


@router.get("/attribution-handovers")
def attribution_handovers(
    shift: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    """归因交班记录：领取日期与两个采样编号的关联。

    传入 shift 时只保留该班组参与的交班批次，并汇总该班组实际领取过的日期，
    便于交班时核对“本班确实领取过哪些日期”。
    """
    from app.database import db

    handovers = calibration.list_handovers(shift=shift, limit=limit)
    records = []
    anomalies = {
        row["id"]: row
        for row in db.fetchall(
            "SELECT id, conclusion, status FROM anomaly_analyses ORDER BY id DESC LIMIT 500"
        )
    }
    shift_claimed_dates: list[str] = []
    for handover in handovers:
        verification = db.fetchone(
            "SELECT * FROM calibration_verifications WHERE id=?",
            [handover["verification_id"]],
        )
        anomaly = anomalies.get(verification["anomaly_analysis_id"]) if verification else None
        for item in handover["items"]:
            if item["status"] == "已领取" and (not shift or item["claimed_shift"] == shift):
                if item["business_date"] not in shift_claimed_dates:
                    shift_claimed_dates.append(item["business_date"])
        records.append({
            "handover_id": handover["id"],
            "verification_id": handover["verification_id"],
            "anomaly_analysis_id": verification["anomaly_analysis_id"] if verification else None,
            "conclusion": anomaly["conclusion"] if anomaly else None,
            "metric_name": handover["metric_name"],
            "version_a": handover["version_a"],
            "version_b": handover["version_b"],
            "start_date": handover["start_date"],
            "end_date": handover["end_date"],
            "scope": handover["scope"],
            "status": handover["status"],
            "from_shift": handover["from_shift"],
            "to_shift": handover["to_shift"],
            "claimable_dates": handover["claimable_dates"],
            "claimed_dates": handover["claimed_dates"],
            "items": [
                {
                    "business_date": item["business_date"],
                    "status": item["status"],
                    "claimed_shift": item["claimed_shift"],
                    "claimed_by": item["claimed_by"],
                    "claimed_at": item["claimed_at"],
                    "value_a": item["value_a"],
                    "value_b": item["value_b"],
                    "diff": item["diff"],
                    "sample_a_no": item["sample_a_no"],
                    "sample_b_no": item["sample_b_no"],
                    "sample_a_id": item["sample_a_id"],
                    "sample_b_id": item["sample_b_id"],
                }
                for item in handover["items"]
            ],
            "note": handover["note"],
            "created_at": handover["created_at"],
        })
    return {
        "items": records,
        "total": len(records),
        "shift": shift,
        "shift_claimed_dates": shift_claimed_dates,
    }
