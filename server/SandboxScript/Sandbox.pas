// Isolated DelphiScript sandbox used by the run_altium_script MCP tool.
//
// This file is a TEMPLATE: run_altium_script never edits it. For every run the
// tool writes a copy with a unique file name under C:\Users\Public\altium_mcp\runs,
// fills in the markers below and starts it with ScriptingSystem:RunScriptFile.
// A unique file per run means a broken script left open in Altium's script
// editor or paused in its debugger can never be re-run by accident.
//
// SandboxLog() flushes to disk on every call, so when a script dies the log
// still shows the last step that completed - the statement after it is the
// culprit.
//
// The tool also makes every while and repeat loop of the script call
// SandboxTick(). A script runs on Altium's UI thread and nothing can stop it
// from outside, so a loop that never ends would freeze Altium until X2.EXE is
// killed. Past the deadline (%GUARD_SECONDS% s) every guarded loop exits.
//
// Layout of a run file: the script's var block joins the globals below, its
// functions and procedures follow the template's helpers, and its statements
// become SbxBody. Run calls SbxBody, so an Exit in the script ends the script
// and Run still writes the result.

const
    REPLACEALL = 1;

var
    LogLines : TStringList;
    LogPath  : String;
    OutPath  : String;
    // Scratch variables: DelphiScript has no inline declarations, so scripts
    // either reuse these or start with their own "var" block (the tool moves
    // that block into Run's declarations).
    ResultText : String;
    S1, S2, S3, S4, S5, S6, S7, S8, S9 : String;
    I1, I2, I3, I4, I5, I6, I7, I8, I9 : Integer;
    B1, B2, B3 : Integer;
    F1, F2, F3, F4 : Double;
    Obj1, Obj2, Obj3, Obj4, Obj5, Obj6, Obj7, Obj8, Obj9 : IDispatch;
    List1, List2, List3 : TStringList;
    IntMan     : IIntegratedLibraryManager;
    DbDoc      : IDatabaseLibDocument;
    // Loop guard state, see SandboxTick
    SbxGuardUntil   : Double;
    SbxGuardTripped : Boolean;
    // Undo groups the script has opened and not yet closed, see SbxPreProcess
    SbxUndoDocs  : TList;
    SbxUndoNames : TStringList;
    SbxUndoPath  : String;
    // Documents the tool snapshotted, see SbxNoteDoc
    SbxSnapDocs  : TStringList;
    // The script's own var block (filled in by the run_altium_script tool).
    // Global, so the script's functions and procedures can use it too.
    // === USER VARS (filled in by the run_altium_script tool) ===
    // === END USER VARS ===

procedure SandboxLog(Msg: String);
begin
    LogLines.Add(Msg);
    LogLines.SaveToFile(LogPath);
end;

// Called on every pass of every while and repeat loop in the script, with the
// loop's line in the submitted script. False once the deadline has passed,
// which ends that loop and every guarded loop after it, so the script runs to
// its end and Altium is released. Code after the loops still runs (so a
// PostProcess after a loop is not skipped); SaveDoc refuses to save.
function SandboxTick(Line : Integer) : Boolean;
begin
    if (not SbxGuardTripped) and (Now > SbxGuardUntil) then
    begin
        SbxGuardTripped := True;
        SandboxLog('LOOP GUARD: the loop at line ' + IntToStr(Line) +
                   ' was still running after %GUARD_SECONDS% s; every loop was cut short from here on');
    end;
    Result := not SbxGuardTripped;
end;

// The tool rewrites the script's SchServer.ProcessControl.PreProcess/PostProcess
// and PCBServer.PreProcess/PostProcess calls into these. PreProcess opens an
// undo group on a document and PostProcess closes it; a group left open (the
// script failed between the two) makes every later edit in that document,
// including the user's, impossible to undo until Altium restarts. So the open
// groups are kept here, closed by SbxCloseUndo when the script ends, and also
// written to a file, so the tool can close them even when the script dies in
// Altium's debugger and never reaches its end.
// The list is a fallback for a script that dies in the debugger; failing to
// write it must never fail the script. A server started before this template
// existed leaves the path token unfilled.
procedure SbxSaveUndo;
begin
    if (SbxUndoPath = '') or (Copy(SbxUndoPath, 1, 1) = '%') then Exit;
    try
        SbxUndoNames.SaveToFile(SbxUndoPath);
    except
    end;
end;

procedure SbxPreProcess(Doc : IDispatch; Param : String);
begin
    SchServer.ProcessControl.PreProcess(Doc, Param);
    SbxUndoDocs.Add(Doc);
    SbxUndoNames.Add('SCH|' + Doc.DocumentName);
    SbxSaveUndo;
end;

procedure SbxPostProcess(Doc : IDispatch; Param : String);
begin
    SchServer.ProcessControl.PostProcess(Doc, Param);
    if SbxUndoNames.Count > 0 then
    begin
        SbxUndoDocs.Delete(SbxUndoDocs.Count - 1);
        SbxUndoNames.Delete(SbxUndoNames.Count - 1);
        SbxSaveUndo;
    end;
end;

procedure SbxPcbPreProcess;
begin
    PCBServer.PreProcess;
    SbxUndoDocs.Add(nil);
    SbxUndoNames.Add('PCB|');
    SbxSaveUndo;
end;

procedure SbxPcbPostProcess;
begin
    PCBServer.PostProcess;
    if SbxUndoNames.Count > 0 then
    begin
        SbxUndoDocs.Delete(SbxUndoDocs.Count - 1);
        SbxUndoNames.Delete(SbxUndoNames.Count - 1);
        SbxSaveUndo;
    end;
end;

// Close, newest first, every undo group the script left open
procedure SbxCloseUndo;
var
    I : Integer;
begin
    for I := SbxUndoNames.Count - 1 downto 0 do
    begin
        try
            if Copy(SbxUndoNames[I], 1, 4) = 'PCB|' then
                PCBServer.PostProcess
            else
                SchServer.ProcessControl.PostProcess(SbxUndoDocs[I], '');
            SandboxLog('UNDO GROUP LEFT OPEN: closed it on ' + SbxUndoNames[I]);
        except
            SandboxLog('UNDO GROUP LEFT OPEN: could not close it on ' + SbxUndoNames[I]);
        end;
    end;
    SbxUndoDocs.Clear;
    SbxUndoNames.Clear;
    SbxSaveUndo;
end;

// Millimetres to Altium internal coordinate units (10000 per mil).
function MM(V : Double) : Integer;
begin
    Result := Round(V * 393700.787);
end;

// The ISch_Document for Path, opening the file first if it is not open.
// SchServer.GetSchDocumentByPath alone returns nil for a closed document, and
// calling anything on nil stops the script.
function OpenSchDoc(Path : String) : IDispatch;
var
    D : IDispatch;
begin
    Result := SchServer.GetSchDocumentByPath(Path);
    if Result = nil then
    begin
        D := Client.OpenDocument('SCH', Path);
        if D <> nil then Client.ShowDocument(D);
        Result := SchServer.GetSchDocumentByPath(Path);
    end;
    if Result = nil then SandboxLog('OpenSchDoc: could not open ' + Path);
end;

// Mark an open document modified and save it. Not after the loop guard has
// cut the script short: the document then holds a half-finished edit.
procedure SaveDoc(Path : String);
var
    D : IDispatch;
begin
    D := Client.GetDocumentByPath(Path);
    if SbxGuardTripped then
        SandboxLog('SaveDoc: not saved after the loop guard stopped the script ' + Path)
    else if D = nil then
        SandboxLog('SaveDoc: not open ' + Path)
    else
    begin
        D.SetModified(True);
        D.DoFileSave('');
    end;
end;

// Numbers as text with a dot as decimal separator, whatever Windows is set to:
// FloatToStr, FormatFloat and StrToFloat follow the locale, so with a decimal
// comma FloatToStr(1.5) is '1,5' and StrToFloat('1.5') fails.
function FmtNum(V : Double; Decimals : Integer = 4) : String;
var
    Pattern : String;
begin
    Pattern := '0';
    if Decimals > 0 then Pattern := '0.' + StringOfChar('#', Decimals);
    Result := StringReplace(FormatFloat(Pattern, V), DecimalSeparator, '.', REPLACEALL);
end;

// Internal coordinate units as millimetres, with a dot
function FmtMM(Coord : Integer; Decimals : Integer = 4) : String;
begin
    Result := FmtNum(Coord / 393700.787, Decimals);
end;

// A number written with a dot or a comma
function ParseNum(S : String) : Double;
begin
    S := Trim(S);
    S := StringReplace(S, '.', DecimalSeparator, REPLACEALL);
    S := StringReplace(S, ',', DecimalSeparator, REPLACEALL);
    Result := StrToFloat(S);
end;

// A length in millimetres ('1.5', '1,5', '1.5mm') or mils ('15mil') as
// internal coordinate units
function ParseMM(S : String) : Integer;
begin
    S := LowerCase(Trim(S));
    if Copy(S, Length(S) - 2, 3) = 'mil' then
        Result := Round(ParseNum(Copy(S, 1, Length(S) - 3)) * 10000)
    else
    begin
        if Copy(S, Length(S) - 1, 2) = 'mm' then S := Copy(S, 1, Length(S) - 2);
        Result := MM(ParseNum(S));
    end;
end;

// A path in lower case with forward slashes turned back and repeated
// separators dropped (a UNC path keeps its leading '\\'), for comparing paths
function SbxNormPath(Path : String) : String;
var
    Lead : String;
begin
    Result := LowerCase(StringReplace(Trim(Path), '/', '\', REPLACEALL));
    Lead := '';
    if Copy(Result, 1, 2) = '\\' then
    begin
        Lead := '\\';
        Result := Copy(Result, 3, Length(Result));
    end;
    while Pos('\\', Result) > 0 do
        Result := StringReplace(Result, '\\', '\', REPLACEALL);
    Result := Lead + Result;
end;

// The open document for a file, or nil. Client.IsDocumentOpen and
// GetDocumentByPath miss a path spelled differently from Altium's.
function SbxFindDoc(Path : String) : IServerDocument;
var
    I, J : Integer;
    Module : IServerModule;
    Want : String;
begin
    Result := nil;
    Want := SbxNormPath(Path);
    for I := 0 to Client.Count - 1 do
    begin
        Module := Client.ServerModule[I];
        if Module <> nil then
            for J := 0 to Module.DocumentCount - 1 do
                if SbxNormPath(Module.Documents[J].FileName) = Want then
                begin
                    Result := Module.Documents[J];
                    Exit;
                end;
    end;
end;

// 'closed', 'open' or 'unsaved' (open with changes not on disk). Modified is
// read through BoolToStr: read directly in a condition it is always False.
function SbxDocState(Path : String) : String;
var
    D : IServerDocument;
begin
    Result := 'closed';
    D := SbxFindDoc(Path);
    if D <> nil then
    begin
        Result := 'open';
        if BoolToStr(D.Modified, True) = 'True' then Result := 'unsaved';
    end;
end;

// Logs whether a document the tool snapshotted is open in Altium and has
// unsaved changes before the script runs: a rollback restores the file on
// disk, so changes that were only in Altium before the run cannot come back.
procedure SbxNoteDoc(Path : String);
begin
    SbxSnapDocs.Add(Path);
    SandboxLog('DOC STATE: ' + SbxDocState(Path) + '|' + Path);
end;

// The same once the script has ended, so a rollback can tell the script's
// unsaved changes from changes made in Altium after the run
procedure SbxNoteDocsAfter;
var
    K : Integer;
begin
    for K := 0 to SbxSnapDocs.Count - 1 do
        SandboxLog('DOC AFTER: ' + SbxDocState(SbxSnapDocs[K]) + '|' + SbxSnapDocs[K]);
end;

// Close a document without saving, for a rollback. Its changes are dropped
// first, so Altium does not ask to save them. Returns 'closed' (it was not
// open), 'open' (it was open and is now closed) or 'stillopen'.
function SbxCloseDoc(Path : String) : String;
var
    D : IServerDocument;
begin
    Result := 'closed';
    D := SbxFindDoc(Path);
    if D <> nil then
    begin
        Result := 'open';
        D.SetModified(False);
        Client.CloseDocument(D);
        if SbxFindDoc(Path) <> nil then Result := 'stillopen';
    end;
end;

// The open documents of the project at ProjPath with unsaved changes, other
// than those in Skip ('|' + path through SbxNormPath, upper case, + '|' for
// each), each followed by '*'. Closing the project would ask to save them.
function SbxProjectUnsaved(ProjPath : String; Skip : String) : String;
var
    P, K : Integer;
    Prj : IProject;
    Doc : IDocument;
    D : IServerDocument;
begin
    Result := '';
    for P := 0 to GetWorkspace.DM_ProjectCount - 1 do
    begin
        Prj := GetWorkspace.DM_Projects(P);
        if Prj <> nil then
            if SbxNormPath(Prj.DM_ProjectFullPath) = SbxNormPath(ProjPath) then
                for K := 0 to Prj.DM_LogicalDocumentCount - 1 do
                begin
                    Doc := Prj.DM_LogicalDocuments(K);
                    if Pos('|' + UpperCase(SbxNormPath(Doc.DM_FullPath)) + '|', Skip) = 0 then
                    begin
                        D := SbxFindDoc(Doc.DM_FullPath);
                        if D <> nil then
                            if BoolToStr(D.Modified, True) = 'True' then
                                Result := Result + Doc.DM_FullPath + '*';
                    end;
                end;
    end;
end;

// === USER ROUTINES (filled in by the run_altium_script tool) ===
// === END USER ROUTINES ===

procedure SbxBody;
begin
    // === BEGIN EXPERIMENT (filled in by the run_altium_script tool) ===
    SandboxLog('no script loaded');
    // === END EXPERIMENT ===
end;

procedure Run;
var
    OutLines   : TStringList;
begin
    LogPath := '%LOG_PATH%';
    OutPath := '%OUT_PATH%';
    LogLines := TStringList.Create;
    SbxGuardUntil := Now + %GUARD_SECONDS% / 86400;
    SbxGuardTripped := False;
    SbxUndoDocs := TList.Create;
    SbxUndoNames := TStringList.Create;
    SbxUndoPath := '%UNDO_PATH%';
    // The scratch lists are ready to use: a list that was never created is
    // nil, and the first List1.Add would stop the script
    List1 := TStringList.Create;
    List2 := TStringList.Create;
    List3 := TStringList.Create;
    ResultText := '{"sandbox": "no result set"}';
    SbxSnapDocs := TStringList.Create;
    // === SNAPSHOT DOCS (filled in by the run_altium_script tool) ===
    // === END SNAPSHOT DOCS ===
    SandboxLog('sandbox start');

    // Everything after the script runs in a finally block, so the lists are
    // freed, undo groups closed and the result written also when the script
    // leaves with Exit: Exit ends Run, and DelphiScript still runs finally
    // blocks on the way out (tested on Altium 26.11)
    try
    try
        SbxBody;
    except
        // Errors inside the body - undeclared identifiers, calls on nil, bad
        // conversions - end up here instead of in a blocking dialog. The tool
        // reads this log line and reports the run as failed.
        SandboxLog('EXCEPTION: ' + ExceptionMessage);
        ResultText := '{"error": "script raised an exception - see steps"}';
    end;
    finally
        List1.Free;
        List2.Free;
        List3.Free;

        SbxCloseUndo;
        SbxNoteDocsAfter;
        SbxSnapDocs.Free;
        SandboxLog('sandbox end');

        OutLines := TStringList.Create;
        try
            OutLines.Text := ResultText;
            OutLines.SaveToFile(OutPath);
        finally
            OutLines.Free;
        end;
    end;
end;
