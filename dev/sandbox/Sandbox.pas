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
        // Prove parameter population: replicate the symbol AND fill its parameters
        // from the database row, so the placed part carries real values instead of
        // placeholders (TOL, PKG_STL, VALUE...).
        //
        // Row for Corp_Part_Number 1102-0001 from RESISTORS_Query:
        //   Value=200  Tolerance=1%  Pkg_Style=0201  Pwr_Rating=1/20W
        //   Description=RES, 0201, 1%, 1/20W, 200   Altium_Footprint=RESC0603X03N

        SandboxLog('opening symbol library');
        Obj2 := Client.OpenDocument('SchLib',
            'N:\IT\Neoventus_Altium_CAD\Altium_Libraries\Altium_Symbols\Passives.SchLib');
        Client.ShowDocument(Obj2);
        Sleep(1500);
        Obj1 := SchServer.GetCurrentSchDocument;

        SandboxLog('finding RES-DISCRETE');
        Obj2 := Obj1.SchLibIterator_Create;
        Obj2.AddFilter_ObjectSet(MkSet(eSchComponent));
        Obj3 := Obj2.FirstSchObject;
        S1 := '';
        while (Obj3 <> nil) do
        begin
            if (Obj3.LibReference = 'RES-DISCRETE') then begin S1 := 'found'; Break; end;
            Obj3 := Obj2.NextSchObject;
        end;

        if (S1 <> 'found') then
            ResultText := '{"error": "symbol not found"}'
        else
        begin
            SandboxLog('replicating');
            Obj3 := Obj3.Replicate;
            Obj1.SchIterator_Destroy(Obj2);

            SandboxLog('creating target sheet');
            S2 := GetWorkSpace.DM_CreateNewDocument('SCH');
            Obj1 := SchServer.GetCurrentSchDocument;
            SandboxLog('target: ' + Obj1.DocumentName);

            Obj3.Designator.Text := 'R901';
            Obj3.Location := Point(MilsToCoord(2000), MilsToCoord(3000));
            Obj3.DesignItemID := '1102-0001';
            SandboxLog('designator/location/DesignItemID set');

            SandboxLog('setting Comment to the DB description');
            Obj3.Comment.Text := 'RES, 0201, 1%, 1/20W, 200';
            SandboxLog('comment set');

            SandboxLog('filling parameters from the DB row');
            Obj2 := Obj3.SchIterator_Create;
            Obj2.AddFilter_ObjectSet(MkSet(eParameter));
            Obj1 := Obj2.FirstSchObject;   // reuse Obj1 as the parameter cursor
            I1 := 0;
            while (Obj1 <> nil) do
            begin
                S3 := UpperCase(Obj1.Name);
                if (S3 = 'VALUE') then begin Obj1.Text := '200'; I1 := I1 + 1; end
                else if (S3 = 'TOLERANCE') then begin Obj1.Text := '1%'; I1 := I1 + 1; end
                else if (S3 = 'PKG_STYLE') then begin Obj1.Text := '0201'; I1 := I1 + 1; end
                else if (S3 = 'PWR_RATING') then begin Obj1.Text := '1/20W'; I1 := I1 + 1; end;
                SandboxLog('  param ' + Obj1.Name + ' now = ' + Obj1.Text);
                Obj1 := Obj2.NextSchObject;
            end;
            Obj3.SchIterator_Destroy(Obj2);
            SandboxLog('filled ' + IntToStr(I1) + ' parameters');

            SandboxLog('registering on the sheet');
            Obj1 := SchServer.GetCurrentSchDocument;
            Obj1.RegisterSchObjectInContainer(Obj3);
            SchServer.RobotManager.SendMessage(Obj1.I_ObjectAddress, c_BroadCast,
                SCHM_PrimitiveRegistration, Obj3.I_ObjectAddress);
            Obj1.GraphicallyInvalidate;
            SandboxLog('registered');

            ResultText := '{"filled": ' + IntToStr(I1) + ', "designator": "R901", "target": "' +
                          Obj1.DocumentName + '"}';
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
