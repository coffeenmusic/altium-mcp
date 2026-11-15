// Exact-path lifecycle operations. Only save_document may write a document.

function DocumentJSON(Value: String): String;
var
    I: Integer;
begin
    Result := '';
    for I := 1 to Length(Value) do
        if Ord(Value[I]) > 127 then Result := Result + '\u' + IntToHex(Ord(Value[I]), 4)
        else Result := Result + Value[I];
end;

function DocumentKindForPath(Path: String): String;
begin
    Result := '';
    case LowerCase(ExtractFileExt(Path)) of
        '.schdoc': Result := 'SCH';
        '.pcbdoc': Result := 'PCB';
        '.schlib': Result := 'SCHLIB';
        '.pcblib': Result := 'PCBLIB';
    end;
end;

function DocumentProject(Path: String): IProject;
var
    I, Matches: Integer;
    Candidate: IProject;
begin
    Result := nil;
    Matches := 0;
    for I := 0 to GetWorkspace.DM_ProjectCount - 1 do
    begin
        Candidate := GetWorkspace.DM_Projects(I);
        if NormalizedPath(Candidate.DM_ProjectFullPath) = NormalizedPath(Path) then
        begin
            Result := Candidate;
            Inc(Matches);
        end;
    end;
    if Matches <> 1 then Result := nil;
end;

function DocumentMembership(Project: IProject; Path: String): Integer;
var
    I: Integer;
begin
    Result := 0;
    for I := 0 to Project.DM_LogicalDocumentCount - 1 do
        if NormalizedPath(Project.DM_LogicalDocuments(I).DM_FullPath) = NormalizedPath(Path) then
            Inc(Result);
end;

function ManageDocument(Action, SpecPath: String): String;
var
    Spec, Props: TStringList;
    Doc: IServerDocument;
    Project: IProject;
    Path, Kind, ProjectPath, ErrorText, PreviousDocumentPath, ActiveDocumentPath: String;
    Started, Saved, WasModified, Changed, DryRun, AlreadyOpen, Opened: Boolean;
    BeforeCount, AfterCount, I, Membership: Integer;
begin
    Result := 'ERROR: document spec file not found';
    if not FileExists(SpecPath) then Exit;
    Spec := TStringList.Create;
    Props := TStringList.Create;
    Started := False;
    Saved := False;
    Changed := False;
    WasModified := False;
    AlreadyOpen := False;
    Opened := False;
    PreviousDocumentPath := '';
    ActiveDocumentPath := '';
    Doc := nil;
    Project := nil;
    ErrorText := '';
    BeforeCount := -1;
    AfterCount := -1;
    try
        Spec.LoadFromFile(SpecPath);
        Path := JSONUnescape(Spec.Values['document_path']);
        Kind := Spec.Values['document_kind'];
        ProjectPath := JSONUnescape(Spec.Values['project_path']);
        DryRun := Spec.Values['dry_run'] = '1';
        try
          repeat
            if (Kind = '') or (DocumentKindForPath(Path) <> Kind) then
                begin ErrorText := 'unsupported document kind or extension mismatch'; Break; end;
            if not DirectoryExists(ExtractFilePath(Path)) then
                begin ErrorText := 'parent directory does not exist'; Break; end;
            Doc := FindOpenDocument(Path);
            AlreadyOpen := Doc <> nil;
            if Doc <> nil then
            begin
                if (NormalizedPath(Doc.FileName) <> NormalizedPath(Path)) or (UpperCase(Doc.Kind) <> Kind) then
                    begin ErrorText := 'open document identity does not match requested path and kind'; Break; end;
                WasModified := Doc.Modified;
            end;
            if Action = 'open_document' then
            begin
                if Client.CurrentView <> nil then
                    if Client.CurrentView.OwnerDocument <> nil then
                        PreviousDocumentPath := Client.CurrentView.OwnerDocument.FileName;
                if Doc = nil then
                begin
                    if not FileExists(Path) then
                        begin ErrorText := 'closed document must exist on disk; no document was created'; Break; end;
                    LogScriptError('open_document: open exact document ' + Path);
                    Started := True;
                    Doc := Client.OpenDocument(Kind, Path);
                    if Doc = nil then begin ErrorText := 'Altium did not open the document'; Break; end;
                    Opened := True;
                end;
                if (NormalizedPath(Doc.FileName) <> NormalizedPath(Path)) or (UpperCase(Doc.Kind) <> Kind) then
                    begin ErrorText := 'opened document identity mismatch'; Break; end;
                if Doc.BeingClosed then begin ErrorText := 'document is closing'; Break; end;
                Started := True;
                Client.ShowDocument(Doc);
                Sleep(100);
                if Client.CurrentView <> nil then
                    if Client.CurrentView.OwnerDocument <> nil then
                        ActiveDocumentPath := Client.CurrentView.OwnerDocument.FileName;
                if NormalizedPath(ActiveDocumentPath) <> NormalizedPath(Path) then
                    begin ErrorText := 'active editor document readback mismatch'; Break; end;
                if AlreadyOpen and (WasModified <> Doc.Modified) then
                    begin ErrorText := 'opening changed Modified on an existing document'; Break; end;
                Changed := Opened or (NormalizedPath(PreviousDocumentPath) <> NormalizedPath(ActiveDocumentPath));
            end
            else if Action = 'create_document' then
            begin
                if FileExists(Path) or (Doc <> nil) then
                    begin ErrorText := 'create_document refuses an existing file or open document'; Break; end;
                LogScriptError('create_document: new unsaved ' + Path);
                Started := True;
                Doc := Client.OpenNewDocument(Kind, '', Path, False);
                if Doc = nil then begin ErrorText := 'Altium did not create the document'; Break; end;
                Changed := True;
                Client.ShowDocument(Doc);
                if (NormalizedPath(Doc.FileName) <> NormalizedPath(Path)) or (UpperCase(Doc.Kind) <> Kind) then
                    begin ErrorText := 'created document identity mismatch; inspect open documents'; Break; end;
                if FileExists(Path) then begin ErrorText := 'creation unexpectedly wrote a file'; Break; end;
            end
            else if Action = 'save_document' then
            begin
                if Doc = nil then begin ErrorText := 'save_document requires the exact document to be open'; Break; end;
                if Pos('\\', Path) = 1 then begin ErrorText := 'network saves are unsupported'; Break; end;
                if Doc.BeingClosed then begin ErrorText := 'document is closing'; Break; end;
                if (Kind = 'SCH') or (Kind = 'SCHLIB') then
                    if SchServer.ProcessControl.ProcessDepth <> 0 then
                        begin ErrorText := 'schematic editor has an active transaction'; Break; end;
                if FileExists(Path) then
                    if (FileGetAttr(Path) and 1) <> 0 then begin ErrorText := 'target file is read-only'; Break; end;
                if not DryRun then
                begin
                    LogScriptError('save_document: save exact document ' + Path);
                    Started := True;
                    Saved := Doc.DoFileSave('');
                    if not Saved then begin ErrorText := 'Altium refused or cancelled saving'; Break; end;
                    if not FileExists(Path) then begin ErrorText := 'saved file is missing'; Break; end;
                    if Doc.Modified then begin ErrorText := 'document is still modified after save'; Break; end;
                    Changed := True;
                end;
            end
            else if Action = 'add_document_to_project' then
            begin
                if (LowerCase(ExtractFileExt(ProjectPath)) <> '.prjpcb') and
                   (LowerCase(ExtractFileExt(ProjectPath)) <> '.libpkg') then
                    begin ErrorText := 'target must be a PrjPcb or LibPkg'; Break; end;
                Project := DocumentProject(ProjectPath);
                if Project = nil then begin ErrorText := 'exact project must be open exactly once'; Break; end;
                if (Doc = nil) and not FileExists(Path) then
                    begin ErrorText := 'source must exist on disk or be an open named document'; Break; end;
                BeforeCount := Project.DM_LogicalDocumentCount;
                Membership := DocumentMembership(Project, Path);
                if Membership > 1 then begin ErrorText := 'source document membership is ambiguous'; Break; end;
                if Membership = 0 then
                begin
                    LogScriptError('add_document_to_project: add ' + Path + ' to ' + ProjectPath);
                    Started := True;
                    Project.DM_AddSourceDocument(Path);
                    Changed := True;
                end;
                AfterCount := Project.DM_LogicalDocumentCount;
                if DocumentMembership(Project, Path) <> 1 then
                    begin ErrorText := 'project membership readback failed'; Break; end;
            end
            else ErrorText := 'unknown document action';
          until True;
        except
            ErrorText := ExceptionMessage;
        end;
        AddJSONBoolean(Props, 'success', ErrorText = '');
        AddJSONProperty(Props, 'action', Action);
        AddJSONProperty(Props, 'document_path', Path);
        AddJSONProperty(Props, 'document_kind', Kind);
        AddJSONBoolean(Props, 'changed', Changed);
        AddJSONBoolean(Props, 'saved', Saved);
        AddJSONBoolean(Props, 'exists_on_disk', FileExists(Path));
        AddJSONBoolean(Props, 'may_have_partial_changes', (ErrorText <> '') and Started);
        AddJSONBoolean(Props, 'save_outcome_unknown', (ErrorText <> '') and Started and (Action = 'save_document') and not Saved);
        if Doc <> nil then
        begin
            AddJSONBoolean(Props, 'was_modified', WasModified);
            AddJSONBoolean(Props, 'is_modified', Doc.Modified);
        end;
        if Action = 'save_document' then AddJSONBoolean(Props, 'dry_run', DryRun);
        if Action = 'open_document' then
        begin
            AddJSONBoolean(Props, 'already_open', AlreadyOpen);
            AddJSONBoolean(Props, 'opened', Opened);
            AddJSONProperty(Props, 'previous_document_path', PreviousDocumentPath);
            AddJSONProperty(Props, 'active_document_path', ActiveDocumentPath);
            AddJSONBoolean(Props, 'focused', NormalizedPath(ActiveDocumentPath) = NormalizedPath(Path));
        end;
        if Action = 'add_document_to_project' then
        begin
            AddJSONProperty(Props, 'project_path', ProjectPath);
            AddJSONBoolean(Props, 'project_saved', False);
            AddJSONInteger(Props, 'document_count_before', BeforeCount);
            AddJSONInteger(Props, 'document_count_after', AfterCount);
        end;
        if ErrorText <> '' then AddJSONProperty(Props, 'error', ErrorText);
        Result := DocumentJSON(BuildJSONObject(Props));
    finally
        Spec.Free;
        Props.Free;
    end;
end;

function ExecuteManageDocument(Action: String; Request: TStringList): String;
var
    I: Integer;
begin
    Result := 'ERROR: document spec_file is required';
    for I := 0 to Request.Count - 1 do
        if Pos('"spec_file":', Request[I]) > 0 then
        begin
            Result := ManageDocument(Action, JSONLineValue(Request[I]));
            Exit;
        end;
end;

// Project selection uses IProject directly; editor tabs remain independent.
function SelectionDocumentStates(Project: IProject): String;
var
    Items, Props: TStringList;
    Source: IDocument;
    OpenDoc: IServerDocument;
    I: Integer;
    State: String;
begin
    Items := TStringList.Create;
    try
        for I := 0 to Project.DM_LogicalDocumentCount - 1 do
        begin
            Source := Project.DM_LogicalDocuments(I);
            OpenDoc := FindOpenDocument(Source.DM_FullPath);
            State := 'closed';
            if OpenDoc <> nil then
            begin
                State := 'open';
                if OpenDoc.Modified then State := 'modified';
            end;
            Props := TStringList.Create;
            try
                AddJSONProperty(Props, 'path', Source.DM_FullPath);
                AddJSONProperty(Props, 'kind', Source.DM_DocumentKind);
                AddJSONProperty(Props, 'state', State);
                Items.Add(BuildJSONObject(Props));
            finally
                Props.Free;
            end;
        end;
        Result := BuildJSONArray(Items);
    finally
        Items.Free;
    end;
end;

procedure ExistingOpenDocumentPaths(Paths: TStringList);
var
    I, J: Integer;
    Module: IServerModule;
    Path: String;
begin
    for I := 0 to Client.Count - 1 do
    begin
        Module := Client.ServerModule[I];
        if Module <> nil then
            for J := 0 to Module.DocumentCount - 1 do
            begin
                Path := Module.Documents[J].FileName;
                if (DocumentKindForPath(Path) <> '') and (Paths.IndexOf(Path) < 0) then Paths.Add(Path);
            end;
    end;
end;

function ExistingOpenDocumentStates(Paths: TStringList): String;
var
    Items, Props: TStringList;
    Doc: IServerDocument;
    State: String;
    I: Integer;
begin
    Items := TStringList.Create;
    try
        for I := 0 to Paths.Count - 1 do
        begin
            Doc := FindOpenDocument(Paths[I]);
            State := 'closed';
            if Doc <> nil then
            begin
                State := 'open';
                if Doc.Modified then State := 'modified';
            end;
            Props := TStringList.Create;
            try
                AddJSONProperty(Props, 'path', Paths[I]);
                AddJSONProperty(Props, 'kind', DocumentKindForPath(Paths[I]));
                AddJSONProperty(Props, 'state', State);
                Items.Add(BuildJSONObject(Props));
            finally
                Props.Free;
            end;
        end;
        Result := BuildJSONArray(Items);
    finally
        Items.Free;
    end;
end;

function SelectOpenProject(SpecPath: String; OpenIfClosed: Boolean = False): String;
var
    Spec, Props, Available, ExistingPaths: TStringList;
    Target, Candidate, Active: IProject;
    Path, PreviousPath, ActivePath, ErrorText, BeforeState, AfterState, Extension: String;
    ExistingBefore, ExistingAfter: String;
    I, Matches: Integer;
    Started, AlreadyOpen, Opened: Boolean;
begin
    Result := 'ERROR: project selection spec file not found';
    if not FileExists(SpecPath) then Exit;
    Spec := TStringList.Create;
    Props := TStringList.Create;
    Available := TStringList.Create;
    ExistingPaths := TStringList.Create;
    Target := nil;
    Started := False;
    AlreadyOpen := False;
    Opened := False;
    ExistingBefore := '[]';
    ExistingAfter := '[]';
    PreviousPath := '';
    ActivePath := '';
    BeforeState := BuildJSONArray(ExistingPaths);
    AfterState := BeforeState;
    ErrorText := '';
    Matches := 0;
    try
        Spec.LoadFromFile(SpecPath);
        Path := JSONUnescape(Spec.Values['project_path']);
        if OpenIfClosed then
        begin
            ExistingOpenDocumentPaths(ExistingPaths);
            ExistingBefore := ExistingOpenDocumentStates(ExistingPaths);
        end;
        Active := GetWorkspace.DM_FocusedProject;
        if Active <> nil then PreviousPath := Active.DM_ProjectFullPath;
        try
          repeat
            Extension := LowerCase(ExtractFileExt(Path));
            if (Extension <> '.prjpcb') and (Extension <> '.libpkg') then
                begin ErrorText := 'target must be a PrjPcb or LibPkg'; Break; end;
            for I := 0 to GetWorkspace.DM_ProjectCount - 1 do
            begin
                Candidate := GetWorkspace.DM_Projects(I);
                Extension := LowerCase(ExtractFileExt(Candidate.DM_ProjectFullPath));
                if (Extension = '.prjpcb') or (Extension = '.libpkg') then
                    Available.Add('"' + JSONEscapeString(Candidate.DM_ProjectFullPath) + '"');
                if NormalizedPath(Candidate.DM_ProjectFullPath) = NormalizedPath(Path) then
                begin
                    Target := Candidate;
                    Inc(Matches);
                end;
            end;
            AlreadyOpen := Matches = 1;
            if not AlreadyOpen then
            begin
                if (Matches > 1) or not OpenIfClosed then
                    begin ErrorText := 'exact project must already be open exactly once; found ' + IntToStr(Matches); Break; end;
                if not FileExists(Path) then
                    begin ErrorText := 'closed project must exist on disk; no project was created'; Break; end;
                LogScriptError('open_project: open exact project ' + Path);
                Started := True;
                Target := GetWorkspace.DM_OpenProject(Path, True);
                // Re-query: do not trust a returned or focused project by itself.
                Target := DocumentProject(Path);
                if Target = nil then begin ErrorText := 'Altium did not open the exact project once'; Break; end;
                Opened := True;
                Available.Add('"' + JSONEscapeString(Target.DM_ProjectFullPath) + '"');
            end;
            if AlreadyOpen then BeforeState := SelectionDocumentStates(Target);
            if NormalizedPath(PreviousPath) <> NormalizedPath(Path) then
            begin
                LogScriptError('select_project: set current project ' + Path);
                Started := True;
                Target.DM_SetAsCurrentProject;
            end;
            Active := GetWorkspace.DM_FocusedProject;
            if Active = nil then begin ErrorText := 'no focused project after selection'; Break; end;
            ActivePath := Active.DM_ProjectFullPath;
            AfterState := SelectionDocumentStates(Target);
            if NormalizedPath(ActivePath) <> NormalizedPath(Path) then
                begin ErrorText := 'focused project readback does not match the requested path'; Break; end;
            if AlreadyOpen and (BeforeState <> AfterState) then
                ErrorText := 'project document open/modified states changed during selection';
          until True;
        except
            ErrorText := ExceptionMessage;
        end;
        Active := GetWorkspace.DM_FocusedProject;
        if Active <> nil then ActivePath := Active.DM_ProjectFullPath;
        if OpenIfClosed then
        begin
            ExistingAfter := ExistingOpenDocumentStates(ExistingPaths);
            if (ErrorText = '') and (ExistingBefore <> ExistingAfter) then
                ErrorText := 'opening changed preexisting document open/modified states';
        end;
        AddJSONBoolean(Props, 'success', ErrorText = '');
        AddJSONProperty(Props, 'project_path', Path);
        AddJSONProperty(Props, 'previous_project_path', PreviousPath);
        AddJSONProperty(Props, 'active_project_path', ActivePath);
        AddJSONBoolean(Props, 'changed', NormalizedPath(PreviousPath) <> NormalizedPath(ActivePath));
        AddJSONBoolean(Props, 'saved', False);
        AddJSONBoolean(Props, 'project_saved', False);
        AddJSONBoolean(Props, 'compiled', False);
        AddJSONBoolean(Props, 'documents_unchanged', BeforeState = AfterState);
        AddJSONBoolean(Props, 'may_have_changed_focus', (ErrorText <> '') and Started);
        Props.Add('"documents_before": ' + BeforeState);
        Props.Add('"documents_after": ' + AfterState);
        Props.Add('"available_project_paths": ' + BuildJSONArray(Available));
        if OpenIfClosed then
        begin
            AddJSONBoolean(Props, 'already_open', AlreadyOpen);
            AddJSONBoolean(Props, 'opened', Opened);
            AddJSONBoolean(Props, 'preexisting_documents_preserved', ExistingBefore = ExistingAfter);
            Props.Add('"preexisting_documents_before": ' + ExistingBefore);
            Props.Add('"preexisting_documents_after": ' + ExistingAfter);
            AddJSONBoolean(Props, 'may_have_opened_project', (ErrorText <> '') and Started);
        end;
        if ErrorText <> '' then AddJSONProperty(Props, 'error', ErrorText);
        Result := DocumentJSON(BuildJSONObject(Props));
    finally
        Spec.Free;
        Props.Free;
        Available.Free;
        ExistingPaths.Free;
    end;
end;

function ExecuteOpenProject(Request: TStringList): String;
var
    I: Integer;
begin
    Result := 'ERROR: project opening spec_file is required';
    for I := 0 to Request.Count - 1 do
        if Pos('"spec_file":', Request[I]) > 0 then
        begin
            Result := SelectOpenProject(JSONLineValue(Request[I]), True);
            Exit;
        end;
end;

function ExecuteSelectProject(Request: TStringList): String;
var
    I: Integer;
begin
    Result := 'ERROR: project selection spec_file is required';
    for I := 0 to Request.Count - 1 do
        if Pos('"spec_file":', Request[I]) > 0 then
        begin
            Result := SelectOpenProject(JSONLineValue(Request[I]));
            Exit;
        end;
end;

function ClosePathIndex(Paths: TStringList; Path: String): Integer;
var
    I: Integer;
begin
    Result := -1;
    for I := 0 to Paths.Count - 1 do
        if NormalizedPath(Paths[I]) = NormalizedPath(Path) then
        begin
            Result := I;
            Exit;
        end;
end;

function CloseScriptPath(Path: String): Boolean;
var
    Extension: String;
begin
    Extension := LowerCase(ExtractFileExt(Path));
    Result := (Extension = '.prjscr') or (Extension = '.pas') or
              (Extension = '.dfm') or (Extension = '.vbs') or (Extension = '.js');
end;

procedure CloseAddOpenPath(Paths: TStringList; Path: String);
begin
    if (FindOpenDocument(Path) <> nil) and (ClosePathIndex(Paths, Path) < 0) then
        Paths.Add(Path);
end;

function ClosePathsJSON(Paths: TStringList): String;
var
    Values: TStringList;
    I: Integer;
begin
    Values := TStringList.Create;
    try
        for I := 0 to Paths.Count - 1 do Values.Add('"' + JSONEscapeString(Paths[I]) + '"');
        Result := BuildJSONArray(Values);
    finally
        Values.Free;
    end;
end;

procedure CloseOutsidePaths(Paths, Affected: TStringList);
var
    I, J: Integer;
    Module: IServerModule;
    Path: String;
begin
    for I := 0 to Client.Count - 1 do
    begin
        Module := Client.ServerModule[I];
        if Module <> nil then
            for J := 0 to Module.DocumentCount - 1 do
            begin
                Path := Module.Documents[J].FileName;
                if not CloseScriptPath(Path) and (ClosePathIndex(Affected, Path) < 0) then
                    CloseAddOpenPath(Paths, Path);
            end;
    end;
end;

function CloseDocumentFromSpec(SpecPath: String): String;
var
    Spec, Props, Affected, Dirty, Cleared, ClosedPaths, Discarded, Remaining, Outside: TStringList;
    Doc: IServerDocument;
    Project: IProject;
    Path, Extension, ErrorText, BeforeState, OutsideBefore, OutsideAfter: String;
    I: Integer;
    RequireClean, IsProject, AlreadyClosed, Started, TargetClosed, WasModified: Boolean;
begin
    Result := 'ERROR: close_document spec file not found';
    if not FileExists(SpecPath) then Exit;
    Spec := TStringList.Create;
    Props := TStringList.Create;
    Affected := TStringList.Create;
    Dirty := TStringList.Create;
    Cleared := TStringList.Create;
    ClosedPaths := TStringList.Create;
    Discarded := TStringList.Create;
    Remaining := TStringList.Create;
    Outside := TStringList.Create;
    ErrorText := '';
    BeforeState := '[]';
    OutsideBefore := '[]';
    OutsideAfter := '[]';
    Started := False;
    RequireClean := True;
    IsProject := False;
    AlreadyClosed := False;
    WasModified := False;
    Path := '';
    try
        Spec.LoadFromFile(SpecPath);
        Path := JSONUnescape(Spec.Values['document_path']);
        Extension := LowerCase(ExtractFileExt(Path));
        try
          repeat
            if (Path = '') or CloseScriptPath(Path) then
                begin ErrorText := 'script documents/projects cannot be closed by this tool'; Break; end;
            if (Spec.Values['require_clean'] <> '0') and (Spec.Values['require_clean'] <> '1') then
                begin ErrorText := 'require_clean must be 0 or 1'; Break; end;
            RequireClean := Spec.Values['require_clean'] = '1';
            Project := DocumentProject(Path);
            IsProject := (Project <> nil) or (Extension = '.prjpcb') or (Extension = '.libpkg');
            if (Project <> nil) and (Extension <> '.prjpcb') and (Extension <> '.libpkg') then
                begin ErrorText := 'only PrjPcb and LibPkg projects are supported'; Break; end;
            Doc := FindOpenDocument(Path);
            AlreadyClosed := (Doc = nil) and (Project = nil);
            if not AlreadyClosed then
            begin
                if Doc = nil then
                    begin ErrorText := 'cannot read the open target Modified flag; refusing to close'; Break; end;
                if IsProject and (Project = nil) then
                    begin ErrorText := 'project workspace identity could not be verified'; Break; end;
                WasModified := Doc.Modified;
                if IsProject then
                begin
                    // Snapshot all open source/generated documents before ANY close.
                    for I := 0 to Project.DM_LogicalDocumentCount - 1 do
                        CloseAddOpenPath(Affected, Project.DM_LogicalDocuments(I).DM_FullPath);
                    for I := 0 to Project.DM_GeneratedDocumentCount - 1 do
                        CloseAddOpenPath(Affected, Project.DM_GeneratedDocuments(I).DM_FullPath);
                end;
                // The project closes last; all references are looked up afresh.
                CloseAddOpenPath(Affected, Path);
            end;
            BeforeState := ExistingOpenDocumentStates(Affected);
            CloseOutsidePaths(Outside, Affected);
            OutsideBefore := ExistingOpenDocumentStates(Outside);
            for I := 0 to Affected.Count - 1 do
            begin
                if CloseScriptPath(Affected[I]) then
                    begin ErrorText := 'project contains open script documents; refusing to close'; Break; end;
                Doc := FindOpenDocument(Affected[I]);
                if Doc = nil then
                    begin ErrorText := 'affected document disappeared during preflight'; Break; end;
                if Doc.BeingClosed then
                    begin ErrorText := 'an affected document is already closing'; Break; end;
                if Doc.Modified then Dirty.Add(Affected[I]);
            end;
            if ErrorText <> '' then Break;
            if RequireClean and (Dirty.Count > 0) then
                begin ErrorText := 'target or project documents have unsaved changes'; Break; end;
            if (Affected.Count > 0) and (SchServer.ProcessControl.ProcessDepth <> 0) then
                begin ErrorText := 'schematic editor has an active transaction'; Break; end;
            if not RequireClean then
                for I := 0 to Dirty.Count - 1 do
                begin
                    Doc := FindOpenDocument(Dirty[I]);
                    Started := True;
                    Cleared.Add(Dirty[I]);
                    Doc.SetModified(False);
                end;
            for I := 0 to Affected.Count - 1 do
            begin
                Doc := FindOpenDocument(Affected[I]);
                if Doc <> nil then
                begin
                    // A newly dirty document must never reach the save prompt.
                    if Doc.Modified then
                        begin ErrorText := 'document became modified after preflight'; Break; end;
                    LogScriptError('close_document: close exact handle ' + Affected[I]);
                    Started := True;
                    Client.CloseDocument(Doc);
                    Doc := nil;  // Never dereference a closed/freed interface.
                end;
                if FindOpenDocument(Affected[I]) <> nil then
                    begin ErrorText := 'Altium did not close an affected document'; Break; end;
            end;
          until True;
        except
            ErrorText := ExceptionMessage;
        end;
        // Restore only Modified flags cleared by this request on surviving buffers.
        for I := 0 to Cleared.Count - 1 do
        begin
            Doc := FindOpenDocument(Cleared[I]);
            if Doc <> nil then Doc.SetModified(True);
        end;
        for I := 0 to Affected.Count - 1 do
            if FindOpenDocument(Affected[I]) <> nil then Remaining.Add(Affected[I])
            else
            begin
                ClosedPaths.Add(Affected[I]);
                if ClosePathIndex(Cleared, Affected[I]) >= 0 then Discarded.Add(Affected[I]);
            end;
        Project := DocumentProject(Path);
        TargetClosed := (FindOpenDocument(Path) = nil) and (Project = nil);
        if (ErrorText = '') and not TargetClosed then ErrorText := 'target closure readback failed';
        if (ErrorText = '') and (Remaining.Count > 0) then ErrorText := 'some affected documents remain open';
        OutsideAfter := ExistingOpenDocumentStates(Outside);
        if (ErrorText = '') and (OutsideBefore <> OutsideAfter) then ErrorText := 'unrelated document states changed';
        AddJSONBoolean(Props, 'success', ErrorText = '');
        AddJSONProperty(Props, 'document_path', Path);
        AddJSONBoolean(Props, 'is_project', IsProject);
        AddJSONBoolean(Props, 'require_clean', RequireClean);
        AddJSONBoolean(Props, 'closed', TargetClosed);
        AddJSONBoolean(Props, 'already_closed', AlreadyClosed);
        AddJSONBoolean(Props, 'changed', ClosedPaths.Count > 0);
        AddJSONBoolean(Props, 'was_modified', WasModified);
        AddJSONBoolean(Props, 'saved', False);
        AddJSONBoolean(Props, 'project_saved', False);
        AddJSONBoolean(Props, 'may_have_partial_changes', Started and (ErrorText <> ''));
        AddJSONBoolean(Props, 'close_outcome_unknown', False);
        AddJSONBoolean(Props, 'unrelated_documents_preserved', OutsideBefore = OutsideAfter);
        Props.Add('"affected_documents": ' + BeforeState);
        Props.Add('"closed_paths": ' + ClosePathsJSON(ClosedPaths));
        Props.Add('"discarded_paths": ' + ClosePathsJSON(Discarded));
        Props.Add('"remaining_open_paths": ' + ClosePathsJSON(Remaining));
        if RequireClean then Props.Add('"blocked_paths": ' + ClosePathsJSON(Dirty))
        else Props.Add('"blocked_paths": []');
        if ErrorText <> '' then AddJSONProperty(Props, 'error', ErrorText);
        Result := DocumentJSON(BuildJSONObject(Props));
    finally
        Spec.Free;
        Props.Free;
        Affected.Free;
        Dirty.Free;
        Cleared.Free;
        ClosedPaths.Free;
        Discarded.Free;
        Remaining.Free;
        Outside.Free;
    end;
end;

function ExecuteCloseDocument(Request: TStringList): String;
var
    I: Integer;
begin
    Result := 'ERROR: close_document spec_file is required';
    for I := 0 to Request.Count - 1 do
        if Pos('"spec_file":', Request[I]) > 0 then
        begin
            Result := CloseDocumentFromSpec(JSONLineValue(Request[I]));
            Exit;
        end;
end;
