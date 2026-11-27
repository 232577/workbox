"""Find eligible original-site tasks for a Word dropped in personal mode."""

from pathlib import Path

from workbox.projects.project_names import original_site_name_match
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
    ranked = [(original_site_name_match(path.stem, task.title), task) for task in tasks]
    best = max((rank for rank, _ in ranked), default=0)
    return tuple(task for rank, task in ranked if rank and rank == best)
