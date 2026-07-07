# Ghost right-click logger — WH_MOUSE_LL low-level hook
# Logs every RIGHT button down: time, screen X/Y, injected flag, screen size
# Stop with Ctrl+C (or kill the background job). Log: ghostclick-log.csv

$ErrorActionPreference = 'Stop'
$logPath = Join-Path $PSScriptRoot 'ghostclick-log.csv'
if (-not (Test-Path $logPath)) {
    'Timestamp,Event,X,Y,Injected,LowLevelInjected,ScreenW,ScreenH,Corner' | Out-File -FilePath $logPath -Encoding utf8
}

Add-Type -TypeDefinition @"
using System;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Windows.Forms;

public class GhostHook {
    public static string LogPath;

    private const int WH_MOUSE_LL = 14;
    private const int WM_RBUTTONDOWN = 0x0204;
    private const int WM_RBUTTONUP   = 0x0205;
    private const int LLMHF_INJECTED = 0x00000001;
    private const int LLMHF_LOWER_IL_INJECTED = 0x00000002;

    [StructLayout(LayoutKind.Sequential)]
    private struct POINT { public int x; public int y; }

    [StructLayout(LayoutKind.Sequential)]
    private struct MSLLHOOKSTRUCT {
        public POINT pt;
        public uint mouseData;
        public uint flags;
        public uint time;
        public IntPtr dwExtraInfo;
    }

    private delegate IntPtr HookProc(int nCode, IntPtr wParam, IntPtr lParam);
    private static HookProc _proc = HookCallback;
    private static IntPtr _hook = IntPtr.Zero;

    [DllImport("user32.dll", SetLastError = true)]
    private static extern IntPtr SetWindowsHookEx(int idHook, HookProc lpfn, IntPtr hMod, uint dwThreadId);
    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool UnhookWindowsHookEx(IntPtr hhk);
    [DllImport("user32.dll")]
    private static extern IntPtr CallNextHookEx(IntPtr hhk, int nCode, IntPtr wParam, IntPtr lParam);
    [DllImport("kernel32.dll")]
    private static extern IntPtr GetModuleHandle(string lpModuleName);
    [DllImport("user32.dll")]
    private static extern int GetSystemMetrics(int nIndex);
    private const int SM_CXVIRTUALSCREEN = 78;
    private const int SM_CYVIRTUALSCREEN = 79;

    private static IntPtr HookCallback(int nCode, IntPtr wParam, IntPtr lParam) {
        if (nCode >= 0) {
            int msg = wParam.ToInt32();
            if (msg == WM_RBUTTONDOWN || msg == WM_RBUTTONUP) {
                MSLLHOOKSTRUCT m = (MSLLHOOKSTRUCT)Marshal.PtrToStructure(lParam, typeof(MSLLHOOKSTRUCT));
                bool injected = (m.flags & LLMHF_INJECTED) != 0;
                bool lowerInjected = (m.flags & LLMHF_LOWER_IL_INJECTED) != 0;
                int sw = GetSystemMetrics(SM_CXVIRTUALSCREEN);
                int sh = GetSystemMetrics(SM_CYVIRTUALSCREEN);
                // is the click in the bottom-right ~15% corner (touchpad right-click zone)?
                string corner = (m.pt.x > sw * 0.80 && m.pt.y > sh * 0.80) ? "BOTTOM_RIGHT" : "";
                string evt = (msg == WM_RBUTTONDOWN) ? "RDOWN" : "RUP";
                string line = string.Format("{0},{1},{2},{3},{4},{5},{6},{7},{8}",
                    DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss.fff"),
                    evt, m.pt.x, m.pt.y, injected, lowerInjected, sw, sh, corner);
                try { File.AppendAllText(LogPath, line + Environment.NewLine); } catch {}
            }
        }
        return CallNextHookEx(_hook, nCode, wParam, lParam);
    }

    public static void Start() {
        using (Process curProcess = Process.GetCurrentProcess())
        using (ProcessModule curModule = curProcess.MainModule) {
            _hook = SetWindowsHookEx(WH_MOUSE_LL, _proc, GetModuleHandle(curModule.ModuleName), 0);
        }
        Application.Run();
    }
}
"@ -ReferencedAssemblies System.Windows.Forms

[GhostHook]::LogPath = $logPath
Write-Host "Ghost-click logger running. Logging RIGHT-clicks to: $logPath"
Write-Host "Use laptop normally. When a ghost right-click happens, it gets logged."
Write-Host "Press Ctrl+C to stop."
[GhostHook]::Start()
