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
    B1         : Boolean;
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
        // === BEGIN EXPERIMENT (rewritten by dev/sandbox_runner.py) ===
        // Read back everything on R902 exactly as stored, to compare against the
        // reference component placed by Altium's own DbLib flow.
        Obj1 := SchServer.GetCurrentSchDocument;
        SandboxLog('doc: ' + Obj1.DocumentName);
        Obj2 := Obj1.SchIterator_Create;
        Obj2.AddFilter_ObjectSet(MkSet(eSchComponent));
        Obj3 := Obj2.FirstSchObject;
        S1 := '';
        while (Obj3 <> nil) do
        begin
            if (Obj3.Designator.Text = 'R902') then begin S1 := 'found'; Break; end;
            Obj3 := Obj2.NextSchObject;
        end;
        Obj1.SchIterator_Destroy(Obj2);

        if (S1 <> 'found') then
            ResultText := '{"error": "R902 not found on current sheet"}'
        else
        begin
            SandboxLog('R902 found; LibReference=' + Obj3.LibReference +
                       ' DesignItemID=' + Obj3.DesignItemID +
                       ' Comment=' + Obj3.Comment.Text);
            List1 := TStringList.Create;
            List1.Add('COMPONENT|' + Obj3.LibReference + '|' + Obj3.DesignItemID + '|' + Obj3.Comment.Text);

            Obj2 := Obj3.SchIterator_Create;
            Obj2.AddFilter_ObjectSet(MkSet(eParameter));
            Obj4 := Obj2.FirstSchObject;
            I1 := 0;
            while (Obj4 <> nil) do
            begin
                I1 := I1 + 1;
                SandboxLog('  PARAM ' + Obj4.Name + ' = ' + Obj4.Text);
                List1.Add('PARAM|' + Obj4.Name + '|' + Obj4.Text);
                Obj4 := Obj2.NextSchObject;
            end;
            Obj3.SchIterator_Destroy(Obj2);

            I2 := 0;
            Obj2 := Obj3.SchIterator_Create;
            Obj2.AddFilter_ObjectSet(MkSet(eImplementation));
            Obj4 := Obj2.FirstSchObject;
            while (Obj4 <> nil) do
            begin
                I2 := I2 + 1;
                SandboxLog('  MODEL type=' + Obj4.ModelType + ' name=' + Obj4.ModelName +
                           ' current=' + BoolToStr(Obj4.IsCurrent, True));
                List1.Add('MODEL|' + Obj4.ModelType + '|' + Obj4.ModelName);
                Obj4 := Obj2.NextSchObject;
            end;
            Obj3.SchIterator_Destroy(Obj2);

            List1.SaveToFile('C:\Users\Public\altium_mcp\sandbox_r902.txt');
            List1.Free;
            ResultText := '{"params": ' + IntToStr(I1) + ', "models": ' + IntToStr(I2) + '}';
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
