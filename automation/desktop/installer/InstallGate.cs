using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Web.Script.Serialization;
using System.Collections.Generic;
using System.Windows.Forms;

class InstallGate {
  static bool Live(int pid, string token) {
    try { var process=Process.GetProcessById(pid); return !process.HasExited && (String.IsNullOrEmpty(token) || process.StartTime.ToUniversalTime().ToFileTimeUtc().ToString()==token); }
    catch { return false; }
  }
  static bool Busy(string state) {
    if(Process.GetProcessesByName("IoTExperimentWorkbench").Length>0) return true;
    string marker=Path.Combine(state,"desktop-process.json");
    try {
      if(File.Exists(marker)) {
        var data=new JavaScriptSerializer().Deserialize<Dictionary<string,object>>(File.ReadAllText(marker));
        if(Live(Convert.ToInt32(data["pid"]),Convert.ToString(data["process_start_token"])) || Live(Convert.ToInt32(data["sidecar_pid"]),Convert.ToString(data["sidecar_start_token"]))) return true;
      }
      string locks=Environment.GetEnvironmentVariable("IOT_EXP_LOCK_ROOT") ?? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),"IoTExperimentWorkbench","locks");
      if(Directory.Exists(locks)) foreach(string lease in Directory.GetFiles(locks,"*.lock")) {
        var owner=new JavaScriptSerializer().Deserialize<Dictionary<string,object>>(File.ReadAllText(lease));
        if(Live(Convert.ToInt32(owner["pid"]),owner.ContainsKey("process_start_token")?Convert.ToString(owner["process_start_token"]):null)) return true;
        if(owner.ContainsKey("owned_processes")) foreach(object entry in (System.Collections.IEnumerable)owner["owned_processes"]) {
          var child=(Dictionary<string,object>)entry;
          if(Live(Convert.ToInt32(child["pid"]),Convert.ToString(child["token"]))) return true;
        }
      }
      return false;
    } catch { return true; }
  }
  [STAThread] static int Main(string[] args) {
    string state=Environment.GetEnvironmentVariable("IOT_EXP_DESKTOP_STATE") ?? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),"org.iotexp.workbench");
    bool check=Array.IndexOf(args,"--check-only")>=0;
    if(Busy(state)) {
      if(!check)MessageBox.Show("请先在工作台完成安全停止并退出，再安装或升级。","实验工作台仍在运行",MessageBoxButtons.OK,MessageBoxIcon.Warning);
      return 2;
    }
    if(check) return 0;
    using(Stream payload=Assembly.GetExecutingAssembly().GetManifestResourceStream("SetupPayload")) {
      if(payload==null){MessageBox.Show("安装载荷缺失，请使用完整发行包。");return 3;}
      string temp=Path.Combine(Path.GetTempPath(),"IoTExperimentInstaller",Guid.NewGuid().ToString("N"));Directory.CreateDirectory(temp);
      string setup=Path.Combine(temp,"Setup.exe");
      using(FileStream target=File.Create(setup)){payload.CopyTo(target);}
      var process=Process.Start(new ProcessStartInfo(setup){UseShellExecute=true});process.WaitForExit();return process.ExitCode;
    }
  }
}
