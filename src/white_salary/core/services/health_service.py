"""
white_salary/core/services/health_service.py

记忆系统健康监控 — 监控记忆系统的运行状态。

借鉴v2的services/health_service.py（173行）：
  - 监控各存储层的容量和状态
  - 检测异常（存储满/读写错误/模块故障）
  - 健康评分
  - 提供给控制面板查看

不用LLM，纯统计/监控。
"""

import time
from pathlib import Path
from typing import Optional

from loguru import logger


class MemoryHealthService:
    """记忆系统健康监控。"""

    def __init__(self, data_dir: str = "data/memory") -> None:
        self._data_dir = Path(data_dir)
        self._errors: list[dict] = []
        self._last_check: float = 0.0

    def check_health(self) -> dict:
        """执行一次健康检查。"""
        self._last_check = time.time()
        report = {
            "timestamp": time.time(),
            "overall_score": 100,
            "checks": {},
            "warnings": [],
            "errors": [],
        }

        # 1. 检查数据目录
        if not self._data_dir.exists():
            report["errors"].append("数据目录不存在")
            report["overall_score"] -= 50

        # 2. 检查各存储文件
        critical_files = {
            "core.db": "核心记忆",
            "long_term.db": "长期记忆",
            "conversation_log.db": "对话日志",
        }
        for filename, name in critical_files.items():
            path = self._data_dir / filename
            if path.exists():
                size_mb = path.stat().st_size / (1024 * 1024)
                report["checks"][name] = {
                    "status": "ok",
                    "size_mb": round(size_mb, 2),
                }
                if size_mb > 100:
                    report["warnings"].append(f"{name}文件较大({size_mb:.0f}MB)")
                    report["overall_score"] -= 5
            else:
                report["checks"][name] = {"status": "missing"}
                report["warnings"].append(f"{name}文件不存在")
                report["overall_score"] -= 10

        # 3. 检查JSON文件
        json_files = list(self._data_dir.glob("*.json"))
        report["checks"]["json_files"] = {
            "status": "ok",
            "count": len(json_files),
        }

        # 4. 检查enhanced目录
        enhanced_dir = self._data_dir / "enhanced"
        if enhanced_dir.exists():
            enhanced_files = list(enhanced_dir.glob("*.json"))
            report["checks"]["enhanced"] = {
                "status": "ok",
                "count": len(enhanced_files),
            }
        else:
            report["checks"]["enhanced"] = {"status": "missing"}

        # 5. 检查磁盘空间（简单估算）
        total_size = sum(
            f.stat().st_size for f in self._data_dir.rglob("*") if f.is_file()
        )
        total_mb = total_size / (1024 * 1024)
        report["checks"]["total_size"] = {
            "status": "ok",
            "size_mb": round(total_mb, 2),
        }
        if total_mb > 500:
            report["warnings"].append(f"记忆数据总量较大({total_mb:.0f}MB)")
            report["overall_score"] -= 10

        report["overall_score"] = max(0, report["overall_score"])
        return report

    def record_error(self, module: str, error: str) -> None:
        """记录一个错误。"""
        self._errors.append({
            "module": module,
            "error": error,
            "time": time.time(),
        })
        if len(self._errors) > 100:
            self._errors = self._errors[-100:]

    def get_recent_errors(self, limit: int = 10) -> list[dict]:
        return self._errors[-limit:]

    @property
    def stats(self) -> dict:
        return {
            "last_check": self._last_check,
            "error_count": len(self._errors),
        }
