/* WebView: the browser equivalent of WindowsClient/View.cpp for the 2016
 * engine.  It owns the GLFW window (the page canvas), the ViewBase renderer
 * and a simple per-frame render call. */
#pragma once

#include <boost/shared_ptr.hpp>

struct GLFWwindow;
namespace RBX { class DataModel; class ViewBase; }

namespace rblx2016 {

class WebView {
public:
    WebView();
    ~WebView();

    bool init(const boost::shared_ptr<RBX::DataModel>& dataModel, int width, int height);
    void frame();
    void resize(int width, int height);
    void shutdown();

    bool ready() const { return view != nullptr; }

private:
    GLFWwindow* window = nullptr;
    RBX::ViewBase* view = nullptr;
    boost::shared_ptr<RBX::DataModel> dataModel;
    int width = 0;
    int height = 0;
    bool glfwStarted = false;
};

}  // namespace rblx2016
