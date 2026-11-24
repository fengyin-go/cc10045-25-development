"""指标存储领域服务：管理指标定义、执行快照计算并与基准对比。

真实数据流：接收指标定义 → 调用 pipeline.compute_metric_snapshot 计算当前值
→ 写入 metric_values 表 → 对比上一期基准值和历史序列 → 检测 z-score 异常
→ 下游告警策略消费者。
"""
from __future__ import annotations

import json
from typing import Any

from app.database import db
from app.pipeline import compute_metric_snapshot
from app.services.base import BaseModuleService


class MetricStoreService(BaseModuleService):
    MODULE = "metric_store"
    ENTITY = "指标快照"
    OPERATION_TERM = "存储"
    LIST_FIELDS = ["指标名称", "快照值", "快照时间", "口径版本", "对比基准"]
    REQUIRED_FIELDS = ["指标名称", "快照值", "快照时间"]
    STATUS_ORDER = ["已保存", "对比成功", "有异常"]
    ACTION_RULES = {"保存快照": "已保存", "标记异常": "有异常"}
    NEGATIVE_ACTIONS = []

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        """登记一条指标快照：按指标名称落到 metrics，快照值写入 metric_values。

        metric_values 才是快照的真实存储；metrics 只保存指标定义，
        这样登记的快照能直接进入按业务日期的口径差异配对。
        """
        missing = [
            field for field in self.REQUIRED_FIELDS
            if not str(values.get(field) or "").strip()
        ]
        if missing:
            return None, missing

        metric_name = str(values.get("指标名称", "")).strip()
        metric = db.fetchone("SELECT * FROM metrics WHERE metric_name=?", [metric_name])
        if metric is None:
            metric_id = db.insert("metrics", {
                "metric_name": metric_name,
                "metric_type": "counter",
                "source_module": "metric_store",
                "calibration_version": int(str(values.get("口径版本") or 1) or 1),
                "unit": "",
                "description": "由指标快照登记自动创建",
            })
        else:
            metric_id = metric["id"]

        snapshot_time = str(values.get("快照时间", "")).strip()
        business_date = snapshot_time[:10]
        try:
            snapshot_value = float(values.get("快照值"))
        except (TypeError, ValueError):
            return None, ["快照值"]

        snapshot_id = db.insert("metric_values", {
            "metric_id": metric_id,
            "snapshot_value": round(snapshot_value, 4),
            "snapshot_time": snapshot_time,
            "calibration_version": int(str(values.get("口径版本") or 1) or 1),
            "baseline_value": values.get("对比基准") or None,
            "comparison_status": "已保存",
            "business_date": business_date,
            "sample_no": "",
            "verified": 1,
        })
        db.update(
            "metric_values", {"sample_no": f"MV-{snapshot_id:06d}"}, "id=?", [snapshot_id]
        )
        return db.fetchone("SELECT * FROM metric_values WHERE id=?", [snapshot_id]), []

    def list_metrics(self, page: int = 1, size: int = 20) -> tuple[list[dict[str, Any]], int]:
        """列出所有指标定义。"""
        total = db.count("metrics")
        rows = db.fetchall(
            "SELECT * FROM metrics ORDER BY id DESC LIMIT ? OFFSET ?",
            [size, (page - 1) * size],
        )
        return rows, total

    def compute_snapshot(self, metric_id: int) -> dict[str, Any]:
        """对指定指标执行一次快照计算并存储。"""
        metric = db.fetchone("SELECT * FROM metrics WHERE id=?", [metric_id])
        if metric is None:
            return {"ok": False, "message": f"指标 {metric_id} 不存在"}
        result = compute_metric_snapshot(dict(metric))
        if result is None:
            return {"ok": False, "message": f"指标 {metric['metric_name']} 计算失败"}
        return {"ok": True, "result": result}

    def compute_all(self) -> dict[str, Any]:
        """对所有指标执行快照计算。"""
        metrics = db.fetchall("SELECT * FROM metrics")
        results = []
        for m in metrics:
            r = self.compute_snapshot(m["id"])
            results.append({
                "metric_name": m["metric_name"],
                "ok": r["ok"],
                "value": r["result"]["snapshot_value"] if r["ok"] else None,
                "status": r["result"]["comparison_status"] if r["ok"] else "failed",
            })
        return {"processed": len(metrics), "results": results}

    def metric_trend(self, metric_id: int, limit: int = 30) -> dict[str, Any]:
        """获取指标的历史趋势数据。"""
        metric = db.fetchone("SELECT * FROM metrics WHERE id=?", [metric_id])
        if metric is None:
            return {"trend": "no_data", "points": []}

        rows = db.fetchall(
            "SELECT * FROM metric_values WHERE metric_id=? ORDER BY snapshot_time DESC LIMIT ?",
            [metric_id, limit],
        )
        values = [r["snapshot_value"] for r in rows]
        if not values:
            return {"trend": "no_data", "points": [], "metric_name": metric["metric_name"]}

        import statistics
        mean_v = statistics.mean(values)

        trend = "stable"
        if len(values) >= 2:
            if values[0] > mean_v * 1.1:
                trend = "upward"
            elif values[0] < mean_v * 0.9:
                trend = "downward"

        return {
            "metric_name": metric["metric_name"],
            "metric_type": metric["metric_type"],
            "unit": metric["unit"],
            "trend": trend,
            "latest": values[0],
            "mean": round(mean_v, 4),
            "min": round(min(values), 4),
            "max": round(max(values), 4),
            "points": [{"time": r["snapshot_time"], "value": r["snapshot_value"],
                       "status": r["comparison_status"]} for r in rows],
        }

    def compare_calibrations(self, metric_id: int, v1: int, v2: int) -> dict[str, Any]:
        """对比同一指标在两个口径版本下的差异。"""
        # 按业务日期配对逐日口径差异，均值只从真正配对成功的日期计算。
        from app.services.calibration import build_calibration_diff

        dates = db.fetchall(
            "SELECT MIN(business_date) AS first_date, MAX(business_date) AS last_date "
            "FROM metric_values WHERE metric_id=? AND calibration_version IN (?, ?) "
            "AND business_date IS NOT NULL AND business_date<>''",
            [metric_id, v1, v2],
        )
        first_date = dates[0]["first_date"] if dates else None
        last_date = dates[0]["last_date"] if dates else None
        diff_view: dict[str, Any] | None = None
        if first_date and last_date:
            diff_view = build_calibration_diff(metric_id, v1, v2, first_date, last_date)
            diff_view.pop("ok", None)

        snapshots_v1 = db.fetchall(
            "SELECT * FROM metric_values WHERE metric_id=? AND calibration_version=? ORDER BY snapshot_time DESC LIMIT 10",
            [metric_id, v1],
        )
        snapshots_v2 = db.fetchall(
            "SELECT * FROM metric_values WHERE metric_id=? AND calibration_version=? ORDER BY snapshot_time DESC LIMIT 10",
            [metric_id, v2],
        )

        import statistics
        mean_v1 = statistics.mean([s["snapshot_value"] for s in snapshots_v1]) if snapshots_v1 else 0
        mean_v2 = statistics.mean([s["snapshot_value"] for s in snapshots_v2]) if snapshots_v2 else 0
        diff_pct = round((mean_v1 - mean_v2) / max(abs(mean_v2), 0.0001) * 100, 2)

        return {
            "metric_id": metric_id,
            "version_a": v1,
            "version_b": v2,
            "mean_a": round(mean_v1, 4),
            "mean_b": round(mean_v2, 4),
            "diff_pct": diff_pct,
            "significant": abs(diff_pct) > 5,
            "daily_diff": diff_view,
        }

    def domain_snapshot(self) -> dict[str, Any]:
        snapshot = super().domain_snapshot()
        snapshot["total_metrics"] = db.count("metrics")
        snapshot["total_snapshots"] = db.count("metric_values")
        abnormal = db.count("metric_values", "comparison_status='有异常'")
        snapshot["abnormal_snapshots"] = abnormal
        return snapshot