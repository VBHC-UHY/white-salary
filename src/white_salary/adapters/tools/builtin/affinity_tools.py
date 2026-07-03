"""好感度工具 — 查询/排行/历史。"""
import json
from pathlib import Path
from ._helpers import tool, P, S, NONE_PARAMS


@tool("affinity_check", "查询用户好感度等级和分数", P(user_id=S("用户ID")))
async def affinity_check(user_id: str = "default") -> str:
    try:
        from white_salary.core.affinity.manager import AffinityManager
        mgr = AffinityManager.get_for_user(user_id) if user_id != "default" else AffinityManager()
        s = mgr.get_stats()
        return f"好感度: {s['level_name']} ({s['points']}分) 连续{s.get('consecutive_days',0)}天"
    except Exception as e:
        return f"查询失败: {e}"


@tool("affinity_ranking", "好感度排行榜")
async def affinity_ranking() -> str:
    try:
        users_dir = Path("data/affinity/users")
        if not users_dir.exists():
            return "暂无数据"
        ranks = []
        for f in users_dir.glob("affinity_*.json"):
            data = json.loads(f.read_text(encoding="utf-8"))
            uid = f.stem.replace("affinity_", "")
            ranks.append((uid, data.get("points", 0)))
        ranks.sort(key=lambda x: x[1], reverse=True)
        if not ranks:
            return "暂无排行"
        return "好感度排行:\n" + "\n".join(f"  {i}. {uid}: {pts:.1f}分" for i, (uid, pts) in enumerate(ranks[:10], 1))
    except Exception as e:
        return f"获取失败: {e}"


@tool("affinity_history", "好感度变化历史", P(user_id=S("用户ID")))
async def affinity_history(user_id: str = "default") -> str:
    try:
        from white_salary.core.affinity.manager import AffinityManager
        mgr = AffinityManager.get_for_user(user_id) if user_id != "default" else AffinityManager()
        hist = mgr._affinity.recent_changes[-10:] if hasattr(mgr._affinity, 'recent_changes') else []
        if not hist:
            return "暂无变化记录"
        return "最近变化:\n" + "\n".join(f"  {h.get('reason','')}: {h.get('delta',0):+.1f}" for h in hist)
    except Exception as e:
        return f"获取失败: {e}"


TOOLS = [fn._tool_def for fn in [affinity_check, affinity_ranking, affinity_history]]
