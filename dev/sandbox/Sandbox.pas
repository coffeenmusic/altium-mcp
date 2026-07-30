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
        // Place a component from a generic spec file produced by dev/make_part_spec.py.
        // Nothing here is table- or part-specific: the spec drives everything.
        //
        // Spec records: SYMBOL, DESIGNATOR, DESIGNITEMID, COMMENT, TABLE, LOCATION,
        //               FOOTPRINT, DESCRIPTION, PARAM|name|value
        //
        // Existing placeholder parameters are overwritten when a PARAM matches by name
        // (case-insensitive); remaining PARAMs are added as new hidden parameters.

        SandboxLog('loading spec file');
        List1 := TStringList.Create;
        List1.LoadFromFile('C:\Users\Public\altium_mcp\part_spec.txt');
        SandboxLog('spec lines: ' + IntToStr(List1.Count));

        // --- pull the header fields out of the spec
        S1 := ''; S2 := ''; S3 := '';
        I1 := 3000; I2 := 3000;
        for I3 := 0 to List1.Count - 1 do
        begin
            Obj5 := nil;
            if (Copy(List1[I3], 1, 7) = 'SYMBOL|') then S1 := Copy(List1[I3], 8, Length(List1[I3]))
            else if (Copy(List1[I3], 1, 11) = 'DESIGNATOR|') then S2 := Copy(List1[I3], 12, Length(List1[I3]))
            else if (Copy(List1[I3], 1, 13) = 'DESIGNITEMID|') then S3 := Copy(List1[I3], 14, Length(List1[I3]));
        end;
        SandboxLog('symbol=' + S1 + ' designator=' + S2 + ' designItemID=' + S3);

        SandboxLog('opening symbol library');
        Obj2 := Client.OpenDocument('SchLib',
            'N:\IT\Neoventus_Altium_CAD\Altium_Libraries\Altium_Symbols\Passives.SchLib');
        Client.ShowDocument(Obj2);
        Sleep(1500);
        Obj1 := SchServer.GetCurrentSchDocument;

        SandboxLog('locating symbol ' + S1);
        Obj2 := Obj1.SchLibIterator_Create;
        Obj2.AddFilter_ObjectSet(MkSet(eSchComponent));
        Obj3 := Obj2.FirstSchObject;
        B1 := False;
        while (Obj3 <> nil) do
        begin
            if (Obj3.LibReference = S1) then begin B1 := True; Break; end;
            Obj3 := Obj2.NextSchObject;
        end;

        if not B1 then
            ResultText := '{"error": "symbol not found"}'
        else
        begin
            SandboxLog('replicating and creating target sheet');
            Obj3 := Obj3.Replicate;
            GetWorkSpace.DM_CreateNewDocument('SCH');
            Obj1 := SchServer.GetCurrentSchDocument;
            SandboxLog('target: ' + Obj1.DocumentName);

            Obj3.Designator.Text := S2;
            Obj3.DesignItemID := S3;
            SandboxLog('identity set');

            SandboxLog('registering');
            Obj1.RegisterSchObjectInContainer(Obj3);
            SchServer.RobotManager.SendMessage(Obj1.I_ObjectAddress, c_BroadCast,
                SCHM_PrimitiveRegistration, Obj3.I_ObjectAddress);

            // --- apply spec records
            SandboxLog('applying spec records');
            I1 := 0;   // params updated
            I2 := 0;   // params added
            for I3 := 0 to List1.Count - 1 do
            begin
                S1 := List1[I3];

                if (Copy(S1, 1, 8) = 'COMMENT|') then
                begin
                    Obj3.Comment.Text := Copy(S1, 9, Length(S1));
                    SandboxLog('  comment set');
                end
                else if (Copy(S1, 1, 12) = 'DESCRIPTION|') then
                begin
                    Obj3.ComponentDescription := Copy(S1, 13, Length(S1));
                    SandboxLog('  description set');
                end
                else if (Copy(S1, 1, 10) = 'FOOTPRINT|') then
                begin
                    S2 := Copy(S1, 11, Length(S1));
                    SandboxLog('  adding footprint model ' + S2);
                    Obj5 := Obj3.AddSchImplementation;
                    Obj5.ModelName := S2;
                    Obj5.ModelType := 'PCBLIB';
                    Obj5.IsCurrent := True;
                    Obj5.UseComponentLibrary := True;
                    SandboxLog('  footprint model added');
                end
                else if (Copy(S1, 1, 6) = 'PARAM|') then
                begin
                    // split name|value
                    S2 := Copy(S1, 7, Length(S1));
                    I3 := I3;  // keep loop var untouched
                    S3 := Copy(S2, Pos('|', S2) + 1, Length(S2));
                    S2 := Copy(S2, 1, Pos('|', S2) - 1);

                    // does the symbol already carry this parameter?
                    B1 := False;
                    Obj2 := Obj3.SchIterator_Create;
                    Obj2.AddFilter_ObjectSet(MkSet(eParameter));
                    Obj4 := Obj2.FirstSchObject;
                    while (Obj4 <> nil) do
                    begin
                        if (UpperCase(Obj4.Name) = UpperCase(S2)) then
                        begin
                            Obj4.Text := S3;
                            B1 := True;
                            Break;
                        end;
                        Obj4 := Obj2.NextSchObject;
                    end;
                    Obj3.SchIterator_Destroy(Obj2);

                    if B1 then
                    begin
                        I1 := I1 + 1;
                        SandboxLog('  updated ' + S2 + ' = ' + S3);
                    end
                    else
                    begin
                        Obj4 := SchServer.SchObjectFactory(eParameter, eCreate_Default);
                        Obj4.Name := S2;
                        Obj4.Text := S3;
                        Obj4.ParamType := eParameterType_String;
                        Obj4.ReadOnlyState := eReadOnly_None;
                        Obj4.IsHidden := True;
                        Obj3.AddSchObject(Obj4);
                        SchServer.RobotManager.SendMessage(Obj3.I_ObjectAddress, c_BroadCast,
                            SCHM_PrimitiveRegistration, Obj4.I_ObjectAddress);
                        I2 := I2 + 1;
                        SandboxLog('  added ' + S2 + ' = ' + S3);
                    end;
                end;
            end;

            SandboxLog('positioning with MoveByXY');
            Obj3.MoveByXY(MilsToCoord(3000 - CoordToMils(Obj3.Location.X)),
                          MilsToCoord(3000 - CoordToMils(Obj3.Location.Y)));
            SandboxLog('positioned');

            Obj1.GraphicallyInvalidate;
            List1.Free;
            ResultText := '{"updated_params": ' + IntToStr(I1) + ', "added_params": ' + IntToStr(I2) +
                          ', "sheet": "' + Obj1.DocumentName + '"}';
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
