"""Non-focusing, input-transparent recognition and click feedback."""
import time
from PyQt6 import QtCore,QtGui,QtWidgets


class Panel(QtWidgets.QWidget):
    """Paint the opaque body explicitly; translucent surfaces only expose corners."""
    def __init__(self,style,flags):
        super().__init__(flags=flags)
        self.panel_style=style

    def paintEvent(self,event):
        style=self.panel_style
        painter=QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        background=QtGui.QColor(style["background"]);background.setAlpha(255)
        painter.setBrush(background)
        painter.setPen(QtGui.QPen(QtGui.QColor(style["accent"]),style["borderWidth"]))
        inset=style["borderWidth"]/2
        painter.drawRoundedRect(QtCore.QRectF(self.rect()).adjusted(inset,inset,-inset,-inset),style["cornerRadius"],style["cornerRadius"])


class Marks(QtWidgets.QWidget):
    def __init__(self,style):
        flags=(QtCore.Qt.WindowType.Tool|QtCore.Qt.WindowType.FramelessWindowHint|
               QtCore.Qt.WindowType.WindowStaysOnTopHint|QtCore.Qt.WindowType.WindowDoesNotAcceptFocus|
               QtCore.Qt.WindowType.WindowTransparentForInput)
        super().__init__(flags=flags)
        self.style=style;self.elements=[];self.target=None;self.ripple=None;self.region=[0,0,1,1]
        self.started=0;self.ripple_started=0;self.hidden_for_capture=False
        self.setWindowTitle("desk · 인식 표시")
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.timer=QtCore.QTimer(self);self.timer.timeout.connect(self.animate)

    def showEvent(self,event):
        super().showEvent(event)
        if self.target is None and not self.timer.isActive():self.timer.start(16)

    def hideEvent(self,event):
        self.timer.stop()
        super().hideEvent(event)

    def scan(self,elements,region):
        self.elements=elements;self.region=region;self.target=None;self.ripple=None;self.started=time.monotonic()
        self.setGeometry(*map(round,region));self.show();self.timer.start(16);self.update()

    def mark_target(self,box,region):
        self.elements=[];self.target=box;self.ripple=None;self.region=region
        self.timer.stop()
        self.setGeometry(*map(round,region));self.show();self.update()

    def clicked(self,x,y,region):
        self.elements=[];self.target=None;self.ripple=(x,y);self.ripple_started=time.monotonic();self.region=region
        self.setGeometry(*map(round,region));self.show();self.timer.start(16);self.update()

    def alive(self):
        if self.target:return True
        if self.ripple:return time.monotonic()-self.ripple_started<self.style["clickRippleMilliseconds"]/1000
        return bool(self.elements) and time.monotonic()-self.started<sum(self.style[k] for k in ("scanSweepMilliseconds","scanHoldMilliseconds","scanFadeMilliseconds"))/1000

    def animate(self):
        if self.hidden_for_capture:return
        if self.alive():self.update()
        else:self.hide()

    def paintEvent(self,event):
        p=QtGui.QPainter(self);p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        ox,oy=self.region[:2]
        def rect(box,color,width=2):
            p.setPen(QtGui.QPen(QtGui.QColor(color),width));p.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            x,y,w,h=box;p.drawRect(QtCore.QRectF(x-ox,y-oy,w,h))
        elapsed=time.monotonic()-self.started
        sweep=self.style["scanSweepMilliseconds"]/1000
        line=min(1,elapsed/sweep)*self.height()
        hold=(self.style["scanSweepMilliseconds"]+self.style["scanHoldMilliseconds"])/1000
        if elapsed>hold:p.setOpacity(max(0,1-(elapsed-hold)/(self.style["scanFadeMilliseconds"]/1000)))
        for index,e in enumerate(self.elements,1):
            if e["box"][1]-oy>line:continue
            color=self.style["scanControl"] if "A" in e["src"] else self.style["scanDetection"] if "D" in e["src"] else self.style["scanText"]
            rect(e["box"],color)
            p.drawText(QtCore.QPointF(e["box"][0]-ox,max(12,e["box"][1]-oy-2)),str(index))
        if self.elements and elapsed<sweep:
            p.setPen(QtGui.QPen(QtGui.QColor(self.style["scanLine"]),2));p.drawLine(QtCore.QPointF(0,line),QtCore.QPointF(self.width(),line))
        p.setOpacity(1)
        if self.target:rect(self.target,self.style["target"],3)
        if self.ripple:
            fraction=(time.monotonic()-self.ripple_started)/(self.style["clickRippleMilliseconds"]/1000)
            p.setOpacity(max(0,1-fraction));radius=self.style["clickRippleRadius"]*min(1,fraction)
            p.setPen(QtGui.QPen(QtGui.QColor(self.style["target"]),3));p.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            p.drawEllipse(QtCore.QPointF(self.ripple[0]-ox,self.ripple[1]-oy),radius,radius)
