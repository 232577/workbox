"""Find eligible original-site tasks for a Word dropped in personal mode."""

from pathlib import Path

from workbox.projects.project_names import project_name_key, versionless_project_key
from workbox.team_delivery.contracts import MATERIAL_PREPARATION_STAGES, Snapshot, Task


def matching_personal_word_tasks(filename: str, snapshot: Snapshot) -> tuple[Task, ...]:
    path = Path(filename)
    if path.suffix.casefold() != '.docx' or snapshot.account is None:
        return ()
    tasks = tuple(task for task in snapshot.tasks if task.owner_id == snapshot.account.id
                  and task.stage in MATERIAL_PREPARATION_STAGES)
    exact = tuple(task for task in tasks if task.title == path.stem)
    if exact:
        return exact
    name = project_name_key(path.stem)
    full_name = tuple(task for task in tasks if project_name_key(task.title) == name)
    if full_name or versionless_project_key(path.stem) is not None:
        return full_name
    return tuple(task for task in tasks if versionless_project_key(task.title) == name)
