"""任务来源和动作日志独立于项目、物理批次及交付事实。"""

SCHEMA = '''
CREATE TABLE IF NOT EXISTS task_groups (
 id INTEGER PRIMARY KEY, code TEXT NOT NULL UNIQUE, received_day TEXT NOT NULL,
 day_sequence INTEGER NOT NULL, title TEXT NOT NULL DEFAULT '', color TEXT NOT NULL,
 created_at TEXT NOT NULL, history_batch_id INTEGER UNIQUE REFERENCES batches(id),
 UNIQUE(received_day,day_sequence)
);
CREATE TABLE IF NOT EXISTS task_group_messages (
 id INTEGER PRIMARY KEY, group_id INTEGER NOT NULL REFERENCES task_groups(id),
 receipt_id INTEGER REFERENCES intake_receipts(id), raw_text TEXT NOT NULL,
 fingerprint TEXT NOT NULL, received_at TEXT NOT NULL, sequence INTEGER NOT NULL,
 request_id TEXT UNIQUE, request_payload TEXT, history_message_id INTEGER UNIQUE,
 UNIQUE(group_id,sequence)
);
CREATE TABLE IF NOT EXISTS task_items (
 id INTEGER PRIMARY KEY, code TEXT NOT NULL UNIQUE,
 original_group_id INTEGER NOT NULL REFERENCES task_groups(id), sequence INTEGER NOT NULL,
 project_id INTEGER NOT NULL REFERENCES projects(id), project_name TEXT NOT NULL,
 created_at TEXT NOT NULL, delivery_floor INTEGER NOT NULL,
 history_review INTEGER NOT NULL DEFAULT 0,
 UNIQUE(original_group_id,sequence)
);
CREATE TABLE IF NOT EXISTS task_message_links (
 message_id INTEGER NOT NULL REFERENCES task_group_messages(id),
 task_id INTEGER NOT NULL REFERENCES task_items(id), PRIMARY KEY(message_id,task_id)
);
CREATE TABLE IF NOT EXISTS task_receive_requests (
 request_id TEXT PRIMARY KEY, message_id INTEGER NOT NULL REFERENCES task_group_messages(id),
 payload TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS task_actions (
 id INTEGER PRIMARY KEY, task_id INTEGER NOT NULL REFERENCES task_items(id),
 kind TEXT NOT NULL CHECK(kind IN ('handoff','undo-handoff','withdraw','reopen','move')),
 binding_id INTEGER REFERENCES task_delivery_bindings(id),
 target_group_id INTEGER REFERENCES task_groups(id), reason TEXT NOT NULL DEFAULT '',
 delivery_floor INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS task_group_actions (
 id INTEGER PRIMARY KEY, group_id INTEGER NOT NULL REFERENCES task_groups(id),
 kind TEXT NOT NULL CHECK(kind IN ('title','color')), value TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS task_delivery_bindings (
 id INTEGER PRIMARY KEY, task_id INTEGER NOT NULL REFERENCES task_items(id),
 event_id INTEGER NOT NULL REFERENCES delivery_events(id), event_action_id INTEGER NOT NULL,
 task_action_id INTEGER NOT NULL, origin TEXT NOT NULL CHECK(origin IN ('automatic','explicit')),
 observed_event_floor INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS task_delivery_observations (
 event_id INTEGER PRIMARY KEY REFERENCES delivery_events(id),
 candidate_task_ids TEXT NOT NULL, result TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_task_items_project ON task_items(project_id);
CREATE INDEX IF NOT EXISTS idx_task_actions_task ON task_actions(task_id,id);
CREATE INDEX IF NOT EXISTS idx_task_bindings_task ON task_delivery_bindings(task_id,id);
'''


def initialize(connection):
    connection.executescript(SCHEMA)
    if 'delivery_floor' not in {r['name'] for r in connection.execute('PRAGMA table_info(task_actions)')}:
        connection.execute('ALTER TABLE task_actions ADD COLUMN delivery_floor INTEGER NOT NULL DEFAULT 0')
    if 'observed_event_floor' not in {r['name'] for r in connection.execute('PRAGMA table_info(task_delivery_bindings)')}:
        connection.execute('ALTER TABLE task_delivery_bindings ADD COLUMN observed_event_floor INTEGER NOT NULL DEFAULT 0')
    # 编号、原文、关联及动作均不可覆盖。派生状态通过追加动作计算。
    for table in ('task_groups', 'task_group_messages', 'task_items', 'task_message_links',
                  'task_actions', 'task_group_actions', 'task_delivery_bindings',
                  'task_delivery_observations', 'task_receive_requests'):
        for action in ('UPDATE', 'DELETE'):
            connection.execute(f'''CREATE TRIGGER IF NOT EXISTS {table}_no_{action.lower()}
                BEFORE {action} ON {table} BEGIN
                SELECT RAISE(ABORT,'任务来源与动作仅允许追加。'); END''')
