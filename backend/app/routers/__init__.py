"""业务路由汇总：资源路由 + 管道路由 + 口径核查路由。"""
from __future__ import annotations

from app.routers import resource
from app.routers import pipeline
from app.routers import calibration

ROUTERS = [resource]
PIPELINE_ROUTER = pipeline.router
CALIBRATION_ROUTER = calibration.router