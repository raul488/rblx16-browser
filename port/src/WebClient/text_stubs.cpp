#include <string>
#include "StringConv.h"
#include "util/Http.h"

namespace RBX {
std::string utf8_encode(const SysPathString& path) { return std::string(path.begin(), path.end()); }
SysPathString utf8_decode(const std::string& path) { return SysPathString(path.begin(), path.end()); }
}
