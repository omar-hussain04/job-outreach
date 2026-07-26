' Launches the desktop app with no console window.
' Double-click this file to open the application.
Option Explicit

Dim fso, shell, here, pyw, app
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

here = fso.GetParentFolderName(WScript.ScriptFullName)
pyw = here & "\.venv\Scripts\pythonw.exe"
app = here & "\app.py"

If Not fso.FileExists(pyw) Then
    MsgBox "Python environment not found:" & vbCrLf & pyw & vbCrLf & vbCrLf & _
           "Run this once in PowerShell:" & vbCrLf & _
           "python -m venv .venv" & vbCrLf & _
           ".\.venv\Scripts\python.exe -m pip install -r requirements.txt", _
           16, "Job Outreach System"
    WScript.Quit 1
End If

If Not fso.FileExists(app) Then
    MsgBox "app.py not found in:" & vbCrLf & here, 16, "Job Outreach System"
    WScript.Quit 1
End If

shell.CurrentDirectory = here
' 0 = hidden window, False = do not wait
shell.Run """" & pyw & """ """ & app & """", 0, False
