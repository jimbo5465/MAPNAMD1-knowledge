"""پکیج db — دسترسی به پایگاه داده مستقل MAPNAMD1-knowledge."""
from db.models import (
    add_user, get_user_by_telegram_id, update_user,
    add_knowledge_entry, get_knowledge_entry_by_id,
    set_knowledge_fields, submit_knowledge_entry,
    set_knowledge_inactive, list_knowledge_entries,
    add_knowledge_photo, list_knowledge_photos,
    set_knowledge_interview_history, get_knowledge_interview_history,
    set_knowledge_tree_path, get_knowledge_tree_path,
    set_knowledge_org_metadata, get_knowledge_org_metadata,
    find_pending_knowledge_by_user,
    add_observation, list_observations_by_user,
    get_observation_by_id, update_observation,
    promote_observation, archive_observation,
)

__all__ = [
    "add_user", "get_user_by_telegram_id", "update_user",
    "add_knowledge_entry", "get_knowledge_entry_by_id",
    "set_knowledge_fields", "submit_knowledge_entry",
    "set_knowledge_inactive", "list_knowledge_entries",
    "add_knowledge_photo", "list_knowledge_photos",
    "set_knowledge_interview_history", "get_knowledge_interview_history",
    "set_knowledge_tree_path", "get_knowledge_tree_path",
    "set_knowledge_org_metadata", "get_knowledge_org_metadata",
    "find_pending_knowledge_by_user",
    "add_observation", "list_observations_by_user",
    "get_observation_by_id", "update_observation",
    "promote_observation", "archive_observation",
]