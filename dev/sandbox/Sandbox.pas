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
        SandboxLog('getting IntegratedLibraryManager');
        IntMan := IntegratedLibraryManager;
        if (IntMan = nil) then
        begin
            ResultText := '{"error": "IntMan nil"}';
            SandboxLog('IntMan is nil - stopping');
        end
        else
        begin
            SandboxLog('locating the DbLib among available libraries');
            S1 := '';
            for I2 := 0 to IntMan.AvailableLibraryCount - 1 do
            begin
                S2 := IntMan.AvailableLibraryPath(I2);
                SandboxLog('  candidate: ' + S2);
                if (Pos('.DbLib', S2) > 0) then
                begin
                    S1 := S2;
                    SandboxLog('  -> selected DbLib: ' + S1);
                end;
            end;

            if (S1 = '') then
            begin
                ResultText := '{"error": "no .DbLib found in available libraries"}';
                SandboxLog('no DbLib found');
            end
            else
            begin
                SandboxLog('calling GetAvailableDBLibDocAtPath (may hit the network share)');
                DbDoc := IntMan.GetAvailableDBLibDocAtPath(S1);
                SandboxLog('DbDoc nil? ' + BoolToStr(DbDoc = nil, True));

                if (DbDoc <> nil) then
                begin
                    SandboxLog('reading GetTableCount');
                    I1 := DbDoc.GetTableCount;
                    SandboxLog('table count = ' + IntToStr(I1));

                    List1 := TStringList.Create;
                    for I2 := 0 to I1 - 1 do
                    begin
                        S3 := DbDoc.GetTableNameAt(I2);
                        SandboxLog('  table ' + IntToStr(I2) + ': ' + S3);
                        List1.Add(S3);
                    end;
                    List1.SaveToFile('C:\Users\Public\altium_mcp\sandbox_dblib_tables.txt');
                    List1.Free;
                    ResultText := '{"dblib": "' + StringReplace(S1, '\', '/', REPLACEALL) +
                                  '", "table_count": ' + IntToStr(I1) + '}';
                end
                else
                    ResultText := '{"error": "GetAvailableDBLibDocAtPath returned nil", "path": "' +
                                  StringReplace(S1, '\', '/', REPLACEALL) + '"}';
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
