#pragma once

#include <QJsonObject>
#include <QList>
#include <QString>

#include <optional>

namespace workbox::original_site {

enum class Role { material, code };
enum class Stage { ready, working, completed, returned, unknown };

struct Account {
    QString username;
    QString name;
    Role role;
    qint64 version;
};

struct Task {
    QString id;
    QString title;
    QString owner;
    QString updated;
    QString rawStatus;
    QString revision;
    Stage stage;
};

struct Snapshot {
    std::optional<Account> account;
    QList<Task> tasks;
};

QString canonicalUsername(const QString& username);

// The browser filters by material owner before returning this wire reply.
Snapshot decodeSnapshot(const QJsonObject& reply);
QList<Task> activeTasks(const Snapshot& snapshot);

}  // namespace workbox::original_site
