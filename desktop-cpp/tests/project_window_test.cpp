#include "prototype_window.hpp"

#include <QApplication>
#include <QComboBox>
#include <QElapsedTimer>
#include <QFontDatabase>
#include <QItemSelectionModel>
#include <QLabel>
#include <QLineEdit>
#include <QPushButton>
#include <QTableView>
#include <QTest>

#include <algorithm>
#include <array>
#include <iostream>
#include <stdexcept>

namespace {

void check(bool condition, const char* message)
{
    if (!condition) {
        throw std::runtime_error(message);
    }
}

bool containsLabel(QWidget& window, const QString& text)
{
    for (const auto* label : window.findChildren<QLabel*>()) {
        if (label->text() == text) {
            return true;
        }
    }
    return false;
}

template<class Widget>
Widget& control(QWidget& window, const QString& accessibleName)
{
    for (auto* widget : window.findChildren<Widget*>()) {
        if (widget->accessibleName() == accessibleName) {
            return *widget;
        }
    }
    throw std::runtime_error("Missing prototype control.");
}

void clickProject(QTableView& table, int row, Qt::KeyboardModifiers modifiers)
{
    const auto cell = table.model()->index(row, 0);
    const auto rectangle = table.visualRect(cell);
    check(!rectangle.isEmpty(), "Could not reach the project row.");
    QTest::mouseClick(table.viewport(), Qt::LeftButton, modifiers, rectangle.center());
}

std::filesystem::path nativePath(const QString& path)
{
#ifdef _WIN32
    return std::filesystem::path(path.toStdWString());
#else
    return std::filesystem::path(path.toStdString());
#endif
}

void testWindow(const std::filesystem::path& source)
{
    QElapsedTimer elapsed;
    elapsed.start();
    const auto window = workbox::prototype::createPrototypeWindow(source);
    window->show();
    QApplication::processEvents();
    std::cout << "In-process window creation and first render: "
              << elapsed.nsecsElapsed() / 1e6 << " ms\n";

    auto& table = control<QTableView>(*window, QStringLiteral("项目清单"));
    auto& search = control<QLineEdit>(*window, QStringLiteral("搜索项目"));
    auto& filter = control<QComboBox>(*window, QStringLiteral("按状态筛选"));
    auto* apply = window->findChild<QPushButton*>();
    check(apply && apply->text() == QStringLiteral("更新所选项目"), "Missing apply button.");
    check(table.model()->rowCount() == 700, "The window did not show all 700 active projects.");
    check(!apply->isEnabled(), "Apply must be disabled with no selection.");

    search.setText(QStringLiteral("aLpHa"));
    QTest::qWait(250);
    check(table.model()->rowCount() == 1, "Search did not filter to the fixture row.");
    search.clear();
    filter.setCurrentIndex(2);
    check(table.model()->rowCount() == 175, "The status filter did not show all 175 active rows.");
    filter.setCurrentIndex(0);
    check(table.model()->rowCount() == 700, "Clearing filters did not restore all rows.");

    clickProject(table, 0, Qt::NoModifier);
    clickProject(table, 1, Qt::ControlModifier);
    check(containsLabel(*window, QStringLiteral("已选 2 项")), "Ctrl multi-selection failed.");
    clickProject(table, 0, Qt::NoModifier);
    clickProject(table, 1, Qt::ShiftModifier);
    check(containsLabel(*window, QStringLiteral("已选 2 项")), "Shift range selection failed.");
    check(containsLabel(*window, QStringLiteral("匿名测试待交件\n待交付\n批次  Sample 测试批次\n截止  2026-12-10")),
          "The focused project summary was not visible.");

    QTest::mouseClick(apply, Qt::LeftButton);
    check(containsLabel(*window, QStringLiteral("已在本窗口将 2 个项目设为「待开发」。")),
          "The batch action did not report success.");
    check(containsLabel(*window, QStringLiteral("已选 2 项")), "Batch refresh lost the visible selection.");
    for (const auto& row : table.selectionModel()->selectedRows()) {
        check(table.model()->index(row.row(), 1).data().toString() == QStringLiteral("待开发"),
              "The batch action skipped a selected row.");
    }
    QApplication::processEvents();
    const auto screenshotPath = qEnvironmentVariable("WORKBOX_CPP_TEST_SCREENSHOT");
    if (!screenshotPath.isEmpty()) {
        check(window->grab().save(screenshotPath), "Unable to save the native Qt screenshot.");
    }

    filter.setCurrentIndex(1);
    auto* selection = table.selectionModel();
    const auto* model = table.model();
    const QItemSelection batch(model->index(0, 0), model->index(119, model->columnCount() - 1));
    selection->select(batch, QItemSelectionModel::ClearAndSelect | QItemSelectionModel::Rows);
    filter.setCurrentIndex(0);
    auto& target = control<QComboBox>(*window, QStringLiteral("设置选中项目的状态"));
    std::array<double, 6> samples{};
    for (std::size_t run = 0; run < samples.size(); ++run) {
        target.setCurrentIndex(run % 2 == 0 ? 2 : 0);
        elapsed.restart();
        apply->click();
        QApplication::processEvents();
        samples[run] = elapsed.nsecsElapsed() / 1e6;
        check(selection->selectedRows().size() == 120, "Batch refresh lost part of 120 selected projects.");
        const auto expected = target.currentText();
        for (const auto& row : selection->selectedRows()) {
            check(model->index(row.row(), 1).data().toString() == expected,
                  "The 120-project batch skipped a selected row.");
        }
    }
    std::sort(samples.begin(), samples.end());
    std::cout << "Batch of 120 plus list refresh, median of six: "
              << (samples[2] + samples[3]) / 2 << " ms\n";
}

}  // namespace

int main(int argc, char* argv[])
{
    QApplication app(argc, argv);
    try {
        check(app.arguments().size() >= 2, "Specify an anonymous fixture database.");
        const auto fontPath = qEnvironmentVariable("WORKBOX_CPP_TEST_FONT");
        if (!fontPath.isEmpty()) {
            const auto font = QFontDatabase::addApplicationFont(fontPath);
            check(font >= 0, "Unable to load the capture font.");
            QApplication::setFont(QFont(QFontDatabase::applicationFontFamilies(font).first(), 9));
        }
        testWindow(nativePath(app.arguments().at(1)));
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
