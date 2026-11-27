#include "prototype_window.hpp"

#include <QApplication>
#include <QCommandLineParser>
#include <QFileDialog>
#include <QMessageBox>
#include <QWidget>

#include <exception>

namespace {

std::filesystem::path filePath(const QString& path)
{
#ifdef _WIN32
    return std::filesystem::path(path.toStdWString());
#else
    return std::filesystem::path(path.toStdString());
#endif
}

}

int main(int argc, char* argv[])
{
    QApplication app(argc, argv);
    QCoreApplication::setApplicationName(QStringLiteral("WorkBoxCppPrototype"));
    QCommandLineParser parser;
    parser.setApplicationDescription(QStringLiteral("使用匿名 SQLite 数据副本体验 WorkBox C++ 原型"));
    parser.addHelpOption();
    parser.addPositionalArgument(QStringLiteral("database"), QStringLiteral("匿名 SQLite 数据库路径"),
                                 QStringLiteral("[database]"));
    parser.process(app);
    const auto arguments = parser.positionalArguments();
    if (arguments.size() > 1) {
        parser.showHelp(2);
    }
    const auto source = arguments.isEmpty() ? QFileDialog::getOpenFileName(nullptr,
        QStringLiteral("打开匿名 WorkBox 数据库"), {},
        QStringLiteral("SQLite 数据库 (*.db *.sqlite *.sqlite3);;所有文件 (*)")) : arguments.first();
    if (source.isEmpty()) {
        return 0;
    }

    try {
        const auto window = workbox::prototype::createPrototypeWindow(filePath(source));
        window->show();
        return app.exec();
    } catch (const std::exception& error) {
        QMessageBox box(QMessageBox::Warning, QStringLiteral("无法打开项目数据库"),
                        QString::fromUtf8(error.what()), QMessageBox::Ok);
        box.setTextFormat(Qt::PlainText);
        box.exec();
        return 1;
    }
}
