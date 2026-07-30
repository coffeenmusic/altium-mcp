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
        // READ-ONLY damage assessment: is the current document a library, and does it
        // contain stray components with our test designators?
        Obj1 := SchServer.GetCurrentSchDocument;
        SandboxLog('current doc: ' + Obj1.DocumentName);
        SandboxLog('objectID = ' + IntToStr(Obj1.ObjectID) + '  (eSchLib=continue, eSchDoc=32)');

        I1 := 0; I2 := 0; S1 := '';
        Obj2 := Obj1.SchIterator_Create;
        Obj2.AddFilter_ObjectSet(MkSet(eSchComponent));
        Obj3 := Obj2.FirstSchObject;
        while (Obj3 <> nil) do
        begin
            I1 := I1 + 1;
            S2 := Obj3.Designator.Text;
            if (S2 = 'R920') or (S2 = 'FB900') or (S2 = 'U900') or (S2 = 'F900') or (S2 = 'J900') or (S2 = 'C900') then
            begin
                I2 := I2 + 1;
                S1 := S1 + S2 + ';';
                SandboxLog('  STRAY test component found: ' + S2 + ' libref=' + Obj3.LibReference);
            end;
            Obj3 := Obj2.NextSchObject;
        end;
        Obj1.SchIterator_Destroy(Obj2);
        SandboxLog('components on this document = ' + IntToStr(I1) + ', stray test parts = ' + IntToStr(I2));
        ResultText := '{"doc": "' + Obj1.DocumentName + '", "objectID": ' + IntToStr(Obj1.ObjectID) +
                      ', "components": ' + IntToStr(I1) + ', "stray": "' + S1 + '"}';
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
