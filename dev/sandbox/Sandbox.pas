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
        // Crux test: can DatabaseLibraryName / DatabaseTableName be set AFTER the
        // component is registered on a sheet? (The earlier crash happened while the
        // replica was still unregistered.) If yes, a scripted part can be genuinely
        // database-linked and "Update from Database" should recognise it.

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
        Obj1.SchIterator_Destroy(Obj2);
        SandboxLog('symbol ' + S1);

        Obj3 := Obj3.Replicate;
        S2 := GetWorkSpace.DM_CreateNewDocument('SCH');
        Obj1 := SchServer.GetCurrentSchDocument;
        SandboxLog('target sheet: ' + Obj1.DocumentName);

        Obj3.Designator.Text := 'R903';
        Obj3.DesignItemID := '1102-0001';
        Obj3.Comment.Text := 'RES-DISCRETE';
        SandboxLog('identity set (Comment = LibReference, matching real DB parts)');

        SandboxLog('registering FIRST');
        Obj1.RegisterSchObjectInContainer(Obj3);
        SchServer.RobotManager.SendMessage(Obj1.I_ObjectAddress, c_BroadCast,
            SCHM_PrimitiveRegistration, Obj3.I_ObjectAddress);
        SandboxLog('registered');

        SandboxLog('NOW attempting DatabaseLibraryName assignment (crashed pre-registration)');
        Obj3.DatabaseLibraryName := 'Neoventus_Components.DbLib';
        SandboxLog('SUCCESS: DatabaseLibraryName set');

        SandboxLog('attempting DatabaseTableName assignment');
        Obj3.DatabaseTableName := 'RESISTORS_Query';
        SandboxLog('SUCCESS: DatabaseTableName set');

        SandboxLog('reading back: dbLib=' + Obj3.DatabaseLibraryName +
                   ' dbTable=' + Obj3.DatabaseTableName);

        Obj1.GraphicallyInvalidate;
        ResultText := '{"db_lib": "' + Obj3.DatabaseLibraryName + '", "db_table": "' +
                      Obj3.DatabaseTableName + '", "designator": "R903", "sheet": "' +
                      Obj1.DocumentName + '"}';
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
