' Creates a Desktop shortcut for the app. Run once (double-click).
'
' Note: WshShell.CreateShortcut cannot write to a path containing non-Latin
' characters -- and this user's Desktop is a OneDrive folder named in Arabic.
' So we build the shortcut at a plain ASCII path first, then copy it across
' with FileSystemObject, which handles Unicode paths correctly.
Option Explicit

Dim fso, shell, here, pyw, app, icon, deskDir, stage, dest, lnk, ok
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

here = fso.GetParentFolderName(WScript.ScriptFullName)
pyw  = here & "\.venv\Scripts\pythonw.exe"
app  = here & "\app.py"
icon = here & "\ui\app.ico"

If Not fso.FileExists(pyw) Then
    MsgBox "Python environment not found:" & vbCrLf & pyw, 16, "Job Outreach System"
    WScript.Quit 1
End If
If Not fso.FileExists(app) Then
    MsgBox "app.py not found in:" & vbCrLf & here, 16, "Job Outreach System"
    WScript.Quit 1
End If

' 1) build the shortcut somewhere with an ASCII-only path
stage = fso.GetSpecialFolder(2) & "\job_outreach_stage.lnk"   ' 2 = TEMP
Set lnk = shell.CreateShortcut(stage)
lnk.TargetPath       = pyw
lnk.Arguments        = """" & app & """"
lnk.WorkingDirectory = here
lnk.WindowStyle      = 7                  ' minimized: no console flash
lnk.Description      = "Job outreach email system"
If fso.FileExists(icon) Then lnk.IconLocation = icon & ",0"
lnk.Save

If Not fso.FileExists(stage) Then
    MsgBox "Could not build the shortcut.", 16, "Job Outreach System"
    WScript.Quit 1
End If

' 2) copy it to the Desktop under its Arabic name
deskDir = shell.SpecialFolders("Desktop")
dest = deskDir & "\" & ChrW(1573) & ChrW(1610) & ChrW(1605) & ChrW(1610) & _
       ChrW(1604) & ChrW(1575) & ChrW(1578) & " " & ChrW(1575) & ChrW(1604) & _
       ChrW(1578) & ChrW(1602) & ChrW(1583) & ChrW(1610) & ChrW(1605) & ".lnk"

On Error Resume Next
fso.CopyFile stage, dest, True
ok = (Err.Number = 0)
On Error GoTo 0
fso.DeleteFile stage, True

If ok And fso.FileExists(dest) Then
    MsgBox "Shortcut created on your Desktop.", 64, "Job Outreach System"
Else
    MsgBox "Could not copy the shortcut to:" & vbCrLf & deskDir, _
           16, "Job Outreach System"
End If
