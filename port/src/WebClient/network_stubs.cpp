/* Offline replacements for the network services that are not linked in the
 * browser build.  The engine only needs the class-name strings and the
 * BitStream operators; no connection is ever opened. */
#include <string>

#include "BitStream.h"
#include "Util/Guid.h"
#include "Util/SystemAddress.h"
#include "ConcurrentRakPeer.h"
#include "FastLog.h"

namespace RBX {

namespace Network {

extern const char* const sClient;
const char* const sClient = "NetworkClient";
extern const char* const sServer;
const char* const sServer = "NetworkServer";
extern const char* const sPeer;
const char* const sPeer = "NetworkPeer";
extern const char* const sReplicator;
const char* const sReplicator = "NetworkReplicator";
extern const char* const sClientReplicator;
const char* const sClientReplicator = "ClientReplicator";
extern const char* const sChatFilter;
const char* const sChatFilter = "ChatFilter";

const SystemAddress RakNetToRbxAddress(const RakNet::SystemAddress& a) {
    return SystemAddress(a.GetBinaryAddress(), a.GetPort());
}

std::string RakNetAddressToString(const RakNet::SystemAddress& a, bool writePort, char delimiter) {
    return a.ToString(writePort, delimiter);
}

bool isTrustedContent(const char* url) {
    return url && *url;
}

void ConcurrentRakPeer::Send(boost::shared_ptr<const RakNet::BitStream>,
                             PacketPriority, PacketReliability, char,
                             RakNet::SystemAddress, bool) {}

}  // namespace Network

template <class T>
RakNet::BitStream& operator>>(RakNet::BitStream& stream, T& value);

RakNet::BitStream& operator<<(RakNet::BitStream& stream, int value) {
    stream.Write(value);
    return stream;
}

template <>
RakNet::BitStream& operator>><int>(RakNet::BitStream& stream, int& value) {
    stream.Read(value);
    return stream;
}

RakNet::BitStream& operator<<(RakNet::BitStream& stream, unsigned char value) {
    stream.Write(value);
    return stream;
}

template <>
RakNet::BitStream& operator>><unsigned char>(RakNet::BitStream& stream, unsigned char& value) {
    stream.Read(value);
    return stream;
}

RakNet::BitStream& operator<<(RakNet::BitStream& stream, const std::string& value) {
    unsigned int size = static_cast<unsigned int>(value.size());
    stream.Write(size);
    stream.Write(value.c_str(), size);
    return stream;
}

template <>
RakNet::BitStream& operator>><std::string>(RakNet::BitStream& stream, std::string& value) {
    unsigned int size = 0;
    stream.Read(size);
    value.resize(size);
    if (size) stream.Read(&value[0], size);
    return stream;
}

RakNet::BitStream& operator<<(RakNet::BitStream& stream, const Guid::Scope& value) {
    stream << value.getName()->toString();
    return stream;
}

template <>
RakNet::BitStream& operator>><Guid::Scope>(RakNet::BitStream& stream, Guid::Scope& value) {
    std::string name;
    stream >> name;
    value.set(name);
    return stream;
}

}  // namespace RBX

namespace DFLog {
FLog::Channel PartStreamingRequests = false;
FLog::Channel WebChatFiltering = false;
}

namespace DFFlag {
bool RCCSupportCloudEdit = false;
}
