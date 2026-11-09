"""Batch-label work grows with source batches, not repeated project membership."""
import project_batches
from database import Database


def test_shared_creation_batch_dates_are_not_reparsed_for_every_project(tmp_path, monkeypatch):
    db = Database(tmp_path / 'anonymous.db')
    for batch in range(3):
        db.create_capture(f'匿名消息{batch}', f'匿名批次{batch}',
                          [(f'匿名项目{batch}-{i}', False) for i in range(100)])
    original = project_batches._date_parts
    parsed_dates = []

    def observed(value):
        parsed_dates.append(value)
        return original(value)

    monkeypatch.setattr(project_batches, '_date_parts', observed)
    with db.connect() as connection:
        connection.execute('BEGIN')
        index = project_batches.read_project_batch_index(connection)
    assert len(index['by_project']) == 300
    assert {marker['key']: marker['total_count'] for marker in index['options']} == {
        'batch:1': 100, 'batch:2': 100, 'batch:3': 100}
    assert index['warnings'] == []
    assert len(parsed_dates) <= 4 * len(index['options']), (
        'Shared batch dates must not be parsed once per project and again per sort')

    # Per-read reuse must not freeze status totals across subsequent reads.
    db.set_status(1, '待交付')
    with db.connect() as connection:
        connection.execute('BEGIN')
        updated = project_batches.read_project_batch_index(connection)
    first = next(marker for marker in updated['options'] if marker['key'] == 'batch:1')
    assert first['pending_delivery_count'] == 1
    assert first['remaining_count'] == 100
    assert first['delivered_count'] == 0
