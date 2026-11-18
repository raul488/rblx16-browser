#include "WebView.h"

#include <GLFW/glfw3.h>
#include <cstdio>

#include <boost/unordered_map.hpp>
#include <boost/unordered_set.hpp>

#include "GfxBase/ViewBase.h"
#include "GfxBase/RenderSettings.h"
#include "RenderView.h"
#include "VisualEngine.h"
#include "SceneUpdater.h"
#include "TextureManager.h"
#include "TextureCompositor.h"
#include "v8datamodel/ContentProvider.h"
#include "RenderSettingsItem.h"
#include "v8datamodel/DataModel.h"
#include "rbx/rbxTime.h"
#include "util/StandardOut.h"
#include "util/IMetric.h"
#include "FastLog.h"

FASTFLAG(GraphicsGL3)

namespace rblx2016 {

namespace {
struct NullMetric : RBX::IMetric {
    std::string getMetric(const std::string&) const override { return std::string(); }
    double getMetricValue(const std::string&) const override { return 0.0; }
};
NullMetric g_nullMetric;
}


WebView::WebView() = default;

WebView::~WebView() { shutdown(); }

bool WebView::init(const boost::shared_ptr<RBX::DataModel>& dataModel, int w, int h) {
    this->dataModel = dataModel;
    width = w > 0 ? w : 800;
    height = h > 0 ? h : 600;

    if (!glfwStarted) {
        if (!glfwInit()) {
            return false;
        }
        glfwStarted = true;
    }

    glfwWindowHint(GLFW_CONTEXT_VERSION_MAJOR, 3);
    glfwWindowHint(GLFW_CONTEXT_VERSION_MINOR, 0);
    glfwWindowHint(GLFW_CLIENT_API, GLFW_OPENGL_ES_API);
    glfwWindowHint(GLFW_RESIZABLE, GLFW_TRUE);
    window = glfwCreateWindow(width, height, "Roblox 2016", nullptr, nullptr);
    if (!window) {
        return false;
    }
    glfwMakeContextCurrent(window);
    glfwSwapInterval(0);

    RBX::OSContext context;
    context.hWnd = window;
    context.width = width;
    context.height = height;

    FFlag::GraphicsGL3 = true;
    RBX::ViewBase::InitPluginModules();
    CRenderSettingsItem& settings = CRenderSettingsItem::singleton();
    settings.setGraphicsMode(RBX::CRenderSettings::OpenGL);
    view = RBX::ViewBase::CreateView(RBX::CRenderSettings::OpenGL, &context, &settings);
    if (!view) {
        return false;
    }

    view->bindWorkspace(dataModel);
    view->onResize(width, height);
    return true;
}

void WebView::frame() {
    if (!view || !window || !dataModel) return;
    glfwMakeContextCurrent(window);
    int w = 0, h = 0;
    glfwGetFramebufferSize(window, &w, &h);
    if (w != width || h != height) {
        width = w;
        height = h;
        view->onResize(w, h);
    }
    view->renderPrepare(&g_nullMetric);
    view->renderPerform(RBX::Time::nowFastSec());
    glfwSwapBuffers(window);
}

WebView::SceneStatus WebView::sceneStatus() const {
    SceneStatus status;
    RBX::Graphics::RenderView* renderView = dynamic_cast<RBX::Graphics::RenderView*>(view);
    if (!renderView) return status;
    RBX::Graphics::VisualEngine& engine = renderView->getVisualEngine();
    RBX::Graphics::SceneUpdater* updater = engine.getSceneUpdater();
    if (!updater) return status;

    status.clusterQueue = updater->getUpdateQueueSize();
    status.waitingForAssets = updater->arePartsWaitingForAssets();
    status.texturesPending = !engine.getTextureCompositor()->isQueueEmpty() ||
                             !engine.getTextureManager()->isQueueEmpty();
    bool contentPending = engine.getContentProvider() &&
                          !engine.getContentProvider()->isRequestQueueEmpty();
    status.settled = status.clusterQueue == 0 && !status.waitingForAssets &&
                     !status.texturesPending && !contentPending;
    return status;
}

void WebView::resize(int w, int h) {
    if (w <= 0 || h <= 0) return;
    if (window) {
        glfwSetWindowSize(window, w, h);
        int fbw = w, fbh = h;
        glfwGetFramebufferSize(window, &fbw, &fbh);
        if (fbw <= 0 || fbh <= 0) { fbw = w; fbh = h; }
        width = fbw;
        height = fbh;
        if (view) view->onResize(fbw, fbh);
    } else {
        width = w;
        height = h;
        if (view) view->onResize(w, h);
    }
}

void WebView::shutdown() {
    if (view) {
        RBX::ViewBase::ShutdownPluginModules();
        delete view;
        view = nullptr;
    }
    if (window) {
        glfwDestroyWindow(window);
        window = nullptr;
    }
    if (glfwStarted) {
        glfwTerminate();
        glfwStarted = false;
    }
}

}  // namespace rblx2016
