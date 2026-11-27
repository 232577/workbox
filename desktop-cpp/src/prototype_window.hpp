#pragma once

#include <filesystem>
#include <memory>

class QWidget;

namespace workbox::prototype {

std::unique_ptr<QWidget> createPrototypeWindow(const std::filesystem::path& source);

}  // namespace workbox::prototype
