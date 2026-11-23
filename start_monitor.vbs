Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
folder = fso.GetParentFolderName(WScript.ScriptFullName)
python = folder & "\.venv\Scripts\pythonw.exe"
If Not fso.FileExists(python) Then
    MsgBox "Create .venv and install requirements first. See README.md.", 48, "MiniToo PC Monitor"
    WScript.Quit 1
End If
shell.CurrentDirectory = folder
shell.Run """" & python & """ """ & folder & "\monitor_supervisor.py""", 0, False
