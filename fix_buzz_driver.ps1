# Windows' usb.inf wrongly lists the Buzz receiver's ID (054C:0002) as a Sony USB hub,
# so every new USB port gets the hub driver (error 31). This forces input.inf (generic HID) instead.
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) {
    Start-Process powershell -Verb RunAs -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    exit
}

Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class NewDev {
    [DllImport("newdev.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    public static extern bool UpdateDriverForPlugAndPlayDevices(IntPtr hwnd, string hwid, string inf, uint flags, out bool reboot);
}
"@

if (-not (Get-PnpDevice -PresentOnly | Where-Object InstanceId -match '^USB\\VID_054C&PID_0002')) {
    Write-Host "Buzz receiver not found. Plug it in and run this again." -ForegroundColor Yellow
} else {
    $reboot = $false
    # INSTALLFLAG_FORCE = 1
    $ok = [NewDev]::UpdateDriverForPlugAndPlayDevices([IntPtr]::Zero, 'USB\VID_054C&PID_0002', "$env:windir\INF\input.inf", 1, [ref]$reboot)
    $err = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
    Start-Sleep 2
    if ($ok -and (Get-PnpDevice -PresentOnly -Class HIDClass | Where-Object InstanceId -match '^HID\\VID_054C&PID_0002')) {
        Write-Host "Buzz controllers fixed: they show up as a game controller now." -ForegroundColor Green
    } else {
        Write-Host "Fix did not apply (ok=$ok, error=$err)." -ForegroundColor Red
    }
}
Read-Host "Press Enter to close"
