// Isolated DelphiScript sandbox used by the run_altium_script MCP tool.
//
// This file is a TEMPLATE: run_altium_script never edits it. For every run the
// tool writes a copy with a unique file name under C:\Users\Public\altium_mcp\runs,
// fills in the markers below and starts it with ScriptingSystem:RunScriptFile.
// A unique file per run means a broken script left open in Altium's script
// editor or paused in its debugger can never be re-run by accident.
//
// SandboxLog() flushes to disk on every call, so when a script dies the log
// still shows the last step that completed - the statement after it is the
// culprit.
//
// The tool also makes every while and repeat loop of the script call
// SandboxTick(). A script runs on Altium's UI thread and nothing can stop it
// from outside, so a loop that never ends would freeze Altium until X2.EXE is
// killed. Past the deadline (%GUARD_SECONDS% s) every guarded loop exits.

const
    REPLACEALL = 1;

var
    LogLines : TStringList;
    LogPath  : String;
    OutPath  : String;
    // Scratch variables: DelphiScript has no inline declarations, so scripts
    // either reuse these or start with their own "var" block (the tool moves
    // that block into Run's declarations).
    S1, S2, S3, S4, S5, S6, S7, S8, S9 : String;
    I1, I2, I3, I4, I5, I6, I7, I8, I9 : Integer;
    B1, B2, B3 : Integer;
    F1, F2, F3, F4 : Double;
    Obj1, Obj2, Obj3, Obj4, Obj5, Obj6, Obj7, Obj8, Obj9 : IDispatch;
    List1, List2, List3 : TStringList;
    IntMan     : IIntegratedLibraryManager;
    DbDoc      : IDatabaseLibDocument;
    // Loop guard state, see SandboxTick
    SbxGuardUntil   : Double;
    SbxGuardTripped : Boolean;

procedure SandboxLog(Msg: String);
begin
    LogLines.Add(Msg);
    LogLines.SaveToFile(LogPath);
end;

// Called on every pass of every while and repeat loop in the script, with the
// loop's line in the submitted script. False once the deadline has passed,
// which ends that loop and every guarded loop after it, so the script runs to
// its end and Altium is released. Code after the loops still runs (so a
// PostProcess after a loop is not skipped); SaveDoc refuses to save.
function SandboxTick(Line : Integer) : Boolean;
begin
    if (not SbxGuardTripped) and (Now > SbxGuardUntil) then
    begin
        SbxGuardTripped := True;
        SandboxLog('LOOP GUARD: the loop at line ' + IntToStr(Line) +
                   ' was still running after %GUARD_SECONDS% s; every loop was cut short from here on');
    end;
    Result := not SbxGuardTripped;
end;

// Millimetres to Altium internal coordinate units (10000 per mil).
function MM(V : Double) : Integer;
begin
    Result := Round(V * 393700.787);
end;

// The ISch_Document for Path, opening the file first if it is not open.
// SchServer.GetSchDocumentByPath alone returns nil for a closed document, and
// calling anything on nil stops the script.
function OpenSchDoc(Path : String) : IDispatch;
var
    D : IDispatch;
begin
    Result := SchServer.GetSchDocumentByPath(Path);
    if Result = nil then
    begin
        D := Client.OpenDocument('SCH', Path);
        if D <> nil then Client.ShowDocument(D);
        Result := SchServer.GetSchDocumentByPath(Path);
    end;
    if Result = nil then SandboxLog('OpenSchDoc: could not open ' + Path);
end;

// Mark an open document modified and save it. Not after the loop guard has
// cut the script short: the document then holds a half-finished edit.
procedure SaveDoc(Path : String);
var
    D : IDispatch;
begin
    D := Client.GetDocumentByPath(Path);
    if SbxGuardTripped then
        SandboxLog('SaveDoc: not saved after the loop guard stopped the script ' + Path)
    else if D = nil then
        SandboxLog('SaveDoc: not open ' + Path)
    else
    begin
        D.SetModified(True);
        D.DoFileSave('');
    end;
end;

procedure Run;
var
    ResultText : String;
    OutLines   : TStringList;
    // === USER VARS (filled in by the run_altium_script tool) ===
    // === END USER VARS ===
begin
    LogPath := '%LOG_PATH%';
    OutPath := '%OUT_PATH%';
    LogLines := TStringList.Create;
    SbxGuardUntil := Now + %GUARD_SECONDS% / 86400;
    SbxGuardTripped := False;
    ResultText := '{"sandbox": "no result set"}';
    SandboxLog('sandbox start');

    try
        // === BEGIN EXPERIMENT (filled in by the run_altium_script tool) ===
        SandboxLog('no script loaded');
        // === END EXPERIMENT ===
    except
        // Errors inside the body - undeclared identifiers, calls on nil, bad
        // conversions - end up here instead of in a blocking dialog. The tool
        // reads this log line and reports the run as failed.
        SandboxLog('EXCEPTION: ' + ExceptionMessage);
        ResultText := '{"error": "script raised an exception - see steps"}';
    end;

    SandboxLog('sandbox end');

    OutLines := TStringList.Create;
    try
        OutLines.Text := ResultText;
        OutLines.SaveToFile(OutPath);
    finally
        OutLines.Free;
    end;
end;
