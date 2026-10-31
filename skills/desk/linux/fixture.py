"""Disposable visible test window. Its event log is test instrumentation only."""
import json
import os
from pathlib import Path
import sys
from PyQt6 import QtCore,QtGui,QtWidgets

app=QtWidgets.QApplication(sys.argv)
QtGui.QGuiApplication.setDesktopFileName("org.desk.Fixture")
state=Path(os.environ["DESK_TEST_STATE"])
state.mkdir(parents=True,exist_ok=True)
log=state/"fixture.jsonl"
def record(kind,**values):
    with log.open("a") as stream:stream.write(json.dumps({"kind":kind,"pid":os.getpid(),**values},ensure_ascii=False)+"\n")
class Window(QtWidgets.QWidget):
    def keyPressEvent(self,event):
        record("key",key=event.key(),text=event.text());super().keyPressEvent(event)
    def wheelEvent(self,event):
        record("wheel",dx=event.angleDelta().x(),dy=event.angleDelta().y());super().wheelEvent(event)
class KeyTrace(QtCore.QObject):
    def eventFilter(self,obj,event):
        if event.type() in (QtCore.QEvent.Type.KeyPress,QtCore.QEvent.Type.KeyRelease):
            record("key-event",key=event.key(),modifiers=event.modifiers().value,pressed=event.type()==QtCore.QEvent.Type.KeyPress)
        return False
trace=KeyTrace();app.installEventFilter(trace)
window=Window();window.setWindowTitle("desk Linux verification");window.resize(740,500)
layout=QtWidgets.QVBoxLayout(window)
title=QtWidgets.QLabel("desk Linux · 한글 입력과 안전 검사");title.setStyleSheet("font-size:22px;font-weight:bold;");layout.addWidget(title)
field=QtWidgets.QLineEdit();field.setAccessibleName("Test input");field.setPlaceholderText("Test input / 한글 입력");layout.addWidget(field)
field.textChanged.connect(lambda text:record("text",text=text))
field.selectionChanged.connect(lambda:record("selection",selected=field.hasSelectedText()))
check=QtWidgets.QCheckBox("Test checkbox");check.setAccessibleName("Test checkbox");check.toggled.connect(lambda value:record("checkbox",value=value));layout.addWidget(check)
button=QtWidgets.QPushButton("Test button");button.setAccessibleName("Test button");counter=[0]
status=QtWidgets.QLabel("Clicks: 0");layout.addWidget(button);layout.addWidget(status)
def click():
    counter[0]+=1;status.setText("Clicks: "+str(counter[0]));record("click",count=counter[0])
button.clicked.connect(click)
button.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
def menu(point):
    popup=QtWidgets.QMenu(button);action=popup.addAction("Test menu item")
    action.triggered.connect(lambda:record("menu"))
    popup.exec(button.mapToGlobal(point))
button.customContextMenuRequested.connect(menu)
canvas=QtWidgets.QLabel("Canvas target");canvas.setMinimumHeight(70);canvas.setStyleSheet("background:#315e90;color:white;font-size:20px;");canvas.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter);layout.addWidget(canvas)
layout.addStretch()
def dump():
    data={"pid":os.getpid(),"window":[window.x(),window.y(),window.width(),window.height()]}
    for name,widget in (("input",field),("checkbox",check),("button",button),("canvas",canvas)):
        point=widget.mapToGlobal(QtCore.QPoint(0,0));data[name]=[point.x(),point.y(),widget.width(),widget.height()]
    (state/"fixture-geometry.json").write_text(json.dumps(data))
    # File-driven changes belong only to this fixture, never a user app.
    control=state/"fixture-control"
    if control.exists():
        command=control.read_text().strip();control.unlink()
        if command=="reflow":layout.insertSpacing(2,65);record("reflow")
        elif command=="canvas":canvas.setStyleSheet("background:#933020;color:white;font-size:20px;");record("canvas")
        elif command=="focus":(check if field.hasFocus() else field).setFocus();record("focus")
        elif command=="close":window.close();app.quit()
timer=QtCore.QTimer();timer.timeout.connect(dump);timer.start(50)
window.show();record("start",pid=os.getpid())
if os.environ.get("DESK_FIXTURE_X11_EWMH")=="1":
    from Xlib import X, Xatom, display
    xdisplay=display.Display();root=xdisplay.screen().root
    native=xdisplay.create_resource_object("window",int(window.winId()))
    native.change_property(xdisplay.intern_atom("_NET_WM_PID"),Xatom.CARDINAL,32,[os.getpid()])
    root.change_property(xdisplay.intern_atom("_NET_ACTIVE_WINDOW"),Xatom.WINDOW,32,[native.id])
    native.set_input_focus(X.RevertToParent,X.CurrentTime);xdisplay.sync()
app.exec()
