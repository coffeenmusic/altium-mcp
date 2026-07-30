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
