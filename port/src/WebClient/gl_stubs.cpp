/* Minimal GL compatibility shims for WebGL2.
 * Everything else in the GLES3 API is provided by Emscripten's libGL. */
#include "Rendering/GfxCore/GL/HeadersGL.h"

#include "GfxCore/GL/DeviceGL.h"

/* WebGL2 has no glMapBuffer; the engine only uses it on the GL2 path. */
extern "C" GLvoid* glMapBuffer(GLenum, GLenum) { return nullptr; }

namespace RBX { namespace Graphics {
DeviceVRGL* DeviceVRGL::createCardboard() { return nullptr; }
} }
