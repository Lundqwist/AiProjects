# Creates (or with -Remove deletes) the "AI Workbench" shortcut in the Start menu.
# Run from anywhere: powershell -ExecutionPolicy Bypass -File app\install-shortcut.ps1 [-Desktop] [-Remove]
param([switch]$Desktop, [switch]$Remove)

$root = Split-Path -Parent $PSScriptRoot
$AppId = 'Lundqwist.AIWorkbench'  # must match SetCurrentProcessExplicitAppUserModelID in main.py
$targets = @([Environment]::GetFolderPath('Programs'))
if ($Desktop) { $targets += [Environment]::GetFolderPath('Desktop') }

# The window tells Windows it is $AppId (main.py). Windows finds the taskbar icon through a shortcut
# carrying that same ID; without one the taskbar shows a generic icon.
Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class ShortcutAppId {
    [StructLayout(LayoutKind.Sequential, Pack = 4)]
    public struct PROPERTYKEY { public Guid fmtid; public uint pid; }
    [StructLayout(LayoutKind.Sequential)]
    public struct PROPVARIANT { public ushort vt; public ushort r1, r2, r3; public IntPtr p; public IntPtr p2; }
    [ComImport, Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    public interface IPropertyStore {
        void GetCount(out uint c); void GetAt(uint i, out PROPERTYKEY k);
        void GetValue(ref PROPERTYKEY k, out PROPVARIANT v); void SetValue(ref PROPERTYKEY k, ref PROPVARIANT v);
        void Commit();
    }
    [DllImport("shell32.dll", CharSet = CharSet.Unicode, PreserveSig = false)]
    static extern void SHGetPropertyStoreFromParsingName(string path, IntPtr bc, int flags, ref Guid iid,
        [MarshalAs(UnmanagedType.Interface)] out IPropertyStore store);
    public static void Set(string lnk, string id) {
        Guid iid = new Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99");
        IPropertyStore store;
        SHGetPropertyStoreFromParsingName(lnk, IntPtr.Zero, 2 /* GPS_READWRITE */, ref iid, out store);
        PROPERTYKEY key = new PROPERTYKEY { fmtid = new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"), pid = 5 };
        PROPVARIANT v = new PROPVARIANT { vt = 31 /* VT_LPWSTR */, p = Marshal.StringToCoTaskMemUni(id) };
        try { store.SetValue(ref key, ref v); store.Commit(); }
        finally { Marshal.FreeCoTaskMem(v.p); Marshal.ReleaseComObject(store); }
    }
}
"@

foreach ($dir in $targets) {
    $lnk = Join-Path $dir 'AI Workbench.lnk'
    if ($Remove) {
        if (Test-Path $lnk) { Remove-Item $lnk; "Removed $lnk" }
        continue
    }
    $pythonw = Join-Path $root '.venv\Scripts\pythonw.exe'
    if (-not (Test-Path $pythonw)) { throw "No virtual environment at $root\.venv - see the README's Setup section." }
    $s = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk)
    $s.TargetPath = $pythonw
    $s.Arguments = '"' + (Join-Path $PSScriptRoot 'main.py') + '"'
    $s.WorkingDirectory = $root
    $s.IconLocation = (Join-Path $PSScriptRoot 'icon.ico')
    $s.Description = 'Local AI: documents, experts, pictures, music, code, voice, invoices, research'
    $s.Save()
    [ShortcutAppId]::Set($lnk, $AppId)
    "Created $lnk (taskbar ID $AppId)"
}
