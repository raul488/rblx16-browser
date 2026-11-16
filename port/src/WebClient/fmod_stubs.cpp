/* WebAudio-backed FMOD subset for the browser port.
 *
 * The engine's sound code runs on the DataModel job thread, while the DOM
 * Audio API only exists on the browser main thread.  Every FMOD call therefore
 * queues a command; rblx_audio_pump() drains the queue on the main thread.
 * Getters use a small shadow state so the job thread never blocks on the DOM.
 */
#include <emscripten.h>
#include <cstdio>
#include <cstring>
#include <cstdint>
#include <deque>
#include <map>
#include <mutex>
#include <string>

#include "fmod.hpp"
#include "fmod_errors.h"

namespace {

enum AudioOp {
    OP_CREATE_MEMORY = 1,
    OP_CREATE_URL,
    OP_PLAY,
    OP_SET_VOLUME,
    OP_SET_PAUSED,
    OP_STOP,
    OP_SET_POSITION,
    OP_SET_FREQUENCY,
    OP_SET_LOOP,
    OP_RELEASE_SOUND,
    OP_MASTER_VOLUME,
    OP_MASTER_MUTE,
};

struct Command {
    int op = 0;
    int token = 0;
    int a = 0;
    int b = 0;
    float f = 0.0f;
    std::string data;
};

std::mutex g_mutex;
std::deque<Command> g_queue;
int g_nextToken = 0x1000;

std::map<FMOD::Sound*, int> g_soundTokens;
std::map<FMOD::Channel*, int> g_channelTokens;
std::map<int, FMOD::Channel*> g_channelByToken;

std::map<FMOD::Channel*, bool> g_paused;
std::map<FMOD::Channel*, bool> g_playing;
std::map<FMOD::Channel*, float> g_volume;
std::map<FMOD::Channel*, unsigned int> g_position;
std::map<FMOD::Channel*, FMOD_CHANNELCONTROL_CALLBACK> g_callbacks;
std::map<FMOD::Channel*, void*> g_userData;

bool isMasterGroup(const void* p) {
    return reinterpret_cast<uintptr_t>(p) == 1;
}

int nextTokenLocked() {
    return g_nextToken++;
}

void enqueue(Command&& c) {
    std::lock_guard<std::mutex> lock(g_mutex);
    g_queue.push_back(std::move(c));
}

}  // namespace

/* ---- main-thread JS helpers ------------------------------------------- */

EM_JS(int, rbx_audio_create_memory, (int token, const char* data, int len, int loop), {
    var A = Module.__rbxAudio || (Module.__rbxAudio = { sounds: {}, channels: {}, masterVolume: 1, masterMute: false });
    try {
        var bytes = HEAPU8.slice(data, data + len);
        var blob = new Blob([bytes], { type: 'audio/mpeg' });
        var url = URL.createObjectURL(blob);
        var audio = new Audio(url);
        audio.preload = 'auto';
        A.sounds[token] = { url: url, audio: audio, loop: !!loop };
        return 1;
    } catch (e) {
        console.warn('[rbx2016] audio create failed', e);
        return 0;
    }
});

EM_JS(int, rbx_audio_create_url, (int token, const char* path, int loop), {
    var A = Module.__rbxAudio || (Module.__rbxAudio = { sounds: {}, channels: {}, masterVolume: 1, masterMute: false });
    try {
        var p = UTF8ToString(path);
        if (p.indexOf('rbxasset://') === 0) p = '/content/' + p.substring(11);
        var i = p.lastIndexOf('/content/');
        if (i >= 0) p = p.substring(i);
        var audio = new Audio(p);
        audio.preload = 'auto';
        A.sounds[token] = { url: p, audio: audio, loop: !!loop };
        return 1;
    } catch (e) {
        console.warn('[rbx2016] audio create url failed', e);
        return 0;
    }
});

EM_JS(void, rbx_audio_play, (int soundToken, int channelToken, int paused), {
    var A = Module.__rbxAudio;
    if (!A || !A.sounds[soundToken]) return;
    var s = A.sounds[soundToken];
    try {
        var audio = new Audio(s.url);
        audio.loop = !!s.loop;
        audio.volume = Math.max(0, Math.min(1, A.masterMute ? 0 : A.masterVolume));
        var channel = { audio: audio, volume: 1, loop: !!s.loop, paused: !!paused };
        A.channels[channelToken] = channel;
        audio.addEventListener('ended', function () {
            if (Module._rblx_audio_ended) Module._rblx_audio_ended(channelToken);
        });
        if (!paused) audio.play().catch(function (e) { if (e.name !== 'AbortError') console.warn('[rbx2016] audio play failed', e); });
    } catch (e) {
        console.warn('[rbx2016] audio play exception', e);
    }
});

EM_JS(void, rbx_audio_set_volume, (int token, float v), {
    var A = Module.__rbxAudio, c = A && A.channels[token];
    if (!c) return;
    c.volume = v;
    c.audio.volume = Math.max(0, Math.min(1, v * (A.masterMute ? 0 : A.masterVolume)));
});

EM_JS(void, rbx_audio_set_paused, (int token, int paused), {
    var A = Module.__rbxAudio, c = A && A.channels[token];
    if (!c) return;
    c.paused = !!paused;
    try {
        if (paused) c.audio.pause();
        else c.audio.play().catch(function (e) { if (e.name !== 'AbortError') console.warn('[rbx2016] audio play failed', e); });
    } catch (e) { console.warn('[rbx2016] audio pause/play exception', e); }
});

EM_JS(void, rbx_audio_stop, (int token), {
    var A = Module.__rbxAudio, c = A && A.channels[token];
    if (!c) return;
    try { c.audio.pause(); c.audio.currentTime = 0; } catch (e) {}
    c.paused = true;
});

EM_JS(void, rbx_audio_set_position, (int token, float ms), {
    var A = Module.__rbxAudio, c = A && A.channels[token];
    if (!c) return;
    try { c.audio.currentTime = Math.max(0, ms) / 1000.0; } catch (e) {}
});

EM_JS(void, rbx_audio_set_frequency, (int token, float hz), {
    var A = Module.__rbxAudio, c = A && A.channels[token];
    if (!c) return;
    try { c.audio.playbackRate = Math.max(0.1, Math.min(4.0, hz / 48000.0)); } catch (e) {}
});

EM_JS(void, rbx_audio_set_loop, (int token, int loop), {
    var A = Module.__rbxAudio, c = A && A.channels[token];
    if (!c) return;
    c.loop = !!loop;
    try { c.audio.loop = !!loop; } catch (e) {}
});

EM_JS(void, rbx_audio_release_sound, (int token), {
    var A = Module.__rbxAudio, s = A && A.sounds[token];
    if (!s) return;
    try { URL.revokeObjectURL(s.url); } catch (e) {}
    delete A.sounds[token];
});

EM_JS(void, rbx_audio_master_volume, (float v), {
    var A = Module.__rbxAudio;
    if (!A) return;
    A.masterVolume = v;
    for (var k in A.channels) {
        var c = A.channels[k];
        c.audio.volume = Math.max(0, Math.min(1, c.volume * (A.masterMute ? 0 : A.masterVolume)));
    }
});

EM_JS(void, rbx_audio_forget, (int token), {
    var A = Module.__rbxAudio;
    if (!A || !A.channels[token]) return;
    try { A.channels[token].audio.pause(); } catch (e) {}
    delete A.channels[token];
});

EM_JS(void, rbx_audio_master_mute, (int mute), {
    var A = Module.__rbxAudio;
    if (!A) return;
    A.masterMute = !!mute;
    for (var k in A.channels) {
        var c = A.channels[k];
        c.audio.volume = Math.max(0, Math.min(1, c.volume * (A.masterMute ? 0 : A.masterVolume)));
    }
});

/* ---- C entry points --------------------------------------------------- */

extern "C" EMSCRIPTEN_KEEPALIVE void rblx_audio_pump() {
    std::deque<Command> local;
    {
        std::lock_guard<std::mutex> lock(g_mutex);
        local.swap(g_queue);
    }
    for (const Command& c : local) {
        switch (c.op) {
        case OP_CREATE_MEMORY:
            if (!c.data.empty()) rbx_audio_create_memory(c.token, c.data.data(), (int)c.data.size(), c.b);
            break;
        case OP_CREATE_URL:
            rbx_audio_create_url(c.token, c.data.c_str(), c.b);
            break;
        case OP_PLAY:
            rbx_audio_play(c.a, c.token, c.b);
            break;
        case OP_SET_VOLUME:
            rbx_audio_set_volume(c.token, c.f);
            break;
        case OP_SET_PAUSED:
            rbx_audio_set_paused(c.token, c.a);
            break;
        case OP_STOP:
            rbx_audio_stop(c.token);
            break;
        case OP_SET_POSITION:
            rbx_audio_set_position(c.token, c.f);
            break;
        case OP_SET_FREQUENCY:
            rbx_audio_set_frequency(c.token, c.f);
            break;
        case OP_SET_LOOP:
            rbx_audio_set_loop(c.token, c.a);
            break;
        case OP_RELEASE_SOUND:
            rbx_audio_release_sound(c.token);
            break;
        case OP_MASTER_VOLUME:
            rbx_audio_master_volume(c.f);
            break;
        case OP_MASTER_MUTE:
            rbx_audio_master_mute(c.a);
            break;
        default:
            break;
        }
    }
}

extern "C" EMSCRIPTEN_KEEPALIVE void rblx_audio_ended(int token) {
    FMOD::Channel* channel = nullptr;
    FMOD_CHANNELCONTROL_CALLBACK callback = nullptr;
    void* userData = nullptr;
    {
        std::lock_guard<std::mutex> lock(g_mutex);
        auto it = g_channelByToken.find(token);
        if (it != g_channelByToken.end()) {
            channel = it->second;
            g_playing[channel] = false;
            auto cb = g_callbacks.find(channel);
            if (cb != g_callbacks.end()) callback = cb->second;
            auto ud = g_userData.find(channel);
            if (ud != g_userData.end()) userData = ud->second;
        }
    }
    if (callback && channel) {
        callback(reinterpret_cast<FMOD_CHANNELCONTROL*>(channel),
                 FMOD_CHANNELCONTROL_CHANNEL,
                 FMOD_CHANNELCONTROL_CALLBACK_END,
                 userData, nullptr);
    }
    {
        std::lock_guard<std::mutex> lock(g_mutex);
        g_channelTokens.erase(channel);
        g_channelByToken.erase(token);
        g_paused.erase(channel);
        g_playing.erase(channel);
        g_volume.erase(channel);
        g_position.erase(channel);
        g_callbacks.erase(channel);
        g_userData.erase(channel);
    }
    rbx_audio_forget(token);
}

/* ---- C API used by the engine ----------------------------------------- */

extern "C" FMOD_RESULT F_API FMOD_System_Create(FMOD_SYSTEM** system) {
    if (system) *system = reinterpret_cast<FMOD_SYSTEM*>(1);
    return FMOD_OK;
}
extern "C" FMOD_RESULT F_API FMOD_Memory_GetStats(int* current, int* max, FMOD_BOOL) {
    if (current) *current = 0;
    if (max) *max = 0;
    return FMOD_OK;
}
extern "C" FMOD_RESULT F_API FMOD_Channel_GetUserData(FMOD_CHANNEL* channel, void** userdata) {
    if (!userdata) return FMOD_OK;
    *userdata = nullptr;
    std::lock_guard<std::mutex> lock(g_mutex);
    auto it = g_userData.find(reinterpret_cast<FMOD::Channel*>(channel));
    if (it != g_userData.end()) *userdata = it->second;
    return FMOD_OK;
}

namespace FMOD {

FMOD_RESULT System::release() { return FMOD_OK; }
FMOD_RESULT System::getVersion(unsigned int* version) { if (version) *version = FMOD_VERSION; return FMOD_OK; }
FMOD_RESULT System::getNumDrivers(int* numdrivers) { if (numdrivers) *numdrivers = 1; return FMOD_OK; }
FMOD_RESULT System::setDriver(int) { return FMOD_OK; }
FMOD_RESULT System::getDriver(int* driver) { if (driver) *driver = 0; return FMOD_OK; }
FMOD_RESULT System::getDriverInfo(int, char* name, int namelen, FMOD_GUID*, int*, FMOD_SPEAKERMODE*, int*) {
    if (name && namelen) {
        std::strncpy(name, "WebAudio", namelen - 1);
        name[namelen - 1] = 0;
    }
    return FMOD_OK;
}
FMOD_RESULT System::setSoftwareFormat(int, FMOD_SPEAKERMODE, int) { return FMOD_OK; }
FMOD_RESULT System::getSoftwareFormat(int* rate, FMOD_SPEAKERMODE* mode, int* speakers) {
    if (rate) *rate = 48000;
    if (mode) *mode = FMOD_SPEAKERMODE_STEREO;
    if (speakers) *speakers = 2;
    return FMOD_OK;
}
FMOD_RESULT System::init(int, FMOD_INITFLAGS, void*) { return FMOD_OK; }
FMOD_RESULT System::update() { return FMOD_OK; }
FMOD_RESULT System::createChannelGroup(const char*, ChannelGroup** group) {
    if (group) *group = reinterpret_cast<ChannelGroup*>(1);
    return FMOD_OK;
}
FMOD_RESULT System::getMasterChannelGroup(ChannelGroup** group) {
    if (group) *group = reinterpret_cast<ChannelGroup*>(1);
    return FMOD_OK;
}
FMOD_RESULT System::createDSP(const FMOD_DSP_DESCRIPTION*, DSP** dsp) {
    if (dsp) *dsp = reinterpret_cast<DSP*>(1);
    return FMOD_OK;
}
FMOD_RESULT System::set3DSettings(float, float, float) { return FMOD_OK; }
FMOD_RESULT System::set3DListenerAttributes(int, const FMOD_VECTOR*, const FMOD_VECTOR*, const FMOD_VECTOR*, const FMOD_VECTOR*) { return FMOD_OK; }
FMOD_RESULT System::setReverbProperties(int, const FMOD_REVERB_PROPERTIES*) { return FMOD_OK; }

FMOD_RESULT System::createSound(const char* name, FMOD_MODE mode, FMOD_CREATESOUNDEXINFO* info, Sound** sound) {
    if (!sound) return FMOD_ERR_INVALID_PARAM;
    int token;
    {
        std::lock_guard<std::mutex> lock(g_mutex);
        token = nextTokenLocked();
    }
    Sound* s = reinterpret_cast<Sound*>(static_cast<uintptr_t>(token));
    {
        std::lock_guard<std::mutex> lock(g_mutex);
        g_soundTokens[s] = token;
    }
    Command c;
    c.token = token;
    c.b = (mode & FMOD_LOOP_NORMAL) ? 1 : 0;
    if (info && info->length && name) {
        c.op = OP_CREATE_MEMORY;
        c.data.assign(name, info->length);
    } else {
        c.op = OP_CREATE_URL;
        c.data = name ? name : "";
    }
    enqueue(std::move(c));
    *sound = s;
    return FMOD_OK;
}

FMOD_RESULT System::createStream(const char* name, FMOD_MODE mode, FMOD_CREATESOUNDEXINFO*, Sound** sound) {
    if (!sound) return FMOD_ERR_INVALID_PARAM;
    int token;
    {
        std::lock_guard<std::mutex> lock(g_mutex);
        token = nextTokenLocked();
    }
    Sound* s = reinterpret_cast<Sound*>(static_cast<uintptr_t>(token));
    {
        std::lock_guard<std::mutex> lock(g_mutex);
        g_soundTokens[s] = token;
    }
    Command c;
    c.op = OP_CREATE_URL;
    c.token = token;
    c.b = (mode & FMOD_LOOP_NORMAL) ? 1 : 0;
    c.data = name ? name : "";
    enqueue(std::move(c));
    *sound = s;
    return FMOD_OK;
}

FMOD_RESULT System::playSound(Sound* sound, ChannelGroup*, bool paused, Channel** out) {
    if (!out) return FMOD_ERR_INVALID_PARAM;
    int soundToken = 0;
    int channelToken;
    {
        std::lock_guard<std::mutex> lock(g_mutex);
        auto it = g_soundTokens.find(sound);
        if (it != g_soundTokens.end()) soundToken = it->second;
        channelToken = nextTokenLocked();
    }
    Channel* ch = reinterpret_cast<Channel*>(static_cast<uintptr_t>(channelToken));
    {
        std::lock_guard<std::mutex> lock(g_mutex);
        g_channelTokens[ch] = channelToken;
        g_channelByToken[channelToken] = ch;
        g_paused[ch] = paused;
        g_playing[ch] = !paused;
        g_volume[ch] = 1.0f;
        g_position[ch] = 0;
    }
    Command c;
    c.op = OP_PLAY;
    c.token = channelToken;
    c.a = soundToken;
    c.b = paused ? 1 : 0;
    enqueue(std::move(c));
    *out = ch;
    return FMOD_OK;
}

FMOD_RESULT System::getCPUUsage(float* dsp, float* stream, float* geometry, float* update, float* total) {
    if (dsp) *dsp = 0; if (stream) *stream = 0; if (geometry) *geometry = 0;
    if (update) *update = 0; if (total) *total = 0;
    return FMOD_OK;
}

FMOD_RESULT System::getChannelsPlaying(int* channels) {
    if (!channels) return FMOD_OK;
    int n = 0;
    std::lock_guard<std::mutex> lock(g_mutex);
    for (auto& p : g_playing) if (p.second) ++n;
    *channels = n;
    return FMOD_OK;
}

FMOD_RESULT Sound::release() {
    int token = 0;
    {
        std::lock_guard<std::mutex> lock(g_mutex);
        auto it = g_soundTokens.find(this);
        if (it != g_soundTokens.end()) {
            token = it->second;
            g_soundTokens.erase(it);
        }
    }
    if (token) {
        Command c;
        c.op = OP_RELEASE_SOUND;
        c.token = token;
        enqueue(std::move(c));
    }
    return FMOD_OK;
}

FMOD_RESULT Sound::getDefaults(float* frequency, int* priority) {
    if (frequency) *frequency = 48000.0f;
    if (priority) *priority = 128;
    return FMOD_OK;
}
FMOD_RESULT Sound::getLength(unsigned int* length, FMOD_TIMEUNIT) {
    if (length) *length = 0;
    return FMOD_OK;
}

FMOD_RESULT ChannelControl::getSystemObject(System** system) {
    if (system) *system = reinterpret_cast<System*>(1);
    return FMOD_OK;
}
FMOD_RESULT ChannelControl::setUserData(void* data) {
    if (isMasterGroup(this)) return FMOD_OK;
    std::lock_guard<std::mutex> lock(g_mutex);
    g_userData[reinterpret_cast<Channel*>(this)] = data;
    return FMOD_OK;
}
FMOD_RESULT ChannelControl::stop() {
    if (isMasterGroup(this)) return FMOD_OK;
    Channel* ch = reinterpret_cast<Channel*>(this);
    int token = 0;
    {
        std::lock_guard<std::mutex> lock(g_mutex);
        auto it = g_channelTokens.find(ch);
        if (it != g_channelTokens.end()) token = it->second;
        g_playing[ch] = false;
        g_paused[ch] = true;
    }
    if (token) {
        Command c; c.op = OP_STOP; c.token = token;
        enqueue(std::move(c));
    }
    return FMOD_OK;
}
FMOD_RESULT ChannelControl::setPaused(bool paused) {
    if (isMasterGroup(this)) return FMOD_OK;
    Channel* ch = reinterpret_cast<Channel*>(this);
    int token = 0;
    {
        std::lock_guard<std::mutex> lock(g_mutex);
        auto it = g_channelTokens.find(ch);
        if (it != g_channelTokens.end()) token = it->second;
        g_paused[ch] = paused;
        g_playing[ch] = !paused;
    }
    if (token) {
        Command c; c.op = OP_SET_PAUSED; c.token = token; c.a = paused ? 1 : 0;
        enqueue(std::move(c));
    }
    return FMOD_OK;
}
FMOD_RESULT ChannelControl::getPaused(bool* paused) {
    if (paused) *paused = true;
    if (isMasterGroup(this)) return FMOD_OK;
    std::lock_guard<std::mutex> lock(g_mutex);
    auto it = g_paused.find(reinterpret_cast<Channel*>(this));
    if (paused && it != g_paused.end()) *paused = it->second;
    return FMOD_OK;
}
FMOD_RESULT ChannelControl::setVolume(float volume) {
    if (isMasterGroup(this)) {
        Command c; c.op = OP_MASTER_VOLUME; c.f = volume;
        enqueue(std::move(c));
        return FMOD_OK;
    }
    Channel* ch = reinterpret_cast<Channel*>(this);
    int token = 0;
    {
        std::lock_guard<std::mutex> lock(g_mutex);
        auto it = g_channelTokens.find(ch);
        if (it != g_channelTokens.end()) token = it->second;
        g_volume[ch] = volume;
    }
    if (token) {
        Command c; c.op = OP_SET_VOLUME; c.token = token; c.f = volume;
        enqueue(std::move(c));
    }
    return FMOD_OK;
}
FMOD_RESULT ChannelControl::getVolume(float* volume) {
    if (volume) *volume = 0.0f;
    if (isMasterGroup(this)) return FMOD_OK;
    std::lock_guard<std::mutex> lock(g_mutex);
    auto it = g_volume.find(reinterpret_cast<Channel*>(this));
    if (volume && it != g_volume.end()) *volume = it->second;
    return FMOD_OK;
}
FMOD_RESULT ChannelControl::setMute(bool mute) {
    if (isMasterGroup(this)) {
        Command c; c.op = OP_MASTER_MUTE; c.a = mute ? 1 : 0;
        enqueue(std::move(c));
    }
    return FMOD_OK;
}
FMOD_RESULT ChannelControl::getMute(bool* mute) {
    if (mute) *mute = false;
    return FMOD_OK;
}
FMOD_RESULT ChannelControl::setMode(FMOD_MODE) { return FMOD_OK; }
FMOD_RESULT ChannelControl::getMode(FMOD_MODE* mode) { if (mode) *mode = FMOD_DEFAULT; return FMOD_OK; }
FMOD_RESULT ChannelControl::isPlaying(bool* playing) {
    if (playing) *playing = false;
    if (isMasterGroup(this)) return FMOD_OK;
    std::lock_guard<std::mutex> lock(g_mutex);
    auto it = g_playing.find(reinterpret_cast<Channel*>(this));
    if (playing && it != g_playing.end()) *playing = it->second;
    return FMOD_OK;
}
FMOD_RESULT ChannelControl::setCallback(FMOD_CHANNELCONTROL_CALLBACK callback) {
    if (isMasterGroup(this)) return FMOD_OK;
    std::lock_guard<std::mutex> lock(g_mutex);
    g_callbacks[reinterpret_cast<Channel*>(this)] = callback;
    return FMOD_OK;
}
FMOD_RESULT ChannelControl::set3DAttributes(const FMOD_VECTOR*, const FMOD_VECTOR*, const FMOD_VECTOR*) { return FMOD_OK; }
FMOD_RESULT ChannelControl::set3DMinMaxDistance(float, float) { return FMOD_OK; }
FMOD_RESULT ChannelControl::addDSP(int, DSP*) { return FMOD_OK; }

FMOD_RESULT Channel::setFrequency(float frequency) {
    int token = 0;
    {
        std::lock_guard<std::mutex> lock(g_mutex);
        auto it = g_channelTokens.find(this);
        if (it != g_channelTokens.end()) token = it->second;
    }
    if (token) {
        Command c; c.op = OP_SET_FREQUENCY; c.token = token; c.f = frequency;
        enqueue(std::move(c));
    }
    return FMOD_OK;
}
FMOD_RESULT Channel::setPriority(int) { return FMOD_OK; }
FMOD_RESULT Channel::setPosition(unsigned int position, FMOD_TIMEUNIT) {
    int token = 0;
    {
        std::lock_guard<std::mutex> lock(g_mutex);
        auto it = g_channelTokens.find(this);
        if (it != g_channelTokens.end()) token = it->second;
        g_position[this] = position;
    }
    if (token) {
        Command c; c.op = OP_SET_POSITION; c.token = token; c.f = (float)position;
        enqueue(std::move(c));
    }
    return FMOD_OK;
}
FMOD_RESULT Channel::getPosition(unsigned int* position, FMOD_TIMEUNIT) {
    if (position) *position = 0;
    std::lock_guard<std::mutex> lock(g_mutex);
    auto it = g_position.find(this);
    if (position && it != g_position.end()) *position = it->second;
    return FMOD_OK;
}
FMOD_RESULT Channel::setChannelGroup(ChannelGroup*) { return FMOD_OK; }
FMOD_RESULT Channel::setLoopCount(int count) {
    int token = 0;
    {
        std::lock_guard<std::mutex> lock(g_mutex);
        auto it = g_channelTokens.find(this);
        if (it != g_channelTokens.end()) token = it->second;
    }
    if (token) {
        Command c; c.op = OP_SET_LOOP; c.token = token; c.a = (count < 0) ? 1 : 0;
        enqueue(std::move(c));
    }
    return FMOD_OK;
}

FMOD_RESULT DSP::setBypass(bool) { return FMOD_OK; }

}  // namespace FMOD
