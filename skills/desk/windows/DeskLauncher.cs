// Native Unicode launcher: does not depend on PowerShell script execution policy.
using System;
using System.IO;
using System.Text;
using System.Diagnostics;
using System.Collections.Generic;
using System.Web.Script.Serialization;
class DeskLauncher {
    static string Quote(string value){var s=new StringBuilder("\"");int slash=0;foreach(char c in value){if(c=='\\'){slash++;continue;}if(c=='"'){s.Append('\\',slash*2+1);s.Append(c);}else{s.Append('\\',slash);s.Append(c);}slash=0;}s.Append('\\',slash*2);s.Append('"');return s.ToString();}
    static int Main(string[] args){Console.OutputEncoding=new UTF8Encoding(false);try{var cfg=new JavaScriptSerializer().Deserialize<Dictionary<string,string>>(File.ReadAllText(Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"desk-launch.json"),Encoding.UTF8));var argv=new List<string>{"-X","utf8",cfg["entry"]};argv.AddRange(args);var quoted=new List<string>();foreach(var a in argv)quoted.Add(Quote(a));var start=new ProcessStartInfo(cfg["python"],string.Join(" ",quoted)){UseShellExecute=false,CreateNoWindow=true,RedirectStandardOutput=true,RedirectStandardError=true,StandardOutputEncoding=Encoding.UTF8,StandardErrorEncoding=Encoding.UTF8};using(var p=new Process()){p.StartInfo=start;p.OutputDataReceived+=delegate(object s,DataReceivedEventArgs e){if(e.Data!=null)Console.Out.WriteLine(e.Data);};p.ErrorDataReceived+=delegate(object s,DataReceivedEventArgs e){if(e.Data!=null)Console.Error.WriteLine(e.Data);};p.Start();p.BeginOutputReadLine();p.BeginErrorReadLine();p.WaitForExit();return p.ExitCode;}}catch(Exception e){Console.Error.WriteLine("desk launcher: "+e.Message);return 2;}}
}
