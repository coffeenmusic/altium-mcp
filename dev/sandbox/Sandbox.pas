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
        SandboxLog('getting IntegratedLibraryManager');
        IntMan := IntegratedLibraryManager;
        SandboxLog('IntMan nil? ' + BoolToStr(IntMan = nil, True));

        if (IntMan <> nil) then
        begin
            SandboxLog('reading AvailableLibraryCount');
            I1 := IntMan.AvailableLibraryCount;
            SandboxLog('AvailableLibraryCount = ' + IntToStr(I1));

            List1 := TStringList.Create;
            for I2 := 0 to I1 - 1 do
            begin
                SandboxLog('  reading library ' + IntToStr(I2));
                S1 := IntMan.AvailableLibraryPath(I2);
                SandboxLog('    path: ' + S1);
                I3 := IntMan.AvailableLibraryType(I2);
                SandboxLog('    type enum: ' + IntToStr(I3));
                List1.Add(IntToStr(I3) + ' | ' + S1);
            end;

            List1.SaveToFile('C:\Users\Public\altium_mcp\sandbox_libs.txt');
            SandboxLog('wrote library list');
            ResultText := '{"library_count": ' + IntToStr(I1) + ', "list_file": "sandbox_libs.txt"}';
            List1.Free;
        end
        else
            ResultText := '{"error": "IntegratedLibraryManager is nil"}';
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
