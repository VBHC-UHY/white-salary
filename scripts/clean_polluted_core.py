"""
scripts/clean_polluted_core.py

2026-07-02 审计修复（批4）：核心记忆污染清洗脚本。

背景（docs/audit-2026-07-02/memory-core.json 第1条 critical）：
  core_memory 表无 user_id 维度，QQ 群任意用户说"我叫X/我在X"即以
  importance=8 覆盖主人核心档案。生产数据实锤污染：
    - user_name     = 星月               （QQ群用户昵称覆盖主人姓名）
    - user_job      = 被禁言了才消停      （群聊残句被 user_job 正则误匹配）
    - user_location = B站发现了一个有趣的  （群聊残句被 user_location 正则误匹配）
    - like_你哦白~乖乖怀孕吧~″ = 我爱你哦白~乖乖怀孕吧~″（群聊残句被喜好正则误匹配）

清洗原则（保守）：
  只删除黑名单里 (key, value) 精确匹配的条目——即"字段值明显是聊天残句/
  非主人姓名"的核心档案；value 已变化的条目跳过并提示人工复核。
  同时打印全部核心条目供人工复核。

存储一致性：
  core_store 是 SQLite(core.db) + JSON(core.json) + TXT(core.txt) 三写。
  本脚本通过 CoreMemoryStore.delete() 删除——该方法删 SQLite 行、删内存缓存
  后重写 JSON 与 TXT，三处天然一致，无需分别操作。

用法（在项目根目录下执行）：
  python scripts/clean_polluted_core.py            # 默认 dry-run，只打印
  python scripts/clean_polluted_core.py --apply    # 真实删除
  可选 --data-dir 指定记忆目录（默认 <项目根>/data/memory）。
  脚本可重复执行：已删除的条目再次运行时自动跳过。
"""

import argparse
import sys
from pathlib import Path

# ================================================================
# 路径引导：不依赖 CWD，按脚本位置定位项目根并把 src 加入 sys.path
# ================================================================
_PROJECT_ROOT: Path = Path(__file__).resolve().parents[1]
_SRC_DIR: Path = _PROJECT_ROOT / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from white_salary.core.memory.core_store import CoreMemoryStore  # noqa: E402


# ================================================================
# 已知污染黑名单：(key, 污染时的value, 污染原因说明)
# 只有 key 与当前 value 都精确匹配才会删除（保守 + 幂等）
# ================================================================
POLLUTED_ENTRIES: list[tuple[str, str, str]] = [
    ("user_name", "星月",
     "QQ群用户'星月'的昵称覆盖了主人的user_name（8星核心记忆）"),
    ("user_job", "被禁言了才消停",
     "群聊残句被user_job正则误匹配（'我是/我做'正则垃圾）"),
    ("user_location", "B站发现了一个有趣的",
     "群聊残句被user_location正则误匹配（'我在'正则垃圾）"),
    ("like_你哦白~乖乖怀孕吧~″", "我爱你哦白~乖乖怀孕吧~″",
     "群聊残句被喜好正则误匹配（'我爱'正则垃圾）"),
]


def print_all_entries(store: CoreMemoryStore) -> None:
    """列出全部核心记忆条目，供人工复核（不做任何修改）。"""
    entries = store.get_all()
    print(f"\n===== 当前核心记忆全量清单（共 {len(entries)} 条，供人工复核）=====")
    for e in entries:
        print(f"  [{e.importance}★][{e.category}][{e.source}] {e.key} = {e.value}")
    print("=" * 60)


def collect_deletions(store: CoreMemoryStore) -> list[tuple[str, str, str]]:
    """
    收集本次将要删除的条目。

    Returns:
        [(key, 当前value, 原因)]，只包含 key 与 value 都精确匹配黑名单的条目
    """
    to_delete: list[tuple[str, str, str]] = []
    for key, polluted_value, reason in POLLUTED_ENTRIES:
        entry = store.get_entry(key)
        if entry is None:
            print(f"  [跳过] {key}: 条目不存在（可能已清理过）")
            continue
        if entry.value != polluted_value:
            print(
                f"  [跳过] {key}: 当前值 '{entry.value}' ≠ 黑名单值 "
                f"'{polluted_value}'，不自动删除，请人工复核"
            )
            continue
        to_delete.append((key, entry.value, reason))
    return to_delete


def main() -> int:
    parser = argparse.ArgumentParser(description="清洗被QQ群友污染的核心记忆档案条目")
    parser.add_argument(
        "--apply", action="store_true",
        help="真实执行删除（不带此参数为dry-run，只打印将删条目）",
    )
    parser.add_argument(
        "--data-dir", type=str, default=str(_PROJECT_ROOT / "data" / "memory"),
        help="记忆数据目录（默认 <项目根>/data/memory）",
    )
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    db_path = data_dir / "core.db"
    if not db_path.exists():
        print(f"[错误] 找不到核心记忆库: {db_path}")
        return 1

    store = CoreMemoryStore(data_dir=str(data_dir))
    print(f"核心记忆库: {db_path}（共 {store.count} 条）")

    # 1. 先打印全量清单供人工复核
    print_all_entries(store)

    # 2. 匹配黑名单
    print("\n===== 黑名单匹配结果 =====")
    to_delete = collect_deletions(store)

    if not to_delete:
        print("\n没有匹配到需要删除的污染条目（可能已全部清理过）。")
        return 0

    print(f"\n===== 将删除以下 {len(to_delete)} 条污染条目 =====")
    for key, value, reason in to_delete:
        print(f"  [删除] {key} = {value}")
        print(f"         原因: {reason}")

    # 3. dry-run / apply 分支
    if not args.apply:
        print("\n[dry-run] 未做任何修改。确认无误后加 --apply 真实执行。")
        return 0

    print("\n[apply] 开始真实删除（SQLite+JSON+TXT 三写同步）...")
    deleted = 0
    for key, value, _reason in to_delete:
        if store.delete(key):
            deleted += 1
            print(f"  [已删] {key} = {value}")
        else:
            print(f"  [失败] {key}: delete() 返回 False")
    print(f"\n完成：删除 {deleted}/{len(to_delete)} 条。当前核心记忆剩余 {store.count} 条。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
