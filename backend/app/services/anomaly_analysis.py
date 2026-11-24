"""异常归因领域服务：对指标异常波动自动执行多维度归因和贡献度分析。

真实数据流：确定分析对象和异常时间点 → pipeline.detect_anomaly 执行 z-score 检测
→ 按归因维度解构贡献度 → 输出分析结论 → 写入 anomaly_analyses 表。
"""
from __future__ import annotations

import json
from typing import Any

from app.database import db
from app.pipeline import detect_anomaly
from app.services.base import BaseModuleService


class AnomalyAnalysisService(BaseModuleService):
    MODULE = "anomaly_analysis"
    ENTITY = "归因分析器"
    OPERATION_TERM = "分析"
    LIST_FIELDS = ["分析对象", "异常时间点", "归因维度", "贡献度排序", "分析结论"]
    REQUIRED_FIELDS = ["分析对象", "异常时间点", "归因维度"]
    STATUS_ORDER = ["待分析", "分析中", "已完成", "分析失败"]
    ACTION_RULES = {"启动分析": "已完成", "取消分析": "分析失败"}
    NEGATIVE_ACTIONS = []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        """通用动作入口：口径核查生成的记录在“启动分析”时按两版采样重算差值，
        其余记录沿用通用的状态流转。"""
        entry = self.get_entry(entry_id)
        if entry is None:
            return None, f"{self.ENTITY} {entry_id} 不存在或已归档"
        target = self.ACTION_RULES.get(action)
        if target is None:
            return None, f"动作「{action}」不属于{self.MODULE}可执行范围"
        if action == "启动分析" and self._calibration_verification(entry_id):
            result = self.run_analysis(entry_id)
            if not result.get("ok"):
                return None, result.get("message", "归因分析失败")
            return self.get_entry(entry_id), f"{self.ENTITY}已按两版采样完成差值核查"
        db.update("anomaly_analyses", {"status": target}, "id=?", [entry_id])
        return self.get_entry(entry_id), f"{self.ENTITY}已{action}"

    def run_analysis(self, entry_id: int) -> dict[str, Any]:
        """执行异常归因分析。先加载记录，再调用管线。

        由口径差异核查生成的记录带有 verification 关联：此时以两版实际采样
        逐日重算差值，生成可核对指标取数的日期记录，而不是回退到通用 z-score。
        """
        entry = self.get_entry(entry_id)
        if entry is None:
            return {"ok": False, "message": f"分析记录 {entry_id} 不存在"}

        # 标记为分析中
        db.update("anomaly_analyses", {"status": "分析中"}, "id=?", [entry_id])

        linked = self._calibration_verification(entry_id)
        if linked is not None:
            return self._run_calibration_analysis(entry_id, linked)

        analysis_data = {
            "分析对象": entry.get("分析对象"),
            "异常时间点": entry.get("异常时间点"),
            "归因维度": entry.get("归因维度"),
        }
        result = detect_anomaly(analysis_data)

        # 回写结论
        conclusion = result.get("conclusion", "分析完成")
        contributions = result.get("contributions", {})
        ranking_json = json.dumps(
            sorted(contributions.items(), key=lambda x: -x[1])[:5],
            ensure_ascii=False,
        )

        db.update("anomaly_analyses", {
            "status": "已完成",
            "contribution_ranking": ranking_json,
            "conclusion": conclusion,
        }, "id=?", [entry_id])

        return {
            "ok": True,
            "target": analysis_data["分析对象"],
            "is_anomaly": result.get("is_anomaly", False),
            "z_score": result.get("z_score", 0),
            "conclusion": conclusion,
            "contributions": contributions,
        }

    @staticmethod
    def _calibration_verification(entry_id: int) -> dict[str, Any] | None:
        return db.fetchone(
            "SELECT * FROM calibration_verifications WHERE anomaly_analysis_id=?",
            [entry_id],
        )

    def _run_calibration_analysis(
        self, entry_id: int, verification: dict[str, Any],
    ) -> dict[str, Any]:
        """以两版实际采样重算逐日差值，并回写归因记录。"""
        from app.services import calibration

        detail = calibration.refresh_verification(verification["id"])
        if not detail.get("ok"):
            db.update("anomaly_analyses", {"status": "分析失败"}, "id=?", [entry_id])
            return detail

        paired = [day for day in detail["days"] if day["pair_state"] == "paired"]
        missing = [day for day in detail["days"] if day["pair_state"] != "paired"]
        ranking = [[day["business_date"], day["diff"]] for day in paired]
        anomaly_id = detail.get("anomaly_analysis_id")
        refreshed = db.fetchone("SELECT * FROM anomaly_analyses WHERE id=?", [entry_id])
        db.update("anomaly_analyses", {
            "status": "已完成",
            "contribution_ranking": json.dumps(ranking, ensure_ascii=False),
            "conclusion": refreshed["conclusion"] if refreshed else "",
        }, "id=?", [entry_id])

        return {
            "ok": True,
            "target": detail["metric_name"],
            "verification_id": detail["id"],
            "is_anomaly": bool(paired),
            "paired_dates": [day["business_date"] for day in paired],
            "dates_without_pair": [day["business_date"] for day in missing],
            "date_records": [
                {
                    "business_date": day["business_date"],
                    "pair_state": day["pair_state"],
                    "value_a": day["value_a"],
                    "value_b": day["value_b"],
                    "diff": day["diff"],
                    "sample_a_no": day["sample_a_no"],
                    "sample_b_no": day["sample_b_no"],
                }
                for day in detail["days"]
            ],
            "conclusion": refreshed["conclusion"] if refreshed else "",
        }

    def recent_anomalies(self, limit: int = 20) -> list[dict[str, Any]]:
        """获取最近的分析结果。"""
        return db.fetchall(
            "SELECT * FROM anomaly_analyses WHERE status='已完成' ORDER BY id DESC LIMIT ?",
            [limit],
        )

    def domain_snapshot(self) -> dict[str, Any]:
        snapshot = super().domain_snapshot()
        snapshot["total_analyses"] = db.count("anomaly_analyses")
        snapshot["completed_analyses"] = db.count("anomaly_analyses", "status='已完成'")
        return snapshot