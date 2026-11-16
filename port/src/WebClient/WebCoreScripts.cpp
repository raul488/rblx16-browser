/* Loads the original 2016 core GUI scripts into the offline client.
 *
 * This is the bootstrap StudioConfigurer::configure performs in
 * Network/GameConfigurer.cpp: create RobloxGui.Modules from the Lua modules on
 * disk, then start ServerStarterScript / StarterScript.  The GUI itself is the
 * stock content/scripts Lua, not a browser recreation. */
#include <cstdio>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>

#include <boost/filesystem.hpp>

#include "v8datamodel/DataModel.h"
#include "v8datamodel/PlayerGui.h"
#include "v8datamodel/ScreenGui.h"
#include "v8datamodel/Folder.h"
#include "v8datamodel/ContentProvider.h"
#include "v8datamodel/RobloxReplicatedStorage.h"
#include "script/ModuleScript.h"
#include "script/ScriptContext.h"
#include "network/Players.h"
#include "util/ProtectedString.h"

namespace rblx2016 {

namespace {

RBX::Folder* ensureFolder(RBX::Instance* parent, const std::string& name) {
    if (!parent) return nullptr;
    if (RBX::Instance* existing = parent->findFirstChildByName(name))
        if (RBX::Folder* folder = RBX::Instance::fastDynamicCast<RBX::Folder>(existing))
            return folder;
    shared_ptr<RBX::Folder> folder = RBX::Creatable<RBX::Instance>::create<RBX::Folder>();
    folder->setName(name);
    folder->setRobloxLocked(true);
    folder->setParent(parent);
    return folder.get();
}

void loadModuleFile(const boost::filesystem::path& file,
                    const boost::filesystem::path& base,
                    RBX::Folder* modules) {
    std::string full = file.string();
    std::string basePath = base.string();
    if (full.compare(0, basePath.size(), basePath) != 0) return;

    std::string rel = full.substr(basePath.size());
    for (size_t i = 0; i < rel.size(); ++i)
        if (rel[i] == '\\') rel[i] = '/';
    while (!rel.empty() && rel[0] == '/') rel.erase(0, 1);
    if (rel.size() <= 4) return;
    rel.erase(rel.size() - 4);  // .lua

    std::ifstream in(full.c_str(), std::ios::in | std::ios::binary);
    if (!in) return;
    std::stringstream source;
    source << in.rdbuf();
    if (source.str().empty()) return;

    RBX::Folder* folder = modules;
    std::string name = rel;
    size_t slash;
    while ((slash = name.find('/')) != std::string::npos) {
        folder = ensureFolder(folder, name.substr(0, slash));
        if (!folder) return;
        name.erase(0, slash + 1);
    }
    if (name.empty()) return;

    shared_ptr<RBX::ModuleScript> module = RBX::Creatable<RBX::Instance>::create<RBX::ModuleScript>();
    module->setName(name);
    module->setSource(RBX::ProtectedString::fromTrustedSource(source.str()));
    module->setRobloxLocked(true);
    module->setParent(folder);
}

void loadModuleTree(const boost::filesystem::path& dir,
                    const boost::filesystem::path& base,
                    RBX::Folder* modules) {
    if (!boost::filesystem::exists(dir)) return;
    boost::filesystem::directory_iterator end;
    for (boost::filesystem::directory_iterator it(dir); it != end; ++it) {
        boost::filesystem::path path = it->path();
        if (boost::filesystem::is_directory(path)) {
            loadModuleTree(path, base, modules);
        } else if (boost::filesystem::is_regular_file(path)) {
            if (path.extension() == ".lua")
                loadModuleFile(path, base, modules);
        }
    }
}

}  // namespace

void loadLocalCoreScripts(RBX::DataModel* dataModel) {
    if (!dataModel) return;

    RBX::CoreGuiService* coreGui = RBX::ServiceProvider::find<RBX::CoreGuiService>(dataModel);
    if (!coreGui) {
        std::fprintf(stderr, "RBLX2016: core scripts: no CoreGui service\n");
        return;
    }
    shared_ptr<RBX::ScreenGui> robloxGui = coreGui->getRobloxScreenGui();
    if (!robloxGui) {
        std::fprintf(stderr, "RBLX2016: core scripts: no RobloxGui\n");
        return;
    }

    RBX::Folder* modules = ensureFolder(robloxGui.get(), "Modules");
    if (!modules) return;

    const std::string scriptsPath = RBX::ContentProvider::assetFolder() + "scripts/Modules";
    boost::filesystem::path base(scriptsPath);
    loadModuleTree(base, base, modules);

    if (!RBX::ServiceProvider::find<RBX::RobloxReplicatedStorage>(dataModel))
        RBX::ServiceProvider::create<RBX::RobloxReplicatedStorage>(dataModel);

    RBX::ScriptContext* scriptContext = RBX::ServiceProvider::create<RBX::ScriptContext>(dataModel);
    if (!scriptContext) return;

    if (RBX::Network::Players::backendProcessing(dataModel))
        scriptContext->addCoreScriptLocal("ServerStarterScript", shared_ptr<RBX::Instance>());
    if (RBX::Network::Players::frontendProcessing(dataModel))
        scriptContext->addCoreScriptLocal("StarterScript", shared_ptr<RBX::Instance>());

    std::fprintf(stderr, "RBLX2016: core scripts started (Modules at %s)\n", scriptsPath.c_str());
}

}  // namespace rblx2016
