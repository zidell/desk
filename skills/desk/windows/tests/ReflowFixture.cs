// Visible disposable fixture: changes mimic delayed page layout and replacement, never user apps.
using System;
using System.Drawing;
using System.Windows.Forms;
class ReflowFixture:Form {
    string mode; Control target; Label result; Timer timer; int clicks=0, traps=0;bool armed=false;
    [STAThread] static void Main(string[] args){Application.EnableVisualStyles();Application.Run(new ReflowFixture(args.Length>0?args[0]:Environment.GetEnvironmentVariable("DESK_REFLOW_MODE")));}
    ReflowFixture(string m){mode=m;Text="desk reflow "+m+" "+Environment.GetEnvironmentVariable("DESK_REFLOW_SESSION");StartPosition=FormStartPosition.Manual;Bounds=new Rectangle(220,160,720,480);
        result=new Label(){Text="CLICKS 0 TRAP 0",Left=30,Top=320,Width=500,Height=40};Controls.Add(result);
        target=NewTarget();Controls.Add(target);
        timer=new Timer(){Interval=mode=="before"?4000:250};timer.Tick+=delegate{timer.Stop();Change();};
        MouseMove+=delegate{armed=true;};
        // Change on hover itself: a delayed 250ms timer incorrectly assumes
        // that every click must pause after cursor arrival.
        if(mode=="before")timer.Start();else target.MouseEnter+=delegate{if(armed&&mode!="stable"&&mode!="double"){armed=false;Change();}};
    }
    Control NewTarget(){Control c=mode=="paint"?(Control)new Panel(){BackColor=Color.RoyalBlue}:new Button(){Text="TARGET"};
        c.AccessibleName="TARGET";c.Bounds=new Rectangle(40,100,220,60);
        c.MouseDown+=delegate{clicks++;result.Text="CLICKS "+clicks+" TRAP "+traps;};return c;}
    void Change(){
        if(mode=="replace"){Controls.Remove(target);target.Dispose();target=NewTarget();Controls.Add(target);}
        else if(mode=="paint"){target.BackColor=Color.Orange;}
        else if(mode=="unrelated"){var label=new Label(){Text="UNRELATED LOADED",Left=400,Top=210,Width=250};Controls.Add(label);}
        else{target.Top+=80;var trap=new Button(){Text="TRAP",Bounds=new Rectangle(40,100,220,60)};trap.MouseDown+=delegate{traps++;result.Text="CLICKS "+clicks+" TRAP "+traps;};Controls.Add(trap);}
    }
}
