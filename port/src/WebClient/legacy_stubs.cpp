/* Remaining offline platform stubs: cookies, legacy HTTP statistics, network
 * service names, and a RakString reader.  None of these perform I/O. */
#include <string>
#include <vector>

#include "util/Http.h"
#include "CookiesEngine.h"
#include "FastLog.h"
#include "BitStream.h"
#include "RakString.h"
#include "network/api.h"
#include "network/WebChatFilter.h"

// --- cookies ---------------------------------------------------------------

std::wstring CookiesEngine::getCookiesFilePath() { return L"/tmp/rblx2016/cookies"; }
void CookiesEngine::setCookiesFilePath(std::wstring&) {}
bool CookiesEngine::reportValue(CookiesEngine&, std::string, std::string) { return true; }

CookiesEngine::CookiesEngine(std::wstring) {}
int CookiesEngine::SetValue(std::string, std::string) { return 1; }
std::string CookiesEngine::GetValue(std::string, int* errorCode, bool* exists) {
    if (errorCode) *errorCode = 0;
    if (exists) *exists = false;
    return std::string();
}
int CookiesEngine::DeleteValue(std::string) { return 1; }

// --- legacy HTTP counters --------------------------------------------------

namespace RBX {

rbx::atomic<int> Http::cdnSuccessCount(0);
rbx::atomic<int> Http::cdnFailureCount(0);
rbx::atomic<int> Http::alternateCdnSuccessCount(0);
rbx::atomic<int> Http::alternateCdnFailureCount(0);
rbx::atomic<int> Http::robloxSuccessCount(0);
rbx::atomic<int> Http::robloxFailureCount(0);
double Http::lastCdnFailureTimeSpan = 0.0;
WindowAverage<double, double> Http::robloxResponce(16);
WindowAverage<double, double> Http::cdnResponce(16);
RBX::mutex* Http::robloxResponceLock = 0;
RBX::mutex* Http::cdnResponceLock = 0;

RBX::mutex* Http::getRobloxResponceLock() { return robloxResponceLock; }
RBX::mutex* Http::getCdnResponceLock() { return cdnResponceLock; }
bool Http::isRobloxSite(const char*) { return false; }
void Http::applyAdditionalHeaders(RBX::HttpAux::AdditionalHeaders&) {}

namespace Network {
extern const char* const sPhysicsPacketCache;
const char* const sPhysicsPacketCache = "PhysicsPacketCache";
extern const char* const sInstancePacketCache;
const char* const sInstancePacketCache = "InstancePacketCache";
extern const char* const sClusterPacketCache;
const char* const sClusterPacketCache = "ClusterPacketCache";
extern const char* const sOneQuarterClusterPacketCache;
const char* const sOneQuarterClusterPacketCache = "OneQuarterClusterPacketCacheBase";

void ConstructModerationFilterTextParamsAndHeaders(std::string, int, int, std::string,
                                                   std::stringstream&,
                                                   RBX::HttpAux::AdditionalHeaders&) {}

void initWithoutSecurity() {}
void initWithPlayerSecurity() {}
}  // namespace Network

}  // namespace RBX

// --- RakNet ----------------------------------------------------------------

namespace RakNet {

bool RakString::Deserialize(char* str, BitStream* bs) {
    bool b;
    unsigned short l;
    b = bs->Read(l);
    if (b && l > 0) b = bs->ReadAlignedBytes((unsigned char*)str, l);
    if (!b) str[0] = 0;
    str[l] = 0;
    return b;
}

}  // namespace RakNet

namespace DFLog {
FLog::Channel HttpTrace = false;
}

namespace DFFlag {
bool UseComSiftUpdatedWebChatFilterParamsAndHeader = false;
}
