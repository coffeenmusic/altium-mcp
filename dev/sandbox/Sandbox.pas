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
    S1, S2, S3 : String;
    I1, I2, I3 : Integer;
    B1         : Integer;   // reused as a loop counter by some experiments
    Obj1, Obj2, Obj3, Obj4, Obj5 : IDispatch;
    List1      : TStringList;
    LibPathFromSpec : String;
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
        // === BEGIN EXPERIMENT (rewritten by dev/sandbox_runner.py) ===
        // Test the update processes against a REAL database-linked part that the user
        // selected and whose parameters they modified.
        //
        // Deliberately does NOT create documents or open libraries - that would steal
        // focus, and these processes act on the focused document / current selection.

        Obj1 := SchServer.GetCurrentSchDocument;
        if (Obj1 = nil) then
        begin
            ResultText := '{"error": "no current schematic"}';
            SandboxLog('no current sch doc');
        end
        else
        begin
            SandboxLog('doc: ' + Obj1.DocumentName);

            // --- find the selected component
            Obj3 := nil;
            Obj2 := Obj1.SchIterator_Create;
            Obj2.AddFilter_ObjectSet(MkSet(eSchComponent));
            Obj4 := Obj2.FirstSchObject;
            I1 := 0;
            while (Obj4 <> nil) do
            begin
                if (Obj4.Selection) then
                begin
                    I1 := I1 + 1;
                    if (Obj3 = nil) then Obj3 := Obj4;
                end;
                Obj4 := Obj2.NextSchObject;
            end;
            Obj1.SchIterator_Destroy(Obj2);
            SandboxLog('selected components = ' + IntToStr(I1));

            if (Obj3 = nil) then
                ResultText := '{"error": "no component is selected on the focused schematic"}'
            else
            begin
                SandboxLog('target: ' + Obj3.Designator.Text +
                           ' designItemID=' + Obj3.DesignItemID +
                           ' dbTable=' + Obj3.DatabaseTableName +
                           ' dbLib=' + Obj3.DatabaseLibraryName);

                List1 := TStringList.Create;
                SandboxLog('--- parameters BEFORE update ---');
                Obj2 := Obj3.SchIterator_Create;
                Obj2.AddFilter_ObjectSet(MkSet(eParameter));
                Obj4 := Obj2.FirstSchObject;
                while (Obj4 <> nil) do
                begin
                    SandboxLog('  ' + Obj4.Name + ' = ' + Obj4.Text);
                    List1.Add('BEFORE|' + Obj4.Name + '|' + Obj4.Text);
                    Obj4 := Obj2.NextSchObject;
                end;
                Obj3.SchIterator_Destroy(Obj2);

                // --- attempt 1
                SandboxLog('>>> RunProcess Sch:UpdatePartDatabaseLinks');
                ResetParameters;
                RunProcess('Sch:UpdatePartDatabaseLinks');
                SandboxLog('>>> returned');
                Sleep(2000);

                SandboxLog('--- parameters AFTER attempt 1 ---');
                S1 := '';
                Obj2 := Obj3.SchIterator_Create;
                Obj2.AddFilter_ObjectSet(MkSet(eParameter));
                Obj4 := Obj2.FirstSchObject;
                while (Obj4 <> nil) do
                begin
                    SandboxLog('  ' + Obj4.Name + ' = ' + Obj4.Text);
                    List1.Add('AFTER1|' + Obj4.Name + '|' + Obj4.Text);
                    S1 := S1 + Obj4.Name + '=' + Obj4.Text + ';';
                    Obj4 := Obj2.NextSchObject;
                end;
                Obj3.SchIterator_Destroy(Obj2);

                // --- attempt 2 (only informative if attempt 1 changed nothing)
                SandboxLog('>>> RunProcess Sch:UpdatePartsFromLibraryList');
                ResetParameters;
                RunProcess('Sch:UpdatePartsFromLibraryList');
                SandboxLog('>>> returned');
                Sleep(2000);

                SandboxLog('--- parameters AFTER attempt 2 ---');
                S2 := '';
                Obj2 := Obj3.SchIterator_Create;
                Obj2.AddFilter_ObjectSet(MkSet(eParameter));
                Obj4 := Obj2.FirstSchObject;
                while (Obj4 <> nil) do
                begin
                    SandboxLog('  ' + Obj4.Name + ' = ' + Obj4.Text);
                    List1.Add('AFTER2|' + Obj4.Name + '|' + Obj4.Text);
                    S2 := S2 + Obj4.Name + '=' + Obj4.Text + ';';
                    Obj4 := Obj2.NextSchObject;
                end;
                Obj3.SchIterator_Destroy(Obj2);

                List1.SaveToFile('C:\Users\Public\altium_mcp\update_selected_test.txt');
                List1.Free;

                Obj1.GraphicallyInvalidate;
                ResultText := '{"designator": "' + Obj3.Designator.Text +
                              '", "designItemID": "' + Obj3.DesignItemID +
                              '", "dbTable": "' + Obj3.DatabaseTableName +
                              '", "changed_by_attempt1": ' + BoolToStr(S1 <> S2, True) + '}';
            end;
        end;
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
