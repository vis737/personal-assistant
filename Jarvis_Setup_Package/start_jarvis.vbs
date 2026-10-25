' ============================================================================
'  JARVIS - logon launcher (silent, no console windows)
'
'  1. starts jarvis_server.py (voice engine + local API on 127.0.0.1:8787)
'  2. starts the HUD dev server (127.0.0.1:8080) if it is not already up
'  3. opens the HUD in an Edge app window (no tabs, app-like chrome)
'
'  Every step is guarded, so running this twice never starts duplicates.
'  Logs land in <package>\logs\engine.log and logs\ui.log
' ============================================================================
Option Explicit

Dim sh, fso, base, logDir, uiDir, py, pyw, bun, engineLog, uiLog, i
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

base = fso.GetParentFolderName(WScript.ScriptFullName)
logDir = base & "\logs"
uiDir = fso.GetParentFolderName(base) & "\frontend"
py = base & "\.venv\Scripts\python.exe"
pyw = base & "\.venv\Scripts\pythonw.exe"
bun = sh.ExpandEnvironmentStrings("%USERPROFILE%") & "\.bun\bin\bun.exe"
engineLog = logDir & "\engine.log"
uiLog = logDir & "\ui.log"

If Not fso.FolderExists(logDir) Then fso.CreateFolder(logDir)
sh.CurrentDirectory = base

Function Q(s)
  Q = Chr(34) & s & Chr(34)
End Function

' cmd /c with a command line containing quotes: cmd keeps them only under
' narrow conditions, otherwise it strips the FIRST and LAST quote of the
' remainder - which silently destroys an unquoted-around-the-edge command.
' We therefore add our own sacrificial pair of quotes around `inner`.
Function CmdRun(inner)
  CmdRun = "cmd /c " & Chr(34) & inner & Chr(34)
End Function

' Append one line to logs\launcher.log so "why didn't it start?" is answerable.
Sub Log(msg)
  Dim f
  On Error Resume Next
  Set f = fso.OpenTextFile(logDir & "\launcher.log", 8, True)
  f.WriteLine "[" & Now & "] " & msg
  f.Close
End Sub

' Best-effort HTTP liveness probe (200 == up).
Function HttpOk(url)
  On Error Resume Next
  Dim h
  Set h = CreateObject("WinHttp.WinHttpRequest.5.1")
  h.SetTimeouts 1000, 1500, 1500, 1500
  h.Open "GET", url, False
  h.Send
  If Err.Number <> 0 Then
    Err.Clear
    HttpOk = False
    Exit Function
  End If
  HttpOk = (h.Status = 200)
  Set h = Nothing
End Function

' ---- 1. voice engine -------------------------------------------------------
Log "launch: base=" & base & " py=" & py
If HttpOk("http://127.0.0.1:8787/api/healthz") Then
  ' already running - do not start a second copy
  Log "engine: already healthy, skipped"
ElseIf fso.FileExists(py) Then
  Log "engine: starting"
  sh.Run CmdRun(Q(py) & " " & Q(base & "\jarvis_server.py") & " >> " & Q(engineLog) & " 2>&1"), 0, False
Else
  Log "engine: SKIPPED - missing " & py
  fso.OpenTextFile(engineLog, 8, True).WriteLine "[" & Now & "] SKIP engine - missing " & py
End If

' ---- 2. dynamic island overlay (top-center, always-on-top) -----------------
' pythonw: no console window. The script locks itself to logs\island.pid, so
' running this twice never leaves two pills on screen.
If fso.FileExists(base & "\jarvis_island.py") And fso.FileExists(pyw) Then
  Log "island: starting (self-guards against duplicates)"
  sh.Run Q(pyw) & " " & Q(base & "\jarvis_island.py"), 0, False
Else
  Log "island: SKIPPED - missing jarvis_island.py or pythonw.exe"
End If

' ---- 3. HUD web UI ---------------------------------------------------------
If Not HttpOk("http://127.0.0.1:8080/") Then
  If fso.FileExists(bun) And fso.FolderExists(uiDir) Then
    Log "ui: starting"
    sh.Run CmdRun("cd /d " & Q(uiDir) & " && " & Q(bun) & " run dev >> " & Q(uiLog) & " 2>&1"), 0, False
  Else
    Log "ui: SKIPPED - bun=" & bun & " ui=" & uiDir
    fso.OpenTextFile(uiLog, 8, True).WriteLine "[" & Now & "] SKIP ui - bun=" & bun & " ui=" & uiDir
  End If
Else
  Log "ui: already healthy, skipped"
End If

' ---- 4. wait for the UI, then open the HUD app window ----------------------
For i = 1 To 60
  If HttpOk("http://127.0.0.1:8080/") Then Exit For
  WScript.Sleep 1000
Next

If HttpOk("http://127.0.0.1:8080/") Then
  Log "hud: opening Edge app window"
  sh.Run "msedge --app=http://127.0.0.1:8080/", 1, False
Else
  Log "hud: UI never became ready - not opening the window"
  fso.OpenTextFile(uiLog, 8, True).WriteLine "[" & Now & "] UI never became ready - not opening the window"
End If
