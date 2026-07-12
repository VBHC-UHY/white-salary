"""Conservative access policy for legacy built-in tool declarations."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BuiltinToolPolicy:
    platforms: tuple[str, ...] = ()
    requires_permission: str = "owner"
    requires_service: str = ""
    side_effect: bool = True


# These tools do not expose personal data and do not mutate local or remote state.
PUBLIC_READ_ONLY_TOOLS = {
    "get_current_time",
    "calculator",
    "random_number",
    "dice_roller",
    "regex",
    "deep_think",
    "web_search",
    "deep_search",
    "news_search",
    "research",
    "fetch_webpage",
    "bilibili_search",
    "bilibili_video_info",
    "get_video_info",
}


# These tools are read-only but expose owner, account, memory, or device data.
OWNER_READ_ONLY_TOOLS = {
    "affinity_check",
    "affinity_ranking",
    "affinity_history",
    "list_reminders",
    "bilibili_feed",
    "view_chat_history",
    "group_history",
    "qq_inbox",
    "view_learned_style",
    "query_knowledge_graph",
    "evaluate_person",
    "path_query",
    "describe_image",
    "memory_search",
    "recall_conversation",
    "learning_stats",
    "qq_get_msg",
    "qq_get_forward_msg",
    "qq_friend_list",
    "qq_group_list",
    "qq_group_member_list",
    "qq_group_member_info",
    "qq_stranger_info",
    "qq_group_info",
    "qq_login_info",
    "qq_group_honor",
    "qq_group_msg_history",
    "qq_recent_contact",
    "qq_group_files",
    "qq_group_file_url",
    "qq_group_file_info",
    "qq_get_record",
    "qq_friend_msg_history",
    "qq_get_essence",
    "qzone_get_feeds",
    "qzone_get_comments",
    "qzone_visit_space",
    "qzone_check_new_comments",
    "qzone_status",
    "check_blocked_users",
    "get_quiet_status",
}


QQ_CAPABLE_MODULES = {"chat", "qq_api", "qzone", "pc_control", "media", "video"}


def get_builtin_policy(module_name: str, tool_name: str) -> BuiltinToolPolicy:
    """Return a complete policy for every legacy built-in tool.

    Unknown built-ins fail closed: owner-only and side-effecting. This prevents
    a newly added tool from silently becoming available to every QQ user.
    """

    if tool_name in PUBLIC_READ_ONLY_TOOLS:
        return BuiltinToolPolicy(requires_permission="", side_effect=False)

    platforms: tuple[str, ...] = ()
    if module_name in QQ_CAPABLE_MODULES:
        platforms = ("desktop", "qq")

    if tool_name in OWNER_READ_ONLY_TOOLS:
        return BuiltinToolPolicy(
            platforms=platforms,
            requires_permission="owner",
            side_effect=False,
        )

    return BuiltinToolPolicy(
        platforms=platforms,
        requires_permission="owner",
        side_effect=True,
    )
