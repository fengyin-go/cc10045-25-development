"""指标口径差异核查与归因交班领域服务。

真实数据流：指标快照按业务日期落库（metric_values.business_date / calibration_version）
→ 按业务日期把两个口径的已验证采样配对，逐日给出差值和缺口径日期
→ 从差异列表发起归因核查，生成可核对取数的日期记录和归因分析器记录
→ 值班主管按单日或整周交班，日班只领取本批次真正可核对的事项
→ 已领取的日期与两个采样编号绑定留痕，口径切换期间重跑旧核查仍可回查。

关键口径约定：
1. 只有同一天、同一指标、两个口径各自都有已验证采样，才算一条可核对配对。
2. 缺一边时值为 NULL，绝不能补 0，也不能拿其他日期的采样顶替。
3. 整周交班要求核查区间内已经到报的每个业务日期两版都齐全；只要有一条日期
   缺一边（有新版没旧版等），本次就不生成可领事项。完全没有任何采样的日期
   单独列在“无采样日期”里，它不算缺一边，也不能被别的日期补齐。
4. 已领取的日期不得再次进入新的交班批次，重跑核查只刷新未处置的日期。
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable

from app.database import db

PAIR_STATE_LABELS = {
    "paired": "两版齐全",
    "missing_a": "缺旧口径",
    "missing_b": "缺新口径",
}

VERIFICATION_STATUS_COMPLETE = "数据齐全"
VERIFICATION_STATUS_INCOMPLETE = "数据不齐"

HANDOVER_PENDING = "待领取"
HANDOVER_CLAIMED = "已领取"
HANDOVER_NOTHING = "无可领事项"
HANDOVER_INCOMPLETE = "数据不齐"


# ============================================================================
# 通用小工具
# ============================================================================

def _parse_date(value: str) -> date:
    return datetime.strptime(value.strip(), "%Y-%m-%d").date()


def _calendar_dates(start: str, end: str) -> list[str]:
    start_d = _parse_date(start)
    end_d = _parse_date(end)
    days: list[str] = []
    cursor = start_d
    while cursor <= end_d:
        days.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return days


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _metric(metric_id: int) -> dict[str, Any] | None:
    return db.fetchone("SELECT * FROM metrics WHERE id=?", [metric_id])


def _pick_verified(rows: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, bool]:
    """同一口径同一天可能有多条采样：优先已验证，其次取最新一条。

    返回 (用于配对的采样, 该口径当天是否存在已验证采样)。
    """
    if not rows:
        return None, False
    verified = [row for row in rows if int(row.get("verified") or 0) == 1]
    pool = verified or rows
    pool.sort(key=lambda row: (str(row.get("snapshot_time") or ""), int(row.get("id") or 0)))
    return pool[-1], bool(verified)


def _sample_brief(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": row["id"],
        "sample_no": row.get("sample_no"),
        "version": row["calibration_version"],
        "business_date": row.get("business_date"),
        "snapshot_time": row.get("snapshot_time"),
        "value": row.get("snapshot_value"),
        "verified": bool(int(row.get("verified") or 0)),
        "status": row.get("comparison_status"),
    }


# ============================================================================
# 口径差异视图：按业务日期配对
# ============================================================================

def _collect_samples(
    metric_id: int, version_a: int, version_b: int, start_date: str, end_date: str,
) -> dict[str, dict[int, list[dict[str, Any]]]]:
    rows = db.fetchall(
        "SELECT * FROM metric_values WHERE metric_id=? "
        "AND calibration_version IN (?, ?) "
        "AND business_date >= ? AND business_date <= ? "
        "ORDER BY business_date ASC, snapshot_time ASC, id ASC",
        [metric_id, version_a, version_b, start_date, end_date],
    )
    grouped: dict[str, dict[int, list[dict[str, Any]]]] = {}
    for row in rows:
        business_date = row.get("business_date") or str(row.get("snapshot_time") or "")[:10]
        if not business_date:
            continue
        version = int(row["calibration_version"])
        grouped.setdefault(business_date, {}).setdefault(version, []).append(row)
    return grouped


def build_calibration_diff(
    metric_id: int,
    version_a: int,
    version_b: int,
    start_date: str,
    end_date: str,
) -> dict[str, Any]:
    """按业务日期逐日配对两个口径的已验证采样。"""
    metric = _metric(metric_id)
    if metric is None:
        return {"ok": False, "message": f"指标 {metric_id} 不存在"}

    grouped = _collect_samples(metric_id, version_a, version_b, start_date, end_date)
    days: list[dict[str, Any]] = []
    paired_values_a: list[float] = []
    paired_values_b: list[float] = []

    for business_date in sorted(grouped):
        sides = grouped[business_date]
        sample_a, verified_a = _pick_verified(sides.get(version_a, []))
        sample_b, verified_b = _pick_verified(sides.get(version_b, []))

        if sample_a is not None and sample_b is not None and verified_a and verified_b:
            pair_state = "paired"
            pair_label = PAIR_STATE_LABELS["paired"]
            value_a = round(float(sample_a["snapshot_value"]), 4)
            value_b = round(float(sample_b["snapshot_value"]), 4)
            diff = round(value_a - value_b, 4)
            diff_pct = (
                round(diff / abs(value_b) * 100, 2) if value_b not in (0, 0.0) else None
            )
            paired_values_a.append(value_a)
            paired_values_b.append(value_b)
        elif sample_a is None or not verified_a:
            pair_state = "missing_a"
            pair_label = "旧口径未验证" if sample_a is not None else PAIR_STATE_LABELS["missing_a"]
            value_a = value_b = diff = diff_pct = None
        else:
            pair_state = "missing_b"
            pair_label = "新口径未验证" if sample_b is not None else PAIR_STATE_LABELS["missing_b"]
            value_a = value_b = diff = diff_pct = None

        days.append({
            "business_date": business_date,
            "pair_state": pair_state,
            "pair_label": pair_label,
            "individually_verifiable": pair_state == "paired",
            "value_a": value_a,
            "value_b": value_b,
            "diff": diff,
            "diff_pct": diff_pct,
            "sample_a_id": sample_a["id"] if sample_a else None,
            "sample_b_id": sample_b["id"] if sample_b else None,
            "sample_a_no": sample_a.get("sample_no") if sample_a else None,
            "sample_b_no": sample_b.get("sample_no") if sample_b else None,
            "sample_a": _sample_brief(sample_a),
            "sample_b": _sample_brief(sample_b),
            "a_has_sample": sample_a is not None,
            "b_has_sample": sample_b is not None,
            "a_verified": verified_a,
            "b_verified": verified_b,
        })

    paired_days = [day for day in days if day["pair_state"] == "paired"]
    missing_days = [day for day in days if day["pair_state"] != "paired"]
    sample_dates = {day["business_date"] for day in days}
    calendar_dates = _calendar_dates(start_date, end_date)
    no_sample_dates = [day for day in calendar_dates if day not in sample_dates]

    mean_a = round(sum(paired_values_a) / len(paired_values_a), 4) if paired_values_a else None
    mean_b = round(sum(paired_values_b) / len(paired_values_b), 4) if paired_values_b else None
    mean_diff = round(mean_a - mean_b, 4) if mean_a is not None and mean_b is not None else None

    missing_by_state = {
        state: [day["business_date"] for day in days if day["pair_state"] == state]
        for state in ("missing_a", "missing_b")
    }

    return {
        "ok": True,
        "metric_id": metric_id,
        "metric_name": metric["metric_name"],
        "unit": metric.get("unit", ""),
        "version_a": version_a,
        "version_b": version_b,
        "start_date": start_date,
        "end_date": end_date,
        "days": days,
        "paired_count": len(paired_days),
        "missing_count": len(missing_days),
        "total_dates": len(days),
        "dates_without_pair": [day["business_date"] for day in missing_days],
        "missing_by_state": missing_by_state,
        "no_sample_dates": no_sample_dates,
        "individually_verifiable_dates": [day["business_date"] for day in paired_days],
        "mean_a": mean_a,
        "mean_b": mean_b,
        "mean_diff": mean_diff,
    }


# ============================================================================
# 采样登记/查询
# ============================================================================

def list_samples(
    metric_id: int | None,
    version: int | None,
    start_date: str | None,
    end_date: str | None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    where = ["1=1"]
    params: list[Any] = []
    if metric_id is not None:
        where.append("metric_id=?")
        params.append(metric_id)
    if version is not None:
        where.append("calibration_version=?")
        params.append(version)
    if start_date:
        where.append("business_date >= ?")
        params.append(start_date)
    if end_date:
        where.append("business_date <= ?")
        params.append(end_date)
    rows = db.fetchall(
        f"SELECT * FROM metric_values WHERE {' AND '.join(where)} "
        "ORDER BY business_date DESC, calibration_version ASC, id DESC LIMIT ?",
        params + [limit],
    )
    return [_sample_brief(row) for row in rows]


def create_sample(
    metric_id: int,
    version: int,
    business_date: str,
    value: float,
    sample_no: str | None = None,
    verified: bool = True,
    snapshot_time: str | None = None,
) -> dict[str, Any]:
    metric = _metric(metric_id)
    if metric is None:
        return {"ok": False, "message": f"指标 {metric_id} 不存在"}

    snapshot_time = snapshot_time or f"{business_date}T09:00:00"
    insert_id = db.insert("metric_values", {
        "metric_id": metric_id,
        "snapshot_value": round(float(value), 4),
        "snapshot_time": snapshot_time,
        "calibration_version": version,
        "baseline_value": None,
        "comparison_status": "已保存",
        "business_date": business_date,
        "sample_no": sample_no or "",
        "verified": 1 if verified else 0,
    })
    if not sample_no:
        db.update("metric_values", {"sample_no": f"MV-{insert_id:06d}"}, "id=?", [insert_id])
    row = db.fetchone("SELECT * FROM metric_values WHERE id=?", [insert_id])
    return {"ok": True, "sample": _sample_brief(row)}


# ============================================================================
# 归因核查：日期记录
# ============================================================================

def _global_dispositions(
    metric_id: int, version_a: int, version_b: int,
) -> dict[str, dict[str, Any]]:
    """同一指标同一对口径下，跨核查记录已领取/待领取的业务日期。

    领取按“指标 + 两个口径 + 业务日期”记账：即使之后换一个区间重新发起核查，
    已经处置过的日期也不会被再次发出。
    """
    dispositions: dict[str, dict[str, Any]] = {}
    claimed = db.fetchall(
        "SELECT vd.business_date, vd.claimed_shift, vd.claimed_at "
        "FROM calibration_verification_dates vd "
        "JOIN calibration_verifications v ON v.id = vd.verification_id "
        "WHERE v.metric_id=? AND v.version_a=? AND v.version_b=? AND vd.claimed=1",
        [metric_id, version_a, version_b],
    )
    for row in claimed:
        dispositions[row["business_date"]] = {
            "status": HANDOVER_CLAIMED,
            "claimed_shift": row["claimed_shift"],
            "claimed_at": row["claimed_at"],
        }
    pending = db.fetchall(
        "SELECT item.business_date FROM calibration_handover_items item "
        "JOIN calibration_verifications v ON v.id = item.verification_id "
        "WHERE v.metric_id=? AND v.version_a=? AND v.version_b=? AND item.status=?",
        [metric_id, version_a, version_b, HANDOVER_PENDING],
    )
    for row in pending:
        dispositions.setdefault(row["business_date"], {
            "status": HANDOVER_PENDING,
            "claimed_shift": None,
            "claimed_at": None,
        })
    return dispositions


def _refresh_date_rows(
    verification: dict[str, Any],
    *,
    preserve_claimed: bool,
) -> list[dict[str, Any]]:
    """按当前采样重建核查日期记录。

    已领取或已进入待领取交班批次的日期整行冻结，保证此前处置过的差值
    和两个采样编号在口径切换期间重跑旧核查时仍可回查。
    """
    verification_id = verification["id"]
    diff = build_calibration_diff(
        verification["metric_id"],
        verification["version_a"],
        verification["version_b"],
        verification["start_date"],
        verification["end_date"],
    )
    if not diff.get("ok"):
        return []

    existing = {
        row["business_date"]: row
        for row in db.fetchall(
            "SELECT * FROM calibration_verification_dates WHERE verification_id=?",
            [verification_id],
        )
    }

    frozen_ids: set[int] = set()
    if preserve_claimed:
        for row in existing.values():
            if int(row.get("claimed") or 0) == 1:
                frozen_ids.add(int(row["id"]))
        pending = db.fetchall(
            "SELECT verification_date_id FROM calibration_handover_items "
            "WHERE verification_id=? AND status=?",
            [verification_id, HANDOVER_PENDING],
        )
        frozen_ids.update(int(item["verification_date_id"]) for item in pending)

    # 同一指标同一对口径下，别的核查记录里已经领取的日期同样视为已处置。
    global_dispositions = _global_dispositions(
        verification["metric_id"], verification["version_a"], verification["version_b"],
    )

    now = _now()
    for day in diff["days"]:
        current = existing.get(day["business_date"])
        if current is not None and int(current["id"]) in frozen_ids:
            continue
        values = {
            "sample_a_id": day["sample_a_id"],
            "sample_b_id": day["sample_b_id"],
            "value_a": day["value_a"],
            "value_b": day["value_b"],
            "diff": day["diff"],
            "diff_pct": day["diff_pct"],
            "pair_state": day["pair_state"],
            "pair_label": day["pair_label"],
            "verified": 1 if day["pair_state"] == "paired" else 0,
            "updated_at": now,
        }
        disposition = global_dispositions.get(day["business_date"])
        if disposition and disposition["status"] == HANDOVER_CLAIMED:
            values.update({
                "claimed": 1,
                "claimed_shift": disposition["claimed_shift"],
                "claimed_at": disposition["claimed_at"],
            })
        if current is None:
            values.update({
                "verification_id": verification_id,
                "business_date": day["business_date"],
                "created_at": now,
            })
            db.insert("calibration_verification_dates", values)
        else:
            db.update(
                "calibration_verification_dates", values, "id=?", [current["id"]]
            )

    # 未冻结但已没有任何采样的日期，从日期记录里移除。
    live_dates = {day["business_date"] for day in diff["days"]}
    for business_date, row in existing.items():
        if business_date in live_dates:
            continue
        if int(row["id"]) in frozen_ids:
            continue
        db.delete("calibration_verification_dates", "id=?", [row["id"]])

    return diff["days"]


def _verification_status(verification_id: int) -> str:
    rows = db.fetchall(
        "SELECT pair_state FROM calibration_verification_dates WHERE verification_id=?",
        [verification_id],
    )
    if not rows:
        return VERIFICATION_STATUS_INCOMPLETE
    if all(row["pair_state"] == "paired" for row in rows):
        return VERIFICATION_STATUS_COMPLETE
    return VERIFICATION_STATUS_INCOMPLETE


def _verification_analysis_text(
    metric_name: str,
    version_a: int,
    version_b: int,
    days: Iterable[dict[str, Any]],
) -> tuple[str, str]:
    day_list = list(days)
    paired = [day for day in day_list if day["pair_state"] == "paired"]
    missing = [day for day in day_list if day["pair_state"] != "paired"]
    parts = [
        f"口径 v{version_a}→v{version_b} 差异核查："
        f"可单独核对 {len(paired)} 天，缺口径 {len(missing)} 天"
    ]
    if paired:
        detail = "、".join(
            f"{day['business_date']} 差 {day['diff']:g}" for day in paired
        )
        parts.append(f"逐日差值 {detail}")
    if missing:
        detail = "、".join(
            f"{day['business_date']}（{PAIR_STATE_LABELS[day['pair_state']]}）"
            for day in missing
        )
        parts.append(f"缺口径日期 {detail}")
    return metric_name, "；".join(parts)


def _sync_anomaly_record(verification: dict[str, Any], days: list[dict[str, Any]]) -> int:
    """把核查结果同步成一条归因分析器记录，供异常归因模块查看。"""
    metric = _metric(verification["metric_id"])
    metric_name = metric["metric_name"] if metric else str(verification["metric_id"])
    _, conclusion = _verification_analysis_text(
        metric_name, verification["version_a"], verification["version_b"], days,
    )
    ranking = [
        [day["business_date"], day["diff"]]
        for day in days
        if day["pair_state"] == "paired"
    ]
    anomaly_time = f"{verification['start_date']}~{verification['end_date']}"
    dimensions = json.dumps(
        [f"口径v{verification['version_a']}", f"口径v{verification['version_b']}"],
        ensure_ascii=False,
    )
    ranking_json = json.dumps(ranking, ensure_ascii=False)
    anomaly_id = verification.get("anomaly_analysis_id")

    payload = {
        "target_name": f"{metric_name} 口径差异核查",
        "anomaly_time": anomaly_time,
        "attribution_dimensions": dimensions,
        "contribution_ranking": ranking_json,
        "conclusion": conclusion,
        "status": "已完成",
    }
    if anomaly_id and db.fetchone("SELECT id FROM anomaly_analyses WHERE id=?", [anomaly_id]):
        db.update("anomaly_analyses", payload, "id=?", [anomaly_id])
        return int(anomaly_id)

    anomaly_id = db.insert("anomaly_analyses", payload)
    db.update(
        "calibration_verifications", {"anomaly_analysis_id": anomaly_id},
        "id=?", [verification["id"]],
    )
    verification["anomaly_analysis_id"] = anomaly_id
    return anomaly_id


def create_verification(
    metric_id: int,
    version_a: int,
    version_b: int,
    start_date: str,
    end_date: str,
    title: str = "",
    requested_by: str = "",
) -> dict[str, Any]:
    """从差异列表发起归因核查请求。

    同一指标、同一对版本、同一周区间重复发起时复用原核查记录：
    只刷新未处置的日期，已领取日期的两个采样编号保持原样。
    """
    metric = _metric(metric_id)
    if metric is None:
        return {"ok": False, "message": f"指标 {metric_id} 不存在"}

    now = _now()
    existing = db.fetchone(
        "SELECT * FROM calibration_verifications WHERE metric_id=? AND version_a=? "
        "AND version_b=? AND start_date=? AND end_date=?",
        [metric_id, version_a, version_b, start_date, end_date],
    )
    if existing:
        verification = existing
        reused = True
        if title:
            db.update("calibration_verifications", {"title": title}, "id=?", [verification["id"]])
            verification["title"] = title
        if requested_by:
            db.update(
                "calibration_verifications", {"requested_by": requested_by},
                "id=?", [verification["id"]],
            )
            verification["requested_by"] = requested_by
    else:
        reused = False
        verification_id = db.insert("calibration_verifications", {
            "metric_id": metric_id,
            "version_a": version_a,
            "version_b": version_b,
            "start_date": start_date,
            "end_date": end_date,
            "title": title or f"{metric['metric_name']} 口径差异核查",
            "requested_by": requested_by,
            "status": VERIFICATION_STATUS_INCOMPLETE,
            "created_at": now,
            "updated_at": now,
        })
        verification = db.fetchone(
            "SELECT * FROM calibration_verifications WHERE id=?", [verification_id]
        )

    days = _refresh_date_rows(verification, preserve_claimed=True)
    status = _verification_status(verification["id"])
    db.update(
        "calibration_verifications",
        {"status": status, "updated_at": _now()},
        "id=?", [verification["id"]],
    )
    verification["status"] = status
    anomaly_id = _sync_anomaly_record(verification, days)

    detail = verification_detail(verification["id"])
    assert detail is not None
    detail["reused"] = reused
    detail["anomaly_analysis_id"] = anomaly_id
    return detail


def refresh_verification(verification_id: int) -> dict[str, Any]:
    """重跑旧核查：只刷新未处置日期，已领取日期与其采样继续可回查。"""
    verification = db.fetchone(
        "SELECT * FROM calibration_verifications WHERE id=?", [verification_id]
    )
    if verification is None:
        return {"ok": False, "message": f"核查记录 {verification_id} 不存在"}

    days = _refresh_date_rows(verification, preserve_claimed=True)
    status = _verification_status(verification_id)
    db.update(
        "calibration_verifications",
        {"status": status, "updated_at": _now()},
        "id=?", [verification_id],
    )
    _sync_anomaly_record(verification, days)
    detail = verification_detail(verification_id)
    assert detail is not None
    detail["refreshed"] = True
    return detail


# ============================================================================
# 核查记录查询
# ============================================================================

def _handover_summary(handover: dict[str, Any]) -> dict[str, Any]:
    items = db.fetchall(
        "SELECT * FROM calibration_handover_items WHERE handover_id=? ORDER BY business_date ASC",
        [handover["id"]],
    )
    return {
        **handover,
        "item_count": len(items),
        "claimed_dates": [
            item["business_date"] for item in items if item["status"] == HANDOVER_CLAIMED
        ],
        "claimable_dates": [
            item["business_date"] for item in items if item["status"] == HANDOVER_PENDING
        ],
    }


def verification_detail(verification_id: int) -> dict[str, Any] | None:
    verification = db.fetchone(
        "SELECT * FROM calibration_verifications WHERE id=?", [verification_id]
    )
    if verification is None:
        return None
    metric = _metric(verification["metric_id"])
    date_rows = db.fetchall(
        "SELECT * FROM calibration_verification_dates WHERE verification_id=? "
        "ORDER BY business_date ASC",
        [verification_id],
    )
    days = []
    for row in date_rows:
        sample_a = (
            db.fetchone("SELECT * FROM metric_values WHERE id=?", [row["sample_a_id"]])
            if row["sample_a_id"] else None
        )
        sample_b = (
            db.fetchone("SELECT * FROM metric_values WHERE id=?", [row["sample_b_id"]])
            if row["sample_b_id"] else None
        )
        days.append({
            "id": row["id"],
            "business_date": row["business_date"],
            "pair_state": row["pair_state"],
            "pair_label": row.get("pair_label") or PAIR_STATE_LABELS.get(
                row["pair_state"], row["pair_state"]
            ),
            "individually_verifiable": row["pair_state"] == "paired",
            "value_a": row["value_a"],
            "value_b": row["value_b"],
            "diff": row["diff"],
            "diff_pct": row["diff_pct"],
            "sample_a_id": row["sample_a_id"],
            "sample_b_id": row["sample_b_id"],
            "sample_a": _sample_brief(sample_a),
            "sample_b": _sample_brief(sample_b),
            "sample_a_no": sample_a.get("sample_no") if sample_a else None,
            "sample_b_no": sample_b.get("sample_no") if sample_b else None,
            "verified": bool(int(row["verified"] or 0)),
            "claimed": bool(int(row["claimed"] or 0)),
            "claimed_shift": row["claimed_shift"],
            "claimed_at": row["claimed_at"],
        })

    handovers = [
        _handover_summary(row)
        for row in db.fetchall(
            "SELECT * FROM calibration_handovers WHERE verification_id=? ORDER BY id DESC",
            [verification_id],
        )
    ]
    claimed_dates = [day["business_date"] for day in days if day["claimed"]]
    return {
        "ok": True,
        "id": verification["id"],
        "metric_id": verification["metric_id"],
        "metric_name": metric["metric_name"] if metric else "",
        "unit": metric.get("unit", "") if metric else "",
        "version_a": verification["version_a"],
        "version_b": verification["version_b"],
        "start_date": verification["start_date"],
        "end_date": verification["end_date"],
        "title": verification["title"],
        "requested_by": verification["requested_by"],
        "status": verification["status"],
        "anomaly_analysis_id": verification["anomaly_analysis_id"],
        "created_at": verification["created_at"],
        "updated_at": verification["updated_at"],
        "days": days,
        "paired_count": sum(1 for day in days if day["pair_state"] == "paired"),
        "missing_count": sum(1 for day in days if day["pair_state"] != "paired"),
        "individually_verifiable_dates": [
            day["business_date"] for day in days if day["pair_state"] == "paired"
        ],
        "dates_without_pair": [
            day["business_date"] for day in days if day["pair_state"] != "paired"
        ],
        "claimed_dates": claimed_dates,
        "handovers": handovers,
    }


def list_verifications(limit: int = 100) -> list[dict[str, Any]]:
    rows = db.fetchall(
        "SELECT * FROM calibration_verifications ORDER BY id DESC LIMIT ?", [limit]
    )
    result = []
    for row in rows:
        metric = _metric(row["metric_id"])
        counts = db.fetchone(
            "SELECT COUNT(*) AS total, "
            "SUM(CASE WHEN pair_state='paired' THEN 1 ELSE 0 END) AS paired, "
            "SUM(CASE WHEN claimed=1 THEN 1 ELSE 0 END) AS claimed "
            "FROM calibration_verification_dates WHERE verification_id=?",
            [row["id"]],
        ) or {}
        result.append({
            **row,
            "metric_name": metric["metric_name"] if metric else "",
            "date_count": int(counts.get("total") or 0),
            "paired_count": int(counts.get("paired") or 0),
            "claimed_count": int(counts.get("claimed") or 0),
        })
    return result


# ============================================================================
# 交班与领取
# ============================================================================

def _pending_item_dates(verification_id: int, exclude_handover_id: int | None = None) -> set[str]:
    sql = (
        "SELECT business_date FROM calibration_handover_items "
        "WHERE verification_id=? AND status=?"
    )
    params: list[Any] = [verification_id, HANDOVER_PENDING]
    if exclude_handover_id is not None:
        sql += " AND handover_id<>?"
        params.append(exclude_handover_id)
    return {row["business_date"] for row in db.fetchall(sql, params)}


def _pending_item_dates_global(metric_id: int, version_a: int, version_b: int) -> set[str]:
    """同一指标同一对口径下，其他核查记录里仍在待领取的日期。"""
    rows = db.fetchall(
        "SELECT item.business_date FROM calibration_handover_items item "
        "JOIN calibration_verifications v ON v.id = item.verification_id "
        "WHERE v.metric_id=? AND v.version_a=? AND v.version_b=? AND item.status=?",
        [metric_id, version_a, version_b, HANDOVER_PENDING],
    )
    return {row["business_date"] for row in rows}


def create_handover(
    verification_id: int,
    scope: str,
    business_date: str | None = None,
    from_shift: str = "",
    to_shift: str = "",
    note: str = "",
) -> dict[str, Any]:
    """发起交班。

    scope=day：只交指定业务日期，要求该日两版采样齐全且未被领取。
    scope=week：要求核查区间内每个自然日两版采样都齐全；不齐时本次不生成可领事项。
    """
    verification = db.fetchone(
        "SELECT * FROM calibration_verifications WHERE id=?", [verification_id]
    )
    if verification is None:
        return {"ok": False, "message": f"核查记录 {verification_id} 不存在"}

    if scope not in ("day", "week"):
        return {"ok": False, "message": "交班范围只支持单日(day)或整周(week)"}
    if scope == "day" and not business_date:
        return {"ok": False, "message": "单日交班必须指定业务日期"}

    rows = db.fetchall(
        "SELECT * FROM calibration_verification_dates WHERE verification_id=? "
        "ORDER BY business_date ASC",
        [verification_id],
    )
    row_by_date = {row["business_date"]: row for row in rows}
    blocked: list[dict[str, Any]] = []

    if scope == "week":
        # 整周交班：本次核查覆盖到的每个业务日期都必须两版齐全。
        # 完全没有采样的日期视为尚未到报，不补 0 也不当作缺一边；
        # 只要有任一天缺一边，本次就不生成可领事项。
        for row in rows:
            if row["pair_state"] != "paired":
                blocked.append({
                    "business_date": row["business_date"],
                    "reason": row.get("pair_label") or PAIR_STATE_LABELS.get(
                        row["pair_state"], row["pair_state"]
                    ),
                })
        candidates = (
            [row for row in rows if row["pair_state"] == "paired"]
            if not blocked else []
        )
    else:
        row = row_by_date.get(str(business_date))
        if row is None:
            return {"ok": False, "message": f"核查记录里没有业务日期 {business_date}"}
        if row["pair_state"] != "paired":
            blocked.append({
                "business_date": row["business_date"],
                "reason": row.get("pair_label") or PAIR_STATE_LABELS.get(
                    row["pair_state"], row["pair_state"]
                ),
            })
        candidates = [row] if not blocked else []

    claimed_dates = {row["business_date"] for row in rows if int(row["claimed"] or 0) == 1}
    claimed_dates |= {
        day for day, disposition in _global_dispositions(
            verification["metric_id"], verification["version_a"], verification["version_b"],
        ).items()
        if disposition["status"] == HANDOVER_CLAIMED
    }
    in_pending = _pending_item_dates(verification_id) | _pending_item_dates_global(
        verification["metric_id"], verification["version_a"], verification["version_b"],
    )
    eligible = [
        row for row in candidates
        if row["business_date"] not in claimed_dates
        and row["business_date"] not in in_pending
        and int(row["verified"] or 0) == 1
    ]

    now = _now()
    if blocked:
        status = HANDOVER_INCOMPLETE
    elif eligible:
        status = HANDOVER_PENDING
    else:
        status = HANDOVER_NOTHING

    if not blocked:
        if not eligible:
            reason = "所选日期已领取或已在其他交班批次中待领取"
            if scope == "week":
                blocked_dates = set(claimed_dates) | set(in_pending)
                overlap = [
                    row["business_date"] for row in candidates
                    if row["business_date"] in blocked_dates
                ]
                reason = (
                    "整周两版数据已齐，但没有未领取的可核对日期"
                    + (f"（{'、'.join(overlap)} 已处置）" if overlap else "")
                )
            note = f"{note}；{reason}".strip("；") if note else reason
        elif scope == "week":
            eligible_dates = {row["business_date"] for row in eligible}
            skipped = [
                row["business_date"] for row in candidates
                if row["business_date"] not in eligible_dates
            ]
            if skipped:
                extra = f"已领取/已在其他批次待领取的日期不再重复发出：{'、'.join(skipped)}"
                note = f"{note}；{extra}".strip("；") if note else extra
    else:
        detail = "、".join(f"{item['business_date']}（{item['reason']}）" for item in blocked)
        extra = f"本次不生成可领事项：{detail}"
        note = f"{note}；{extra}".strip("；") if note else extra

    handover_id = db.insert("calibration_handovers", {
        "verification_id": verification_id,
        "scope": scope,
        "business_date": business_date if scope == "day" else None,
        "from_shift": from_shift,
        "to_shift": to_shift,
        "status": status,
        "claimable_count": len(eligible),
        "claimed_count": 0,
        "note": note,
        "created_at": now,
    })

    for row in eligible:
        db.insert("calibration_handover_items", {
            "handover_id": handover_id,
            "verification_id": verification_id,
            "verification_date_id": row["id"],
            "business_date": row["business_date"],
            "sample_a_id": row["sample_a_id"],
            "sample_b_id": row["sample_b_id"],
            "value_a": row["value_a"],
            "value_b": row["value_b"],
            "diff": row["diff"],
            "status": HANDOVER_PENDING,
            "created_at": now,
        })

    db.update(
        "calibration_verifications",
        {"updated_at": now},
        "id=?", [verification_id],
    )
    result = handover_detail(handover_id)
    assert result is not None
    result["blocked_dates"] = blocked
    return result


def claim_handover(
    handover_id: int,
    claimed_by: str = "",
    shift: str = "",
) -> dict[str, Any]:
    """日班领取交班批次；已领取的日期不会再被重复发出。"""
    handover = db.fetchone(
        "SELECT * FROM calibration_handovers WHERE id=?", [handover_id]
    )
    if handover is None:
        return {"ok": False, "message": f"交班记录 {handover_id} 不存在"}
    if handover["status"] == HANDOVER_CLAIMED:
        return {"ok": False, "message": "该交班批次已领取，不能重复领取"}
    if handover["status"] == HANDOVER_INCOMPLETE:
        return {"ok": False, "message": "整周两版数据尚未补齐，本次没有可领取事项"}
    if handover["status"] == HANDOVER_NOTHING:
        return {"ok": False, "message": "该交班批次没有可领取事项"}

    items = db.fetchall(
        "SELECT * FROM calibration_handover_items WHERE handover_id=? AND status=?",
        [handover_id, HANDOVER_PENDING],
    )
    if not items:
        return {"ok": False, "message": "该交班批次已没有待领取事项"}

    now = _now()
    claimed_shift = shift or handover["to_shift"] or "日班"
    for item in items:
        db.update("calibration_handover_items", {
            "status": HANDOVER_CLAIMED,
            "claimed_shift": claimed_shift,
            "claimed_by": claimed_by,
            "claimed_at": now,
        }, "id=?", [item["id"]])
        db.update("calibration_verification_dates", {
            "claimed": 1,
            "claimed_shift": claimed_shift,
            "claimed_at": now,
            "updated_at": now,
        }, "id=?", [item["verification_date_id"]])

    db.update("calibration_handovers", {
        "status": HANDOVER_CLAIMED,
        "claimed_count": len(items),
        "claimable_count": 0,
        "to_shift": claimed_shift,
        "note": (handover["note"] + "；" if handover["note"] else "")
                + f"{claimed_shift}已领取 {len(items)} 个日期",
    }, "id=?", [handover_id])

    result = handover_detail(handover_id)
    assert result is not None
    result["claimed_count"] = len(items)
    return result


def handover_detail(handover_id: int) -> dict[str, Any] | None:
    handover = db.fetchone(
        "SELECT * FROM calibration_handovers WHERE id=?", [handover_id]
    )
    if handover is None:
        return None
    verification = db.fetchone(
        "SELECT * FROM calibration_verifications WHERE id=?",
        [handover["verification_id"]],
    )
    metric = _metric(verification["metric_id"]) if verification else None
    items = db.fetchall(
        "SELECT * FROM calibration_handover_items WHERE handover_id=? ORDER BY business_date ASC",
        [handover_id],
    )
    enriched_items = []
    for item in items:
        sample_a = db.fetchone(
            "SELECT * FROM metric_values WHERE id=?", [item["sample_a_id"]]
        )
        sample_b = db.fetchone(
            "SELECT * FROM metric_values WHERE id=?", [item["sample_b_id"]]
        )
        enriched_items.append({
            **item,
            "pair_label": PAIR_STATE_LABELS["paired"],
            "sample_a": _sample_brief(sample_a),
            "sample_b": _sample_brief(sample_b),
            "sample_a_no": sample_a.get("sample_no") if sample_a else None,
            "sample_b_no": sample_b.get("sample_no") if sample_b else None,
        })
    return {
        "ok": True,
        "id": handover["id"],
        "verification_id": handover["verification_id"],
        "metric_id": verification["metric_id"] if verification else None,
        "metric_name": metric["metric_name"] if metric else "",
        "version_a": verification["version_a"] if verification else None,
        "version_b": verification["version_b"] if verification else None,
        "start_date": verification["start_date"] if verification else None,
        "end_date": verification["end_date"] if verification else None,
        "scope": handover["scope"],
        "business_date": handover["business_date"],
        "from_shift": handover["from_shift"],
        "to_shift": handover["to_shift"],
        "status": handover["status"],
        "claimable_count": handover["claimable_count"],
        "claimed_count": handover["claimed_count"],
        "note": handover["note"],
        "created_at": handover["created_at"],
        "items": enriched_items,
        "claimable_dates": [
            item["business_date"] for item in items if item["status"] == HANDOVER_PENDING
        ],
        "claimed_dates": [
            item["business_date"] for item in items if item["status"] == HANDOVER_CLAIMED
        ],
    }


def list_handovers(
    verification_id: int | None = None,
    shift: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    where = ["1=1"]
    params: list[Any] = []
    if verification_id is not None:
        where.append("verification_id=?")
        params.append(verification_id)
    if shift:
        where.append("(from_shift=? OR to_shift=?)")
        params.extend([shift, shift])
    rows = db.fetchall(
        f"SELECT * FROM calibration_handovers WHERE {' AND '.join(where)} "
        "ORDER BY id DESC LIMIT ?",
        params + [limit],
    )
    results = []
    for row in rows:
        detail = handover_detail(row["id"])
        assert detail is not None
        results.append(detail)
    return results
