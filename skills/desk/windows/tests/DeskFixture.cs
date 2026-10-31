// Disposable visible UI used only by smoke.py; no user files or app data.
using System;
using System.Drawing;
using System.Windows.Forms;
class DeskFixture : Form {
    [STAThread] static void Main(){Application.EnableVisualStyles();Application.Run(new DeskFixture());}
    DeskFixture(){Text="desk Windows 검증";StartPosition=FormStartPosition.Manual;Bounds=new Rectangle(200,150,660,430);
        var input=new TextBox(){Name="testInput",AccessibleName="검증 입력",Left=30,Top=45,Width=360};
        var label=new Label(){Text="상태: 준비",Left=30,Top=155,Width=560,Height=45};
        var button=new Button(){Text="확인",Left=420,Top=40,Width=120,Height=40};button.Click+=delegate{label.Text="RESULT: "+input.Text;};
        var a=new Button(){Text="동일",Left=30,Top=230,Width=100};var b=new Button(){Text="동일",Left=160,Top=230,Width=100};
        var check=new CheckBox(){Text="검증 체크",Left=30,Top=105,Width=180};
        var dialog=new Button(){Text="대화상자",Left=300,Top=230,Width=120};dialog.Click+=delegate{MessageBox.Show(this,"모달 검증 창입니다","desk 모달",MessageBoxButtons.OK);};
        var noop=new Button(){Text="변화 없음",Left=450,Top=230,Width=120};
        Controls.AddRange(new Control[]{input,label,button,a,b,check,dialog,noop});}
}
