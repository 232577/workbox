#include "prototype_window.hpp"

#include "project_store.hpp"

#include <QAbstractTableModel>
#include <QComboBox>
#include <QHeaderView>
#include <QHBoxLayout>
#include <QItemSelectionModel>
#include <QLabel>
#include <QLineEdit>
#include <QMessageBox>
#include <QPushButton>
#include <QScrollBar>
#include <QSignalBlocker>
#include <QSplitter>
#include <QStringList>
#include <QTableView>
#include <QTimer>
#include <QVBoxLayout>

#include <array>
#include <exception>
#include <numeric>
#include <unordered_set>
#include <utility>

namespace {
using namespace workbox::prototype;

constexpr std::array statuses{Status::pending, Status::active, Status::ready, Status::delivered};
constexpr int editableStatusCount = 3;

QString statusName(Status status)
{
    switch (status) {
    case Status::pending: return QStringLiteral("待开发");
    case Status::active: return QStringLiteral("开发中");
    case Status::ready: return QStringLiteral("待交付");
    case Status::delivered: return QStringLiteral("已交付");
    }
    return {};
}

QString decode(const std::string& value)
{
    return QString::fromUtf8(value.data(), static_cast<qsizetype>(value.size()));
}

void showError(QWidget* owner, const QString& title, const std::exception& error)
{
    QMessageBox box(QMessageBox::Warning, title, QString::fromUtf8(error.what()), QMessageBox::Ok, owner);
    box.setTextFormat(Qt::PlainText);
    box.exec();
}


class ProjectModel final : public QAbstractTableModel {
public:
    using QAbstractTableModel::QAbstractTableModel;

    int rowCount(const QModelIndex& parent = {}) const override
    {
        return parent.isValid() ? 0 : static_cast<int>(projects_.size());
    }

    int columnCount(const QModelIndex& parent = {}) const override
    {
        return parent.isValid() ? 0 : 5;
    }

    QVariant headerData(int section, Qt::Orientation orientation, int role) const override
    {
        if (orientation != Qt::Horizontal || role != Qt::DisplayRole || section < 0 || section >= columnCount()) {
            return {};
        }
        const std::array headers{QStringLiteral("项目名称"), QStringLiteral("状态"),
                                 QStringLiteral("批次"), QStringLiteral("截止日期"), QStringLiteral("加急")};
        return headers[section];
    }

    QVariant data(const QModelIndex& index, int role) const override
    {
        const auto* project = projectAt(index.row());
        if (!project) {
            return {};
        }
        if (role == Qt::ToolTipRole && index.column() == 0) {
            return QStringLiteral("<qt>%1</qt>").arg(decode(project->name).toHtmlEscaped());
        }
        if (role != Qt::DisplayRole) {
            return {};
        }
        switch (index.column()) {
        case 0: return decode(project->name);
        case 1: return statusName(project->status);
        case 2: return decode(project->batchName);
        case 3: return decode(project->dueDate);
        case 4: return project->urgent ? QStringLiteral("是") : QString();
        default: return {};
        }
    }

    const Project* projectAt(int row) const
    {
        if (row < 0 || row >= rowCount()) {
            return nullptr;
        }
        return &projects_[row];
    }

    void replace(std::vector<Project> projects)
    {
        beginResetModel();
        projects_ = std::move(projects);
        endResetModel();
    }

private:
    std::vector<Project> projects_;
};

class PrototypeWindow final : public QWidget {
public:
    explicit PrototypeWindow(const std::filesystem::path& source)
        : store_(source)
    {
        setWindowTitle(QStringLiteral("WorkBox 项目管理 · C++ 原型"));
        resize(1010, 620);
        setMinimumSize(760, 450);

        auto* layout = new QVBoxLayout(this);
        auto* filters = new QHBoxLayout;
        search_ = new QLineEdit;
        search_->setPlaceholderText(QStringLiteral("搜索项目名称、批次、备注或 ZIP 文件名"));
        search_->setAccessibleName(QStringLiteral("搜索项目"));
        search_->setMaxLength(1000);
        search_->setClearButtonEnabled(true);
        filter_ = new QComboBox;
        filter_->setAccessibleName(QStringLiteral("按状态筛选"));
        filter_->addItem(QStringLiteral("全部状态"));
        for (Status status : statuses) {
            filter_->addItem(statusName(status));
        }
        filters->addWidget(search_, 1);
        filters->addWidget(filter_);
        layout->addLayout(filters);

        counts_ = new QLabel;
        layout->addWidget(counts_);

        auto* splitter = new QSplitter(Qt::Horizontal);
        table_ = new QTableView;
        model_ = new ProjectModel(table_);
        table_->setModel(model_);
        table_->setAccessibleName(QStringLiteral("项目清单"));
        table_->setSelectionBehavior(QAbstractItemView::SelectRows);
        table_->setSelectionMode(QAbstractItemView::ExtendedSelection);
        table_->setEditTriggers(QAbstractItemView::NoEditTriggers);
        table_->setVerticalScrollMode(QAbstractItemView::ScrollPerPixel);
        table_->setAlternatingRowColors(true);
        table_->setWordWrap(false);
        table_->horizontalHeader()->setSectionResizeMode(0, QHeaderView::Stretch);
        table_->setColumnWidth(1, 84);
        table_->setColumnWidth(2, 128);
        table_->setColumnWidth(3, 104);
        table_->setColumnWidth(4, 56);
        table_->verticalHeader()->hide();
        splitter->addWidget(table_);

        auto* side = new QWidget;
        side->setMinimumWidth(224);
        auto* sideLayout = new QVBoxLayout(side);
        selected_ = new QLabel;
        sideLayout->addWidget(selected_);
        details_ = new QLabel;
        details_->setWordWrap(true);
        details_->setTextFormat(Qt::PlainText);
        sideLayout->addWidget(details_);
        sideLayout->addStretch();
        auto* targetLabel = new QLabel(QStringLiteral("设置选中项目的状态"));
        target_ = new QComboBox;
        target_->setAccessibleName(targetLabel->text());
        for (int i = 0; i < editableStatusCount; ++i) {
            target_->addItem(statusName(statuses[i]));
        }
        targetLabel->setBuddy(target_);
        sideLayout->addWidget(targetLabel);
        sideLayout->addWidget(target_);
        apply_ = new QPushButton(QStringLiteral("更新所选项目"));
        sideLayout->addWidget(apply_);
        splitter->addWidget(side);
        splitter->setStretchFactor(0, 1);
        splitter->setCollapsible(0, false);
        splitter->setCollapsible(1, false);
        layout->addWidget(splitter, 1);

        message_ = new QLabel(QStringLiteral("已加载原数据的内存副本。本次操作只影响这个窗口。"));
        message_->setWordWrap(true);
        layout->addWidget(message_);

        searchTimer_.setSingleShot(true);
        searchTimer_.setInterval(120);
        connect(&searchTimer_, &QTimer::timeout, this, [this] { reload(); });
        connect(search_, &QLineEdit::textChanged, this, [this] { searchTimer_.start(); });
        connect(filter_, &QComboBox::currentIndexChanged, this, [this] {
            searchTimer_.stop();
            reload();
        });
        connect(table_->selectionModel(), &QItemSelectionModel::selectionChanged, this, [this] { updateDetails(); });
        connect(table_->selectionModel(), &QItemSelectionModel::currentRowChanged, this, [this] { updateDetails(); });
        connect(apply_, &QPushButton::clicked, this, [this] { applyStatus(); });

        render(store_.listProjects());
    }

private:
    void reload()
    {
        const int selectedFilter = filter_->currentIndex();
        std::optional<Status> status;
        if (selectedFilter > 0) {
            status = statuses[selectedFilter - 1];
        }
        try {
            render(store_.listProjects(search_->text().toUtf8().toStdString(), status));
        } catch (const std::exception& error) {
            showError(this, QStringLiteral("无法刷新项目清单"), error);
        }
    }

    void render(ProjectListing listing)
    {
        auto* selectionModel = table_->selectionModel();
        std::unordered_set<std::int64_t> selectedIds;
        for (const auto& index : selectionModel->selectedRows()) {
            selectedIds.insert(model_->projectAt(index.row())->id);
        }
        const auto* current = model_->projectAt(selectionModel->currentIndex().row());
        const auto currentId = current ? current->id : 0;
        const int topRow = table_->rowAt(0);
        const auto* top = model_->projectAt(topRow);
        const auto topId = top ? top->id : 0;
        const int topOffset = top ? table_->rowViewportPosition(topRow) : 0;
        const int previousScroll = table_->verticalScrollBar()->value();

        {
            const QSignalBlocker blockSelection(selectionModel);
            model_->replace(std::move(listing.projects));
            QItemSelection restored;
            QModelIndex firstSelected;
            QModelIndex currentIndex;
            QModelIndex topIndex;
            for (int row = 0; row < model_->rowCount(); ++row) {
                const auto id = model_->projectAt(row)->id;
                const auto index = model_->index(row, 0);
                if (selectedIds.contains(id)) {
                    restored.select(index, index);
                    if (!firstSelected.isValid()) {
                        firstSelected = index;
                    }
                }
                if (id == currentId) {
                    currentIndex = index;
                }
                if (id == topId) {
                    topIndex = index;
                }
            }
            selectionModel->select(restored, QItemSelectionModel::ClearAndSelect | QItemSelectionModel::Rows);
            selectionModel->setCurrentIndex(currentIndex.isValid() ? currentIndex : firstSelected,
                                            QItemSelectionModel::NoUpdate);
            if (topIndex.isValid()) {
                table_->scrollTo(topIndex, QAbstractItemView::PositionAtTop);
                const int offset = table_->rowViewportPosition(topIndex.row()) - topOffset;
                table_->verticalScrollBar()->setValue(table_->verticalScrollBar()->value() + offset);
            } else {
                table_->verticalScrollBar()->setValue(previousScroll);
            }
        }

        QStringList counts;
        const auto total = std::accumulate(listing.statusCounts.begin(), listing.statusCounts.end(), 0);
        counts.append(QStringLiteral("共 %1 项").arg(total));
        for (std::size_t i = 0; i < statuses.size(); ++i) {
            counts.append(QStringLiteral("%1 %2").arg(statusName(statuses[i])).arg(listing.statusCounts[i]));
        }
        counts_->setText(counts.join(QStringLiteral("  ·  ")));
        updateDetails();
    }

    void updateDetails()
    {
        const auto* selectionModel = table_->selectionModel();
        const auto rows = selectionModel->selectedRows();
        selected_->setText(QStringLiteral("已选 %1 项").arg(rows.size()));
        target_->setEnabled(false);
        apply_->setEnabled(false);
        if (rows.isEmpty()) {
            details_->setText(QStringLiteral("选择项目查看摘要。按 Ctrl 或 Shift 可选择多项。"));
            return;
        }

        const auto focusedRow = selectionModel->currentIndex().row();
        const bool currentSelected = focusedRow >= 0 && selectionModel->isRowSelected(focusedRow, {});
        const auto& project = *model_->projectAt(currentSelected ? focusedRow : rows.first().row());
        QStringList details{decode(project.name), statusName(project.status)};
        if (!project.batchName.empty()) {
            details.append(QStringLiteral("批次  %1").arg(decode(project.batchName)));
        }
        if (!project.dueDate.empty()) {
            details.append(QStringLiteral("截止  %1").arg(decode(project.dueDate)));
        }

        bool blocked = false;
        for (const auto& index : rows) {
            blocked = blocked || model_->projectAt(index.row())->blocked;
        }
        if (blocked) {
            details.append(QStringLiteral("所选项目中有工作目录需要恢复的项目。"));
        } else if (rows.size() > 500) {
            details.append(QStringLiteral("一次最多更新 500 项。"));
        } else {
            target_->setEnabled(true);
            apply_->setEnabled(true);
        }
        details_->setText(details.join(u'\n'));
    }

    void applyStatus()
    {
        const auto rows = table_->selectionModel()->selectedRows();
        if (rows.isEmpty() || rows.size() > 500) {
            return;
        }
        const auto target = statuses[target_->currentIndex()];
        std::vector<std::int64_t> selectedIds;
        std::vector<std::int64_t> deliveredIds;
        selectedIds.reserve(rows.size());
        for (const auto& index : rows) {
            const auto& project = *model_->projectAt(index.row());
            if (project.blocked) {
                return;
            }
            selectedIds.push_back(project.id);
            if (project.status == Status::delivered) {
                deliveredIds.push_back(project.id);
            }
        }
        if (!deliveredIds.empty()) {
            const auto answer = QMessageBox::question(this, QStringLiteral("重设已交付项目状态"),
                QStringLiteral("将选中的 %1 个项目设为「%2」？其中 %3 个项目已交付。\n\n"
                               "继续操作会清除当前的交付确认，历史交付记录保留。")
                    .arg(selectedIds.size()).arg(statusName(target)).arg(deliveredIds.size()),
                QMessageBox::Yes | QMessageBox::No, QMessageBox::No);
            if (answer != QMessageBox::Yes) {
                return;
            }
        }

        try {
            store_.setProjectStatus(selectedIds, target, deliveredIds);
        } catch (const std::exception& error) {
            showError(this, QStringLiteral("批量状态未更新"), error);
            return;
        }
        searchTimer_.stop();
        reload();
        message_->setText(QStringLiteral("已在本窗口将 %1 个项目设为「%2」。")
            .arg(selectedIds.size()).arg(statusName(target)));
    }

    ProjectStore store_;
    QTimer searchTimer_;
    QLineEdit* search_{};
    QComboBox* filter_{};
    QLabel* counts_{};
    QTableView* table_{};
    ProjectModel* model_{};
    QLabel* selected_{};
    QLabel* details_{};
    QComboBox* target_{};
    QPushButton* apply_{};
    QLabel* message_{};
};
}

namespace workbox::prototype {

std::unique_ptr<QWidget> createPrototypeWindow(const std::filesystem::path& source)
{
    return std::make_unique<PrototypeWindow>(source);
}

}  // namespace workbox::prototype
