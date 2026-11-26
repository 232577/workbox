#include "project_store.hpp"

#include <algorithm>
#include <array>
#include <iostream>
#include <stdexcept>

namespace {

using workbox::prototype::ProjectListing;
using workbox::prototype::ProjectStore;
using workbox::prototype::Status;
using Id = std::int64_t;

void check(bool condition, const char* message) {
    if (!condition) {
        throw std::runtime_error(message);
    }
}

std::vector<Id> projectIds(const ProjectListing& listing) {
    std::vector<Id> ids;
    for (const auto& project : listing.projects) {
        ids.push_back(project.id);
    }
    return ids;
}

void expectRejected(ProjectStore& store, std::span<const Id> ids, Status status) {
    try {
        store.setProjectStatus(ids, status);
    } catch (const std::exception&) {
        return;
    }
    throw std::runtime_error("The store accepted a disallowed change.");
}

void expectUnreadable(const std::filesystem::path& source) {
    try {
        const ProjectStore denied(source);
    } catch (const std::exception&) {
        return;
    }
    throw std::runtime_error("The store opened a missing or incompatible source.");
}

int run(const std::filesystem::path& source, const std::filesystem::path& incompatible) {
    ProjectStore store(source);
    check(projectIds(store.listProjects()) == std::vector<Id>{2, 3, 1, 8, 4},
          "The list must filter archive/delete/merge and keep priority ordering.");
    check(projectIds(store.listProjects("  aLpHa  ")) == std::vector<Id>{1},
          "Project-name search must trim and ignore ASCII case.");
    check(projectIds(store.listProjects("%")) == std::vector<Id>{2},
          "SQL wildcard characters must be literal.");
    check(projectIds(store.listProjects(" ' OR 1=1 -- ")).empty(),
          "Search text must be bound as data.");
    check(projectIds(store.listProjects("review note")) == std::vector<Id>{1},
          "Search must include notes.");
    check(projectIds(store.listProjects("测试批次")).size() == 5,
          "Search must include original batch names and accept Chinese.");
    check(projectIds(store.listProjects("shared.zip")) == std::vector<Id>{4},
          "Two matching ZIPs must not duplicate the project.");
    const auto active = store.listProjects({}, Status::active);
    check(projectIds(active) == std::vector<Id>{2}, "Status filter chose the wrong row.");
    check(active.statusCounts == std::array<int, 4>{2, 1, 1, 1},
          "Status counts must include all matching rows before the status filter.");

    for (const Id invalid : {4, 5, 6, 7, 8, 404}) {
        expectRejected(store, std::array<Id, 2>{1, invalid}, Status::ready);
        check(projectIds(store.listProjects({}, Status::pending)) == std::vector<Id>{1, 8},
              "An invalid member must roll back the entire batch.");
    }
    expectRejected(store, {}, Status::ready);
    expectRejected(store, std::vector<Id>(501, 1), Status::active);
    expectRejected(store, std::array<Id, 2>{1, 1}, Status::ready);
    expectRejected(store, std::array<Id, 1>{0}, Status::ready);
    expectRejected(store, std::array<Id, 1>{1}, Status::delivered);

    store.setProjectStatus(std::array<Id, 2>{1, 3}, Status::active);
    check(projectIds(store.listProjects({}, Status::active)) == std::vector<Id>{2, 3, 1},
          "All selected eligible rows must change in the same batch.");
    store.setProjectStatus(std::array<Id, 2>{1, 4}, Status::ready, std::array<Id, 1>{4});
    check(projectIds(store.listProjects({}, Status::ready)) == std::vector<Id>{1, 4},
          "A specifically confirmed delivered project may return to ready.");

    const ProjectStore reopened(source);
    check(projectIds(reopened.listProjects({}, Status::delivered)) == std::vector<Id>{4},
          "Changes must never be written back to the source.");
    expectUnreadable(incompatible);
    const auto missing = source.parent_path() / "missing.db";
    expectUnreadable(missing);
    check(!std::filesystem::exists(missing), "Opening a missing source created a file.");
    return 0;
}

}  // namespace

#ifdef _WIN32
int wmain(int argc, wchar_t* argv[]) {
#else
int main(int argc, char* argv[]) {
#endif
    try {
        if (argc != 3) {
            throw std::runtime_error("Pass the anonymous and incompatible databases.");
        }
        return run(argv[1], argv[2]);
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
