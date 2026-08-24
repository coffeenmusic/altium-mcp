// Isolated DelphiScript sandbox used by the run_altium_script MCP tool.
//
// This is deliberately a SEPARATE script project from Altium_API: a compile
// error or crash in user-supplied script must never break the working MCP
// tooling.
//
// SandboxLog() flushes to disk on every call, so when a script dies silently
// (Altium leaves it paused in the debugger with no dialog) the log still shows
// the last step that completed - the statement after it is the culprit.

const
    REPLACEALL = 1;
    // Used by ReloadSelf to locate this unit on disk (see bottom of file).
    SANDBOX_PROJECT = 'Sandbox.PrjScr';
    SANDBOX_UNIT = 'Sandbox.pas';
    SANDBOX_RELOAD_MARKER = 'C:\Users\Public\altium_mcp\sandbox_reload_done.txt';

var
    LogLines : TStringList;
    LogPath  : String;
    OutPath  : String;
    // Scratch variables: DelphiScript has no inline declarations, so scripts
    // passed to the tool reuse these rather than declaring their own.
    S1, S2, S3 : String;
    I1, I2, I3 : Integer;
    B1         : Integer;
    Obj1, Obj2, Obj3, Obj4, Obj5 : IDispatch;
    List1      : TStringList;
    IntMan     : IIntegratedLibraryManager;
    DbDoc      : IDatabaseLibDocument;

procedure SandboxLog(Msg: String);
begin
    LogLines.Add(Msg);
    LogLines.SaveToFile(LogPath);
end;

procedure Run;
var
    ResultText : String;
    OutLines   : TStringList;
begin
    LogPath := 'C:\Users\Public\altium_mcp\sandbox_log.txt';
    OutPath := 'C:\Users\Public\altium_mcp\sandbox_result.json';
    LogLines := TStringList.Create;
    ResultText := '{"sandbox": "no result set"}';
    SandboxLog('sandbox start');

    try
        // === BEGIN EXPERIMENT (rewritten by the run_altium_script tool) ===
        SandboxLog('no script loaded');
        // === END EXPERIMENT ===
    except
        SandboxLog('EXCEPTION escaped the script body');
        ResultText := '{"error": "exception escaped script - see log for last step"}';
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

// --- Stale editor buffer workaround ------------------------------------------
//
// RunScript compiles the copy of Sandbox.pas that Altium's script editor holds
// in memory, NOT the file on disk. The document stays open between runs, so an
// externally rewritten script body is ignored and the previously loaded script
// runs again - producing a stale result that looks like a fresh success.
//
// ReloadSelf forces the open document to re-read the file. It is invoked in its
// own RunScript call before every run, and must never be called from Run:
// reloading the unit that is currently executing is undefined behaviour.

// Absolute path of this unit. DelphiScript has no "path of the running script",
// so find our own project among the open ones and derive the folder from it -
// the same approach ScriptProjectPath() uses in AltiumScript/other_utils.pas.
function SandboxUnitPath : String;
var
    Prj       : IProject;
    Candidate : String;
    i         : Integer;
begin
    result := '';
    if (GetWorkspace = nil) then exit;

    for i := 0 to GetWorkspace.DM_ProjectCount - 1 do
    begin
        Prj := GetWorkspace.DM_Projects(i);
        if (Prj <> nil) and (AnsiPos(SANDBOX_PROJECT, Prj.DM_ProjectFullPath) > 0) then
        begin
            // Altium can keep stale copies of a project open; accept only the
            // one whose folder really holds the unit.
            Candidate := ExtractFilePath(Prj.DM_ProjectFullPath) + SANDBOX_UNIT;
            if FileExists(Candidate) then
            begin
                result := Candidate;
                exit;
            end;
        end;
    end;
end;

procedure ReloadSelf;
var
    Doc     : IServerDocument;
    PasPath : String;
    Status  : String;
    Marker  : TStringList;
begin
    Doc := nil;
    PasPath := SandboxUnitPath;

    if (PasPath = '') then
        Status := 'ERROR: no open project matching ' + SANDBOX_PROJECT
    else
    begin
        Doc := Client.GetDocumentByPath(PasPath);
        if (Doc = nil) then
            // Not open in the IDE, so nothing is cached: the run reads disk.
            Status := 'not open: ' + PasPath
        else
        begin
            Doc.DoFileLoad;
            Status := 'reloaded: ' + PasPath;
        end;
    end;

    Marker := TStringList.Create;
    try
        Marker.Text := Status;
        Marker.SaveToFile(SANDBOX_RELOAD_MARKER);
    finally
        Marker.Free;
    end;
end;
