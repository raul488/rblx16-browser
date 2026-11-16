/* Minimal URL helpers replacing the old w3c libwww parser.  Only the
 * scheme/host/path split used by ContentId is implemented. */
#include <cstring>
#include <string>

namespace {

std::string requestPart(const std::string& url, int wanted) {
    size_t schemeEnd = url.find("://");
    std::string scheme = schemeEnd == std::string::npos ? "" : url.substr(0, schemeEnd);
    size_t hostStart = schemeEnd == std::string::npos ? 0 : schemeEnd + 3;
    size_t pathStart = url.find_first_of("/?#", hostStart);
    std::string host = url.substr(hostStart,
                                  pathStart == std::string::npos ? std::string::npos : pathStart - hostStart);
    std::string path = pathStart == std::string::npos ? "" : url.substr(pathStart);
    size_t query = path.find_first_of("?#");
    if (query != std::string::npos) path = path.substr(0, query);

    if (wanted == 16) return scheme;  // PARSE_ACCESS
    if (wanted == 8) return host;     // PARSE_HOST
    if (wanted == 4) return path;     // PARSE_PATH
    return "";
}

char* copyOut(const std::string& value) {
    char* out = new char[value.size() + 1];
    std::memcpy(out, value.c_str(), value.size() + 1);
    return out;
}

}  // namespace

extern "C" char* HTParse(const char* aName, const char*, int wanted) {
    return copyOut(requestPart(aName ? aName : "", wanted));
}

extern "C" char* HTSimplify(char** filename) {
    return copyOut(filename && *filename ? *filename : "");
}
