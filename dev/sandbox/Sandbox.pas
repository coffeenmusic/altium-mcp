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
        // DECISIVE TEST: place an identity-only part, then run the process
        // Sch:UpdatePartDatabaseLinks (0 parameters) and see whether Altium populates
        // the parameters and model by itself.

        SandboxLog('opening symbol library');
        Obj2 := Client.OpenDocument('SchLib',
            'N:\IT\Neoventus_Altium_CAD\Altium_Libraries\Altium_Symbols\Passives.SchLib');
        Client.ShowDocument(Obj2);
        Sleep(1500);
        Obj1 := SchServer.GetCurrentSchDocument;

        Obj2 := Obj1.SchLibIterator_Create;
        Obj2.AddFilter_ObjectSet(MkSet(eSchComponent));
        Obj3 := Obj2.FirstSchObject;
        S1 := '';
        while (Obj3 <> nil) do
        begin
            if (Obj3.LibReference = 'RES-DISCRETE') then begin S1 := 'found'; Break; end;
            Obj3 := Obj2.NextSchObject;
        end;
        SandboxLog('symbol ' + S1);
        Obj3 := Obj3.Replicate;

        SandboxLog('creating target schematic');
        GetWorkSpace.DM_CreateNewDocument('SCH');
        Obj1 := SchServer.GetCurrentSchDocument;
        SandboxLog('target: ' + Obj1.DocumentName + ' objectID=' + IntToStr(Obj1.ObjectID));

        if (Obj1.ObjectID <> 32) then
            ResultText := '{"error": "target not a schematic"}'
        else
        begin
            Obj3.Designator.Text := 'R931';
            Obj3.DesignItemID := '1112-0003';
            SandboxLog('identity set: R930 / 1112-0003 (NO parameters, NO model)');

            Obj1.RegisterSchObjectInContainer(Obj3);
            SchServer.RobotManager.SendMessage(Obj1.I_ObjectAddress, c_BroadCast,
                SCHM_PrimitiveRegistration, Obj3.I_ObjectAddress);
            Obj3.Selection := True;
            SandboxLog('registered and selected');

            SandboxLog('params BEFORE update:');
            I1 := 0;
            Obj2 := Obj3.SchIterator_Create;
            Obj2.AddFilter_ObjectSet(MkSet(eParameter));
            Obj4 := Obj2.FirstSchObject;
            while (Obj4 <> nil) do
            begin
                I1 := I1 + 1;
                SandboxLog('    ' + Obj4.Name + ' = ' + Obj4.Text);
                Obj4 := Obj2.NextSchObject;
            end;
            Obj3.SchIterator_Destroy(Obj2);

            SandboxLog('>>> running Sch:UpdatePartsFromLibraryList (may open the update wizard)');
            ResetParameters;
            RunProcess('Sch:UpdatePartsFromLibraryList');
            SandboxLog('>>> process returned');
            Sleep(2000);

            SandboxLog('params AFTER update:');
            I2 := 0;
            Obj2 := Obj3.SchIterator_Create;
            Obj2.AddFilter_ObjectSet(MkSet(eParameter));
            Obj4 := Obj2.FirstSchObject;
            while (Obj4 <> nil) do
            begin
                I2 := I2 + 1;
                SandboxLog('    ' + Obj4.Name + ' = ' + Obj4.Text);
                Obj4 := Obj2.NextSchObject;
            end;
            Obj3.SchIterator_Destroy(Obj2);

            I3 := 0;
            Obj2 := Obj3.SchIterator_Create;
            Obj2.AddFilter_ObjectSet(MkSet(eImplementation));
            Obj4 := Obj2.FirstSchObject;
            while (Obj4 <> nil) do
            begin
                I3 := I3 + 1;
                SandboxLog('    MODEL ' + Obj4.ModelType + '/' + Obj4.ModelName);
                Obj4 := Obj2.NextSchObject;
            end;
            Obj3.SchIterator_Destroy(Obj2);

            SandboxLog('dbTable now = ' + Obj3.DatabaseTableName + ' dbLib = ' + Obj3.DatabaseLibraryName);
            ResultText := '{"params_before": ' + IntToStr(I1) + ', "params_after": ' + IntToStr(I2) +
                          ', "models_after": ' + IntToStr(I3) + ', "dbTable": "' + Obj3.DatabaseTableName + '"}';
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
