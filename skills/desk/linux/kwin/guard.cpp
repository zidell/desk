// SPDX-License-Identifier: GPL-2.0-or-later
// Session-scoped KWin adapter. No app data or accessibility action calls.
#include <QQmlExtensionPlugin>
#include <qqml.h>
#include <QDBusConnection>
#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonArray>
#include <QFile>
#include <QDir>
#include <QTimer>
#include <QElapsedTimer>
#include <QSet>
#include <input.h>
#include <input_event.h>
#include <keyboard_input.h>
#include <pointer_input.h>
#include <cursor.h>
#include <workspace.h>
#include <window.h>
#include <wayland_server.h>
#include <xkb.h>
#include <linux/input-event-codes.h>
using namespace KWin;
class Guard : public QObject, public InputEventFilter {
    Q_OBJECT
    Q_CLASSINFO("D-Bus Interface", "org.desk.Guard")
    QString state;
    bool injecting=false, armed=false, escHeld=false;
    quint64 epoch=0, userEpoch=0;
    QPointF origin;
    double threshold=10;
    QElapsedTimer lease;
    QSet<uint> keys, buttons;
    static auto now(){return std::chrono::duration_cast<std::chrono::microseconds>(std::chrono::steady_clock::now().time_since_epoch());}
    bool live() const {return armed && lease.isValid() && lease.elapsed()<3000;}
    bool locked() const {return waylandServer() && waylandServer()->isScreenLocked();}
    void write(const QString &name,const QByteArray &data){QFile f(state+"/"+name);if(f.open(QIODevice::WriteOnly|QIODevice::Truncate))f.write(data);}
    void activity(bool generatedPointer=false){if(!live())return;++epoch;if(!generatedPointer)++userEpoch;write("user-input",QByteArray::number(epoch));}
    void release(){injecting=true;for(auto k:keys)input()->keyboard()->processKey(k,KeyboardKeyState::Released,now());for(auto b:buttons)input()->pointer()->processButton(b,PointerButtonState::Released,now());input()->pointer()->processFrame();keys.clear();buttons.clear();injecting=false;}
    void stop(){if(!armed)return;write("stopped","physical ESC or safety stop\n");armed=false;release();}
    QJsonObject window() const {
        auto w=workspace()->activeWindow();if(!w)return {};
        const auto g=w->frameGeometry(),c=w->clientGeometry();
        QJsonArray popups;
        for(auto popup:workspace()->stackingOrder())if(popup!=w && popup->isShown() && popup->pid()==w->pid()){
            bool family=false;auto parent=popup->transientFor();
            for(int i=0;parent&&i<16;++i,parent=parent->transientFor())if(parent==w){family=true;break;}
            if(family){const auto p=popup->frameGeometry(),pc=popup->clientGeometry();
                popups.append(QJsonObject{{"id",popup->internalId().toString()},{"box",QJsonArray{p.x(),p.y(),p.width(),p.height()}},{"clientBox",QJsonArray{pc.x(),pc.y(),pc.width(),pc.height()}}});}
        }
        return {{"id",w->internalId().toString()},{"pid",int(w->pid())},{"app",w->resourceClass()},
          {"title",w->caption()},{"box",QJsonArray{g.x(),g.y(),g.width(),g.height()}},
          {"clientBox",QJsonArray{c.x(),c.y(),c.width(),c.height()}},{"popups",popups}};
    }
    bool allowed(const QString &expected,const QString &generation){
        if(!live()||locked()||QFile::exists(state+"/stopped"))return false;
        if(generation!=QString::number(epoch))return false;
        const auto want=QJsonDocument::fromJson(expected.toUtf8()).object(), current=window();
        return !want.isEmpty() && want["id"]==current["id"] && want["pid"]==current["pid"] && want["box"]==current["box"];
    }
public:
    Guard():InputEventFilter(InputFilterOrder::VirtualTerminal){
        input()->installInputEventFilter(this);
        QDBusConnection::sessionBus().registerObject("/DeskGuard",this,QDBusConnection::ExportAllSlots);
        auto t=new QTimer(this);connect(t,&QTimer::timeout,this,[this]{if(armed&&(!live()||locked()))stop();});t->start(100);
    }
    ~Guard() override {release();QDBusConnection::sessionBus().unregisterObject("/DeskGuard");}
    bool keyboardKey(KeyboardKeyEvent *e) override {
        if(injecting)return false;
        if(e->key==Qt::Key_Escape && (live()||escHeld)){
            if(e->state==KeyboardKeyState::Pressed){escHeld=true;stop();}
            if(e->state==KeyboardKeyState::Released)escHeld=false;
            return true;
        }
        activity();return false;
    }
    bool pointerMotion(PointerMotionEvent *e) override {
        if(injecting){origin=e->position;return false;}
        if(QLineF(origin,e->position).length()>=threshold){origin=e->position;activity(e->device==nullptr);}return false;
    }
    bool pointerButton(PointerButtonEvent *) override {if(!injecting)activity();return false;}
    bool pointerAxis(PointerAxisEvent *e) override {if(!injecting)activity(e->device==nullptr);return false;}
    bool touchDown(TouchDownEvent *) override {activity();return false;}
    bool touchMotion(TouchMotionEvent *) override {activity();return false;}
    bool touchUp(TouchUpEvent *) override {activity();return false;}
public Q_SLOTS:
    bool PlaceOverlay(int pid,int x,int y,int width,int height){
        for(auto w:workspace()->stackingOrder())if(w->pid()==pid && w->resourceClass()=="org.desk.Tool" && w->caption()=="desk · 인식 표시"){
            w->setKeepAbove(true);w->setOnAllDesktops(true);w->moveResize(QRectF(x,y,width,height));return true;
        }return false;
    }
    QString Windows(){
        QJsonArray list;for(auto w:workspace()->stackingOrder())if(w->isShown() && w->isOnCurrentDesktop() && w->isOnCurrentActivity() && (w->isNormalWindow()||w->isDialog()))
            list.append(QJsonObject{{"id",w->internalId().toString()},{"pid",int(w->pid())},{"app",w->resourceClass()},{"title",w->caption()}});
        return QString::fromUtf8(QJsonDocument(list).toJson(QJsonDocument::Compact));
    }
    bool Activate(QString id){
        if(!live()||locked()||QFile::exists(state+"/stopped"))return false;
        for(auto w:workspace()->stackingOrder())if(w->internalId().toString()==id && !w->isDeleted() && (w->isNormalWindow()||w->isDialog())){
            w->setMinimized(false);workspace()->activateWindow(w,true);return true;
        }return false;
    }
    bool PlacePanel(int pid,int x,int y,int width,int height){
        for(auto w:workspace()->stackingOrder())if(w->pid()==pid && w->resourceClass()=="org.desk.Tool" && w->caption()=="desk · 중지(ESC)"){
            w->setKeepAbove(true);w->setOnAllDesktops(true);w->moveResize(QRectF(x,y,width,height));return true;
        }return false;
    }
    QString Snapshot(){auto w=window();auto p=input()->globalPointer();w["pointer"]=QJsonArray{p.x(),p.y()};w["epoch"]=QString::number(epoch);w["userEpoch"]=QString::number(userEpoch);w["locked"]=locked();w["armed"]=live();
        bool busy=false;for(auto key:input()->keyboard()->pressedKeys())if(!keys.contains(key))busy=true;
        Qt::MouseButtons own;
        if(buttons.contains(BTN_LEFT))own|=Qt::LeftButton;
        if(buttons.contains(BTN_RIGHT))own|=Qt::RightButton;
        if(buttons.contains(BTN_MIDDLE))own|=Qt::MiddleButton;
        busy|=bool(input()->pointer()->buttons()&~own);
        w["physicalInputHeld"]=busy;return QString::fromUtf8(QJsonDocument(w).toJson(QJsonDocument::Compact));}
    bool Arm(QString path,double pixels){
        if(!QDir::isAbsolutePath(path)||!QFile::exists(path)||pixels<1||pixels>100)return false;
        release();state=path;threshold=pixels;epoch=0;origin=input()->globalPointer();lease.start();armed=true;userEpoch=0;write("user-input","0");return true;
    }
    void Heartbeat(){if(armed)lease.restart();}
    void Disarm(){release();armed=false;}
    bool Motion(double x,double y,QString expected,QString generation){
        if(!allowed(expected,generation))return false;injecting=true;input()->pointer()->processMotionAbsolute({x,y},now());input()->pointer()->processFrame();origin=input()->globalPointer();injecting=false;return true;
    }
    bool Button(uint button,bool down,QString expected,QString generation){
        if(down&&!allowed(expected,generation))return false;
        if(down){
            auto focus=input()->pointer()->focus(),active=workspace()->activeWindow();bool family=false;
            for(int i=0;focus&&i<16;++i,focus=focus->transientFor())if(focus==active){family=true;break;}
            if(!family)return false;
        }
        if(!down&&!buttons.contains(button))return false;
        injecting=true;input()->pointer()->processButton(button,down?PointerButtonState::Pressed:PointerButtonState::Released,now());input()->pointer()->processFrame();injecting=false;
        if(down)buttons.insert(button);else buttons.remove(button);return true;
    }
    bool Key(QString symbol,bool down,QString expected,QString generation){
        if(down&&!allowed(expected,generation))return false;
        const auto sym=xkb_keysym_from_name(symbol.toUtf8().constData(),XKB_KEYSYM_CASE_INSENSITIVE);
        // KWin returns evdev codes here (it already subtracts EVDEV_OFFSET).
        auto code=input()->keyboard()->xkb()->keycodeFromKeysym(sym);if(!code||code->keyCode==0)return false;
        const uint key=code->keyCode;
        if(!down&&!keys.contains(key))return false;
        injecting=true;input()->keyboard()->processKey(key,down?KeyboardKeyState::Pressed:KeyboardKeyState::Released,now());injecting=false;
        if(down)keys.insert(key);else keys.remove(key);return true;
    }
    bool Keys(QStringList symbols,QString expected,QString generation){
        if(symbols.isEmpty()||symbols.size()>6)return false;
        QStringList held;bool result=true;
        for(const auto &symbol:symbols){
            if(!Key(symbol,true,expected,generation)){result=false;break;}
            held.append(symbol);
        }
        for(auto i=held.crbegin();i!=held.crend();++i)Key(*i,false,expected,generation);
        return result;
    }
    bool Scroll(double dx,double dy,QString expected,QString generation){
        if(!allowed(expected,generation))return false;injecting=true;
        if(dy)input()->pointer()->processAxis(PointerAxis::Vertical,dy*15,int(dy*120),PointerAxisSource::Wheel,false,now());
        if(dx)input()->pointer()->processAxis(PointerAxis::Horizontal,dx*15,int(dx*120),PointerAxisSource::Wheel,false,now());
        input()->pointer()->processFrame();
        injecting=false;return true;
    }
};
class Extension : public QQmlExtensionPlugin {
    Q_OBJECT
    Q_PLUGIN_METADATA(IID QQmlExtensionInterface_iid)
public:void registerTypes(const char *uri) override {qmlRegisterType<Guard>(uri,1,0,"Guard");}
};
#include "guard.moc"
