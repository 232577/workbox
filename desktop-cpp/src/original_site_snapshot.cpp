#include "original_site_snapshot.hpp"

#include <QCryptographicHash>
#include <QJsonArray>
#include <QJsonDocument>
#include <QSet>

#include <array>
#include <stdexcept>

namespace workbox::original_site {
namespace {

constexpr qint64 maxSiteInteger = 9'007'199'254'740'991;

QString readText(const QJsonObject& object, const QString& key, bool allowEmpty = false)
{
    const auto value = object.value(key);
    const auto text = value.toString();
    if (!value.isString() || !text.isValidUtf16() || (!allowEmpty && text.isEmpty())) {
        throw std::invalid_argument("原站待办或账号缺少可核对的文字。");
    }
    return text;
}

Role readRole(const QString& value)
{
    if (value == QStringLiteral("材料")) {
        return Role::material;
    }
    if (value == QStringLiteral("代码")) {
        return Role::code;
    }
    throw std::invalid_argument("原站账号角色不明确。");
}

Account readAccount(const QJsonObject& object)
{
    const auto username = readText(object, QStringLiteral("username"));
    const auto name = readText(object, QStringLiteral("name"));
    const auto role = readRole(readText(object, QStringLiteral("role")));
    const auto version = object.value(QStringLiteral("version")).toInteger(-1);
    if (canonicalUsername(username).isEmpty() || name.trimmed().isEmpty()
            || version < 0 || version > maxSiteInteger) {
        throw std::invalid_argument("原站账号信息不完整。");
    }
    return {username, name, role, version};
}

bool isSiteId(const QString& value)
{
    if (value.isEmpty() || value.size() > 16 || value.startsWith(QLatin1Char('0'))) {
        return false;
    }
    for (const auto character : value) {
        if (character < QLatin1Char('0') || character > QLatin1Char('9')) {
            return false;
        }
    }
    return value.toLongLong() <= maxSiteInteger;
}

Stage readStage(const QString& status)
{
    if (status == QStringLiteral("待制作")) {
        return Stage::ready;
    }
    if (status == QStringLiteral("进行中")) {
        return Stage::working;
    }
    if (status == QStringLiteral("已完成")) {
        return Stage::completed;
    }
    if (status == QStringLiteral("预审退回")) {
        return Stage::returned;
    }
    return Stage::unknown;
}

QString observedRevision(const Task& task, const QString& username)
{
    // Existing bindings may store this Python json.dumps() digest. Add its
    // default separator spaces outside the encoded UTF-8 JSON strings.
    const std::array fields{task.id, task.title, username, task.owner, task.updated, task.rawStatus};
    QByteArray input("[");
    for (std::size_t index = 0; index < fields.size(); ++index) {
        if (index != 0) {
            input += ", ";
        }
        const auto encoded = QJsonDocument(QJsonArray{fields[index]}).toJson(QJsonDocument::Compact);
        input += encoded.mid(1, encoded.size() - 2);
    }
    input += ']';
    return QStringLiteral("observed-") +
            QString::fromLatin1(QCryptographicHash::hash(input, QCryptographicHash::Sha256).toHex());
}

Task readTask(const QJsonObject& row, const QString& username)
{
    const auto id = readText(row, QStringLiteral("id"));
    const auto title = readText(row, QStringLiteral("title"));
    const auto owner = readText(row, QStringLiteral("owner"));
    const auto updated = readText(row, QStringLiteral("updated"), true);
    const auto status = readText(row, QStringLiteral("status"));
    const auto label = title.trimmed();
    if (!isSiteId(id) || label.isEmpty() || label.toUcs4().size() > 200
            || label.contains(QChar::Null) || label.contains(QLatin1Char('\r'))
            || label.contains(QLatin1Char('\n'))) {
        throw std::invalid_argument("原站任务编号或名称不完整。");
    }
    Task task{id, title, owner, updated, status, {}, readStage(status)};
    task.revision = observedRevision(task, username);
    return task;
}

}  // namespace

QString canonicalUsername(const QString& username)
{
    return username.normalized(QString::NormalizationForm_C).trimmed().toLower();
}

Snapshot decodeSnapshot(const QJsonObject& reply)
{
    if (reply.contains(QStringLiteral("error"))) {
        throw std::invalid_argument("原站没有返回可核对的待办。");
    }
    const auto account = reply.value(QStringLiteral("account"));
    const auto rows = reply.value(QStringLiteral("rows"));
    if ((!account.isNull() && !account.isObject()) || !rows.isArray()) {
        throw std::invalid_argument("原站待办响应格式不完整。");
    }
    Snapshot result;
    const auto entries = rows.toArray();
    if (!account.isNull()) {
        result.account = readAccount(account.toObject());
    }
    if (!result.account || result.account->role != Role::material) {
        if (!entries.isEmpty()) {
            throw std::invalid_argument("只有已登录的材料账号可以读取此待办。");
        }
        return result;
    }

    const auto username = canonicalUsername(result.account->username);
    QSet<QString> seen;
    result.tasks.reserve(entries.size());
    for (const auto& entry : entries) {
        if (!entry.isObject()) {
            throw std::invalid_argument("原站待办中包含无效任务。");
        }
        const auto task = readTask(entry.toObject(), username);
        if (seen.contains(task.id)) {
            throw std::invalid_argument("原站待办中有重复任务编号。");
        }
        seen.insert(task.id);
        result.tasks.append(task);
    }
    return result;
}

QList<Task> activeTasks(const Snapshot& snapshot)
{
    QList<Task> active;
    if (!snapshot.account || snapshot.account->role != Role::material) {
        return active;
    }
    for (const auto& task : snapshot.tasks) {
        if (task.stage == Stage::ready || task.stage == Stage::working || task.stage == Stage::returned) {
            active.append(task);
        }
    }
    return active;
}

}  // namespace workbox::original_site
