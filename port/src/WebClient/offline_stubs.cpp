/* Offline stubs for HTTP, memory statistics, and the few engine globals that
 * normally live in network-only translation units.  Nothing here opens a
 * connection. */
#include <string>
#include <boost/function.hpp>

#include "util/Http.h"
#include "util/MemoryStats.h"
#include "NetworkSettings.h"
#include "GuidRegistryService.h"
#include "network/api.h"
#include "util/ProgramMemoryChecker.h"
#include "script/LuaVM.h"
#include "network/Player.h"
#include "network/Players.h"
#include "raknet/Source/PacketPriority.h"
#include "raknet/Source/RakMemoryOverride.h"
#include <cstdlib>
#include <cstring>
#include <sstream>
#include <stdexcept>
#include "raknet/Source/Itoa.h"
#include "raknet/Source/SuperFastHash.h"

#ifdef __EMSCRIPTEN__
#include <emscripten/emscripten.h>
#include <emscripten/fetch.h>
#include <emscripten/threading.h>
#include <cstring>
#include <mutex>
#include <unordered_map>
#include <vector>
#endif

namespace RBX {

Http::API Http::defaultApi = Http::WinHttp;
std::string Http::gameSessionID;
int Http::playerCount = 0;
std::string Http::placeID;

void Http::init() {}
void Http::init(API, CookieSharingPolicy) {}
void Http::SetUseStatistics(bool) {}
void Http::SetUseCurl(bool) {}

#ifdef __EMSCRIPTEN__
namespace {

// Synchronous fetch; worker threads only (the browser forbids it on the main
// thread). ContentProvider and the HttpService request threads are pthreads.
bool httpFetchSync(const std::string& url, const char* method,
                   const std::string& body, const std::string& contentType,
                   const RBX::HttpAux::AdditionalHeaders& extra,
                   std::string& response, std::string& error) {
    response.clear();
    error.clear();
    if (emscripten_is_main_browser_thread()) {
        error = "synchronous HTTP on the browser main thread";
        return false;
    }
    emscripten_fetch_attr_t attr;
    emscripten_fetch_attr_init(&attr);
    std::strcpy(attr.requestMethod, method);
    attr.attributes = EMSCRIPTEN_FETCH_LOAD_TO_MEMORY | EMSCRIPTEN_FETCH_SYNCHRONOUS;

    // Emscripten fetch expects alternating name/value C strings, not
    // "Name: value" lines.
    std::vector<std::string> headerStorage;
    if (!contentType.empty()) {
        headerStorage.push_back("Content-Type");
        headerStorage.push_back(contentType);
    }
    for (RBX::HttpAux::AdditionalHeaders::const_iterator it = extra.begin(); it != extra.end(); ++it) {
        headerStorage.push_back(it->first);
        headerStorage.push_back(it->second);
    }
    std::vector<const char*> headerPointers;
    for (size_t i = 0; i < headerStorage.size(); ++i)
        headerPointers.push_back(headerStorage[i].c_str());
    headerPointers.push_back(NULL);
    attr.requestHeaders = headerPointers.data();

    if (!body.empty()) {
        attr.requestData = body.data();
        attr.requestDataSize = body.size();
    }
    attr.timeoutMSecs = 20000;

    emscripten_fetch_t* fetch = emscripten_fetch(&attr, url.c_str());
    if (!fetch) {
        error = "fetch failed to start";
        return false;
    }
    bool ok = fetch->status >= 200 && fetch->status < 300;
    if (ok) {
        response.assign(fetch->data ? fetch->data : "", fetch->numBytes);
    } else {
        error = "HTTP " + std::to_string(static_cast<int>(fetch->status));
        if (fetch->status == 0)
            error = "Network request failed (unreachable or blocked by CORS)";
    }
    emscripten_fetch_close(fetch);
    return ok;
}

void httpRunAsync(boost::function<void(std::string*, std::exception*)> handler,
                  std::string url, std::string method, std::string body,
                  std::string contentType, RBX::HttpAux::AdditionalHeaders extra) {
    boost::thread([=]() {
        std::string response, error;
        if (httpFetchSync(url, method.c_str(), body, contentType, extra, response, error)) {
            handler(&response, NULL);
        } else {
            std::runtime_error failure(error);
            handler(NULL, &failure);
        }
    }).detach();
}

}  // namespace
#endif

void Http::post(const std::string& input, const std::string& contentType, bool,
                boost::function<void(std::string*, std::exception*)> handler, bool) {
    if (!handler) return;
#ifdef __EMSCRIPTEN__
    httpRunAsync(handler, url, "POST", input, contentType, additionalHeaders);
#else
    std::string empty; handler(&empty, nullptr);
#endif
}
void Http::post(boost::shared_ptr<std::istream> input, const std::string& contentType, bool compress,
                boost::function<void(std::string*, std::exception*)> handler, bool) {
    std::string data;
    if (input) {
        std::ostringstream buffer;
        buffer << input->rdbuf();
        data = buffer.str();
    }
    post(data, contentType, compress, handler, true);
}
void Http::post(std::istream&, const std::string&, bool, std::string&, bool) {}

void Http::get(std::string& response, bool) {
#ifdef __EMSCRIPTEN__
    std::string error;
    httpFetchSync(url, "GET", "", "", additionalHeaders, response, error);
#else
    response.clear();
#endif
}

void Http::get(boost::function<void(std::string*, std::exception*)> handler, bool) {
    if (!handler) return;
#ifdef __EMSCRIPTEN__
    httpRunAsync(handler, url, "GET", "", "", additionalHeaders);
#else
    std::string empty; handler(&empty, nullptr);
#endif
}

namespace MemoryStats {
memsize_t freeMemoryBytes() { return (memsize_t)512 * 1024 * 1024; }
}

namespace Network {
std::string securityKey;
}

namespace MemoryStats {
memsize_t totalMemoryBytes() { return (memsize_t)2048 * 1024 * 1024; }
memsize_t usedMemoryBytes() { return (memsize_t)256 * 1024 * 1024; }
}

}  // namespace RBX

volatile const size_t rbxGoldHash = 0;

RBX_REGISTER_CLASS(RBX::Network::Player);
RBX_REGISTER_CLASS(RBX::Network::Players);
RBX_REGISTER_CLASS(RBX::NetworkSettings);
RBX_REGISTER_CLASS(RBX::Network::GuidRegistryService);
RBX_REGISTER_ENUM(RBX::NetworkSettings::PhysicsSendMethod);
RBX_REGISTER_ENUM(RBX::NetworkSettings::PhysicsReceiveMethod);
RBX_REGISTER_ENUM(PacketPriority);
RBX_REGISTER_ENUM(PacketReliability);
RBX_REGISTER_ENUM(RBX::Network::Player::MembershipType);
RBX_REGISTER_ENUM(RBX::Network::Player::ChatMode);
RBX_REGISTER_ENUM(RBX::Network::Players::PlayerChatType);
RBX_REGISTER_ENUM(RBX::Network::Players::ChatOption);

static void* rb_malloc(size_t n, const char*, unsigned) { return std::malloc(n); }
void* (*rakMalloc_Ex)(size_t, const char*, unsigned) = rb_malloc;

static void* rb_realloc(void* p, size_t n, const char*, unsigned) { return std::realloc(p, n); }
static void rb_free(void* p, const char*, unsigned) { std::free(p); }
void* (*rakRealloc_Ex)(void*, size_t, const char*, unsigned) = rb_realloc;
void (*rakFree_Ex)(void*, const char*, unsigned) = rb_free;

uint32_t SuperFastHashIncremental(const char* data, int len, unsigned int lastHash) {
    uint32_t h = lastHash ? lastHash : 2166136261u;
    for (int i = 0; i < len; ++i) { h ^= (unsigned char)data[i]; h *= 16777619u; }
    return h;
}

char* Itoa(int value, char* result, int base) {
    static const char digits[] = "0123456789abcdefghijklmnopqrstuvwxyz";
    char tmp[34]; int n = 0; unsigned int v = value < 0 ? (unsigned)(-value) : (unsigned)value;
    if (base < 2 || base > 36) { *result = 0; return result; }
    do { tmp[n++] = digits[v % base]; v /= base; } while (v);
    char* out = result; if (value < 0) *out++ = '-';
    while (n) *out++ = tmp[--n];
    *out = 0; return result;
}

namespace RakNet {
class SocketLayer { public: static const char* DomainNameToIP(const char*); };
const char* SocketLayer::DomainNameToIP(const char*) { return ""; }
}


namespace DFFlag {
bool UseAssetTypeHeader = true;
}

namespace FLog {
Channel NetworkStepsMultipliers = false;
}
