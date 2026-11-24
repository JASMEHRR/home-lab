<#
  Creates the EyeToy Camera and Buzz Lights shortcuts with their Windows app identity
  (System.AppUserModel.ID), in this folder, on the Desktop (Buzz Lights) and in the Start menu.

  Why: both apps run on Python. Without a matching identity on a shortcut, Windows treats their
  windows as "Python", and pinning a running window pins bare pythonw.exe, which opens nothing.
  With it, the window groups under its own icon and any pin really starts the app.

  Usage: powershell -ExecutionPolicy Bypass -File make_shortcuts.ps1
#>
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$Dir = $PSScriptRoot
$PythonW = 'C:\Users\jasme\AppData\Local\Programs\Python\Python312\pythonw.exe'
$Pyw = 'C:\Windows\pyw.exe'
$StartMenu = [Environment]::GetFolderPath('Programs')
$Desktop = [Environment]::GetFolderPath('Desktop')

foreach ($p in @($PythonW, $Pyw, (Join-Path $Dir 'eyetoy.py'), (Join-Path $Dir 'buzz.py'))) {
    if (-not (Test-Path -LiteralPath $p)) { throw "Missing: $p" }
}

Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;
using System.Text;

public static class AppShortcut {
    [ComImport, Guid("000214F9-0000-0000-C000-000000000046"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IShellLinkW {
        void GetPath([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder file, int cch, IntPtr fd, uint flags);
        void GetIDList(out IntPtr pidl);
        void SetIDList(IntPtr pidl);
        void GetDescription([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder name, int cch);
        void SetDescription([MarshalAs(UnmanagedType.LPWStr)] string name);
        void GetWorkingDirectory([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder dir, int cch);
        void SetWorkingDirectory([MarshalAs(UnmanagedType.LPWStr)] string dir);
        void GetArguments([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder args, int cch);
        void SetArguments([MarshalAs(UnmanagedType.LPWStr)] string args);
        void GetHotkey(out short hotkey);
        void SetHotkey(short hotkey);
        void GetShowCmd(out int cmd);
        void SetShowCmd(int cmd);
        void GetIconLocation([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder path, int cch, out int index);
        void SetIconLocation([MarshalAs(UnmanagedType.LPWStr)] string path, int index);
        void SetRelativePath([MarshalAs(UnmanagedType.LPWStr)] string rel, uint reserved);
        void Resolve(IntPtr hwnd, uint flags);
        void SetPath([MarshalAs(UnmanagedType.LPWStr)] string file);
    }

    [ComImport, Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IPropertyStore {
        void GetCount(out uint count);
        void GetAt(uint index, out PropertyKey key);
        void GetValue(ref PropertyKey key, out PropVariant value);
        void SetValue(ref PropertyKey key, ref PropVariant value);
        void Commit();
    }

    [StructLayout(LayoutKind.Sequential, Pack = 4)]
    struct PropertyKey { public Guid fmtid; public uint pid; }

    // PROPVARIANT: 8-byte header, then a 16-byte union (24 bytes on 64-bit).
    [StructLayout(LayoutKind.Explicit, Size = 24)]
    struct PropVariant { [FieldOffset(0)] public ushort vt; [FieldOffset(8)] public IntPtr pointer; }

    [ComImport, Guid("00021401-0000-0000-C000-000000000046")]
    class ShellLink { }

    const ushort VT_LPWSTR = 31;
    static readonly Guid AppUserModelFmtid = new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3");

    public static void Save(string lnk, string target, string args, string workDir,
                            string icon, int iconIndex, string description, string appId) {
        var link = (IShellLinkW)new ShellLink();
        try {
            link.SetPath(target);
            link.SetArguments(args);
            link.SetWorkingDirectory(workDir);
            link.SetIconLocation(icon, iconIndex);
            link.SetDescription(description);

            var key = new PropertyKey { fmtid = AppUserModelFmtid, pid = 5 }; // System.AppUserModel.ID
            var value = new PropVariant { vt = VT_LPWSTR, pointer = Marshal.StringToCoTaskMemUni(appId) };
            try {
                var store = (IPropertyStore)link;
                store.SetValue(ref key, ref value);
                store.Commit();
            } finally {
                Marshal.FreeCoTaskMem(value.pointer);
            }
            ((IPersistFile)link).Save(lnk, true);
        } finally {
            Marshal.ReleaseComObject(link);
        }
    }
}
'@

function Make([string]$lnk, [string]$target, [string]$arguments, [string]$icon, [int]$iconIndex, [string]$description, [string]$appId) {
    [AppShortcut]::Save($lnk, $target, $arguments, $Dir, $icon, $iconIndex, $description, $appId)
    Write-Host "  $lnk"
}

# pythonw.exe directly: launched by Explorer (taskbar pin), the pyw.exe launcher started nothing.
$eyetoy = @{ target = $PythonW; arguments = "`"$Dir\eyetoy.py`""; icon = "$env:windir\System32\DDORes.dll"; iconIndex = 88
             description = 'Open the EyeToy camera window'; appId = 'EyeToyCamera.Viewer' }
$buzz = @{ target = $PythonW; arguments = "`"$Dir\buzz.py`" --lights"; icon = "$env:windir\System32\imageres.dll"; iconIndex = 333
           description = "Buzz Lights: a press lights that player's buzzer. Close its window to turn off."; appId = 'BuzzLights.Helper' }

Write-Host 'EyeToy Camera:'
$Pinned = Join-Path $env:APPDATA 'Microsoft\Internet Explorer\Quick Launch\User Pinned\TaskBar'
$eyetoyLinks = @((Join-Path $Dir 'EyeToy Camera.lnk'), (Join-Path $StartMenu 'EyeToy Camera.lnk'))
if (Test-Path -LiteralPath (Join-Path $Pinned 'EyeToy Camera.lnk')) { $eyetoyLinks += (Join-Path $Pinned 'EyeToy Camera.lnk') }
foreach ($lnk in $eyetoyLinks) {
    Make $lnk $eyetoy.target $eyetoy.arguments $eyetoy.icon $eyetoy.iconIndex $eyetoy.description $eyetoy.appId
}
Write-Host 'Buzz Lights:'
foreach ($lnk in @((Join-Path $Dir 'Buzz Lights.lnk'), (Join-Path $Desktop 'Buzz Lights.lnk'), (Join-Path $StartMenu 'Buzz Lights.lnk'))) {
    Make $lnk $buzz.target $buzz.arguments $buzz.icon $buzz.iconIndex $buzz.description $buzz.appId
}

$tracker = @{ target = $PythonW; arguments = "`"$Dir\posture\app.py`""; icon = "$env:windir\System32\DDORes.dll"; iconIndex = 85
              description = 'Posture Coach: posture, focus and time at the desk (laptop webcam + EyeToy). Numbers only, no video.'; appId = 'PostureTracker.App' }
$report = @{ target = $PythonW; arguments = "`"$Dir\posture\posture_report.py`""; icon = "$env:windir\System32\imageres.dll"; iconIndex = 22
             description = 'Open the posture report: hours at the desk and posture by day.'; appId = 'PostureReport.App' }
Write-Host 'Posture Tracker and Posture Report:'
# The app used to be called Posture Tracker. A copy you pinned to the taskbar keeps that name, so refresh it too.
$trackerLinks = @((Join-Path $Dir 'Posture Coach.lnk'), (Join-Path $StartMenu 'Posture Coach.lnk'))
$pinnedTracker = Join-Path $Pinned 'Posture Tracker.lnk'
if (Test-Path -LiteralPath $pinnedTracker) { $trackerLinks += $pinnedTracker }
foreach ($old in @((Join-Path $Dir 'Posture Tracker.lnk'), (Join-Path $StartMenu 'Posture Tracker.lnk'))) {
    if (Test-Path -LiteralPath $old) { Remove-Item -LiteralPath $old -ErrorAction Stop }   # replaced by Posture Coach.lnk
}
foreach ($lnk in $trackerLinks) {
    Make $lnk $tracker.target $tracker.arguments $tracker.icon $tracker.iconIndex $tracker.description $tracker.appId
}
foreach ($lnk in @((Join-Path $Dir 'Posture Report.lnk'), (Join-Path $StartMenu 'Posture Report.lnk'))) {
    Make $lnk $report.target $report.arguments $report.icon $report.iconIndex $report.description $report.appId
}
