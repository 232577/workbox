"""Select the only available material set for a one-click delivery."""

from workbox.storage.delivery_archive import ProjectDeliveryArchive
from workbox.storage.native_files import plain_path


def resolve_board_delivery(service, project_id):
    """Choose a source. The existing delivery worker verifies contents before committing."""
    project = service.get_project(project_id)
    workspace = project.get('workspace')
    working_files = False
    if workspace and plain_path(workspace['path']).is_dir():
        working_files = ProjectDeliveryArchive.has_workspace_files(service, project_id)

    archives = [version['id'] for version in project['zips']
                if version.get('bundle_state') != 'needs-review' and plain_path(version['path']).is_file()]
    if len(archives) + int(working_files) > 1:
        raise ValueError('此项目有多个可交付版本。请在「全部项目」打开该项目，点「确认交付」选择材料。')
    if working_files:
        return None
    if archives:
        return archives[0]
    raise ValueError('此项目还没有可交付的工作文件或已核对的 ZIP。')
