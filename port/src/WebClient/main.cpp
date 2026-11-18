/* WebClient: minimal browser entry point for the 2016 engine.
 *
 * Phase 1: receive a place from JS, deserialize it into a VISIT_SOLO
 * DataModel, and keep the DataModel alive.  The render/input/audio platform
 * layer is added around this once the engine links.
 */
#include <cstdio>
#include <cstring>
#include <cstdint>
#include <sstream>
#include <string>
#include <memory>
#include <thread>
#include <atomic>
#include <functional>
#include <boost/bind.hpp>
#include <algorithm>
#include <array>
#include <cmath>
#include <chrono>

#include <emscripten/emscripten.h>

#include "v8datamodel/ContentProvider.h"
#include "v8datamodel/DataModel.h"
#include "v8datamodel/Game.h"
#include "v8datamodel/Workspace.h"
#include "v8world/World.h"
#include "v8datamodel/PartInstance.h"
#include "v8datamodel/InputObject.h"
#include "v8datamodel/UserInputService.h"
#include "v8datamodel/ScreenGui.h"
#include "v8datamodel/GuiBase2d.h"
#include "v8datamodel/Hopper.h"
#include "v8datamodel/HttpService.h"
#include "v8datamodel/GameSettings.h"
#include "v8datamodel/GameBasicSettings.h"
#include "v8datamodel/TouchInputService.h"
#include "v8tree/Verb.h"
#include "FastLog.h"
#include "v8datamodel/Tool.h"
#include "v8datamodel/JointInstance.h"
#include "humanoid/Humanoid.h"
#include "network/Players.h"
#include "network/Player.h"
#include "v8datamodel/PlayerScripts.h"
#include "v8datamodel/CharacterAppearance.h"
#include "v8datamodel/Decal.h"
#include "v8datamodel/FileMesh.h"
#include "v8datamodel/Attachment.h"
#include "v8datamodel/Camera.h"
#include "util/BrickColor.h"
#include "script/ScriptContext.h"
#include "script/CoreScript.h"
#include "script/script.h"
#include "WebView.h"
#include "RenderSettingsItem.h"
#include "v8xml/Serializer.h"
#include "security/SecurityContext.h"
#include "rbx/TaskScheduler.h"
#include "util/RunStateOwner.h"
#include "util/StandardOut.h"

// Render options read by the patched VisualEngine (see port/src_patch).
extern "C" {
    int rblx2016_light_grid_mode = 0;  // 0 auto, 1 2D, 2 none
    int rblx2016_gles2_shaders = 0;    // 1 = glsles shader pack
}

namespace {
    boost::shared_ptr<RBX::DataModel> g_dataModel;
    std::string g_username = "Player1";
    std::unique_ptr<rblx2016::WebView> g_view;
    int g_lastError = 0;
    volatile int g_frameCount = 0;
    std::atomic<bool> g_exitRequested(false);
    std::string g_pendingPlace;
    std::atomic<bool> g_placeParseStarted(false);
    std::atomic<bool> g_placeLoaded(false);
    std::atomic<bool> g_gameLoaded(false);
    std::atomic<bool> g_settleStarted(false);
    std::atomic<int> g_renderFramesSinceLoad(0);
    std::atomic<int> g_workspaceParts(0);
    std::chrono::steady_clock::time_point g_settleAt;
    std::chrono::steady_clock::time_point g_loadStart;
    // Worn catalog items, "shirt:1;pants:2;acc:3;body:24,..." from the page.
    std::string g_avatarSpec;
    std::string g_assetBase;
    std::atomic<RBX::Instance*> g_avatarCharacter(nullptr);
    std::atomic<bool> g_avatarBusy(false);
    // See the camera hand-back in rblx_frame.
    std::atomic<RBX::Instance*> g_cameraCharacter(nullptr);
    std::atomic<bool> g_cameraChecked(false);
    std::chrono::steady_clock::time_point g_characterSeenAt;
    std::chrono::steady_clock::time_point g_lastCharacterAttempt;
    int g_characterAttempts = 0;
    bool g_recordingVideo = false;
    bool g_touchEnabled = false;
}

// Mirrors the desktop client: core-script print/warn and Lua errors go to the log.
static void onStandardOut(const RBX::StandardOutMessage& msg) {
    const char* tag = msg.type == RBX::MESSAGE_ERROR ? "ERROR" :
                      msg.type == RBX::MESSAGE_WARNING ? "WARN" : "OUT";
    std::fprintf(stderr, "RBLX2016 %s: %s\n", tag, msg.message.c_str());
}

extern "C" void rblx_audio_pump();

extern "C" {

int rblx_load_place();

EMSCRIPTEN_KEEPALIVE int rblx_start_place(const unsigned char* data, int size, const char* username, const char* assetBase, int touchEnabled) {
    if (!data || size <= 0) return -1;
    if (username && *username) g_username = username;
    g_touchEnabled = touchEnabled != 0;
    g_lastError = 0;
    g_placeParseStarted.store(false);
    g_placeLoaded.store(false);
    g_gameLoaded.store(false);
    g_settleStarted.store(false);
    g_renderFramesSinceLoad.store(0);
    g_workspaceParts.store(0);
    g_pendingPlace.assign(reinterpret_cast<const char*>(data), static_cast<size_t>(size));
    g_loadStart = std::chrono::steady_clock::now();

    try {
        RBX::Game::globalInit(false);
        static bool stdoutHooked = false;
        if (!stdoutHooked) {
            RBX::StandardOut::singleton()->messageOut.connect(&onStandardOut);
            stdoutHooked = true;
        }
        // From the retail ClientAppSettings.json. Chat resets the GUI inset
        // without the topbar flag, and C++ Camera::step overrides the Lua
        // CameraScript without the camera flag.
        FLog::SetValue("UseInGameTopBar", "true");
        FLog::SetValue("UserAllCamerasInLua", "true");
        // Dismiss the stock loading screen on game.Loaded instead of waiting
        // for ContentProvider's queue, which stalls on offline content.
        FLog::SetValue("UseGameLoadedInLoadingScript", "true");
        // Weld accessories to the character attachment they name, not to the
        // head.
        FLog::SetValue("AccessoriesAndAttachments", "true");
        // Mesh offsets (package body parts) must be applied in part space.
        // Auto throttle reacts to scheduler jitter and halves the simulation
        // rate; this is local Play Solo, so keep full-rate physics.
        RBX::EThrottle::globalDebugEThrottle = RBX::EThrottle::ThrottleDisabled;
        RBX::ContentProvider::setAssetFolder("/content");
        // Start the stock LoadingScript immediately so it can paint before
        // the place is deserialized.
        g_dataModel = RBX::DataModel::createDataModel(true, nullptr, true);
        // The core Settings menu's Leave button uses SetVerb("Exit"), which the
        // native client provides in WindowsClient/GameVerbs.cpp.
        new RBX::BoundVerb(g_dataModel.get(), "Exit", [](RBX::VerbContainer*) {
            g_exitRequested.store(true);
        });
        // The native client provides these verbs in WindowsClient/GameVerbs.cpp.
        // The browser shell owns the actual capture/download and fullscreen.
        new RBX::BoundVerb(g_dataModel.get(), "Screenshot", [](RBX::VerbContainer*) {
            emscripten_run_script("window.rblxScreenshot && window.rblxScreenshot()");
        });
        new RBX::BoundVerb(g_dataModel.get(), "RecordToggle", [](RBX::VerbContainer*) {
            if (g_recordingVideo) {
                emscripten_run_script("window.rblxRecordStop && window.rblxRecordStop()");
                g_recordingVideo = false;
                RBX::GameSettings::singleton().videoRecordingSignal(false);
            } else if (emscripten_run_script_int(
                           "window.rblxRecordStart ? (window.rblxRecordStart() ? 1 : 0) : 0")) {
                g_recordingVideo = true;
                RBX::GameSettings::singleton().videoRecordingSignal(true);
            }
        });
        new RBX::BoundVerb(g_dataModel.get(), "ToggleFullScreen", [](RBX::VerbContainer*) {
            emscripten_run_script("window.rblxToggleFullscreen && window.rblxToggleFullscreen()");
        });
        if (assetBase && *assetBase) {
            g_assetBase = assetBase;
            if (RBX::ContentProvider* contentProvider = RBX::ServiceProvider::find<RBX::ContentProvider>(g_dataModel.get()))
                contentProvider->setBaseUrl(assetBase);
        }

        if (RBX::RunService* rs = RBX::ServiceProvider::create<RBX::RunService>(g_dataModel.get())) {
            std::fprintf(stderr, "RBLX2016: runservice -> run\n");
            rs->run();
        }

        g_view.reset(new rblx2016::WebView());
        if (!g_view->init(g_dataModel, 800, 600)) {
            RBX::StandardOut::singleton()->print(RBX::MESSAGE_WARNING,
                "RBLX2016: renderer failed to start; place is loaded headless");
            g_view.reset();
            return rblx_load_place();
        }
        return 0;
    } catch (const std::exception& e) {
        g_lastError = 1;
        RBX::StandardOut::singleton()->printf(RBX::MESSAGE_ERROR, "RBLX2016: %s", e.what());
        return 2;
    } catch (...) {
        g_lastError = 1;
        RBX::StandardOut::singleton()->printf(RBX::MESSAGE_ERROR, "RBLX2016: unknown load error");
        return 3;
    }
}

EMSCRIPTEN_KEEPALIVE int rblx_loading_gui_ready() {
    if (!g_dataModel) return 0;
    RBX::CoreGuiService* coreGui = RBX::ServiceProvider::find<RBX::CoreGuiService>(g_dataModel.get());
    if (!coreGui) return 0;
    return coreGui->findFirstChildByName("RobloxLoadingGui") ? 1 : 0;
}

EMSCRIPTEN_KEEPALIVE int rblx_load_place() {
    if (!g_dataModel || g_pendingPlace.empty()) return -1;
    if (g_placeParseStarted.exchange(true)) return 0;

    std::string place;
    place.swap(g_pendingPlace);
    try {
        g_dataModel->submitTask([place](RBX::DataModel* dm) {
          auto doLoad = [&]() {
            RBX::Security::Impersonator impersonate(RBX::Security::LocalGUI_);
            auto phaseStart = std::chrono::steady_clock::now();
            std::stringstream stream(place);
            Serializer serializer;
            serializer.load(stream, dm);
            auto afterSerialize = std::chrono::steady_clock::now();
            dm->processAfterLoad();
            auto afterLoad = std::chrono::steady_clock::now();
            // Places serialize HttpService with HttpEnabled=false. Turn it on
            // for this local session so scripts reading the flag see the truth
            // (checkEverything also bypasses the gate under Emscripten).
            std::function<void(RBX::Instance*)> enableHttp = [&](RBX::Instance* node) {
                if (RBX::HttpService* service = RBX::Instance::fastDynamicCast<RBX::HttpService>(node)) {
                    if (const RBX::Reflection::PropertyDescriptor* enabled =
                            service->getDescriptor().findPropertyDescriptor("HttpEnabled"))
                        enabled->setVariant(service, true);
                }
                if (node->getChildren())
                    for (size_t i = 0; i < node->getChildren()->size(); ++i)
                        enableHttp((*node->getChildren())[i].get());
            };
            enableHttp(dm);
            dm->getWorkspace()->createTerrain();
            dm->workspaceLoadedSignal();
            auto afterTerrain = std::chrono::steady_clock::now();
            if (RBX::Network::Players* players = dm->find<RBX::Network::Players>()) {
                if (!players->getLocalPlayerDangerous()) {
                    // A non-test UserId keeps the player out of guest chat mode:
                    // getChatMode() returns Menu for negative ids, which hides
                    // the stock chat icon and chat window.
                    players->createLocalPlayer(1, false);
                    RBX::Network::Player* lp =
                        RBX::Instance::fastDynamicCast<RBX::Network::Player>(players->getLocalPlayerDangerous());
                    if (lp) {
                        // Player::setName requires WritePlayer; the stock test
                        // player path does this under Replicator impersonation.
                        RBX::Security::Impersonator impersonate(RBX::Security::Replicator_);
                        lp->setName(g_username);
                    }
                }
            }
            if (RBX::UserInputService* uis = dm->find<RBX::UserInputService>()) {
                if (g_touchEnabled) {
                    uis->setTouchEnabled(true);
                    uis->setMouseEnabled(false);
                    uis->setKeyboardEnabled(false);
                    RBX::ServiceProvider::create<RBX::TouchInputService>(dm);
                    RBX::StandardOut::singleton()->print(RBX::MESSAGE_INFO,
                        "RBLX2016: touch controls enabled");
                } else {
                    uis->setKeyboardEnabled(true);
                    uis->setMouseEnabled(true);
                }
            }

            RBX::Workspace* ws = dm->getWorkspace();
            size_t parts = ws ? ws->countDescendantsOfType<RBX::PartInstance>() : 0;
            g_workspaceParts.store(static_cast<int>(parts));
            if (ws && ws->getCamera()) {
                G3D::Vector3 mn(1e9f, 1e9f, 1e9f), mx(-1e9f, -1e9f, -1e9f);
                G3D::Vector3 sum(0, 0, 0);
                G3D::Vector3 spawnPos(0, 0, 0);
                bool hasSpawn = false;
                int counted = 0;
                std::function<void(RBX::Instance*)> walk = [&](RBX::Instance* node) {
                    if (RBX::PartInstance* p = RBX::Instance::fastDynamicCast<RBX::PartInstance>(node)) {
                        G3D::Vector3 t = p->getCoordinateFrame().translation;
                        sum += t; ++counted;
                        if (!hasSpawn && p->getClassName() == "SpawnLocation") { spawnPos = t; hasSpawn = true; }
                        mn = G3D::Vector3(std::min(mn.x,t.x), std::min(mn.y,t.y), std::min(mn.z,t.z));
                        mx = G3D::Vector3(std::max(mx.x,t.x), std::max(mx.y,t.y), std::max(mx.z,t.z));
                    }
                    if (node->getChildren()) for (size_t i = 0; i < node->getChildren()->size(); ++i) walk((*node->getChildren())[i].get());
                };
                walk(ws);
                G3D::Vector3 center = hasSpawn ? spawnPos : (counted ? sum / (float)counted : (mn + mx) * 0.5f);
                G3D::Vector3 eye = center + G3D::Vector3(0, 45, 180);
                G3D::CoordinateFrame cf;
                cf.translation = eye;
                cf.lookAt(center);
                ws->getCamera()->setCameraCoordinateFrame(cf);
            }

            auto afterPlayer = std::chrono::steady_clock::now();
            dm->startCoreScripts(true);
            auto afterScripts = std::chrono::steady_clock::now();
            auto ms = [](const std::chrono::steady_clock::time_point& a,
                         const std::chrono::steady_clock::time_point& b) {
                return std::chrono::duration<double, std::milli>(b - a).count();
            };
            RBX::StandardOut::singleton()->printf(RBX::MESSAGE_INFO,
                "RBLX2016: load phases serialize=%.0f afterLoad=%.0f terrain=%.0f player=%.0f scripts=%.0f ms",
                ms(phaseStart, afterSerialize), ms(afterSerialize, afterLoad),
                ms(afterLoad, afterTerrain), ms(afterTerrain, afterPlayer),
                ms(afterPlayer, afterScripts));

            double loadMs = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - g_loadStart).count();
            RBX::StandardOut::singleton()->printf(RBX::MESSAGE_INFO,
                "RBLX2016: loaded place (%d bytes) in %.0f ms, workspace parts=%d, instances=%d, player=%s",
                (int)place.size(), loadMs, static_cast<int>(parts), dm->getNumInstance(), g_username.c_str());
          };
          try {
              doLoad();
          } catch (const std::exception& e) {
              g_lastError = 1;
              RBX::StandardOut::singleton()->printf(RBX::MESSAGE_ERROR,
                  "RBLX2016: place load task failed: %s", e.what());
          } catch (...) {
              g_lastError = 1;
              RBX::StandardOut::singleton()->printf(RBX::MESSAGE_ERROR,
                  "RBLX2016: place load task failed");
          }
          g_placeLoaded.store(true);
        }, RBX::DataModelJob::Write);
        return 0;
    } catch (const std::exception& e) {
        g_lastError = 1;
        RBX::StandardOut::singleton()->printf(RBX::MESSAGE_ERROR, "RBLX2016: place load failed: %s", e.what());
        return 2;
    } catch (...) {
        g_lastError = 1;
        RBX::StandardOut::singleton()->printf(RBX::MESSAGE_ERROR, "RBLX2016: place load failed");
        return 3;
    }
}

// ---------------------------------------------------------------- avatar
// The page owns the catalog and sends the selection as
// "shirt:1;pants:2;acc:3;body:24,24,24,24,24,24". Everything is worn through
// Player::LoadCharacterAppearance, which welds accessories and routes the
// face decal to the head.
namespace {

struct AvatarEntry {
    std::string type;
    long long id = 0;
    std::string value;  // part lists for packages, unused otherwise
};

std::vector<AvatarEntry> parseAvatarSpec(const std::string& spec, int* body) {
    std::vector<AvatarEntry> entries;
    for (int i = 0; i < 6; ++i) body[i] = 24;
    size_t pos = 0;
    while (pos < spec.size()) {
        size_t end = spec.find(';', pos);
        std::string entry = spec.substr(pos, end == std::string::npos ? std::string::npos : end - pos);
        pos = end == std::string::npos ? spec.size() : end + 1;
        size_t colon = entry.find(':');
        if (colon == std::string::npos) continue;
        std::string type = entry.substr(0, colon);
        std::string value = entry.substr(colon + 1);
        if (type == "body") {
            std::istringstream numbers(value);
            std::string part;
            int slot = 0;
            while (slot < 6 && std::getline(numbers, part, ','))
                body[slot++] = std::atoi(part.c_str());
            continue;
        }
        AvatarEntry e;
        e.type = type;
        e.value = value;
        e.id = std::atoll(value.c_str());
        if (e.id > 0 || type == "pkg" || type == "pkghead") entries.push_back(e);
    }
    return entries;
}

std::string avatarUrl(const std::string& base, long long id) {
    return base + "catalog/item/" + std::to_string(id);
}

void addCharacterAttachment(RBX::PartInstance* part, const char* name, const G3D::Vector3& offset) {
    if (!part || part->findFirstChildByName(name)) return;
    boost::shared_ptr<RBX::Attachment> attachment =
        RBX::Creatable<RBX::Instance>::create<RBX::Attachment>();
    attachment->setName(name);
    RBX::CoordinateFrame frame;
    frame.translation = offset;
    attachment->setFrameInPart(frame);
    attachment->setParent(part);
}

// R6 characters have no attachment points; accessories weld to them by name.
void ensureCharacterAttachments(RBX::Humanoid* humanoid) {
    if (!humanoid) return;
    if (RBX::PartInstance* head = humanoid->getHeadSlow()) {
        G3D::Vector3 s = head->getPartSizeXml();
        addCharacterAttachment(head, "HatAttachment", G3D::Vector3(0, s.y * 0.5f, 0));
        addCharacterAttachment(head, "HairAttachment", G3D::Vector3(0, s.y * 0.5f + 0.1f, 0));
        addCharacterAttachment(head, "FaceFrontAttachment", G3D::Vector3(0, 0, -s.z * 0.5f));
    }
    if (RBX::PartInstance* torso = humanoid->getVisibleTorsoSlow()) {
        G3D::Vector3 s = torso->getPartSizeXml();
        const float hx = s.x * 0.5f, hy = s.y * 0.5f, hz = s.z * 0.5f;
        addCharacterAttachment(torso, "NeckAttachment", G3D::Vector3(0, hy, 0));
        addCharacterAttachment(torso, "BodyFrontAttachment", G3D::Vector3(0, 0, -hz));
        addCharacterAttachment(torso, "BodyBackAttachment", G3D::Vector3(0, 0, hz));
        addCharacterAttachment(torso, "LeftCollarAttachment", G3D::Vector3(-hx, hy, 0));
        addCharacterAttachment(torso, "RightCollarAttachment", G3D::Vector3(hx, hy, 0));
        addCharacterAttachment(torso, "WaistCenterAttachment", G3D::Vector3(0, -hy, 0));
        addCharacterAttachment(torso, "WaistFrontAttachment", G3D::Vector3(0, -hy, -hz));
        addCharacterAttachment(torso, "WaistBackAttachment", G3D::Vector3(0, -hy, hz));
    }
    if (RBX::PartInstance* arm = humanoid->getLeftArmSlow()) {
        G3D::Vector3 s = arm->getPartSizeXml();
        addCharacterAttachment(arm, "LeftShoulderAttachment", G3D::Vector3(0, s.y * 0.5f, 0));
        addCharacterAttachment(arm, "LeftGripAttachment", G3D::Vector3(0, -s.y * 0.5f, 0));
    }
    if (RBX::PartInstance* arm = humanoid->getRightArmSlow()) {
        G3D::Vector3 s = arm->getPartSizeXml();
        addCharacterAttachment(arm, "RightShoulderAttachment", G3D::Vector3(0, s.y * 0.5f, 0));
        addCharacterAttachment(arm, "RightGripAttachment", G3D::Vector3(0, -s.y * 0.5f, 0));
    }
}

// Hats and shoulder/back items are engine models, not textures.
bool isAccessoryModel(const std::string& type) {
    return type == "hat" || type == "shoulder" || type == "back" || type == "acc";
}

// A loaded model may wrap the wearable (body packages are a Folder holding a
// CharacterMesh), so keep only the appearance pieces and their children.
void collectAppearance(const boost::shared_ptr<RBX::Instance>& root,
                       std::vector<boost::shared_ptr<RBX::Instance>>& out) {
    if (!root) return;
    RBX::Instance* instance = root.get();
    // Deliberately not DataModelMesh: a model's FileMesh would be applied as
    // the head mesh and replace it.
    bool wearable = RBX::Instance::fastDynamicCast<RBX::CharacterAppearance>(instance) ||
                    RBX::Instance::fastDynamicCast<RBX::Decal>(instance) ||
                    dynamic_cast<RBX::IEquipable*>(instance) != nullptr;
    if (wearable) out.push_back(root);
    if (root->getChildren())
        for (size_t i = 0; i < root->getChildren()->size(); ++i)
            collectAppearance((*root->getChildren())[i], out);
}

void applyAvatarTask(const boost::shared_ptr<RBX::DataModel>& dm, const std::string& base,
                     const std::vector<AvatarEntry>& entries, std::array<int, 6> body,
                     const std::vector<boost::shared_ptr<RBX::Instance>>& hats) {
    dm->submitTask([base, entries, body, hats](RBX::DataModel* model) {
        RBX::Network::Players* players = model->find<RBX::Network::Players>();
        RBX::Network::Player* player = players
            ? RBX::Instance::fastDynamicCast<RBX::Network::Player>(players->getLocalPlayerDangerous())
            : nullptr;
        if (!player || !player->getCharacter()) {
            g_avatarBusy.store(false);
            return;
        }
        ensureCharacterAttachments(RBX::Humanoid::modelIsCharacter(player->getCharacter()));
        std::vector<boost::shared_ptr<RBX::Instance>> worn;
        for (const AvatarEntry& e : entries) {
            if (isAccessoryModel(e.type)) continue;
            if (e.type == "shirt") {
                boost::shared_ptr<RBX::Shirt> item = RBX::Creatable<RBX::Instance>::create<RBX::Shirt>();
                item->setTemplate(RBX::TextureId(avatarUrl(base, e.id)));
                worn.push_back(item);
            } else if (e.type == "pants") {
                boost::shared_ptr<RBX::Pants> item = RBX::Creatable<RBX::Instance>::create<RBX::Pants>();
                item->setTemplate(RBX::TextureId(avatarUrl(base, e.id)));
                worn.push_back(item);
            } else if (e.type == "tshirt") {
                boost::shared_ptr<RBX::ShirtGraphic> item =
                    RBX::Creatable<RBX::Instance>::create<RBX::ShirtGraphic>();
                RBX::ShirtGraphic::prop_Graphic.setValue(item.get(),
                    RBX::TextureId(avatarUrl(base, e.id)));
                worn.push_back(item);
            } else if (e.type == "face") {
                boost::shared_ptr<RBX::Decal> item = RBX::Creatable<RBX::Instance>::create<RBX::Decal>();
                item->setName("face");
                item->setTexture(RBX::TextureId(avatarUrl(base, e.id)));
                worn.push_back(item);
            }
        }
        boost::shared_ptr<RBX::BodyColors> colors = RBX::Creatable<RBX::Instance>::create<RBX::BodyColors>();
        RBX::BodyColors::prop_HeadColor.setValue(colors.get(), RBX::BrickColor(body[0]));
        RBX::BodyColors::prop_LeftArmColor.setValue(colors.get(), RBX::BrickColor(body[1]));
        RBX::BodyColors::prop_RightArmColor.setValue(colors.get(), RBX::BrickColor(body[2]));
        RBX::BodyColors::prop_TorsoColor.setValue(colors.get(), RBX::BrickColor(body[3]));
        RBX::BodyColors::prop_LeftLegColor.setValue(colors.get(), RBX::BrickColor(body[4]));
        RBX::BodyColors::prop_RightLegColor.setValue(colors.get(), RBX::BrickColor(body[5]));
        worn.insert(worn.begin(), colors);
        for (const boost::shared_ptr<RBX::Instance>& model : hats) collectAppearance(model, worn);

        for (const boost::shared_ptr<RBX::Instance>& item : worn) {
            if (!item) continue;
            try {
                player->loadCharacterAppearanceScript(item);
            } catch (const std::exception& e) {
                RBX::StandardOut::singleton()->printf(RBX::MESSAGE_WARNING,
                    "RBLX2016: avatar item failed: %s", e.what());
            }
        }
        g_avatarCharacter.store(player->getCharacter());
        g_avatarBusy.store(false);
        RBX::StandardOut::singleton()->printf(RBX::MESSAGE_INFO,
            "RBLX2016: avatar applied (%d items, body %d/%d/%d/%d/%d/%d)",
            (int)worn.size(), body[0], body[1], body[2], body[3], body[4], body[5]);
    }, RBX::DataModelJob::Write);
}

// Models load off the main thread, like the engine's own content fetches.
void startAvatarApplication(const boost::shared_ptr<RBX::DataModel>& dm,
                            const std::string& spec, const std::string& base) {
    if (g_avatarBusy.exchange(true)) return;
    std::thread([dm, spec, base]() {
        std::array<int, 6> body;
        std::vector<AvatarEntry> entries = parseAvatarSpec(spec, body.data());
        std::vector<boost::shared_ptr<RBX::Instance>> hats;
        try {
            RBX::ContentProvider* contentProvider =
                RBX::ServiceProvider::find<RBX::ContentProvider>(dm.get());
            for (const AvatarEntry& e : entries) {
                if (!isAccessoryModel(e.type) || !contentProvider) continue;
                std::vector<boost::shared_ptr<RBX::Instance>> loaded;
                try {
                    contentProvider->blockingLoadInstances(
                        RBX::ContentId(avatarUrl(base, e.id)), loaded);
                } catch (const std::exception& ex) {
                    RBX::StandardOut::singleton()->printf(RBX::MESSAGE_WARNING,
                        "RBLX2016: accessory %lld failed: %s", e.id, ex.what());
                    continue;
                }
                for (const boost::shared_ptr<RBX::Instance>& inst : loaded)
                    if (inst) hats.push_back(inst);
            }
        } catch (...) {
            g_avatarBusy.store(false);
            return;
        }
        try {
            applyAvatarTask(dm, base, entries, body, hats);
        } catch (const std::exception& e) {
            RBX::StandardOut::singleton()->printf(RBX::MESSAGE_WARNING,
                "RBLX2016: avatar apply failed: %s", e.what());
            g_avatarBusy.store(false);
        }
    }).detach();
}

}  // namespace

EMSCRIPTEN_KEEPALIVE void rblx_set_render_option(const char* name, int value) {
    if (!name) return;
    std::string option(name);
    if (option == "lightgrid") rblx2016_light_grid_mode = value;
    else if (option == "shaders") rblx2016_gles2_shaders = value;
    std::fprintf(stderr, "RBLX2016: render option %s=%d\n", name, value);
}

EMSCRIPTEN_KEEPALIVE void rblx_set_avatar(const char* spec) {
    g_avatarSpec = spec ? spec : "";
    g_avatarCharacter.store(nullptr);
    RBX::StandardOut::singleton()->printf(RBX::MESSAGE_INFO,
        "RBLX2016: avatar spec %s", g_avatarSpec.c_str());
}

EMSCRIPTEN_KEEPALIVE void rblx_frame() {
    rblx_audio_pump();
    if (g_exitRequested.exchange(false))
        emscripten_run_script("window.rblxLeaveGame && window.rblxLeaveGame()");
    // The place task holds the Write lock while it deserializes and joins, so
    // taking that lock here would freeze the tab. The load runs on scheduler
    // threads; skip frames until it finishes.
    if (g_placeParseStarted.load() && !g_placeLoaded.load())
        return;
    if (g_dataModel) {
        static RBX::Time lastRender = RBX::Time::now<RBX::Time::Fast>();
        RBX::Time now = RBX::Time::now<RBX::Time::Fast>();
        float dt = static_cast<float>((now - lastRender).seconds());
        lastRender = now;
        if (dt < 0.0f) dt = 0.0f;
        if (dt > 0.1f) dt = 0.1f;
        // The desktop client's frame loop does this before rendering; it drives
        // UserInputService, RenderStepped and the camera.
        RBX::DataModel::LegacyLock lock(g_dataModel, RBX::DataModelJob::Write);
        g_dataModel->renderStep(dt);
        // Places without a SpawnLocation (or with broken spawn scripts) never
        // get a character; retry auto-load once a second, ten times.
        if (g_placeLoaded.load() && g_characterAttempts < 10) {
            RBX::Network::Players* spawnPlayers = g_dataModel->find<RBX::Network::Players>();
            RBX::Network::Player* spawnPlayer = spawnPlayers
                ? RBX::Instance::fastDynamicCast<RBX::Network::Player>(
                      spawnPlayers->getLocalPlayerDangerous()) : nullptr;
            std::chrono::steady_clock::time_point nowAttempt = std::chrono::steady_clock::now();
            if (spawnPlayer && !spawnPlayer->getCharacter() &&
                std::chrono::duration<double>(nowAttempt - g_lastCharacterAttempt).count() > 1.0) {
                g_lastCharacterAttempt = nowAttempt;
                ++g_characterAttempts;
                try {
                    spawnPlayer->loadCharacter(true, std::string());
                } catch (const std::exception& e) {
                    RBX::StandardOut::singleton()->printf(RBX::MESSAGE_WARNING,
                        "RBLX2016: character spawn attempt %d failed: %s",
                        g_characterAttempts, e.what());
                }
            }
        }
        // Places can leave the camera on a fixed/spectator type and never hand
        // control back. Restore the stock Custom camera once the character has
        // been alive briefly; a place that re-asserts its camera still wins.
        if (RBX::Workspace* cameraWorkspace = g_dataModel->getWorkspace()) {
            if (RBX::Camera* camera = cameraWorkspace->getCamera()) {
                RBX::Network::Players* cameraPlayers = g_dataModel->find<RBX::Network::Players>();
                RBX::Network::Player* cameraPlayer = cameraPlayers
                    ? RBX::Instance::fastDynamicCast<RBX::Network::Player>(
                          cameraPlayers->getLocalPlayerDangerous()) : nullptr;
                RBX::Instance* cameraCharacter = cameraPlayer ? cameraPlayer->getCharacter() : nullptr;
                if (cameraCharacter != g_cameraCharacter.load()) {
                    g_cameraCharacter.store(cameraCharacter);
                    g_cameraChecked.store(false);
                    g_characterSeenAt = std::chrono::steady_clock::now();
                }
                if (cameraCharacter && !g_cameraChecked.load() &&
                    std::chrono::duration<double>(
                        std::chrono::steady_clock::now() - g_characterSeenAt).count() > 2.5) {
                    g_cameraChecked.store(true);
                    RBX::Humanoid* cameraHumanoid = RBX::Humanoid::modelIsCharacter(cameraCharacter);
                    if (cameraHumanoid && camera->getCameraType() != RBX::Camera::CUSTOM_CAMERA &&
                        camera->getCameraSubjectInstanceDangerous() != cameraHumanoid) {
                        int staleType = (int)camera->getCameraType();
                        camera->setCameraSubject(cameraHumanoid);
                        camera->setCameraType(RBX::Camera::CUSTOM_CAMERA);
                        RBX::StandardOut::singleton()->printf(RBX::MESSAGE_INFO,
                            "RBLX2016: camera was left on type %d, restored Custom", staleType);
                    }
                }
            }
        }
        // Wear the catalog avatar as soon as a character exists, and again
        // after a respawn. Hat models load on a worker thread.
        if (!g_avatarSpec.empty() && !g_avatarBusy.load()) {
            RBX::Network::Players* avatarPlayers = g_dataModel->find<RBX::Network::Players>();
            RBX::Network::Player* avatarPlayer = avatarPlayers
                ? RBX::Instance::fastDynamicCast<RBX::Network::Player>(
                      avatarPlayers->getLocalPlayerDangerous()) : nullptr;
            RBX::Instance* avatarChar = avatarPlayer ? avatarPlayer->getCharacter() : nullptr;
            if (avatarChar && avatarChar != g_avatarCharacter.load())
                startAvatarApplication(g_dataModel, g_avatarSpec, g_assetBase);
        }
        if (g_placeLoaded.load() && !g_gameLoaded.load()) {
            if (!g_settleStarted.exchange(true)) {
                // Build the initial FastCluster/light-grid scene in one pass
                // while the stock loading screen is still up, instead of
                // letting pieces pop in over budgeted frames after game.Loaded.
                CRenderSettingsItem::singleton().setEagerBulkExecution(true);
                g_settleAt = std::chrono::steady_clock::now();
            }
            RBX::Network::Players* players = g_dataModel->find<RBX::Network::Players>();
            RBX::Network::Player* local = players
                ? RBX::Instance::fastDynamicCast<RBX::Network::Player>(players->getLocalPlayerDangerous()) : nullptr;
            bool hasCharacter = local && local->getCharacter();
            double sinceSettle = std::chrono::duration<double>(
                std::chrono::steady_clock::now() - g_settleAt).count();
            bool timedOut = sinceSettle > 8.0;
            int frames = g_renderFramesSinceLoad.load();
            rblx2016::WebView::SceneStatus scene = g_view->sceneStatus();
            // The character appears on the scheduler threads, so wait for it,
            // then for the renderer to finish building the scene. A place that
            // never resolves its content must not keep the screen up forever.
            bool sceneReady = scene.settled && frames >= 2;
            bool hardTimeout = sinceSettle > 20.0;
            if ((hasCharacter || timedOut) && (sceneReady || hardTimeout)) {
                g_gameLoaded.store(true);
                RBX::StandardOut::singleton()->printf(RBX::MESSAGE_INFO,
                    "RBLX2016: game loaded (character=%d timeout=%d settle=%.0fms frames=%d queue=%u waits=%d textures=%d parts=%d)",
                    (int)hasCharacter, (int)timedOut, sinceSettle * 1000.0, frames,
                    (unsigned)scene.clusterQueue, (int)scene.waitingForAssets,
                    (int)scene.texturesPending, g_workspaceParts.load());
                CRenderSettingsItem::singleton().setEagerBulkExecution(false);
                g_dataModel->setIsGameLoaded(true);
            }
        }
    }
    if (g_view) {
        g_view->frame();
        g_frameCount++;
        if (g_placeLoaded.load() && !g_gameLoaded.load())
            g_renderFramesSinceLoad.fetch_add(1);
    }
}

EMSCRIPTEN_KEEPALIVE void rblx_resize(int w, int h) {
    if (g_view) g_view->resize(w, h);
}

// Browser fullscreen changes are asynchronous; keep the engine's Fullscreen
// setting in sync when the user enters or leaves via the browser UI.
EMSCRIPTEN_KEEPALIVE void rblx_set_fullscreen(int on) {
    RBX::GameBasicSettings::singleton().setFullScreen(on != 0);
}

// Optional main-page setting: the 2016 scheduler throttles physics/rendering
// to 30 FPS by default. Clearing the throttle lets the browser rAF rate drive
// the frame loop.
EMSCRIPTEN_KEEPALIVE void rblx_set_unlock_fps(int on) {
    RBX::DataModel::throttleAt30Fps = (on == 0);
    RBX::TaskScheduler::singleton().DataModel30fpsThrottle = (on == 0);
    RBX::StandardOut::singleton()->printf(RBX::MESSAGE_INFO,
        "RBLX2016: FPS cap %s", on ? "unlocked" : "30 FPS");
}

EMSCRIPTEN_KEEPALIVE int rblx_has_view() { return g_view ? 1 : 0; }

EMSCRIPTEN_KEEPALIVE int rblx_frame_count() { return g_frameCount; }

EMSCRIPTEN_KEEPALIVE void rblx_set_camera(float ex, float ey, float ez, float tx, float ty, float tz) {
    if (!g_dataModel) return;
    g_dataModel->submitTask([=](RBX::DataModel*) {
        RBX::Workspace* ws = g_dataModel->getWorkspace();
        if (!ws || !ws->getCamera()) return;
        G3D::CoordinateFrame cf;
        cf.translation = G3D::Vector3(ex, ey, ez);
        cf.lookAt(G3D::Vector3(tx, ty, tz));
        ws->getCamera()->setCameraCoordinateFrame(cf);
    }, RBX::DataModelJob::Write);
}

EMSCRIPTEN_KEEPALIVE void rblx_key_event(int keyCode, int modCode, int modifiedKey, int isDown) {
    if (!g_dataModel) return;
    RBX::UserInputService* uis = RBX::ServiceProvider::find<RBX::UserInputService>(g_dataModel.get());
    if (!uis) return;
    RBX::KeyCode key = static_cast<RBX::KeyCode>(keyCode);
    RBX::ModCode mod = static_cast<RBX::ModCode>(modCode);
    uis->setKeyState(key, mod, static_cast<char>(modifiedKey), isDown != 0);
    shared_ptr<RBX::InputObject> event = RBX::Creatable<RBX::Instance>::create<RBX::InputObject>(
        RBX::InputObject::TYPE_KEYBOARD,
        isDown ? RBX::InputObject::INPUT_STATE_BEGIN : RBX::InputObject::INPUT_STATE_END,
        key, mod, static_cast<char>(modifiedKey), g_dataModel.get());
    uis->fireInputEvent(event, NULL);
}

EMSCRIPTEN_KEEPALIVE void rblx_mouse_button(int button, int x, int y, int isDown) {
    if (!g_dataModel) return;
    RBX::UserInputService* uis = RBX::ServiceProvider::find<RBX::UserInputService>(g_dataModel.get());
    if (!uis) return;
    // DOM order is 0=left, 1=middle, 2=right. Roblox's MouseButton2 is the
    // right button and MouseButton3 is the middle button.
    RBX::InputObject::UserInputType type = RBX::InputObject::TYPE_MOUSEBUTTON1;
    if (button == 2) type = RBX::InputObject::TYPE_MOUSEBUTTON2;
    else if (button == 1) type = RBX::InputObject::TYPE_MOUSEBUTTON3;
    shared_ptr<RBX::InputObject> event = RBX::Creatable<RBX::Instance>::create<RBX::InputObject>(
        type,
        isDown ? RBX::InputObject::INPUT_STATE_BEGIN : RBX::InputObject::INPUT_STATE_END,
        G3D::Vector3(static_cast<float>(x), static_cast<float>(y), 0),
        G3D::Vector3(0, 0, 0), g_dataModel.get());
    uis->fireInputEvent(event, NULL);
}

EMSCRIPTEN_KEEPALIVE void rblx_mouse_wheel(int x, int y, int delta) {
    if (!g_dataModel) return;
    RBX::UserInputService* uis = RBX::ServiceProvider::find<RBX::UserInputService>(g_dataModel.get());
    if (!uis) return;
    shared_ptr<RBX::InputObject> event = RBX::Creatable<RBX::Instance>::create<RBX::InputObject>(
        RBX::InputObject::TYPE_MOUSEWHEEL,
        RBX::InputObject::INPUT_STATE_CHANGE,
        G3D::Vector3(static_cast<float>(x), static_cast<float>(y), static_cast<float>(delta)),
        G3D::Vector3(0, 0, 0), g_dataModel.get());
    uis->fireInputEvent(event, NULL);
}

EMSCRIPTEN_KEEPALIVE void rblx_mouse_move(int x, int y, int dx, int dy) {
    if (!g_dataModel) return;
    RBX::UserInputService* uis = RBX::ServiceProvider::find<RBX::UserInputService>(g_dataModel.get());
    if (!uis) return;
    shared_ptr<RBX::InputObject> event = RBX::Creatable<RBX::Instance>::create<RBX::InputObject>(
        RBX::InputObject::TYPE_MOUSEMOVEMENT,
        RBX::InputObject::INPUT_STATE_CHANGE,
        G3D::Vector3(static_cast<float>(x), static_cast<float>(y), 0),
        G3D::Vector3(static_cast<float>(dx), static_cast<float>(dy), 0), g_dataModel.get());
    uis->fireInputEvent(event, NULL);
}

EMSCRIPTEN_KEEPALIVE void rblx_touch_event(int id, float x, float y, int state) {
    if (!g_dataModel || state < 0 || state > 3) return;
    RBX::TouchInputService* service =
        RBX::ServiceProvider::find<RBX::TouchInputService>(g_dataModel.get());
    if (!service) {
        service = RBX::ServiceProvider::create<RBX::TouchInputService>(g_dataModel.get());
    }
    if (!service) return;
    service->addTouchToBuffer(
        reinterpret_cast<void*>(static_cast<uintptr_t>(id + 1)),
        G3D::Vector3(x, y, 0),
        static_cast<RBX::InputObject::UserInputState>(state));
}

EMSCRIPTEN_KEEPALIVE void rblx_stop() {
    if (g_view) { g_view->shutdown(); g_view.reset(); }
    RBX::DataModel::closeDataModel(g_dataModel);
    g_dataModel.reset();
    RBX::Game::globalExit();
}

EMSCRIPTEN_KEEPALIVE int rblx_last_error() { return g_lastError; }

EMSCRIPTEN_KEEPALIVE void rblx_force_character() {
    if (!g_dataModel) return;
    RBX::Network::Players* players = g_dataModel->find<RBX::Network::Players>();
    RBX::Network::Player* lp = players ? RBX::Instance::fastDynamicCast<RBX::Network::Player>(players->getLocalPlayerDangerous()) : nullptr;
    if (!lp) return;
    std::fprintf(stderr, "RBLX2016: forcing character load (direct)\n");
    try {
        g_dataModel->submitTask([lp](RBX::DataModel*) {
            std::fprintf(stderr, "RBLX2016: force task run lp=%p char=%p\n", (void*)lp, (void*)lp->getCharacter());
            if (!lp->getCharacter()) {
                lp->loadCharacter(true, std::string());
            }
        }, RBX::DataModelJob::Write);
    } catch (const std::exception& e) {
        std::fprintf(stderr, "RBLX2016: force character failed: %s\n", e.what());
    } catch (...) {
        std::fprintf(stderr, "RBLX2016: force character failed: unknown\n");
    }
}

EMSCRIPTEN_KEEPALIVE void rblx_run_script(const char* source) {
    if (!g_dataModel || !source) return;
    RBX::Network::Players* players = g_dataModel->find<RBX::Network::Players>();
    RBX::Network::Player* lp = players ? RBX::Instance::fastDynamicCast<RBX::Network::Player>(players->getLocalPlayerDangerous()) : nullptr;
    if (!lp) { std::fprintf(stderr, "RBLX2016: runScript no local player\n"); return; }
    std::string src(source);
    std::fprintf(stderr, "RBLX2016: runScript injecting %d bytes\n", (int)src.size());
    g_dataModel->submitTask([lp, src](RBX::DataModel*) {
        RBX::PlayerScripts* ps = lp->findFirstChildOfType<RBX::PlayerScripts>();
        if (!ps) { std::fprintf(stderr, "RBLX2016: runScript no PlayerScripts\n"); return; }
        shared_ptr<RBX::LocalScript> script = RBX::Creatable<RBX::Instance>::create<RBX::LocalScript>();
        script->setName("InjectedScript");
        script->setEmbeddedCode(RBX::ProtectedString::fromTrustedSource(src));
        script->setParent(ps);
    }, RBX::DataModelJob::Write);
}

EMSCRIPTEN_KEEPALIVE void rblx_debug_player() {
    if (!g_dataModel) return;
    g_dataModel->submitTask([](RBX::DataModel*) {
        RBX::Network::Players* players = g_dataModel->find<RBX::Network::Players>();
        RBX::Network::Player* lp = players ? RBX::Instance::fastDynamicCast<RBX::Network::Player>(players->getLocalPlayerDangerous()) : nullptr;
        if (!lp) { std::fprintf(stderr, "RBLX2016: no local player\n"); return; }
        RBX::Instance* character = lp->getCharacter();
        float x=0,y=0,z=0;
        if (character) {
            if (RBX::PartInstance* root = RBX::Instance::fastDynamicCast<RBX::PartInstance>(character->findFirstChildByName("HumanoidRootPart")))
                { G3D::Vector3 t = root->getCoordinateFrame().translation; x=t.x; y=t.y; z=t.z; }
        }
        int parts = character ? character->countDescendantsOfType<RBX::PartInstance>() : 0;
        float health = -1.0f;
        if (character) {
            if (RBX::Humanoid* h = RBX::Humanoid::modelIsCharacter(character))
                health = h->getHealth();
        }
        float cx=0,cy=0,cz=0;
        int camType=-1, vw=0, vh=0;
        std::string subject = "(none)";
        if (RBX::Workspace* ws = g_dataModel->getWorkspace()) {
            if (ws->getCamera()) {
                G3D::Vector3 t = ws->getCamera()->getCameraCoordinateFrame().translation; cx=t.x; cy=t.y; cz=t.z;
                camType = (int)ws->getCamera()->getCameraType();
                G3D::Vector2int16 vp = ws->getCamera()->getViewport(); vw = vp.x; vh = vp.y;
                if (RBX::Instance* s = ws->getCamera()->getCameraSubjectInstanceDangerous()) subject = s->getName();
            }
        }
        RBX::PlayerScripts* ps = lp->findFirstChildOfType<RBX::PlayerScripts>();
        std::fprintf(stderr, "RBLX2016: player name=%s character=%s pos=%.1f %.1f %.1f parts=%d health=%.0f playerscripts=%s cam=(%.1f %.1f %.1f) type=%d viewport=%dx%d subject=%s\n",
            lp->getName().c_str(), character ? character->getName().c_str() : "(none)", x,y,z, parts, health, ps ? "yes" : "no", cx,cy,cz, camType, vw, vh, subject.c_str());
        if (ps && ps->getChildren()) {
            RBX::Instances kids = *ps->getChildren();
            std::fprintf(stderr, "RBLX2016: playerscripts children=%d\n", (int)kids.size());
            for (size_t i = 0; i < kids.size() && i < 12; ++i)
                std::fprintf(stderr, "  %s (%s)\n", kids[i]->getName().c_str(), kids[i]->getClassName().c_str());
        }
        if (RBX::Instance* backpack = lp->findFirstChildByName("Backpack")) {
            if (!backpack->getChildren()) { std::fprintf(stderr, "RBLX2016: backpack children=<null>\n"); return; }
            RBX::Instances items = *backpack->getChildren();
            std::fprintf(stderr, "RBLX2016: backpack items=%d\n", (int)items.size());
            for (size_t i = 0; i < items.size() && i < 10; ++i) {
                if (RBX::HopperBin* bin = RBX::Instance::fastDynamicCast<RBX::HopperBin>(items[i].get()))
                    std::fprintf(stderr, "RBLX2016: bp item %s (HopperBin) type=%d active=%d\n", items[i]->getName().c_str(), (int)bin->getBinType(), (int)bin->active);
                else
                    std::fprintf(stderr, "RBLX2016: bp item %s (%s)\n", items[i]->getName().c_str(), items[i]->getClassName().c_str());
            }
        }
        if (character && character->getChildren()) {
            RBX::Instances kids = *character->getChildren();
            std::fprintf(stderr, "RBLX2016: character children=%d\n", (int)kids.size());
            for (size_t i = 0; i < kids.size() && i < 15; ++i)
                std::fprintf(stderr, "RBLX2016: char item %s (%s)\n", kids[i]->getName().c_str(), kids[i]->getClassName().c_str());
        }
    }, RBX::DataModelJob::Read);
}

EMSCRIPTEN_KEEPALIVE void rblx_debug_joints() {
    if (!g_dataModel) return;
    g_dataModel->submitTask([](RBX::DataModel*) {
        RBX::Workspace* ws = g_dataModel->getWorkspace();
        if (!ws) return;
        int hinges=0, motors=0, welds=0, snaps=0, parts=0;
        int printed = 0;
        std::function<void(RBX::Instance*)> walk = [&](RBX::Instance* node) {
            const std::string& c = node->getClassNameStr();
            if (c == "Part") ++parts;
            else if (c == "Hinge") ++hinges;
            else if (c == "Motor") ++motors;
            else if (c == "Weld") ++welds;
            else if (c == "Snap") ++snaps;
            if ((c == "Hinge" || c == "Motor") && printed < 12) {
                ++printed;
                if (RBX::JointInstance* j = RBX::Instance::fastDynamicCast<RBX::JointInstance>(node)) {
                    RBX::PartInstance* p0 = j->getPart0();
                    RBX::PartInstance* p1 = j->getPart1();
                    G3D::Vector3 t = p1 ? p1->getCoordinateFrame().translation : G3D::Vector3(0,0,0);
                    float desired = -1, current = -1;
                    if (RBX::Motor* m = RBX::Instance::fastDynamicCast<RBX::Motor>(node)) {
                        desired = m->getDesiredAngle();
                        current = m->getCurrentAngle();
                    }
                    std::fprintf(stderr, "RBLX2016: joint %s %s part0=%s part1=%s p1=(%.1f %.1f %.1f) desired=%.2f current=%.2f\n",
                        c.c_str(), node->getName().c_str(),
                        p0 ? p0->getName().c_str() : "(none)", p1 ? p1->getName().c_str() : "(none)", t.x, t.y, t.z, desired, current);
                }
            }
            if (node->getChildren())
                for (size_t i = 0; i < node->getChildren()->size(); ++i) walk((*node->getChildren())[i].get());
        };
        walk(ws);
        std::fprintf(stderr, "RBLX2016: workspace parts=%d hinges=%d motors=%d welds=%d snaps=%d\n", parts, hinges, motors, welds, snaps);
    }, RBX::DataModelJob::Read);
}

EMSCRIPTEN_KEEPALIVE void rblx_debug_anim() {
    if (!g_dataModel) return;
    g_dataModel->submitTask([](RBX::DataModel*) {
        RBX::Network::Players* players = g_dataModel->find<RBX::Network::Players>();
        RBX::Network::Player* lp = players ? RBX::Instance::fastDynamicCast<RBX::Network::Player>(players->getLocalPlayerDangerous()) : nullptr;
        RBX::ModelInstance* character = lp ? lp->getCharacter() : nullptr;
        if (!character) { std::fprintf(stderr, "RBLX2016: anim no character\n"); return; }
        RBX::Humanoid* humanoid = RBX::Humanoid::modelIsCharacter(character);
        RBX::PartInstance* torso = RBX::Instance::fastDynamicCast<RBX::PartInstance>(character->findFirstChildByName("Torso"));
        if (!torso) torso = RBX::Instance::fastDynamicCast<RBX::PartInstance>(character->findFirstChildByName("HumanoidRootPart"));
        if (!torso) { std::fprintf(stderr, "RBLX2016: anim no torso\n"); return; }
        std::fprintf(stderr, "RBLX2016: anim state=%d walkspeed=%.1f\n", humanoid ? (int)humanoid->getCurrentStateType() : -1, humanoid ? humanoid->getWalkSpeed() : 0.0f);
        {
            std::function<void(RBX::Instance*)> jw = [&](RBX::Instance* n) {
                if (RBX::Motor6D* m = RBX::Instance::fastDynamicCast<RBX::Motor6D>(n))
                    std::fprintf(stderr, "RBLX2016: anim joint %s current=%.3f desired=%.3f\n", m->getName().c_str(), m->getCurrentAngle(), m->getDesiredAngle());
                if (n->getChildren()) for (size_t i = 0; i < n->getChildren()->size(); ++i) jw((*n->getChildren())[i].get());
            };
            jw(character);
        }
        const char* names[] = {"Head", "Left Arm", "Right Arm", "Left Leg", "Right Leg"};
        for (int i = 0; i < 5; ++i) {
            RBX::PartInstance* part = RBX::Instance::fastDynamicCast<RBX::PartInstance>(character->findFirstChildByName(names[i]));
            if (!part) continue;
            G3D::CoordinateFrame rel = torso->getCoordinateFrame().inverse() * part->getCoordinateFrame();
            std::fprintf(stderr, "RBLX2016: anim %s rot=(%.3f %.3f %.3f / %.3f %.3f %.3f)\n", names[i],
                rel.rotation[0][0], rel.rotation[0][1], rel.rotation[0][2],
                rel.rotation[1][0], rel.rotation[1][1], rel.rotation[1][2]);
        }
    }, RBX::DataModelJob::Read);
}

EMSCRIPTEN_KEEPALIVE void rblx_debug_asset(const char* path) {
    if (!path) return;
    std::ifstream in(path, std::ios::in | std::ios::binary);
    if (!in) { std::fprintf(stderr, "RBLX2016: asset missing %s\n", path); return; }
    std::stringstream ss; ss << in.rdbuf();
    std::string data = ss.str();
    std::fprintf(stderr, "RBLX2016: asset %s bytes=%d\n", path, (int)data.size());
    std::istringstream buffered(data);
    try {
        RBX::Instances instances;
        Serializer().loadInstances(buffered, instances);
        std::fprintf(stderr, "RBLX2016: asset instances=%d\n", (int)instances.size());
        for (size_t i = 0; i < instances.size() && i < 5; ++i)
            std::fprintf(stderr, "  %s (%s) children=%d\n", instances[i]->getName().c_str(), instances[i]->getClassName().c_str(), (int)instances[i]->numChildren());
    } catch (const std::exception& e) {
        std::fprintf(stderr, "RBLX2016: asset parse error: %s\n", e.what());
    }
}

EMSCRIPTEN_KEEPALIVE void rblx_debug_gui() {
    if (!g_dataModel) return;
    g_dataModel->submitTask([](RBX::DataModel*) {
        RBX::CoreGuiService* cg = RBX::ServiceProvider::find<RBX::CoreGuiService>(g_dataModel.get());
        if (!cg) { std::fprintf(stderr, "RBLX2016: gui no CoreGui\n"); return; }
        shared_ptr<RBX::ScreenGui> gui = cg->getRobloxScreenGui();
        if (!gui) { std::fprintf(stderr, "RBLX2016: gui no RobloxGui\n"); return; }
        std::fprintf(stderr, "RBLX2016: gui RobloxGui pos=(%.1f %.1f) size=(%.1f %.1f) children=%d\n",
            gui->getAbsolutePosition().x, gui->getAbsolutePosition().y,
            gui->getAbsoluteSize().x, gui->getAbsoluteSize().y, (int)gui->numChildren());
        int printed = 0;
        std::function<void(RBX::Instance*, int)> walk = [&](RBX::Instance* node, int depth) {
            if (printed++ > 4000 || depth > 12) return;
            if (RBX::GuiBase2d* g = RBX::Instance::fastDynamicCast<RBX::GuiBase2d>(node)) {
                RBX::GuiObject* go = RBX::Instance::fastDynamicCast<RBX::GuiObject>(node);
                std::fprintf(stderr, "RBLX2016: gui %*s%s (%s) rect=(%.1f %.1f %.1f %.1f) vis=%d active=%d\n", depth*2, "",
                    node->getName().c_str(), node->getClassName().c_str(),
                    g->getRect2D().x0(), g->getRect2D().y0(), g->getRect2D().x1(), g->getRect2D().y1(),
                    go ? (int)go->getVisible() : -1, go ? (int)go->getActive() : -1);
            }
            if (node->getChildren()) for (size_t i = 0; i < node->getChildren()->size(); ++i)
                walk((*node->getChildren())[i].get(), depth + 1);
        };
        walk(gui.get(), 0);
        if (RBX::Network::Players* players = g_dataModel->find<RBX::Network::Players>()) {
            if (RBX::Network::Player* lp = RBX::Instance::fastDynamicCast<RBX::Network::Player>(players->getLocalPlayerDangerous())) {
                if (RBX::PlayerGui* pg = lp->findFirstChildOfType<RBX::PlayerGui>()) {
                    std::fprintf(stderr, "RBLX2016: gui PlayerGui children=%d\n", (int)pg->numChildren());
                    printed = 0;
                    walk(pg, 0);
                }
            }
        }
    }, RBX::DataModelJob::Read);
}

EMSCRIPTEN_KEEPALIVE int rblx_test_thread() {
    static std::atomic<int> flag(0);
    flag = 0;
    std::thread t([](){ flag = 1; });
    t.join();
    return flag.load();
}

}  // extern "C"

int main() {
    /* The browser shell calls rblx_start_place; do not auto-run. */
    return 0;
}
