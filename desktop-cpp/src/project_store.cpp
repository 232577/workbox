#include "project_store.hpp"

#ifdef _WIN32
#include <winsqlite/winsqlite3.h>
#else
#include <sqlite3.h>
#endif

#include <array>
#include <stdexcept>
#include <unordered_set>

namespace workbox::prototype {
namespace {

constexpr std::array<std::string_view, 4> statusNames{"待开发", "开发中", "待交付", "已交付"};

constexpr const char* listingSql = R"sql(
    WITH search(term) AS (SELECT lower(?))
    SELECT p.id, p.name, p.status, p.urgent, d.due_date, b.name, pb.project_id IS NOT NULL
    FROM projects p JOIN batches b ON b.id=p.batch_id
    LEFT JOIN project_details d ON d.project_id=p.id
    LEFT JOIN project_deletions x ON x.project_id=p.id
    LEFT JOIN project_change_blocks pb ON pb.project_id=p.id
    CROSS JOIN search
    WHERE d.archived_at IS NULL AND x.deleted_at IS NULL
        AND NOT EXISTS (SELECT 1 FROM project_merges pm WHERE pm.source_id=p.id)
        AND (search.term=''
            OR instr(lower(p.name),search.term)>0
            OR instr(lower(b.name),search.term)>0
            OR instr(lower(COALESCE(d.notes,'')),search.term)>0
            OR EXISTS (SELECT 1 FROM zips z JOIN zip_project_links l ON l.zip_id=z.id
                       WHERE l.project_id=p.id AND instr(lower(z.filename),search.term)>0))
    ORDER BY p.urgent DESC, d.due_date IS NULL, d.due_date ASC, p.id DESC
)sql";

[[noreturn]] void fail(sqlite3* connection, const char* context) {
    throw std::runtime_error(std::string(context) + " " +
                             (connection ? sqlite3_errmsg(connection) : "SQLite"));
}

void requireOk(int result, sqlite3* connection, const char* context) {
    if (result != SQLITE_OK) {
        fail(connection, context);
    }
}

void execute(sqlite3* connection, const char* sql) {
    requireOk(sqlite3_exec(connection, sql, nullptr, nullptr, nullptr),
              connection, "数据库操作失败。");
}

class Statement {
public:
    Statement(sqlite3* connection, const std::string& sql) : connection_(connection) {
        sqlite3_stmt* statement = nullptr;
        const int result = sqlite3_prepare_v2(connection, sql.c_str(), -1, &statement, nullptr);
        statement_.reset(statement);
        requireOk(result, connection, "无法读取 WorkBox 数据结构。");
    }

    void bind(int index, std::string_view value) {
        requireOk(sqlite3_bind_text(statement_.get(), index, value.empty() ? "" : value.data(),
                                   static_cast<int>(value.size()), SQLITE_TRANSIENT),
                  connection_, "无法绑定查询文字。");
    }

    void bind(int index, std::int64_t value) {
        requireOk(sqlite3_bind_int64(statement_.get(), index, value),
                  connection_, "无法绑定项目编号。");
    }

    bool next() {
        const int result = sqlite3_step(statement_.get());
        if (result == SQLITE_ROW) {
            return true;
        }
        if (result != SQLITE_DONE) {
            fail(connection_, "无法执行项目查询。");
        }
        return false;
    }

    std::string text(int column) const {
        const auto* bytes = sqlite3_column_text(statement_.get(), column);
        if (!bytes) {
            return {};
        }
        return {reinterpret_cast<const char*>(bytes),
                static_cast<std::size_t>(sqlite3_column_bytes(statement_.get(), column))};
    }

    std::int64_t integer(int column) const {
        return sqlite3_column_int64(statement_.get(), column);
    }

private:
    struct Finalize {
        void operator()(sqlite3_stmt* statement) const noexcept { sqlite3_finalize(statement); }
    };
    sqlite3* connection_;
    std::unique_ptr<sqlite3_stmt, Finalize> statement_;
};

class WriteTransaction {
public:
    explicit WriteTransaction(sqlite3* connection) : connection_(connection) {
        execute(connection_, "BEGIN IMMEDIATE");
    }

    ~WriteTransaction() {
        if (!committed_) {
            sqlite3_exec(connection_, "ROLLBACK", nullptr, nullptr, nullptr);
        }
    }

    void commit() {
        execute(connection_, "COMMIT");
        committed_ = true;
    }

private:
    sqlite3* connection_;
    bool committed_ = false;
};

Status parseStatus(const std::string& value) {
    for (std::size_t index = 0; index < statusNames.size(); ++index) {
        if (value == statusNames[index]) {
            return static_cast<Status>(index);
        }
    }
    throw std::runtime_error("项目包含不支持的状态。");
}

std::string_view editableStatusName(Status target) {
    switch (target) {
    case Status::pending:
        return statusNames[0];
    case Status::active:
        return statusNames[1];
    case Status::ready:
        return statusNames[2];
    default:
        throw std::invalid_argument("批量操作仅支持待开发、开发中和待交付。");
    }
}

std::string_view trimSearch(std::string_view query) {
    constexpr std::string_view whitespace = " \t\n\r\f\v";
    const auto first = query.find_first_not_of(whitespace);
    if (first == std::string_view::npos) {
        return {};
    }
    return query.substr(first, query.find_last_not_of(whitespace) - first + 1);
}

std::string placeholders(std::size_t count) {
    std::string result = "?";
    for (std::size_t i = 1; i < count; ++i) {
        result += ",?";
    }
    return result;
}

void bindIds(Statement& statement, std::span<const std::int64_t> ids, int index) {
    for (const auto id : ids) {
        statement.bind(index++, id);
    }
}

}  // namespace

void ProjectStore::CloseDatabase::operator()(sqlite3* connection) const noexcept {
    sqlite3_close(connection);
}

ProjectStore::ProjectStore(const std::filesystem::path& source) {
    if (!std::filesystem::is_regular_file(source)) {
        throw std::invalid_argument("请选择一个已有的匿名 WorkBox 数据库副本。");
    }
    sqlite3* original = nullptr;
    const auto path = source.u8string();
    int result = sqlite3_open_v2(reinterpret_cast<const char*>(path.c_str()), &original,
                                 SQLITE_OPEN_READONLY | SQLITE_OPEN_PRIVATECACHE, nullptr);
    std::unique_ptr<sqlite3, CloseDatabase> input(original);
    requireOk(result, original, "无法以只读方式打开数据库。");

    sqlite3* snapshot = nullptr;
    result = sqlite3_open_v2(":memory:", &snapshot, SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE |
                            SQLITE_OPEN_PRIVATECACHE, nullptr);
    connection_.reset(snapshot);
    requireOk(result, snapshot, "无法创建内存副本。");

    sqlite3_backup* backup = sqlite3_backup_init(snapshot, "main", original, "main");
    if (!backup) {
        fail(snapshot, "无法读取数据库副本。");
    }
    const int copyResult = sqlite3_backup_step(backup, -1);
    const int finishResult = sqlite3_backup_finish(backup);
    if (copyResult != SQLITE_DONE || finishResult != SQLITE_OK) {
        fail(snapshot, "无法完成数据库复制，请检查输入是否可读。");
    }
    execute(snapshot, "PRAGMA foreign_keys=ON");
    Statement version(snapshot, "PRAGMA user_version");
    if (!version.next() || version.integer(0) != 1) {
        throw std::runtime_error("此数据库版本尚未在 C++ 原型中验证。");
    }
    const Statement schema(snapshot, listingSql);
    const Statement statusFields(snapshot,
                                 "SELECT delivered_at, delivered_zip_id, updated_at FROM projects LIMIT 0");
}

ProjectListing ProjectStore::listProjects(std::string_view search, std::optional<Status> status) const {
    search = trimSearch(search);
    if (search.size() > 4000) {
        throw std::invalid_argument("搜索文字过长。");
    }
    Statement statement(connection_.get(), listingSql);
    statement.bind(1, search);
    ProjectListing result;
    while (statement.next()) {
        const auto state = parseStatus(statement.text(2));
        ++result.statusCounts[static_cast<std::size_t>(state)];
        if (!status || state == *status) {
            result.projects.push_back({statement.integer(0), statement.text(1), state,
                                       statement.integer(3) != 0, statement.text(4),
                                       statement.text(5), statement.integer(6) != 0});
        }
    }
    return result;
}

void ProjectStore::setProjectStatus(std::span<const std::int64_t> ids, Status target,
                                    std::span<const std::int64_t> confirmedDelivered) {
    if (ids.empty() || ids.size() > 500) {
        throw std::invalid_argument("请选择 1 至 500 个不同的项目。");
    }
    std::unordered_set<std::int64_t> unique;
    for (const auto id : ids) {
        if (id <= 0 || !unique.insert(id).second) {
            throw std::invalid_argument("批量操作包含无效或重复的项目编号。");
        }
    }
    const auto targetName = editableStatusName(target);
    const std::unordered_set<std::int64_t> permitted(confirmedDelivered.begin(), confirmedDelivered.end());
    const auto inClause = placeholders(ids.size());
    auto* connection = connection_.get();
    WriteTransaction transaction(connection);
    {
        Statement checks(connection, R"sql(
            SELECT p.id, p.status,
                EXISTS (SELECT 1 FROM project_details d WHERE d.project_id=p.id AND d.archived_at IS NOT NULL)
                OR EXISTS (SELECT 1 FROM project_deletions x WHERE x.project_id=p.id)
                OR EXISTS (SELECT 1 FROM project_merges m WHERE m.source_id=p.id)
                OR EXISTS (SELECT 1 FROM project_change_blocks b WHERE b.project_id=p.id)
            FROM projects p WHERE p.id IN (
        )sql" + inClause + ")");
        bindIds(checks, ids, 1);
        std::size_t count = 0;
        while (checks.next()) {
            if (checks.integer(2) != 0) {
                throw std::runtime_error("所选项目中有已归档、已删除、已合并或等待目录恢复的项目。");
            }
            if (parseStatus(checks.text(1)) == Status::delivered && !permitted.contains(checks.integer(0))) {
                throw std::runtime_error("所选项目中有未经确认的已交付项目，请核对后再试。");
            }
            ++count;
        }
        if (count != ids.size()) {
            throw std::runtime_error("部分所选项目已不存在，请刷新清单。");
        }
    }

    Statement update(connection, R"sql(
        UPDATE projects SET status=?, delivered_at=NULL, delivered_zip_id=NULL,
            updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id IN (
    )sql" + inClause + ")");
    update.bind(1, targetName);
    bindIds(update, ids, 2);
    update.next();
    if (sqlite3_changes(connection) != static_cast<int>(ids.size())) {
        throw std::runtime_error("批量状态写入不完整，已撤销本次操作。");
    }
    transaction.commit();
}

}  // namespace workbox::prototype
