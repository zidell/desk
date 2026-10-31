// Visible component test; sends no keyboard or mouse input to user applications.
using System;
using System.IO;
using System.Linq;
using System.Drawing;
using System.Diagnostics;
using System.Collections.Generic;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Windows.Forms;
class WidgetTest {
    [DllImport("user32.dll")] static extern bool SetProcessDpiAwarenessContext(IntPtr value);
    [DllImport("user32.dll")] static extern uint GetDpiForSystem();
    static void Assert(bool condition,string reason){if(!condition)throw new Exception(reason);}
    [STAThread] static int Main(string[] args){
        SetProcessDpiAwarenessContext(new IntPtr(-4));Application.EnableVisualStyles();
        Native.State=args[0];Directory.CreateDirectory(Native.State);Native.Write("panel-lines.json","[\"첫 번째 작업 기록\",\"두 번째 작업 기록\"]");
        int result=0;var panel=new Native.Panel();var style=new Native.WidgetStyle();Native.FocusOverlay scan=null;
        panel.Shown+=delegate{
            var state=new Native.ScanState(){mode="scan",label="화면 인식 · 3개 요소",hwnd=Native.GetForegroundWindow().ToInt64(),frame=new[]{300,300,500,300},items=new List<Native.ScanItem>{new Native.ScanItem(){box=new double[]{330,350,100,30},kind="A"},new Native.ScanItem(){box=new double[]{450,420,100,30},kind="O"},new Native.ScanItem(){box=new double[]{570,480,100,30},kind="D"}}};
            scan=new Native.FocusOverlay(style,state);scan.Show();
            var timer=new System.Windows.Forms.Timer(){Interval=1000};timer.Tick+=delegate{
                timer.Stop();try{
                    Assert(!File.Exists(Path.Combine(Native.State,"stopped")),"사용자가 중지함");
                    var area=Screen.PrimaryScreen.WorkingArea;float scale=GetDpiForSystem()/96f;
                    Assert(Math.Abs(panel.Left+panel.Width/2.0-(area.Left+area.Width/2.0))<=1,"하단 중앙 배치 실패");
                    Assert(Math.Abs(area.Bottom-panel.Bottom-12*scale)<=1,"하단 여백 실패");
                    Assert(panel.Controls[0].Text=="중지(ESC)","한 줄 라벨 불일치");
                    Assert(Native.PointVisible(panel.Handle,panel.Left+30,panel.Top+30),"인식 표시가 아래 위젯의 클릭을 가로챔");
                    using(var bitmap=new Bitmap(Path.Combine(Native.State,"widget-preview.png"))){Color c=bitmap.GetPixel(bitmap.Width/2,1);Assert(Math.Abs(c.R-219)<15&&Math.Abs(c.G-43)<15&&Math.Abs(c.B-43)<15,"빨간 테두리 불일치");}
                    using(var image=new Bitmap(scan.Width,scan.Height)){scan.DrawToBitmap(image,new Rectangle(0,0,image.Width,image.Height));image.Save(Path.Combine(Native.State,"scan-preview.png"));foreach(string key in new[]{"scanControl","scanText","scanDetection"}){var color=style.C(key);int count=0;for(int x=300;x<720;x++)for(int y=330;y<540;y++){var c=image.GetPixel(x-scan.Left,y-scan.Top);if(c.R==color.R&&c.G==color.G&&c.B==color.B)count++;}Assert(count>30,"인식 사각형 색상 누락: "+key);}}
                    Native.Write("focus-hidden","1");var hidden=Stopwatch.StartNew();while(!File.Exists(Path.Combine(Native.State,"focus-hidden-ack"))&&hidden.ElapsedMilliseconds<500){Application.DoEvents();System.Threading.Thread.Sleep(5);}Assert(File.Exists(Path.Combine(Native.State,"focus-hidden-ack")),"캡처 숨김 확인 누락");Assert(scan.IsDisposed,"인식 표시가 캡처 전에 닫히지 않음");File.Delete(Path.Combine(Native.State,"focus-hidden"));
                    Assert(!panel.HandleKey(27,0x100,0x4445534B),"desk가 보낸 ESC를 사용자 중지로 오인");
                    Assert(!File.Exists(Path.Combine(Native.State,"stopped")),"자체 ESC로 중지됨");
                    var elapsed=Stopwatch.StartNew();
                    if(args.Length>1&&args[1]=="button")typeof(Control).GetMethod("OnMouseDown",BindingFlags.Instance|BindingFlags.NonPublic).Invoke(panel.Controls[0],new object[]{new MouseEventArgs(MouseButtons.Left,1,40,20,0)});
                    else Assert(panel.HandleKey(27,0x100,0),"ESC 소비 실패");
                    Assert(File.Exists(Path.Combine(Native.State,"stopped")),"한 번의 중지 입력으로 상태 파일이 생성되지 않음");
                    Assert(elapsed.ElapsedMilliseconds<200,"중지 지연이 200ms 이상");
                    try{Native.CheckStop();throw new Exception("중지 뒤 입력 허용");}catch(Native.DeskError e){Assert(e.Code==3,"중지 종료 코드 불일치");}
                    Console.WriteLine("PASS: centered red widget, 중지(ESC), A/O/D scan, own ESC ignored, "+(args.Length>1?args[1]:"ESC")+" stop "+elapsed.ElapsedMilliseconds+" ms");
                }catch(Exception e){Console.Error.WriteLine(e.Message);result=e.Message=="사용자가 중지함"?3:1;}
                finally{if(scan!=null)scan.Close();panel.Close();}
            };timer.Start();
        };
        Application.Run(panel);return result;
    }
}
