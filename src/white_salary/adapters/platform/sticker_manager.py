"""
white_salary/adapters/platform/sticker_manager.py

QQ表情包管理器 — 管理和发送自定义表情包。

借鉴v2的sticker_manager.py：
  - v2的轮换选择（round-robin + shuffle）防重复，很好，保留
  - v2用LLM自动描述表情包内容，保留
  - v2每2小时扫描新表情，我们改为启动时扫描一次
  - v2把sticker.json和图片放在data/下，我们也用同样的结构

功能：
  - 自动扫描data/sticker/目录下的图片
  - 每个表情包有描述（可LLM自动生成或手动填写）
  - 轮换选择防止重复发送同一个表情
  - QQ发送时转为CQ码格式
"""

import base64
import json
import random
from pathlib import Path
from typing import Optional

from loguru import logger


class StickerManager:
    """
    表情包管理器。

    使用方式:
        sm = StickerManager(data_dir="data")
        sm.init()
        sticker_id = sm.get_next()  # 获取下一个表情包ID
        cq_code = sm.to_cq_code(sticker_id)  # 转为CQ码
    """

    def __init__(self, data_dir: str = "data") -> None:
        self._sticker_dir = Path(data_dir) / "sticker"
        self._sticker_dir.mkdir(parents=True, exist_ok=True)
        self._config_path = Path(data_dir) / "config" / "sticker.json"
        self._config_path.parent.mkdir(parents=True, exist_ok=True)

        self._stickers: dict[str, dict] = {}  # id -> {desc, path}
        self._order: list[str] = []            # 轮换顺序
        self._cursor: int = 0                  # 当前位置

    def init(self) -> int:
        """初始化：加载配置 + 扫描新表情。返回表情包总数。"""
        self._load_config()
        new_count = self._scan_new()
        if new_count > 0:
            logger.info(f"[Sticker] 发现 {new_count} 个新表情包")
            self._save_config()

        # 初始化轮换顺序
        self._order = list(self._stickers.keys())
        random.shuffle(self._order)
        self._cursor = 0

        logger.info(f"[Sticker] 已加载 {len(self._stickers)} 个表情包")
        return len(self._stickers)

    def get_next(self) -> Optional[str]:
        """获取下一个表情包ID（轮换选择）。"""
        if not self._order:
            return None

        if self._cursor >= len(self._order):
            random.shuffle(self._order)
            self._cursor = 0

        sticker_id = self._order[self._cursor]
        self._cursor += 1
        return sticker_id

    def get_path(self, sticker_id: str) -> Optional[Path]:
        """获取表情包的文件路径。"""
        info = self._stickers.get(sticker_id)
        if not info:
            return None
        path = self._sticker_dir / info["path"]
        return path if path.exists() else None

    def get_description(self, sticker_id: str) -> str:
        """获取表情包描述。"""
        info = self._stickers.get(sticker_id)
        return info.get("desc", "") if info else ""

    def to_cq_code(self, sticker_id: str) -> Optional[str]:
        """将表情包转为QQ CQ码格式（base64图片）。"""
        path = self.get_path(sticker_id)
        if not path:
            return None

        try:
            img_data = path.read_bytes()
            b64 = base64.b64encode(img_data).decode()
            return f"[CQ:image,file=base64://{b64}]"
        except Exception as e:
            logger.warning(f"[Sticker] 读取表情包失败 {sticker_id}: {e}")
            return None

    def to_cq_random(self) -> Optional[str]:
        """随机选一个表情包并转为CQ码。"""
        sid = self.get_next()
        if sid:
            return self.to_cq_code(sid)
        return None

    def register(self, filename: str, description: str = "") -> str:
        """注册一个新表情包。返回分配的ID。"""
        # 自增ID
        max_id = max((int(k) for k in self._stickers if k.isdigit()), default=0)
        new_id = str(max_id + 1)

        self._stickers[new_id] = {
            "desc": description,
            "path": filename,
        }
        self._order.append(new_id)
        self._save_config()
        return new_id

    @property
    def count(self) -> int:
        return len(self._stickers)

    def _scan_new(self) -> int:
        """扫描sticker目录，注册未登记的图片。"""
        known_paths = {info["path"] for info in self._stickers.values()}
        new_count = 0

        for f in self._sticker_dir.iterdir():
            if f.suffix.lower() in (".jpg", ".jpeg", ".png", ".gif", ".webp"):
                if f.name not in known_paths:
                    self.register(f.name, description="")
                    new_count += 1

        return new_count

    def _load_config(self) -> None:
        if self._config_path.exists():
            try:
                data = json.loads(self._config_path.read_text(encoding="utf-8"))
                # 验证文件存在性
                for sid, info in data.items():
                    path = self._sticker_dir / info.get("path", "")
                    if path.exists():
                        self._stickers[sid] = info
            except Exception:
                pass

    def _save_config(self) -> None:
        try:
            self._config_path.write_text(
                json.dumps(self._stickers, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass
