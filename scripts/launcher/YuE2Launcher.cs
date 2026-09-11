using System;
using System.Diagnostics;
using System.IO;
using System.Text;

internal static class YuE2Launcher
{
    [STAThread]
    private static int Main(string[] args)
    {
        Console.OutputEncoding = Encoding.UTF8;
        Console.Title = "T8star-Aix - YuE2 本地整合包";

        bool noPause = HasArgument(args, "--no-pause") || Console.IsInputRedirected;
        bool noBrowser = HasArgument(args, "--no-browser");
        string kitRoot = AppDomain.CurrentDomain.BaseDirectory.TrimEnd(Path.DirectorySeparatorChar);
        string script = Path.Combine(kitRoot, "scripts", "start_local.ps1");
        if (!File.Exists(script))
            script = Path.Combine(kitRoot, "scripts", "start_webui.ps1");
        string runtime = Path.Combine(kitRoot, "runtime", "core", "python.exe");

        Console.WriteLine();
        Console.WriteLine("YuE2-T8 本地整合包启动器");
        Console.WriteLine("制作者 By B站UP主:T8star-Aix");
        Console.WriteLine();

        if (!File.Exists(script))
            return Finish(2, "[启动失败] 找不到启动脚本，请完整解压整合包后再运行。", noPause);
        if (!File.Exists(runtime))
            return Finish(3, "[启动失败] 运行环境不完整，请确认压缩包已完整解压。", noPause);

        Console.WriteLine("[YuE2] 正在启动本地工作室，请稍候...");
        ProcessStartInfo startInfo = new ProcessStartInfo();
        startInfo.FileName = "powershell.exe";
        startInfo.Arguments = "-NoProfile -ExecutionPolicy Bypass -File \"" + script + "\"" + (noBrowser ? " -NoBrowser" : "");
        startInfo.WorkingDirectory = kitRoot;
        startInfo.UseShellExecute = false;
        startInfo.CreateNoWindow = false;

        try
        {
            using (Process process = Process.Start(startInfo))
            {
                process.WaitForExit();
                if (process.ExitCode != 0)
                    return Finish(process.ExitCode, "[启动失败] 请根据上方提示处理，详细日志位于 logs\\server.stderr.log。", noPause);
            }
        }
        catch (Exception exception)
        {
            return Finish(4, "[启动失败] " + exception.Message, noPause);
        }

        Console.WriteLine();
        Console.ForegroundColor = ConsoleColor.Green;
        Console.WriteLine("[启动成功] 本地工作室地址：http://127.0.0.1:8189");
        Console.ResetColor();
        Console.WriteLine("关闭此窗口不会停止后台服务；需要停止时请运行“停止本地服务.ps1”。");
        return Finish(0, null, noPause);
    }

    private static bool HasArgument(string[] args, string expected)
    {
        foreach (string argument in args)
            if (string.Equals(argument, expected, StringComparison.OrdinalIgnoreCase))
                return true;
        return false;
    }

    private static int Finish(int exitCode, string message, bool noPause)
    {
        if (!string.IsNullOrEmpty(message))
        {
            Console.WriteLine();
            Console.ForegroundColor = exitCode == 0 ? ConsoleColor.Green : ConsoleColor.Red;
            Console.WriteLine(message);
            Console.ResetColor();
        }
        if (!noPause)
        {
            Console.WriteLine();
            Console.Write("按任意键关闭此窗口...");
            Console.ReadKey(true);
        }
        return exitCode;
    }
}
