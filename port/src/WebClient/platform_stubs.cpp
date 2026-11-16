/* Local platform values for the browser build.  Nothing here touches the
 * network; these are the OS/hardware queries the engine makes at startup. */
#include <string>
#include <boost/filesystem.hpp>

#include "rbx/SystemUtil.h"
#include "util/FileSystem.h"

namespace RBX {
namespace SystemUtil {

std::string getCPUMake() { return "WebAssembly"; }
uint64_t getCPUSpeed() { return 1000000000ull; }
uint64_t getCPULogicalCount() { return 1; }
uint64_t getCPUCoreCount() { return 1; }
uint64_t getCPUPhysicalCount() { return 1; }
bool isCPU64Bit() { return true; }
uint64_t getMBSysRAM() { return 2048; }
uint64_t getMBSysAvailableRAM() { return 1024; }
uint64_t getVideoMemory() { return 256; }
std::string osPlatform() { return "Web"; }
int osPlatformId() { return 0; }
std::string osVer() { return "browser"; }
std::string deviceName() { return "browser"; }
std::string getGPUMake() { return "WebGL2"; }
std::string getMaxRes() { return "1920x1080"; }

}  // namespace SystemUtil

namespace FileSystem {

boost::filesystem::path getUserDirectory(bool, FileSystemDir, const char*) {
    return boost::filesystem::path("/tmp/rblx2016");
}
boost::filesystem::path getLogsDirectory() {
    return boost::filesystem::path("/tmp/rblx2016/logs");
}

}  // namespace FileSystem
}  // namespace RBX
