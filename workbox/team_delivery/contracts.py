"""桌面、交件处理和网页操作共用的业务数据。"""

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol


class Stage(str, Enum):
    READY = '待制作'
    WORKING = '进行中'
    COMPLETED = '材料完成'
    RETURNED = '预审退回'
    UNKNOWN = '待核对'


class Outcome(str, Enum):
    SUBMITTED = '已提交'
    NEEDS_SELECTION = '请选择任务'
    PENDING = '待核对'
    BLOCKED = '无法交件'


@dataclass(frozen=True, slots=True)
class Account:
    id: str
    name: str
    role: str  # material、code 或其他只读角色
    session: str
    username: str = ''


@dataclass(frozen=True, slots=True)
class Handoff:
    id: str
    actor_id: str
    request_id: str


@dataclass(frozen=True, slots=True)
class Task:
    id: str
    title: str
    owner_id: str
    stage: Stage
    revision: str
    handoff: Handoff | None = None


@dataclass(frozen=True, slots=True)
class Snapshot:
    account: Account | None
    tasks: tuple[Task, ...] = ()


@dataclass(frozen=True, slots=True)
class Material:
    name: str
    path: Path
    sha256: str


@dataclass(frozen=True, slots=True)
class DropResult:
    outcome: Outcome
    message: str
    task_id: str | None = None
    material: Material | None = None
    receipt_id: str | None = None
    prepared: bool = False
    prepared_task: Task | None = None


class GatewayError(RuntimeError):
    """给当前员工看的受控操作结果。"""


class RefreshDeferred(GatewayError):
    """网页暂不适合同步，保留已有且仍属于当前账号的任务。"""


class ActionUncertain(GatewayError):
    """动作可能已执行，必须回读再判断。"""


class Gateway(Protocol):
    def snapshot(self) -> Snapshot: ...

    def task(self, task_id: str) -> Task | None: ...

    def begin(self, account: Account, task: Task, request_id: str) -> str:
        """返回该请求实际产生的版本，重放仍返回同一版本。"""
        ...

    def submit(self, account: Account, task: Task, request_id: str, material: Material) -> None: ...
