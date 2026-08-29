// Standalone development sandbox for probing unproven Altium API calls.
//
// Deliberately SEPARATE from the production Altium_API script project: a
// compile error or crash in an experiment must never break the working MCP
// tooling. This project is invoked on its own by dev/sandbox_runner.py.
//
// Every SandboxLog() call flushes to disk immediately, so if an API call
// hard-crashes the script (leaving it paused in the debugger with no visible
// error), the log still shows the last step that completed - the statement
// after it is the culprit.

const
    // Constants the production units define; the sandbox is standalone so it
    // must declare anything experiments borrow from them
    REPLACEALL = 1;
    // Used by ReloadSelf to locate this unit on disk (see bottom of file).
    SANDBOX_PROJECT = 'Sandbox.PrjScr';
    SANDBOX_UNIT = 'Sandbox.pas';
    SANDBOX_RELOAD_MARKER = 'C:\Users\Public\altium_mcp\sandbox_reload_done.txt';

var
    LogLines : TStringList;
    LogPath  : String;
    OutPath  : String;
    // Generic scratch variables for experiment bodies (Pascal has no inline
    // declarations, so experiments reuse these)
    S1, S2, S3, S4, S5 : String;
    I1, I2, I3, I4, I5 : Integer;
    B1, B1MAX, I2X : Integer;   // reused as loop counters by some experiments
    Obj1, Obj2, Obj3, Obj4, Obj5, Obj6, Obj7 : IDispatch;
    List1, List2 : TStringList;
    TargetDoc  : ISch_Document;
    SavedCounts : String;   // stash for results a later loop would clobber
    BodyCross   : Integer;  // own counter - I3 already holds suspect junctions
    LibPathFromSpec : String;
    IntMan     : IIntegratedLibraryManager;
    DbDoc      : IDatabaseLibDocument;

procedure SandboxLog(Msg: String);
begin
    LogLines.Add(Msg);
    LogLines.SaveToFile(LogPath);
end;

// Pipe-delimited field accessor, 0-based. DelphiScript has no Split, and
// spec files are line-based pipe records, so experiments need this a lot.
// Returns '' for an index past the end.
function SbxField(S: String; Index: Integer): String;
var
    i, start, idx : Integer;
begin
    Result := '';
    idx := 0;
    start := 1;
    for i := 1 to Length(S) do
    begin
        if (S[i] = '|') then
        begin
            if (idx = Index) then
            begin
                Result := Copy(S, start, i - start);
                Exit;
            end;
            idx := idx + 1;
            start := i + 1;
        end;
    end;
    if (idx = Index) then
        Result := Copy(S, start, Length(S) - start + 1);
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
        // === BEGIN EXPERIMENT (rewritten by dev/sandbox_runner.py) ===
        Obj3 := Client.GetDocumentByPath('Sheet14.SchDoc');
        if (Obj3 <> nil) then
        begin
            if (Pos('Cerillo', Obj3.FileName) > 0) then
                SandboxLog('ABORT: project sheet matched')
            else
            begin
                Obj3.DoSafeChangeFileNameAndSave('C:\Users\Public\altium_mcp\rebuilt\Sheet7_rebuilt.SchDoc', 'SCH');
                SandboxLog('saved');
            end;
        end;
        ResultText := '{"ok": true}';
        // === END EXPERIMENT ===
    except
        SandboxLog('EXCEPTION escaped the experiment body');
        ResultText := '{"error": "exception escaped experiment - see log for last step"}';
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
