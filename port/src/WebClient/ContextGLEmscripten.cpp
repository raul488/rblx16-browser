#include "ContextGL.h"
#include "HeadersGL.h"
#include <GLFW/glfw3.h>
#include <utility>
#include <cstdio>

namespace RBX { namespace Graphics {

class ContextGLEmscripten : public ContextGL {
public:
    explicit ContextGLEmscripten(void* windowHandle) : window(static_cast<GLFWwindow*>(windowHandle)) {}
    ~ContextGLEmscripten() override {}
    void setCurrent() override { glfwMakeContextCurrent(window); }
    void swapBuffers() override { glfwSwapBuffers(window); }
    unsigned int getMainFramebufferId() override { return 0; }
    bool isMainFramebufferRetina() override { return false; }
    std::pair<unsigned int, unsigned int> updateMainFramebuffer(unsigned int width, unsigned int height) override {
        // Never resize the window here: the caller passes the previous
        // framebuffer size. Query the real canvas size instead.
        int w=0,h=0; glfwGetFramebufferSize(window,&w,&h);
        static int lastW=-1,lastH=-1;
        if (w!=lastW || h!=lastH) { std::fprintf(stderr, "RBLX2016: engine framebuffer=%dx%d\n", w, h); lastW=w; lastH=h; }
        glViewport(0,0,w,h);
        return std::make_pair((unsigned int)w,(unsigned int)h);
    }
private:
    GLFWwindow* window;
};

ContextGL* ContextGL::create(void* windowHandle) { return new ContextGLEmscripten(windowHandle); }

}}
