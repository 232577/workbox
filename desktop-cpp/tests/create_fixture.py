"""Generate an anonymous, real WorkBox-schema database for the native prototype."""

import argparse
from contextlib import closing
from hashlib import sha256
from pathlib import Path
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from workbox.projects.database import Database


def create_fixture(path: Path, extra_projects: int = 0) -> None:
    with path.open("xb"):
        pass
    db = Database(path)
    with db.connect() as connection:
        connection.execute("INSERT INTO batches(id,name) VALUES (1,'Sample 测试批次')")
        connection.execute("INSERT INTO messages(id,batch_id,raw_text) VALUES (1,1,'匿名原型数据')")
        projects = [
            (1, "匿名测试_Alpha", "待开发", 0),
            (2, "匿名测试百分比%", "开发中", 1),
            (3, "匿名测试待交件", "待交付", 0),
            (4, "匿名测试已交件", "已交付", 0),
            (5, "匿名已归档", "待开发", 0),
            (6, "匿名已删除", "待开发", 0),
            (7, "匿名已合并", "待开发", 0),
            (8, "匿名等待恢复", "待开发", 0),
        ]
        states = ("待开发", "开发中", "待交付", "已交付")
        projects.extend(
            (number, f"匿名基准项目 {number:04}", states[number % 4], 0)
            for number in range(9, 9 + extra_projects)
        )
        connection.executemany(
            "INSERT INTO projects(id,message_id,batch_id,name,status,urgent) VALUES (?,1,1,?,?,?)",
            projects,
        )
        connection.execute("""INSERT INTO project_details(project_id,notes,due_date,archived_at) VALUES
            (1,'review note','2026-12-20',NULL),
            (3,'','2026-12-10',NULL),
            (5,'',NULL,'2026-11-01T00:00:00Z')""")
        connection.execute("INSERT INTO project_deletions(project_id) VALUES (6)")
        connection.execute("""INSERT INTO project_merges(source_id,target_id,operation_id,snapshot_json)
            VALUES (7,1,'prototype-merge','{}')""")
        connection.execute("""INSERT INTO project_change_blocks
            VALUES (8,'prototype-recovery','待恢复匿名目录')""")
        connection.executemany(
            """INSERT INTO zips(id,project_id,path,filename,size,sha256,mtime_ns)
               VALUES (?,4,?,'Fixture_SHARED.zip',1,?,0)""",
            [(1, "prototype-copy-a.zip", "a" * 64), (2, "prototype-copy-b.zip", "b" * 64)],
        )
        connection.execute("""UPDATE projects SET delivered_at='2026-11-01T01:00:00Z',
            delivered_zip_id=1 WHERE id=4""")
        connection.execute("""INSERT INTO delivery_events(project_id,project_name,batch_name,kind,zip_id,archive_path)
            VALUES (4,'匿名测试已交件','Sample 测试批次','manual-zip',1,'fixture-archive.zip')""")


def run_test(executable: Path, extra_projects: int) -> None:
    with TemporaryDirectory(prefix="workbox-cpp-") as folder:
        source = Path(folder) / "匿名项目.db"
        create_fixture(source, extra_projects)
        incompatible = Path(folder) / "incompatible.db"
        with closing(sqlite3.connect(incompatible)) as db:
            db.execute("PRAGMA user_version=1")
        before = sha256(source.read_bytes()).digest()
        subprocess.run([str(executable), str(source), str(incompatible)], check=True)
        if sha256(source.read_bytes()).digest() != before:
            raise AssertionError("The prototype modified its input database.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path, nargs="?")
    parser.add_argument("--extra-projects", type=int, default=0)
    parser.add_argument("--test", type=Path)
    args = parser.parse_args()
    if args.test and args.path is None and args.extra_projects >= 0:
        run_test(args.test, args.extra_projects)
    elif args.path and args.test is None and args.extra_projects >= 0:
        create_fixture(args.path, args.extra_projects)
    else:
        parser.error("Specify a fresh fixture path or --test with a C++ test executable.")
