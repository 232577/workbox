#include "original_site_snapshot.hpp"

#include <QJsonArray>

#include <array>
#include <iostream>
#include <stdexcept>

namespace {

using namespace workbox::original_site;

void require(bool accepted, const char* message)
{
    if (!accepted) {
        throw std::runtime_error(message);
    }
}

QJsonObject account(const QString& username = QStringLiteral("FiXTURE"),
                    const QString& role = QStringLiteral("材料"))
{
    return {{QStringLiteral("username"), username},
            {QStringLiteral("name"), QStringLiteral("账号显示名")},
            {QStringLiteral("role"), role},
            {QStringLiteral("version"), 3}};
}

QJsonObject task(const QString& id, const QString& status,
                 const QString& title = QStringLiteral("匿名项目 · 范例"))
{
    return {{QStringLiteral("id"), id}, {QStringLiteral("title"), title},
            {QStringLiteral("status"), status},
            {QStringLiteral("owner"), QStringLiteral("显示姓名")},
            {QStringLiteral("updated"), QStringLiteral("2026-11-26T12:00:00+08:00")}};
}

QJsonObject reply(const QJsonValue& who, const QJsonArray& rows)
{
    return {{QStringLiteral("account"), who}, {QStringLiteral("rows"), rows}};
}

void rejects(const QJsonObject& input, const char* message)
{
    try {
        decodeSnapshot(input);
    } catch (const std::invalid_argument&) {
        return;
    }
    require(false, message);
}

void validateSnapshot()
{
    const auto snapshot = decodeSnapshot(reply(account(), {
        task(QStringLiteral("201"), QStringLiteral("待制作")),
        task(QStringLiteral("202"), QStringLiteral("进行中")),
        task(QStringLiteral("203"), QStringLiteral("预审退回")),
        task(QStringLiteral("204"), QStringLiteral("已完成")),
        task(QStringLiteral("205"), QStringLiteral("其他状态"))
    }));
    require(snapshot.account.has_value(), "A material account was discarded.");
    require(snapshot.account->role == Role::material && snapshot.account->version == 3,
            "The authenticated role or account version was lost.");
    require(snapshot.account->username == QStringLiteral("FiXTURE"), "The original username was changed.");
    require(snapshot.tasks.size() == 5, "Tasks with an identical display title must retain distinct IDs.");
    require(snapshot.tasks.first().owner == QStringLiteral("显示姓名"),
            "The owner display label must not be interpreted as the account username.");
    require(snapshot.tasks.first().revision == QStringLiteral(
            "observed-1628bb1e0de7bf633a8e4a927189d6de3b2153c5cedd2fbdc83e374b4c24286e"),
            "The digest differs from the currently deployed Python format.");
    require(snapshot.tasks.at(3).stage == Stage::completed && snapshot.tasks.at(4).stage == Stage::unknown,
            "Completed and unrecognized material states must be distinguishable.");
    const auto active = activeTasks(snapshot);
    require(active.size() == 3 && active.at(0).stage == Stage::ready
            && active.at(1).stage == Stage::working && active.at(2).stage == Stage::returned,
            "The delivery-completed and unknown stages must not be imported as active tasks.");

    auto unicode = task(QStringLiteral("9007199254740991"), QStringLiteral("预审退回"),
                        QStringLiteral("《匿名资料 \"方案\" \\ 甲🔧》"));
    unicode.insert(QStringLiteral("owner"), QStringLiteral("名字, 负责人"));
    unicode.insert(QStringLiteral("updated"), QStringLiteral("\n\v\t\u2028"));
    const auto unicodeSnapshot = decodeSnapshot(reply(
        account(QStringLiteral(" CAF E\u0301 ")), {unicode}));
    require(canonicalUsername(unicodeSnapshot.account->username) == QStringLiteral("caf é"),
            "Unicode normalization, trim, or case did not match the site identity.");
    require(unicodeSnapshot.tasks.first().revision == QStringLiteral(
            "observed-9142b2f9cb531aa8e2285a8b27b709aa6eb4c37e9dca60a432b5cd681fb87e3e"),
            "The Python digest differs for Unicode and escaped JSON characters.");
}

void validateEmptyAndMalformedSnapshots()
{
    const auto signedOut = decodeSnapshot(reply(QJsonValue::Null, {}));
    require(!signedOut.account && signedOut.tasks.isEmpty() && activeTasks(signedOut).isEmpty(),
            "Signed-out replies must remain distinguishable from empty account snapshots.");
    const auto materialEmpty = decodeSnapshot(reply(account(), {}));
    require(materialEmpty.account && materialEmpty.tasks.isEmpty(), "A verified empty list was lost.");
    const auto code = decodeSnapshot(reply(account(QStringLiteral("code-fixture"), QStringLiteral("代码")), {}));
    require(code.account && code.account->role == Role::code && activeTasks(code).isEmpty(),
            "Code accounts cannot provide active material tasks.");

    const auto ready = task(QStringLiteral("201"), QStringLiteral("待制作"));
    auto error = reply(QJsonValue::Null, {});
    error.insert(QStringLiteral("error"), QStringLiteral("account"));
    rejects(error, "A blocked authentication reply was treated as a successful empty result.");
    rejects({}, "A missing account and rows were treated as a signed-out reply.");
    rejects(reply(QJsonValue::Null, {ready}), "A signed-out reply exposed material rows.");
    rejects(reply(account(QStringLiteral("code-fixture"), QStringLiteral("代码")), {ready}),
            "A code account exposed material rows.");
    rejects(reply(account(QStringLiteral("account"), QStringLiteral("other")), {}),
            "An unknown role was accepted.");
    rejects(reply(account(), {ready, ready}), "A duplicate original-site task was accepted.");
    rejects(reply(account(), {ready, 99}), "A malformed second task was silently dropped.");
    QJsonObject badAccount = account();
    badAccount.insert(QStringLiteral("version"), 1.5);
    rejects(reply(badAccount, {}), "Invalid account generation was accepted.");

    for (const auto& id : std::array{QString(), QStringLiteral("01"), QStringLiteral("9007199254740992"),
                                    QStringLiteral("٢٠١"), QStringLiteral("-4")}) {
        rejects(reply(account(), {task(id, QStringLiteral("待制作"))}),
                "A task ID outside canonical safe JSON integer range was accepted.");
    }
    auto missingOwner = ready;
    missingOwner.remove(QStringLiteral("owner"));
    rejects(reply(account(), {missingOwner}), "Missing owner evidence was accepted.");
    auto badTitle = ready;
    badTitle.insert(QStringLiteral("title"), QStringLiteral("项目\n另起一行"));
    rejects(reply(account(), {badTitle}), "A malformed task title was accepted.");
}

}  // namespace

int main()
{
    try {
        validateSnapshot();
        validateEmptyAndMalformedSnapshots();
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
