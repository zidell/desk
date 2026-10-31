// SPDX-License-Identifier: LGPL-2.1-or-later
#include <window.h>
extern "C" void desk_layer(QWindow *window,int overlay,int margin){
    auto layer=LayerShellQt::Window::get(window);
    // KWin treats unknown scopes as normal windows and applies its opening zoom.
    // Recognition marks are tooltip surfaces; their top-to-bottom sweep is
    // painted by Marks itself and must not scale with a window animation.
    layer->setScope(overlay?"tooltip":"desk");layer->setLayer(LayerShellQt::Window::LayerOverlay);
    layer->setKeyboardInteractivity(LayerShellQt::Window::KeyboardInteractivityNone);
    layer->setActivateOnShow(false);layer->setExclusiveZone(0);
    if(overlay)layer->setAnchors(LayerShellQt::Window::Anchors(LayerShellQt::Window::AnchorTop)|LayerShellQt::Window::AnchorBottom|LayerShellQt::Window::AnchorLeft|LayerShellQt::Window::AnchorRight);
    else {layer->setAnchors(LayerShellQt::Window::AnchorBottom);layer->setMargins(QMargins(0,0,0,margin));}
}
