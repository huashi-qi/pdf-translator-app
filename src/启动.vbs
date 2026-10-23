' PDF Translator - launcher (no console window)
' Default entry: the SIDEBAR edition (the main UI now).
' Uses WScript.Shell.Run with window mode 0 so no black console flashes.
' NOTE: keep this file ASCII-only. VBScript (cscript/wscript) reads .vbs as
'       ANSI unless it has a UTF-16 BOM, so non-ASCII text here would break.
Option Explicit

Dim fso, shell, here, pyw, py, app
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

here = fso.GetParentFolderName(WScript.ScriptFullName)
pyw = here & "\runtime\pythonw.exe"
py = here & "\runtime\python.exe"
app = here & "\app_sidebar.py"

If Not fso.FileExists(pyw) Then
    MsgBox "Bundled runtime not found:" & vbCrLf & pyw & vbCrLf & vbCrLf & _
           "Please make sure the whole folder was extracted.", 16, "PDF Translator"
    WScript.Quit 1
End If

' Double-check the entry file
If Not fso.FileExists(app) Then
    app = here & "\app_sidebar.py"
End If
If Not fso.FileExists(app) Then
    MsgBox "Application entry not found in:" & vbCrLf & here, 16, "PDF Translator"
    WScript.Quit 1
End If

shell.CurrentDirectory = here
' window style 0 = hidden, bWaitOnReturn False = do not wait
shell.Run """" & pyw & """ """ & app & """", 0, False
