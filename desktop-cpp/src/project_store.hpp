#pragma once

#include <array>
#include <cstdint>
#include <filesystem>
#include <memory>
#include <optional>
#include <span>
#include <string>
#include <string_view>
#include <vector>

struct sqlite3;

namespace workbox::prototype {

enum class Status { pending, active, ready, delivered };

struct Project {
    std::int64_t id;
    std::string name;
    Status status;
    bool urgent;
    std::string dueDate;
    std::string batchName;
    bool blocked;
};

struct ProjectListing {
    std::vector<Project> projects;
    std::array<int, 4> statusCounts{};
};

// The source is opened read-only. Edits apply to this process's private snapshot.
class ProjectStore {
public:
    explicit ProjectStore(const std::filesystem::path& source);

    ProjectListing listProjects(std::string_view search = {},
                                std::optional<Status> status = std::nullopt) const;
    void setProjectStatus(std::span<const std::int64_t> ids, Status target,
                          std::span<const std::int64_t> confirmedDelivered = {});

private:
    struct CloseDatabase {
        void operator()(sqlite3* connection) const noexcept;
    };
    std::unique_ptr<sqlite3, CloseDatabase> connection_;
};

}  // namespace workbox::prototype
