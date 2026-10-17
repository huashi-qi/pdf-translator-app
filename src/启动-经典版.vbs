' PDF Translator - CLASSIC UI launcher (backup)
' The sidebar edition is the main UI now; this one is kept as a fallback.
' NOTE: keep this file ASCII-only.
Option Explicit

Dim fso, shell, here, pyw, app
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

here = fso.GetParentFolderName(WScript.ScriptFullName)
pyw = here & "\runtime\pythonw.exe"
app = here & "\app.py"

If Not fso.FileExists(pyw) Then
    MsgBox "Bundled runtime not found:" & vbCrLf & pyw, 16, "PDF Translator"
    WScript.Quit 1
End If
If Not fso.FileExists(app) Then
    MsgBox "app.py not found:" & vbCrLf & app, 16, "PDF Translator"
    WScript.Quit 1
End If

shell.CurrentDirectory = here
shell.Run """" & pyw & """ """ & app & """", 0, False
