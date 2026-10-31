// Windows-only primitives. UI Automation is read-only; all actions use real input.
using System;
using System.IO;
using System.Text;
using System.Linq;
using System.Drawing;
using System.Drawing.Imaging;
using System.Drawing.Drawing2D;
using System.Diagnostics;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Threading;
using System.Windows.Automation;
using System.Windows.Forms;
using System.Web.Script.Serialization;

static class Native {
    [StructLayout(LayoutKind.Sequential)] public struct POINT { public int X, Y; public POINT(int x,int y){X=x;Y=y;} }
    [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L,T,R,B; }
    [StructLayout(LayoutKind.Sequential)] struct GUITHREADINFO { public int cbSize, flags; public IntPtr active,focus,capture,menu,move,caret; public RECT caretRect; }
    [StructLayout(LayoutKind.Sequential)] struct MOUSEINPUT { public int dx,dy; public uint data,flags,time; public UIntPtr extra; }
    [StructLayout(LayoutKind.Sequential)] struct KEYBDINPUT { public ushort vk,scan; public uint flags,time; public UIntPtr extra; }
    [StructLayout(LayoutKind.Explicit)] struct UNION { [FieldOffset(0)] public MOUSEINPUT mouse; [FieldOffset(0)] public KEYBDINPUT key; }
    [StructLayout(LayoutKind.Sequential)] struct INPUT { public uint type; public UNION data; }
    [StructLayout(LayoutKind.Sequential)] struct KBDHOOK { public uint vk,scan,flags,time; public UIntPtr extra; }
    [StructLayout(LayoutKind.Sequential)] struct MOUSEHOOK { public POINT pt; public uint data,flags,time; public UIntPtr extra; }
    public delegate IntPtr Hook(int code,IntPtr wp,IntPtr lp);
    public delegate void WinEvent(IntPtr hook,uint evt,IntPtr hwnd,int obj,int child,uint tid,uint time);
    delegate bool EnumProc(IntPtr h,IntPtr p);
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] static extern bool GetWindowRect(IntPtr h,out RECT r);
    [DllImport("user32.dll")] static extern int GetWindowText(IntPtr h,StringBuilder s,int n);
    [DllImport("user32.dll")] static extern int GetClassName(IntPtr h,StringBuilder s,int n);
    [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr h,out uint pid);
    [DllImport("user32.dll")] static extern bool GetGUIThreadInfo(uint thread,ref GUITHREADINFO info);
    [DllImport("user32.dll")] public static extern IntPtr GetAncestor(IntPtr h,uint flags);
    [DllImport("user32.dll")] static extern IntPtr WindowFromPoint(POINT p);
    [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr h);
    [DllImport("user32.dll")] static extern bool IsWindowEnabled(IntPtr h);
    [DllImport("user32.dll")] static extern bool EnumWindows(EnumProc f,IntPtr p);
    [DllImport("user32.dll")] static extern bool ShowWindow(IntPtr h,int cmd);
    [DllImport("user32.dll")] static extern bool SetForegroundWindow(IntPtr h);
    [DllImport("user32.dll")] static extern bool BringWindowToTop(IntPtr h);
    [DllImport("user32.dll")] static extern bool AttachThreadInput(uint a,uint b,bool attach);
    [DllImport("kernel32.dll")] static extern uint GetCurrentThreadId();
    [DllImport("user32.dll")] static extern bool SetCursorPos(int x,int y);
    [DllImport("user32.dll")] static extern bool GetCursorPos(out POINT p);
    [DllImport("user32.dll")] static extern bool ClientToScreen(IntPtr h,ref POINT p);
    [DllImport("user32.dll",SetLastError=true)] static extern uint SendInput(uint n,INPUT[] inputs,int size);
    [DllImport("user32.dll")] public static extern IntPtr SetWindowsHookEx(int id,Hook hook,IntPtr module,uint tid);
    [DllImport("user32.dll")] public static extern bool UnhookWindowsHookEx(IntPtr h);
    [DllImport("user32.dll")] public static extern IntPtr CallNextHookEx(IntPtr h,int code,IntPtr wp,IntPtr lp);
    [DllImport("user32.dll")] public static extern IntPtr SetWinEventHook(uint min,uint max,IntPtr module,WinEvent callback,uint pid,uint tid,uint flags);
    [DllImport("user32.dll")] public static extern bool UnhookWinEvent(IntPtr h);
    [DllImport("kernel32.dll",CharSet=CharSet.Unicode)] public static extern IntPtr GetModuleHandle(string name);
    [DllImport("user32.dll")] static extern bool SetProcessDpiAwarenessContext(IntPtr value);
    [DllImport("user32.dll")] static extern uint GetDpiForSystem();
    [DllImport("user32.dll")] static extern uint GetDpiForWindow(IntPtr hwnd);
    [DllImport("winmm.dll")] static extern uint timeBeginPeriod(uint period);
    [DllImport("winmm.dll")] static extern uint timeEndPeriod(uint period);
    [DllImport("user32.dll")] public static extern bool SetWindowDisplayAffinity(IntPtr h,uint affinity);
    [DllImport("user32.dll")] static extern IntPtr OpenInputDesktop(uint flags,bool inherit,uint access);
    [DllImport("user32.dll")] static extern bool CloseDesktop(IntPtr h);
    [DllImport("user32.dll",CharSet=CharSet.Unicode)] static extern bool GetUserObjectInformation(IntPtr h,int index,StringBuilder value,int length,out int needed);
    public static string State; static uint lockedPid; static int[] lockedBox; static string lockedEpoch;
    static JavaScriptSerializer json = new JavaScriptSerializer();
    static string F(string name){ return Path.Combine(State,name); }
    public static void Write(string name,string text){string temp=F(name)+"."+Process.GetCurrentProcess().Id+".tmp";File.WriteAllText(temp,text,new UTF8Encoding(false));if(File.Exists(F(name)))File.Replace(temp,F(name),null);else File.Move(temp,F(name));}
    public static string Read(string name){try{using(var file=new FileStream(F(name),FileMode.Open,FileAccess.Read,FileShare.ReadWrite|FileShare.Delete))using(var reader=new StreamReader(file,Encoding.UTF8))return reader.ReadToEnd();}catch{return "";}}
    public static void Output(object o){Console.WriteLine(json.Serialize(o));}
    public static string Title(IntPtr h){var s=new StringBuilder(2048);GetWindowText(h,s,s.Capacity);return s.ToString();}
    public static string Class(IntPtr h){var s=new StringBuilder(256);GetClassName(h,s,s.Capacity);return s.ToString();}
    public static uint Pid(IntPtr h){uint p;GetWindowThreadProcessId(h,out p);return p;}
    public static int[] Bounds(IntPtr h){RECT r;GetWindowRect(h,out r);return new int[]{r.L,r.T,r.R-r.L,r.B-r.T};}
    public static bool DesktopOK(){IntPtr h=OpenInputDesktop(0,false,1);if(h==IntPtr.Zero)return false;try {var s=new StringBuilder(256);int n;return GetUserObjectInformation(h,2,s,512,out n)&&s.ToString().Equals("Default",StringComparison.OrdinalIgnoreCase);}finally{CloseDesktop(h);}}
    public static void CheckStop(){if(File.Exists(F("stopped")))throw new DeskError(3,"중지됨: 사용자 새 요청 전에는 재시작하지 마세요.");if(!DesktopOK())throw new DeskError(2,"보호된 데스크톱 또는 잠금 화면: 사용자에게 넘겨 주세요.");}
    public static Dictionary<string,object> Window(){var h=GetForegroundWindow();uint p=Pid(h);string name="";try{name=Process.GetProcessById((int)p).ProcessName;}catch{}var g=new GUITHREADINFO();g.cbSize=Marshal.SizeOf(g);GetGUIThreadInfo(0,ref g);return new Dictionary<string,object>{{"hwnd",h.ToInt64()},{"pid",p},{"app",name},{"title",Title(h)},{"class",Class(h)},{"box",Bounds(h)},{"focus",g.focus.ToInt64()},{"desktop",DesktopOK()}};}
    public static string Focus(){try{var e=AutomationElement.FocusedElement;return string.Join(".",e.GetRuntimeId())+"|"+e.Current.ControlType.ProgrammaticName+"|"+e.Current.BoundingRectangle.ToString();}catch{return "unavailable";}}
    public static int[] FocusEditBox(){
        int[] box=null;
        var worker=new Thread(()=>{try{var e=AutomationElement.FocusedElement;var c=e.Current;var r=c.BoundingRectangle;if(c.ControlType==ControlType.Edit&&!c.IsOffscreen&&!r.IsEmpty)box=new[]{(int)Math.Floor(r.X),(int)Math.Floor(r.Y),(int)Math.Ceiling(r.Width),(int)Math.Ceiling(r.Height)};}catch{}});
        worker.IsBackground=true;worker.SetApartmentState(ApartmentState.MTA);worker.Start();
        return worker.Join(500)?box:null;
    }
    static bool SameFamily(IntPtr a,IntPtr b){return a==b || GetAncestor(a,2)==GetAncestor(b,2) || (Class(GetAncestor(a,2))=="#32768" && Pid(a)==Pid(b));}
    public static bool PointVisible(IntPtr target,int x,int y){var h=WindowFromPoint(new POINT(x,y));return h!=IntPtr.Zero&&SameFamily(h,target);}
    static bool Contains(int[] b,int x,int y){return x>=b[0]&&x<b[0]+b[2]&&y>=b[1]&&y<b[1]+b[3];}
    public static void GuardPoint(int x,int y){CheckStop();IntPtr front=GetForegroundWindow();if(!IsWindowEnabled(front)||!PointVisible(front,x,y))throw new DeskError(1,"대상이 다른 창·대화상자·패널에 가려져 있어 누르지 않음.");}
    public static void GuardTarget(long expected,long focus){CheckStop();if(lockedEpoch!=null&&Read("user-input")!=lockedEpoch)throw new DeskError(6,"사용자 입력으로 화면이 바뀌어 입력을 멈춤.");if(GetForegroundWindow().ToInt64()!=expected || (lockedPid!=0 && Pid(new IntPtr(expected))!=lockedPid) || (lockedBox!=null && !Bounds(new IntPtr(expected)).SequenceEqual(lockedBox)))throw new DeskError(6,"입력 중 대상 창이 바뀌어 멈춤. desk see로 다시 확인하세요.");if(focus!=0){var g=new GUITHREADINFO();g.cbSize=Marshal.SizeOf(g);GetGUIThreadInfo(0,ref g);if(g.focus.ToInt64()!=focus)throw new DeskError(6,"키보드 포커스가 바뀌어 멈춤.");}if(File.Exists(F("user-input"))&&(DateTime.UtcNow-File.GetLastWriteTimeUtc(F("user-input"))).TotalSeconds<0.12)throw new DeskError(6,"사용자 입력이 감지되어 입력을 멈춤.");}
    static void Emit(INPUT i,long hwnd,long focus){GuardTarget(hwnd,focus);if(SendInput(1,new INPUT[]{i},Marshal.SizeOf(typeof(INPUT)))!=1)throw new DeskError(2,"Windows가 입력을 거부함: 대상 앱과 desk의 권한 수준을 확인하세요.");}
    static INPUT Mouse(uint flags,uint data){var i=new INPUT();i.type=0;i.data.mouse.flags=flags;i.data.mouse.data=data;i.data.mouse.extra=new UIntPtr(0x4445534B);return i;}
    static INPUT Key(ushort vk,ushort scan,uint flags){var i=new INPUT();i.type=1;i.data.key.vk=vk;i.data.key.scan=scan;i.data.key.flags=flags;i.data.key.extra=new UIntPtr(0x4445534B);return i;}
    static void Release(INPUT i){SendInput(1,new INPUT[]{i},Marshal.SizeOf(typeof(INPUT)));}
    internal class ClickBinding {public string id {get;set;} public string role {get;set;} public string name {get;set;} public int[] box {get;set;} public bool enabled {get;set;}}
    internal class ClickToken {public int[] point {get;set;} public int[] box {get;set;} public string pixels {get;set;} public ClickBinding binding {get;set;} public string inputEpoch {get;set;}}
    public static ClickBinding BindPoint(int x,int y){
        CheckStop();GuardPoint(x,y);
        ClickBinding binding=null;Exception failure=null;
        var worker=new Thread(()=>{try{binding=BindAccessiblePoint(x,y);}catch(Exception e){failure=e;}});
        worker.IsBackground=true;worker.SetApartmentState(ApartmentState.MTA);worker.Start();
        if(!worker.Join(500)){
            CheckStop();GuardPoint(x,y);
            Console.Error.WriteLine("접근성 응답 없음: 창·좌표·화면 픽셀 검증으로 확인합니다.");
            return null;
        }
        CheckStop();GuardPoint(x,y);
        if(failure!=null)throw new DeskError(2,"접근성 대상 확인 실패: "+failure.GetType().Name);
        return binding;
    }
    static ClickBinding BindAccessiblePoint(int x,int y){
        try{var element=AutomationElement.FromPoint(new System.Windows.Point(x,y));for(int i=0;i<10&&element!=null;i++){
            var c=element.Current;var r=c.BoundingRectangle;string role=c.ControlType.ProgrammaticName.Replace("ControlType.","");object pattern;
            bool pressable=element.TryGetCurrentPattern(InvokePattern.Pattern,out pattern)||element.TryGetCurrentPattern(TogglePattern.Pattern,out pattern)||element.TryGetCurrentPattern(SelectionItemPattern.Pattern,out pattern)||new[]{"Edit","ComboBox","Slider","TabItem","MenuItem","Hyperlink","Button","CheckBox","RadioButton","ListItem","DataItem","TreeItem"}.Contains(role);
            if(!c.IsOffscreen&&!r.IsEmpty&&r.Contains(x,y)&&pressable)return new ClickBinding(){id=string.Join(".",element.GetRuntimeId()),role=role,name=c.IsPassword?"(보호된 입력칸)":Clean(c.Name),box=new[]{(int)Math.Floor(r.X),(int)Math.Floor(r.Y),(int)Math.Ceiling(r.Width),(int)Math.Ceiling(r.Height)},enabled=c.IsEnabled};
            if(c.ControlType==ControlType.Window||c.ControlType==ControlType.Document)break;
            element=TreeWalker.ControlViewWalker.GetParent(element);
        }}catch(ElementNotAvailableException){}catch(InvalidOperationException){}
        return null;
    }
    static bool BindingEqual(ClickBinding a,ClickBinding b){if(a==null||b==null)return a==null&&b==null;return a.id==b.id&&a.role==b.role&&a.name==b.name&&a.enabled==b.enabled&&a.box.SequenceEqual(b.box);}
    static Rectangle CaretBox(ClickBinding binding){
        if(binding==null||binding.role!="Edit")return Rectangle.Empty;
        var g=new GUITHREADINFO();g.cbSize=Marshal.SizeOf(g);
        if(!GetGUIThreadInfo(0,ref g)||g.caret==IntPtr.Zero)return Rectangle.Empty;
        var p=new POINT(g.caretRect.L,g.caretRect.T);if(!ClientToScreen(g.caret,ref p))return Rectangle.Empty;
        var r=new Rectangle(p.X,p.Y,g.caretRect.R-g.caretRect.L,g.caretRect.B-g.caretRect.T);
        var target=new Rectangle(binding.box[0],binding.box[1],binding.box[2],binding.box[3]);
        return target.Contains(r)?r:Rectangle.Empty;
    }
    public static void GuardPress(int x,int y){
        CheckStop();ClickToken token;
        try{token=json.Deserialize<ClickToken>(File.ReadAllText(F("click-guard.json"),Encoding.UTF8));}catch{throw new DeskError(6,"클릭 근거가 없습니다. desk로 화면을 다시 읽으세요.");}
        if(token.inputEpoch!=Read("user-input"))throw new DeskError(6,"클릭 준비 중 사용자 입력이 있어 멈춤.");
        if(token.point==null||token.point.Length!=2||token.point[0]!=x||token.point[1]!=y||token.box==null||token.box.Length!=4||!Contains(token.box,x,y))throw new DeskError(6,"클릭 좌표와 확인한 대상이 다릅니다.");
        var actual=BindPoint(x,y);
        if(!BindingEqual(token.binding,actual)||(actual!=null&&!actual.enabled))throw new DeskError(6,"마우스를 누르기 직전에 대상의 정체·위치가 바뀌어 멈춤.");
        // Pixels are not compared under the pointer: hover tint, underline and tooltips
        // legitimately repaint the target there. ArmStep verified them before movement.
        CheckStop();GuardPoint(x,y);
        if(token.inputEpoch!=Read("user-input")||!BindingEqual(token.binding,BindPoint(x,y)))throw new DeskError(6,"누르기 직전에 대상 또는 사용자 입력이 바뀜.");
    }
    static void RebaseDouble(int x,int y){
        var token=json.Deserialize<ClickToken>(Read("click-guard.json"));
        // Only a retained accessible identity permits first-click focus/selection repaint.
        if(token.binding==null)return;
        if(!BindingEqual(token.binding,BindPoint(x,y)))throw new DeskError(6,"첫 클릭 뒤 대상이 바뀌어 두 번째 클릭을 취소함.");
        string image=F("click-double.png");Capture(image,token.box,false);
        token.pixels=image;Write("click-guard.json",json.Serialize(token));
    }
    static void Move(int x,int y,long hwnd){
        var all=SystemInformation.VirtualScreen;if(!all.Contains(x,y))throw new DeskError(1,"화면 밖 좌표");GuardTarget(hwnd,0);
        POINT start;GetCursorPos(out start);double dx=x-start.X,dy=y-start.Y,distance=Math.Sqrt(dx*dx+dy*dy);
        var style=new WidgetStyle();double scale=DpiScale(new IntPtr(hwnd));
        double duration=distance/(Math.Max(1,style.N("cursorPointsPerSecond"))*scale)*1000;
        int steps=Math.Max(1,(int)Math.Ceiling(duration/Math.Max(1,style.N("cursorFrameMilliseconds"))));var clock=Stopwatch.StartNew();
        bool precise=timeBeginPeriod(1)==0;
        try{for(int step=1;step<=steps;step++){
            double deadline=duration*step/steps;
            while(clock.Elapsed.TotalMilliseconds<deadline){GuardTarget(hwnd,0);Thread.Sleep(1);}
            double t=duration<=0?1:Math.Min(1,clock.Elapsed.TotalMilliseconds/duration);int px=t>=1?x:(int)Math.Round(start.X+dx*t),py=t>=1?y:(int)Math.Round(start.Y+dy*t);
            var i=Mouse(0x8000|0x4000|1,0);i.data.mouse.dx=(int)Math.Round((px-all.Left)*65535.0/Math.Max(1,all.Width-1));i.data.mouse.dy=(int)Math.Round((py-all.Top)*65535.0/Math.Max(1,all.Height-1));Emit(i,hwnd,0);
            if(t>=1)break;
        }}finally{if(precise)timeEndPeriod(1);}
    }
    static double DpiScale(IntPtr hwnd){uint dpi=GetDpiForWindow(hwnd);return (dpi==0?96:dpi)/96.0;}
    static void ClickEffect(int x,int y,long hwnd){Write("click-effect",json.Serialize(new {id=Guid.NewGuid().ToString("N"),x=x,y=y,scale=DpiScale(new IntPtr(hwnd))}));}
    static ushort Vk(string k){switch(k){case "ctrl":case "control":return 0x11;case "alt":case "option":return 0x12;case "shift":return 0x10;case "win":case "super":return 0x5B;case "cmd":case "primary":return 0x11;case "enter":case "return":return 0x0D;case "tab":return 9;case "esc":case "escape":return 0x1B;case "space":return 0x20;case "backspace":return 8;case "delete":return 0x2E;case "left":return 0x25;case "up":return 0x26;case "right":return 0x27;case "down":return 0x28;case "home":return 0x24;case "end":return 0x23;case "pageup":return 0x21;case "pagedown":return 0x22;case "plus":return 0xBB;case "minus":return 0xBD;}if(k.Length==1&&char.IsLetterOrDigit(k[0]))return (ushort)char.ToUpperInvariant(k[0]);int n;if(k.StartsWith("f")&&int.TryParse(k.Substring(1),out n)&&n>=1&&n<=24)return (ushort)(0x70+n-1);throw new DeskError(2,"지원하지 않는 키: "+k);}
    public static void Action(string kind,string[] a,long hwnd,long focus,string suppliedText=null,bool showEffect=true){
        if(kind=="key"){string[] parts=a[0].ToLowerInvariant().Split('+');var held=new List<ushort>();try{foreach(string p in parts){ushort v=Vk(p);Emit(Key(v,0,0),hwnd,focus);held.Add(v);}}finally{held.Reverse();foreach(ushort v in held)Release(Key(v,0,2));}return;}
        if(kind=="type"){if(suppliedText==null)Console.InputEncoding=Encoding.UTF8;string text=suppliedText??Console.In.ReadToEnd();foreach(char ch in text){ushort v=ch=='\n'?(ushort)13:ch=='\t'?(ushort)9:(ushort)0;INPUT down=v!=0?Key(v,0,0):Key(0,ch,4);INPUT up=v!=0?Key(v,0,2):Key(0,ch,6);Emit(down,hwnd,focus);Release(up);}return;}
        int x=int.Parse(a[0]),y=int.Parse(a[1]);if(kind!="move")GuardPoint(x,y);Move(x,y,hwnd);if(kind=="move")return;
        if(kind=="scroll"){int amount=int.Parse(a[3])*120;string dir=a[2];Emit(Mouse(dir=="left"||dir=="right"?0x1000u:0x800u,unchecked((uint)(dir=="down"||dir=="left"?-amount:amount))),hwnd,0);return;}
        uint dn=kind=="rclick"?8u:2u,upflag=kind=="rclick"?16u:4u;
        int times=kind=="dclick"?2:1;for(int t=0;t<times;t++){GuardPress(x,y);Emit(Mouse(dn,0),hwnd,0);try{if(kind=="drag"){lockedBox=null;Move(int.Parse(a[2]),int.Parse(a[3]),hwnd);}else Thread.Sleep(30);}finally{Release(Mouse(upflag,0));}if(times==2&&t==0){Thread.Sleep(40);RebaseDouble(x,y);}}
        if(showEffect)ClickEffect(kind=="drag"?int.Parse(a[2]):x,kind=="drag"?int.Parse(a[3]):y,hwnd);
    }
    public static void Capture(string path,int[] b,bool report=true){CheckStop();Rectangle r=Rectangle.Intersect(new Rectangle(b[0],b[1],b[2],b[3]),SystemInformation.VirtualScreen);if(r.Width<=0||r.Height<=0)throw new DeskError(2,"빈 캡처 영역");using(var bitmap=new Bitmap(r.Width,r.Height,PixelFormat.Format32bppArgb)){using(var g=Graphics.FromImage(bitmap)){g.CopyFromScreen(r.Left,r.Top,0,0,r.Size,CopyPixelOperation.SourceCopy);}bitmap.Save(path,ImageFormat.Png);}if(report)Output(new {box=new int[]{r.Left,r.Top,r.Width,r.Height},path=path});}
    class StepWindow {public long hwnd {get;set;} public uint pid {get;set;} public int[] box {get;set;} public long focus {get;set;}}
    class StepPlan {public int[] point {get;set;} public int[] box {get;set;} public string pixels {get;set;} public ClickBinding observedBinding {get;set;} public string inputEpoch {get;set;}}
    class StepRequest {public StepRequest[] actions {get;set;} public string kind {get;set;} public string[] args {get;set;} public string text {get;set;} public StepWindow expected {get;set;} public string epoch {get;set;} public StepPlan plan {get;set;}}
    static int[] VisibleBox(int[] box){var r=Rectangle.Intersect(new Rectangle(box[0],box[1],box[2],box[3]),SystemInformation.VirtualScreen);return new[]{r.Left,r.Top,r.Width,r.Height};}
    static void CleanCapture(string path,int[] box){
        File.Delete(F("focus-hidden-ack"));Write("focus-hidden","1");
        try{
            var clock=Stopwatch.StartNew();
            while(!File.Exists(F("focus-hidden-ack"))){CheckStop();if(clock.ElapsedMilliseconds>1000)throw new DeskError(2,"표시 레이어 숨김 확인 실패");Thread.Sleep(5);}
            Capture(path,box,false);
            var panel=Read("panel-rect");
            if(panel.Length>0){int[] p=panel.Split(',').Select(int.Parse).ToArray();using(var image=new Bitmap(path))using(var clean=new Bitmap(image.Width,image.Height,PixelFormat.Format32bppArgb)){using(var g=Graphics.FromImage(clean)){g.DrawImageUnscaled(image,0,0);g.FillRectangle(Brushes.Black,p[0]-box[0],p[1]-box[1],p[2],p[3]);}image.Dispose();clean.Save(path,ImageFormat.Png);}}
        }finally{File.Delete(F("focus-hidden"));}
    }
    static void ArmStep(StepPlan plan,string pre,int[] region,int x,int y){
        if(plan==null||plan.point==null||plan.point[0]!=x||plan.point[1]!=y||plan.inputEpoch!=Read("user-input"))throw new DeskError(6,"클릭할 화면 근거가 바뀌었습니다.");
        var binding=BindPoint(x,y);
        string armed=F("step-armed.png");
        using(var source=new Bitmap(pre))using(var crop=new Bitmap(plan.box[2],plan.box[3],PixelFormat.Format32bppArgb)){
            using(var g=Graphics.FromImage(crop))g.DrawImageUnscaled(source,region[0]-plan.box[0],region[1]-plan.box[1]);
            // Enumeration and hit-testing can return different nodes for the
            // same pixels. Bind the current hit target before moving, then
            // retain its identity through GuardPress at mouse-down.
            if(plan.observedBinding==null||!BindingEqual(plan.observedBinding,binding)){using(var original=new Bitmap(plan.pixels)){if(original.Size!=crop.Size)throw new DeskError(6,"클릭 영역 크기가 바뀌었습니다.");for(int py=0;py<crop.Height;py++)for(int px=0;px<crop.Width;px++)if(original.GetPixel(px,py).ToArgb()!=crop.GetPixel(px,py).ToArgb())throw new DeskError(6,"클릭 대상 화면이 바뀌었습니다.");}}
            crop.Save(armed,ImageFormat.Png);
        }
        Write("click-guard.json",json.Serialize(new ClickToken(){point=plan.point,box=plan.box,pixels=armed,binding=binding,inputEpoch=plan.inputEpoch}));
    }
    static Dictionary<string,object> RunStep(StepRequest request){
        var expected=request.expected;
        lockedPid=expected.pid;lockedBox=expected.box;lockedEpoch=request.epoch;
        bool keys=request.kind=="key"||request.kind=="type";
        GuardTarget(expected.hwnd,keys?expected.focus:0);
        var before=Window();var preBox=VisibleBox((int[])before["box"]);
        bool pointer=!keys;int x=0,y=0;
        if(pointer){x=int.Parse(request.args[0]);y=int.Parse(request.args[1]);}
        var editBox=request.kind=="type"?FocusEditBox():null;
        string pre=F("pre.png"),post=F("post.png");CleanCapture(pre,preBox);
        bool pressing=new[]{"click","dclick","rclick","drag"}.Contains(request.kind);
        if(pressing)ArmStep(request.plan,pre,preBox,x,y);
        Action(request.kind,request.args,expected.hwnd,keys?expected.focus:0,request.text,false);
        Thread.Sleep(80);CheckStop();var after=Window();
        bool closed=request.kind=="key"&&request.args[0].Equals("alt+f4",StringComparison.OrdinalIgnoreCase)&&(uint)after["pid"]!=expected.pid;
        if(closed)return new Dictionary<string,object>{{"closed",true},{"after",after}};
        if((uint)after["pid"]!=expected.pid)throw new DeskError(6,"입력 뒤 다른 앱으로 바뀌었습니다.");
        var postBox=VisibleBox((int[])after["box"]);CleanCapture(post,postBox);
        if(pressing)ClickEffect(request.kind=="drag"?int.Parse(request.args[2]):x,request.kind=="drag"?int.Parse(request.args[3]):y,expected.hwnd);
        return new Dictionary<string,object>{{"closed",false},{"before",before},{"after",after},{"preBox",preBox},{"postBox",postBox},{"editBox",editBox}};
    }
    static void Step(){
        Console.InputEncoding=Encoding.UTF8;
        var request=json.Deserialize<StepRequest>(Console.In.ReadToEnd());
        if(request.actions==null){Output(RunStep(request));return;}
        if(request.actions.Length==0||request.actions.Length>20)throw new DeskError(2,"행동 묶음은 1~20개입니다.");
        StepWindow expected=request.expected;Dictionary<string,object> result=null;object before=null,preBox=null;
        for(int i=0;i<request.actions.Length;i++){
            var item=request.actions[i];item.expected=expected;item.epoch=request.epoch;
            result=RunStep(item);
            if((bool)result["closed"]){Output(result);return;}
            if(i==0){before=result["before"];preBox=result["preBox"];File.Copy(F("pre.png"),F("sequence-pre.png"),true);}
            expected=json.Deserialize<StepWindow>(json.Serialize(result["after"]));
        }
        File.Copy(F("sequence-pre.png"),F("pre.png"),true);
        result["before"]=before;result["preBox"]=preBox;result["editBox"]=null;
        result["completed"]=request.actions.Length;Output(result);
    }
    static string Clean(string s){return (s??"").Replace('\t',' ').Replace('\r',' ').Replace('\n',' ');}
    public static void Uia(){CheckStop();IntPtr h=GetForegroundWindow();var root=AutomationElement.FromHandle(h);var q=new Queue<AutomationElement>();q.Enqueue(root);var watch=Stopwatch.StartNew();var rows=new List<object>();var screen=SystemInformation.VirtualScreen;int count=0;
        while(q.Count>0&&count++<6000&&watch.Elapsed.TotalSeconds<3){var e=q.Dequeue();try{var c=e.Current;var r=c.BoundingRectangle;bool visible=!c.IsOffscreen&&!r.IsEmpty&&r.Width>=3&&r.Height>=3;bool pass=visible&&screen.IntersectsWith(new Rectangle((int)r.X,(int)r.Y,(int)r.Width,(int)r.Height));
            if(pass&&e!=root&&PointVisible(h,(int)(r.X+r.Width/2),(int)(r.Y+r.Height/2))){string role=c.ControlType.ProgrammaticName.Replace("ControlType.","");var states=new List<string>();if(!c.IsEnabled)states.Add("비활성");object pat;
                if(e.TryGetCurrentPattern(TogglePattern.Pattern,out pat))states.Add(((TogglePattern)pat).Current.ToggleState==ToggleState.On?"켬":"끔");
                if(e.TryGetCurrentPattern(SelectionItemPattern.Pattern,out pat)&&((SelectionItemPattern)pat).Current.IsSelected)states.Add("선택됨");
                if(e.TryGetCurrentPattern(ExpandCollapsePattern.Pattern,out pat)&&((ExpandCollapsePattern)pat).Current.ExpandCollapseState==ExpandCollapseState.Expanded)states.Add("펼침");
                bool interactive=e.TryGetCurrentPattern(InvokePattern.Pattern,out pat)||e.TryGetCurrentPattern(TogglePattern.Pattern,out pat)||e.TryGetCurrentPattern(SelectionItemPattern.Pattern,out pat)||new[]{"Edit","ComboBox","Slider","TabItem","MenuItem","Hyperlink","Button","CheckBox","RadioButton","ListItem","DataItem","TreeItem","Text"}.Contains(role);
                if(interactive){int[] box=new[]{(int)Math.Floor(r.X),(int)Math.Floor(r.Y),(int)Math.Ceiling(r.Width),(int)Math.Ceiling(r.Height)};rows.Add(new {box=box,role=role,name=c.IsPassword?"(보호된 입력칸)":Clean(c.Name),state=states.Count>0?"["+string.Join(", ",states)+"]":"",id=string.Join(".",e.GetRuntimeId()),password=c.IsPassword});}}
            var walker=TreeWalker.ControlViewWalker;var child=walker.GetFirstChild(e);while(child!=null&&q.Count<6000){q.Enqueue(child);child=walker.GetNextSibling(child);}
        }catch(ElementNotAvailableException){}catch(InvalidOperationException){}}
        if(GetForegroundWindow()!=h)throw new DeskError(6,"화면을 읽는 중 창이 바뀜");Output(new {elements=rows,partial=q.Count>0,visited=count,focus=Focus()});}
    public static void Activate(string name){string stem=Path.GetFileNameWithoutExtension(name);bool exact=Path.IsPathRooted(name)&&File.Exists(name);var hits=new List<IntPtr>();EnumWindows(delegate(IntPtr h,IntPtr p){if(IsWindowVisible(h)&&Title(h).Length>0){try{var proc=Process.GetProcessById((int)Pid(h));if(exact ? string.Equals(Path.GetFullPath(proc.MainModule.FileName),Path.GetFullPath(name),StringComparison.OrdinalIgnoreCase) : proc.ProcessName.Equals(stem,StringComparison.OrdinalIgnoreCase))hits.Add(h);}catch{}}return true;},IntPtr.Zero);if(hits.Count>1)throw new DeskError(1,"여러 창이 일치함: desk windows로 확인하고 desk open --hwnd 번호 사용");if(hits.Count==1){ActivateWindow(hits[0]);}else{Process.Start(new ProcessStartInfo(name){UseShellExecute=true});}}
    public static void Windows(){var rows=new List<object>();EnumWindows(delegate(IntPtr h,IntPtr p){if(IsWindowVisible(h)&&Title(h).Length>0){string app="";try{app=Process.GetProcessById((int)Pid(h)).ProcessName;}catch{}rows.Add(new {hwnd=h.ToInt64(),pid=Pid(h),app=app,title=Title(h),box=Bounds(h)});}return true;},IntPtr.Zero);Output(rows);}
    public static void ActivateWindow(IntPtr target){
        CheckStop();if(!IsWindowVisible(target))throw new DeskError(1,"보이는 창이 아님");
        ShowWindow(target,9);if(GetForegroundWindow()==target)return;
        if(SetForegroundWindow(target)&&GetForegroundWindow()==target)return;
        uint ignored;uint front=GetWindowThreadProcessId(GetForegroundWindow(),out ignored),own=GetCurrentThreadId();
        bool attached=front!=own&&AttachThreadInput(own,front,true);
        try{BringWindowToTop(target);SetForegroundWindow(target);}finally{if(attached)AttachThreadInput(own,front,false);}
        if(GetForegroundWindow()!=target){
            CheckStop();
            var down=Key(0x12,0,0);var up=Key(0x12,0,2);
            if(SendInput(1,new INPUT[]{down},Marshal.SizeOf(typeof(INPUT)))!=1)throw new DeskError(1,"Windows가 앱 전환 입력을 거부했습니다");
            try{SetForegroundWindow(target);}finally{Release(up);}
        }
        if(GetForegroundWindow()!=target)throw new DeskError(1,"Windows가 창 활성화를 거부했습니다");
    }
    [STAThread] static int Main(string[] args){Console.OutputEncoding=new UTF8Encoding(false);try{SetProcessDpiAwarenessContext(new IntPtr(-4));State=Environment.GetEnvironmentVariable("DESK_STATE")??Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),".cache","desk");Directory.CreateDirectory(State);string cmd=args[0];if(cmd=="step")Step();else if(cmd=="window")Output(Window());else if(cmd=="focus")Console.WriteLine(Focus());else if(cmd=="focus-edit-box")Output(FocusEditBox());else if(cmd=="windows")Windows();else if(cmd=="size")Output(SystemInformation.VirtualScreen);else if(cmd=="uia")Uia();else if(cmd=="capture")Capture(args[1],args[2].Split(',').Select(int.Parse).ToArray());else if(cmd=="guard-point")GuardPoint(int.Parse(args[1]),int.Parse(args[2]));else if(cmd=="click-bind")Output(BindPoint(int.Parse(args[1]),int.Parse(args[2])));else if(cmd=="open")Activate(args[1]);else if(cmd=="activate"){ActivateWindow(new IntPtr(long.Parse(args[1]))); }else if(cmd=="action"){lockedPid=uint.Parse(args[4]);lockedBox=args[5].Split(',').Select(int.Parse).ToArray();Action(args[1],args.Skip(6).ToArray(),long.Parse(args[2]),long.Parse(args[3]));}else if(cmd=="panel"){Application.EnableVisualStyles();Application.Run(new Panel());}else throw new DeskError(2,"unknown native command");return 0;}catch(DeskError e){Console.Error.WriteLine(e.Message);return e.Code;}catch(Exception e){Console.Error.WriteLine("desk-native: "+e.Message);return 2;}}
    public class DeskError:Exception{public int Code;public DeskError(int code,string msg):base(msg){Code=code;}}
    internal class WidgetStyle {
        Dictionary<string,object> values;
        public WidgetStyle(){string path=Environment.GetEnvironmentVariable("DESK_WIDGET");if(string.IsNullOrEmpty(path))throw new DeskError(2,"공통 widget.json 경로가 없습니다. desk 실행기를 사용하세요.");values=json.Deserialize<Dictionary<string,object>>(File.ReadAllText(path,Encoding.UTF8));}
        public int N(string key){return Convert.ToInt32(values[key]);}
        public string S(string key){return Convert.ToString(values[key]);}
        public Color C(string key){return ColorTranslator.FromHtml(S(key));}
    }
    static GraphicsPath Round(RectangleF rect,float radius){var p=new GraphicsPath();float d=Math.Min(radius*2,Math.Min(rect.Width,rect.Height));p.AddArc(rect.X,rect.Y,d,d,180,90);p.AddArc(rect.Right-d,rect.Y,d,d,270,90);p.AddArc(rect.Right-d,rect.Bottom-d,d,d,0,90);p.AddArc(rect.X,rect.Bottom-d,d,d,90,90);p.CloseFigure();return p;}
    class StopButton:Control {
        WidgetStyle style;float scale;Action clicked;
        public StopButton(WidgetStyle spec,float s,Action click){style=spec;scale=s;clicked=click;TabStop=false;AccessibleRole=AccessibleRole.PushButton;AccessibleName="중지 · ESC";Cursor=Cursors.Hand;SetStyle(ControlStyles.UserPaint|ControlStyles.AllPaintingInWmPaint|ControlStyles.OptimizedDoubleBuffer,true);Text=style.S("stopLabel");}
        protected override void WndProc(ref Message m){if(m.Msg==0x21){m.Result=new IntPtr(3);return;}base.WndProc(ref m);}
        protected override void OnMouseDown(MouseEventArgs e){if(e.Button==MouseButtons.Left)clicked();}
        protected override void OnPaint(PaintEventArgs e){e.Graphics.SmoothingMode=SmoothingMode.AntiAlias;using(var p=Round(new RectangleF(0,0,Width-1,Height-1),style.N("stopRadius")*scale))using(var brush=new SolidBrush(style.C("accent"))){e.Graphics.FillPath(brush,p);}using(var font=new Font(SystemFonts.MessageBoxFont.FontFamily,style.N("stopFontSize")*scale,FontStyle.Bold,GraphicsUnit.Pixel))using(var brush=new SolidBrush(style.C("foreground")))using(var format=new StringFormat(){Alignment=StringAlignment.Center,LineAlignment=StringAlignment.Center}){e.Graphics.DrawString(Text,font,brush,new RectangleF(0,0,Width,Height),format);}}
    }
    internal class Panel:Form {
        WidgetStyle style=new WidgetStyle();float scale;StopButton stop;System.Windows.Forms.Timer timer;
        POINT pointerAnchor;bool pointerAnchorSet;
        Hook keyboard,mouse;WinEvent events;IntPtr kh,mh,eh;string lastFocus="",older="",newer="desk 준비 중";
        Form marker,ripple;FocusOverlay scan;string lastScan="",lastClick=Read("click-effect");DateTime stoppedAt=DateTime.MinValue;Rectangle workArea;
        protected override bool ShowWithoutActivation {get{return true;}}
        protected override CreateParams CreateParams {get{var p=base.CreateParams;p.ExStyle|=0x08000000|0x80;return p;}}
        protected override void WndProc(ref Message m){if(m.Msg==0x21){m.Result=new IntPtr(3);return;}base.WndProc(ref m);}
        int Px(int value){return (int)Math.Round(value*scale);}
        void Place(){var screen=Screen.PrimaryScreen.WorkingArea;workArea=screen;int width=Math.Min(Px(style.N("width")),screen.Width-Px(24)),height=Px(style.N("height"));Bounds=new Rectangle(screen.Left+(screen.Width-width)/2,screen.Bottom-Px(style.N("bottomMargin"))-height,width,height);using(var path=Round(new RectangleF(0,0,Width,Height),Px(style.N("cornerRadius")))){Region=new Region(path);}if(stop!=null)stop.Bounds=new Rectangle(Width-Px(style.N("stopWidth")+style.N("stopInset")),Px(style.N("stopTop")),Px(style.N("stopWidth")),Height-2*Px(style.N("stopTop")));if(IsHandleCreated)Write("panel-rect",string.Join(",",new[]{Left,Top,Width,Height}));}
        public Panel(){Text="desk 제어";StartPosition=FormStartPosition.Manual;AutoScaleMode=AutoScaleMode.None;FormBorderStyle=FormBorderStyle.None;TopMost=true;ShowInTaskbar=false;scale=GetDpiForSystem()/96f;BackColor=style.C("background");SetStyle(ControlStyles.UserPaint|ControlStyles.AllPaintingInWmPaint|ControlStyles.OptimizedDoubleBuffer,true);stop=new StopButton(style,scale,Stop);Controls.Add(stop);Place();
            pointerAnchorSet=GetCursorPos(out pointerAnchor);keyboard=Keyboard;mouse=Mouse;events=Event;IntPtr mod=GetModuleHandle(null);kh=SetWindowsHookEx(13,keyboard,mod,0);mh=SetWindowsHookEx(14,mouse,mod,0);eh=SetWinEventHook(3,0x800B,IntPtr.Zero,events,0,0,2);
            if(kh==IntPtr.Zero||mh==IntPtr.Zero)throw new DeskError(2,"사용자 입력·ESC 감지 설치 실패");timer=new System.Windows.Forms.Timer(){Interval=80};timer.Tick+=Tick;timer.Start();Shown+=delegate{SetWindowDisplayAffinity(Handle,0x11);Write("overlay.pid",Process.GetCurrentProcess().Id.ToString());Place();Tick(null,EventArgs.Empty);using(var bitmap=new Bitmap(Width,Height)){DrawToBitmap(bitmap,new Rectangle(0,0,Width,Height));bitmap.Save(F("widget-preview.png"),ImageFormat.Png);}};FormClosed+=delegate{timer.Stop();UnhookWindowsHookEx(kh);UnhookWindowsHookEx(mh);UnhookWinEvent(eh);DropMarks();if(Read("overlay.pid")==Process.GetCurrentProcess().Id.ToString())File.Delete(F("overlay.pid"));};}
        protected override void OnPaint(PaintEventArgs e){base.OnPaint(e);e.Graphics.SmoothingMode=SmoothingMode.AntiAlias;float border=style.N("borderWidth")*scale;using(var path=Round(new RectangleF(border/2,border/2,Width-border,Height-border),style.N("cornerRadius")*scale))using(var pen=new Pen(style.C("accent"),border)){e.Graphics.DrawPath(pen,path);}using(var format=new StringFormat(){Trimming=StringTrimming.EllipsisCharacter,FormatFlags=StringFormatFlags.NoWrap}){float textWidth=stop.Left-Px(24);using(var font=new Font(SystemFonts.MessageBoxFont.FontFamily,style.N("olderFontSize")*scale,FontStyle.Regular,GraphicsUnit.Pixel))using(var brush=new SolidBrush(style.C("muted"))){e.Graphics.DrawString(older,font,brush,new RectangleF(Px(16),Px(12),textWidth,Px(16)),format);}using(var font=new Font(SystemFonts.MessageBoxFont.FontFamily,style.N("newerFontSize")*scale,FontStyle.Bold,GraphicsUnit.Pixel))using(var brush=new SolidBrush(style.C("foreground"))){e.Graphics.DrawString(newer,font,brush,new RectangleF(Px(16),Px(32),textWidth,Px(22)),format);}}}
        void DropMarks(){if(marker!=null){marker.Close();marker=null;}if(scan!=null){scan.Close();scan=null;}if(ripple!=null){ripple.Close();ripple=null;}}
        void ShowStopped(){if(stoppedAt!=DateTime.MinValue)return;stoppedAt=DateTime.UtcNow;older=newer;newer="■ 중지됨 · AI 제어를 막았습니다";stop.Text="중지됨";stop.Invalidate();DropMarks();Invalidate();}
        void Stop(){File.WriteAllText(F("stopped"),"ESC / 중지 버튼 · "+DateTime.UtcNow.ToString("o"),new UTF8Encoding(false));ShowStopped();}
        void UserInput(){try{File.WriteAllText(F("user-input"),Guid.NewGuid().ToString("N"));}catch{}}
        internal bool HandleKey(uint vk,int message,ulong extra){if(extra==0x4445534B)return false;UserInput();if(vk!=27)return false;if(message==0x100||message==0x104)Stop();return true;}
        IntPtr Keyboard(int code,IntPtr wp,IntPtr lp){if(code>=0){var k=(KBDHOOK)Marshal.PtrToStructure(lp,typeof(KBDHOOK));if(HandleKey(k.vk,wp.ToInt32(),k.extra.ToUInt64()))return new IntPtr(1);}return CallNextHookEx(kh,code,wp,lp);}
        IntPtr Mouse(int code,IntPtr wp,IntPtr lp){if(code>=0){var m=(MOUSEHOOK)Marshal.PtrToStructure(lp,typeof(MOUSEHOOK));if(m.extra.ToUInt64()==0x4445534B){pointerAnchor=m.pt;pointerAnchorSet=true;}else if(wp.ToInt32()==0x200){if(!pointerAnchorSet){pointerAnchor=m.pt;pointerAnchorSet=true;}else{double dx=(double)m.pt.X-pointerAnchor.X,dy=(double)m.pt.Y-pointerAnchor.Y;double threshold=style.N("userPointerThresholdPixels");if(dx*dx+dy*dy>=threshold*threshold){pointerAnchor=m.pt;UserInput();}}}else{pointerAnchor=m.pt;pointerAnchorSet=true;UserInput();}}return CallNextHookEx(mh,code,wp,lp);}
        void Event(IntPtr hook,uint evt,IntPtr hwnd,int obj,int child,uint tid,uint time){if(hwnd!=Handle&&(evt==3||evt==0x8005||evt==0x800B)){try{File.WriteAllText(F("window-event"),DateTime.UtcNow.Ticks.ToString());}catch{}}}
        void Tick(object sender,EventArgs e){try{Write("heartbeat",DateTime.UtcNow.ToString("o"));if(File.Exists(F("panel-close"))){File.Delete(F("panel-close"));Close();return;}if(workArea!=Screen.PrimaryScreen.WorkingArea)Place();if(File.Exists(F("stopped"))){ShowStopped();if((DateTime.UtcNow-stoppedAt).TotalSeconds>2.5)Close();return;}var lines=json.Deserialize<string[]>(Read("panel-lines.json"))??new string[0];string o=lines.Length>1?lines[lines.Length-2]:"",n=lines.Length>0?lines[lines.Length-1]:"";if(o!=older||n!=newer){older=o;newer=n;Invalidate();}
            if(File.Exists(F("focus-hidden"))){DropMarks();lastFocus=Read("highlight");lastScan=Read("focus-viz.json");lastClick=Read("click-effect");if(!File.Exists(F("focus-hidden-ack")))Write("focus-hidden-ack","1");return;}
            string s=Read("focus-viz.json");if(s!=lastScan){lastScan=s;if(scan!=null){scan.Close();scan=null;}if(s.Length>0){var state=json.Deserialize<ScanState>(s);if(state.mode=="scan"){scan=new FocusOverlay(style,state);scan.Show();}}}
            string f=Read("highlight");if(f!=lastFocus){lastFocus=f;if(marker!=null){marker.Close();marker=null;}if(scan!=null){scan.Close();scan=null;}if(f.Length>0){int[] b=f.Split(',').Select(int.Parse).ToArray();marker=new Mark(new Rectangle(b[0],b[1],b[2],b[3]));marker.Show();}}
            string click=Read("click-effect");if(click!=lastClick){lastClick=click;if(click.Length>0){var effect=json.Deserialize<ClickVisual>(click);if(ripple!=null)ripple.Close();ripple=new Ripple(style,effect);ripple.Show();}}
        }catch{}}
    }
    internal class ScanItem {public double[] box {get;set;} public string kind {get;set;}}
    internal class ClickVisual {public string id {get;set;} public int x {get;set;} public int y {get;set;} public double scale {get;set;}}
    internal class Ripple:Form {
        WidgetStyle style;ClickVisual point;Stopwatch clock=Stopwatch.StartNew();System.Windows.Forms.Timer timer;
        protected override bool ShowWithoutActivation{get{return true;}}
        protected override CreateParams CreateParams{get{var p=base.CreateParams;p.ExStyle|=0x08000000|0x80|0x20;return p;}}
        public Ripple(WidgetStyle spec,ClickVisual data){style=spec;point=data;int radius=(int)Math.Ceiling((5+style.N("clickRippleRadius"))*point.scale)+6;StartPosition=FormStartPosition.Manual;FormBorderStyle=FormBorderStyle.None;ShowInTaskbar=false;TopMost=true;BackColor=Color.Magenta;TransparencyKey=Color.Magenta;Bounds=new Rectangle(point.x-radius,point.y-radius,radius*2,radius*2);DoubleBuffered=true;Shown+=delegate{SetWindowDisplayAffinity(Handle,0x11);};timer=new System.Windows.Forms.Timer(){Interval=16};timer.Tick+=delegate{if(clock.Elapsed.TotalMilliseconds>=style.N("clickRippleMilliseconds")||File.Exists(F("stopped"))||File.Exists(F("focus-hidden"))){Close();return;}Invalidate();};timer.Start();FormClosed+=delegate{timer.Stop();timer.Dispose();};}
        protected override void OnPaint(PaintEventArgs e){double t=Math.Min(1,clock.Elapsed.TotalMilliseconds/Math.Max(1,style.N("clickRippleMilliseconds")));float radius=(float)((5+style.N("clickRippleRadius")*t)*point.scale),cx=Width/2f,cy=Height/2f;e.Graphics.SmoothingMode=SmoothingMode.AntiAlias;Color color=style.C("target");using(var brush=new SolidBrush(Color.FromArgb((int)(65*(1-t)),color)))e.Graphics.FillEllipse(brush,cx-radius,cy-radius,radius*2,radius*2);using(var pen=new Pen(Color.FromArgb((int)(255*(1-t)),color),(float)(3*point.scale)))e.Graphics.DrawEllipse(pen,cx-radius,cy-radius,radius*2,radius*2);}
    }
    internal class ScanState {public string mode {get;set;} public string label {get;set;} public long hwnd {get;set;} public int[] frame {get;set;} public List<ScanItem> items {get;set;}}
    internal class FocusOverlay:Form {
        WidgetStyle style;ScanState state;Stopwatch watch=Stopwatch.StartNew();System.Windows.Forms.Timer timer;
        protected override bool ShowWithoutActivation{get{return true;}}
        protected override CreateParams CreateParams{get{var p=base.CreateParams;p.ExStyle|=0x08000000|0x80|0x20;return p;}}
        public FocusOverlay(WidgetStyle spec,ScanState data){style=spec;state=data;StartPosition=FormStartPosition.Manual;FormBorderStyle=FormBorderStyle.None;ShowInTaskbar=false;TopMost=true;BackColor=Color.Magenta;TransparencyKey=Color.Magenta;Bounds=SystemInformation.VirtualScreen;DoubleBuffered=true;Shown+=delegate{SetWindowDisplayAffinity(Handle,0x11);};timer=new System.Windows.Forms.Timer(){Interval=16};timer.Tick+=delegate{double elapsed=watch.Elapsed.TotalMilliseconds;if(elapsed>style.N("scanHoldMilliseconds")+style.N("scanFadeMilliseconds")||File.Exists(F("stopped"))||File.Exists(F("focus-hidden"))||GetForegroundWindow().ToInt64()!=state.hwnd){Close();return;}Opacity=Math.Min(1,Math.Max(.01,(style.N("scanHoldMilliseconds")+style.N("scanFadeMilliseconds")-elapsed)/style.N("scanFadeMilliseconds")));Invalidate();};timer.Start();FormClosed+=delegate{timer.Stop();timer.Dispose();};}
        protected override void OnPaint(PaintEventArgs e){double elapsed=watch.Elapsed.TotalMilliseconds,sweep=style.N("scanSweepMilliseconds");var g=e.Graphics;g.SmoothingMode=SmoothingMode.AntiAlias;var r=state.frame;int ox=Left,oy=Top;
            if(elapsed<sweep+250){double t=Math.Min(1,elapsed/sweep);t=t<.5?2*t*t:1-Math.Pow(-2*t+2,2)/2;float y=(float)(r[1]-oy+r[3]*t);using(var pen=new Pen(style.C("scanLine"),2))g.DrawLine(pen,r[0]-ox,y,r[0]+r[2]-ox,y);}
            double top=state.items.Count>0?state.items.Min(i=>i.box[1]):r[1],bottom=state.items.Count>0?state.items.Max(i=>i.box[1]+i.box[3]):r[1]+r[3];
            foreach(var item in state.items){var b=item.box;if(elapsed<sweep*(b[1]-top)/Math.Max(1,bottom-top))continue;Color color=style.C(item.kind=="A"?"scanControl":item.kind=="O"?"scanText":"scanDetection");using(var path=Round(new RectangleF((float)(b[0]-ox-2),(float)(b[1]-oy-2),(float)b[2]+4,(float)b[3]+4),4))using(var pen=new Pen(color,1.5f)){g.DrawPath(pen,path);}}
            if(!string.IsNullOrEmpty(state.label)){using(var font=new Font(SystemFonts.MessageBoxFont.FontFamily,13,FontStyle.Bold,GraphicsUnit.Pixel)){var size=g.MeasureString(state.label,font);float x=Math.Max(4,Math.Min(Width-size.Width-20,r[0]-ox+r[2]/2-size.Width/2)),y=Math.Max(4,r[1]-oy+8);using(var p=Round(new RectangleF(x,y,size.Width+16,size.Height+6),10))using(var bg=new SolidBrush(style.C("scanControl"))){g.FillPath(bg,p);}g.DrawString(state.label,font,Brushes.White,x+8,y+3);}}
        }
    }
    class Mark:Form{WidgetStyle style=new WidgetStyle();protected override bool ShowWithoutActivation{get{return true;}}protected override CreateParams CreateParams{get{var p=base.CreateParams;p.ExStyle|=0x08000000|0x80|0x20;return p;}}public Mark(Rectangle r){StartPosition=FormStartPosition.Manual;FormBorderStyle=FormBorderStyle.None;ShowInTaskbar=false;TopMost=true;BackColor=Color.Magenta;TransparencyKey=Color.Magenta;Bounds=Rectangle.Inflate(r,4,4);Shown+=delegate{SetWindowDisplayAffinity(Handle,0x11);};}protected override void OnPaint(PaintEventArgs e){using(var p=new Pen(style.C("target"),3)){e.Graphics.DrawRectangle(p,2,2,Width-5,Height-5);}}}
}
