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
    Obj1, Obj2, Obj3 : IDispatch;
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
        // Deterministic placement: replicate the symbol from its source SchLib into a
        // schematic, then attach DbLib linkage fields so the part is database-linked.
        //   symbol RES-DISCRETE lives in N:\...\Altium_Symbols\Passives.SchLib
        //   DbLib key: Corp_Part_Number 1102-0001, table RESISTORS

        SandboxLog('opening source symbol library');
        Obj2 := Client.OpenDocument('SchLib',
            'N:\IT\Neoventus_Altium_CAD\Altium_Libraries\Altium_Symbols\Passives.SchLib');
        SandboxLog('server doc nil? ' + BoolToStr(Obj2 = nil, True));
        Client.ShowDocument(Obj2);
        Sleep(1500);

        SandboxLog('getting the SchLib document');
        Obj1 := SchServer.GetCurrentSchDocument;
        SandboxLog('lib doc nil? ' + BoolToStr(Obj1 = nil, True));
        SandboxLog('lib doc name: ' + Obj1.DocumentName);

        SandboxLog('searching for RES-DISCRETE in the library');
        Obj3 := nil;
        Obj2 := Obj1.SchLibIterator_Create;
        Obj2.AddFilter_ObjectSet(MkSet(eSchComponent));
        Obj3 := Obj2.FirstSchObject;
        S1 := '';
        while (Obj3 <> nil) do
        begin
            if (Obj3.LibReference = 'RES-DISCRETE') then
            begin
                SandboxLog('  found RES-DISCRETE');
                S1 := 'found';
                Break;
            end;
            Obj3 := Obj2.NextSchObject;
        end;

        if (S1 <> 'found') then
        begin
            Obj1.SchIterator_Destroy(Obj2);
            ResultText := '{"error": "RES-DISCRETE not found in Passives.SchLib"}';
            SandboxLog('symbol not found');
        end
        else
        begin
            SandboxLog('replicating the library component');
            Obj3 := Obj3.Replicate;
            Obj1.SchIterator_Destroy(Obj2);
            SandboxLog('replicate returned nil? ' + BoolToStr(Obj3 = nil, True));

            SandboxLog('creating target schematic');
            S2 := GetWorkSpace.DM_CreateNewDocument('SCH');
            SandboxLog('target doc: ' + S2);
            Obj1 := SchServer.GetCurrentSchDocument;
            SandboxLog('target nil? ' + BoolToStr(Obj1 = nil, True) + ' name: ' + Obj1.DocumentName);

            SandboxLog('setting component fields');
            Obj3.Designator.Text := 'R900';
            Obj3.Location := Point(MilsToCoord(1000), MilsToCoord(2000));
            SandboxLog('  designator/location set');

            Obj3.DesignItemID := '1102-0001';
            SandboxLog('  DesignItemID set');
            // DatabaseLibraryName/DatabaseTableName are NOT writable on
            // ISch_Component - assigning them kills the script (pinpointed by the
            // sandbox step log). Skipped; DB linkage handled separately.

            SandboxLog('registering component in the schematic');
            Obj1.RegisterSchObjectInContainer(Obj3);
            SchServer.RobotManager.SendMessage(Obj1.I_ObjectAddress, c_BroadCast,
                SCHM_PrimitiveRegistration, Obj3.I_ObjectAddress);
            SandboxLog('registered');

            I1 := 0;
            S3 := '';
            Obj2 := Obj1.SchIterator_Create;
            Obj2.AddFilter_ObjectSet(MkSet(eSchComponent));
            Obj3 := Obj2.FirstSchObject;
            while (Obj3 <> nil) do
            begin
                I1 := I1 + 1;
                S3 := S3 + Obj3.LibReference + '/' + Obj3.Designator.Text + ';';
                Obj3 := Obj2.NextSchObject;
            end;
            Obj1.SchIterator_Destroy(Obj2);
            SandboxLog('components on target = ' + IntToStr(I1) + ' -> ' + S3);

            Obj1.GraphicallyInvalidate;
            ResultText := '{"placed_count": ' + IntToStr(I1) + ', "components": "' + S3 +
                          '", "target": "' + Obj1.DocumentName + '"}';
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
