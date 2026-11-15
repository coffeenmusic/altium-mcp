// Sheet settings/view commands and exact project-only persistence.

function SchematicSheetSizeName(Value: Integer): String;
begin
    Result := 'other';
    if Value = eSheetA0 then Result := 'A0'
    else if Value = eSheetA1 then Result := 'A1'
    else if Value = eSheetA2 then Result := 'A2'
    else if Value = eSheetA3 then Result := 'A3'
    else if Value = eSheetA4 then Result := 'A4';
end;

function SchematicSheetSizeValue(Name: String): Integer;
begin
    Result := -1;
    if Name = 'A0' then Result := eSheetA0
    else if Name = 'A1' then Result := eSheetA1
    else if Name = 'A2' then Result := eSheetA2
    else if Name = 'A3' then Result := eSheetA3
    else if Name = 'A4' then Result := eSheetA4;
end;

function ExecuteSchematicDocumentCommand(Command: String; Request: TStringList): String;
var
    Spec, Props, Sources: TStringList;
    Path, SpecPath, SizeName, ErrorText, BeforeStates, AfterStates: String;
    PreviousProject: String;
    I, Style, BeforeStyle: Integer;
    Doc: ISch_Document;
    ClientDoc: IServerDocument;
    Project, Previous, Current: IProject;
    WasModified, Started, DryRun, ProjectSaved, Changed, Editing: Boolean;
begin
    Spec := TStringList.Create;
    Props := TStringList.Create;
    Sources := TStringList.Create;
    ErrorText := '';
    Path := '';
    Started := False;
    Editing := False;
    Changed := False;
    ProjectSaved := False;
    WasModified := False;
    BeforeStates := '[]';
    AfterStates := '[]';
    PreviousProject := '';
    ClientDoc := nil;
    Doc := nil;
    try
      try
        repeat
            SpecPath := '';
            for I := 0 to Request.Count - 1 do
                if Pos('"spec_file"', Request[I]) > 0 then SpecPath := JSONLineValue(Request[I]);
            if not FileExists(SpecPath) then begin ErrorText := 'spec file not found'; Break; end;
            Spec.LoadFromFile(SpecPath);
            if Command = 'save_project' then
            begin
                Path := JSONUnescape(Spec.Values['project_path']);
                DryRun := Spec.Values['dry_run'] = '1';
                Project := DocumentProject(Path);
                if Project = nil then begin ErrorText := 'exact saved project must be open exactly once'; Break; end;
                if not FileExists(Path) then begin ErrorText := 'project file does not exist'; Break; end;
                ClientDoc := FindOpenDocument(Path);
                AddJSONBoolean(Props, 'project_buffer_found', ClientDoc <> nil);
                if ClientDoc = nil then begin ErrorText := 'open project buffer is unavailable'; Break; end;
                AddJSONBoolean(Props, 'project_was_modified', ClientDoc.Modified);
                for I := 0 to Project.DM_LogicalDocumentCount - 1 do
                    Sources.Add(Project.DM_LogicalDocuments(I).DM_FullPath);
                BeforeStates := ExistingOpenDocumentStates(Sources);
                if Spec.Values['expected_source_count'] <> '' then
                begin
                    if Sources.Count <> StrToIntDef(Spec.Values['expected_source_count'], -1) then
                    begin ErrorText := 'project membership changed since preflight'; Break; end;
                    for I := 0 to Sources.Count - 1 do
                        if ClosePathIndex(Sources, JSONUnescape(Spec.Values['expected_source.' + IntToStr(I)])) < 0 then
                        begin ErrorText := 'project membership changed since preflight'; Break; end;
                    if ErrorText <> '' then Break;
                end;
                for I := 0 to Sources.Count - 1 do
                begin
                    ClientDoc := FindOpenDocument(Sources[I]);
                    if ClientDoc <> nil then
                        if ClientDoc.Modified then begin ErrorText := 'source documents must be clean; project-only save refused'; Break; end;
                    if not FileExists(Sources[I]) then begin ErrorText := 'source document has no saved file'; Break; end;
                end;
                if ErrorText <> '' then Break;
                if not DryRun then
                begin
                    Previous := GetWorkspace.DM_FocusedProject;
                    if Previous <> nil then PreviousProject := Previous.DM_ProjectFullPath;
                    try
                        Project.DM_SetAsCurrentProject;
                        Current := GetWorkspace.DM_FocusedProject;
                        if Current = nil then begin ErrorText := 'no focused project; save not attempted'; Break; end;
                        if NormalizedPath(Current.DM_ProjectFullPath) <> NormalizedPath(Path) then
                        begin ErrorText := 'target project focus mismatch; save not attempted'; Break; end;
                        Started := True;
                        ResetParameters;
                        AddStringParameter('ObjectKind', 'FocusedProject');
                        RunProcess('WorkspaceManager:SaveObject');
                    finally
                        ResetParameters;
                        if PreviousProject <> '' then
                        begin
                            Previous := DocumentProject(PreviousProject);
                            if Previous <> nil then Previous.DM_SetAsCurrentProject;
                        end;
                    end;
                    ClientDoc := FindOpenDocument(Path);
                    if ClientDoc = nil then begin ErrorText := 'project buffer unavailable after save'; Break; end;
                    if ClientDoc.Modified then begin ErrorText := 'project remains Modified after save'; Break; end;
                    ProjectSaved := True;
                end;
                AfterStates := ExistingOpenDocumentStates(Sources);
                if BeforeStates <> AfterStates then ErrorText := 'source buffer states changed';
                AddJSONBoolean(Props, 'dry_run', DryRun);
                AddJSONBoolean(Props, 'source_states_unchanged', BeforeStates = AfterStates);
                Props.Add('"source_paths": ' + ClosePathsJSON(Sources));
                Break;
            end;
            Path := JSONUnescape(Spec.Values['sheet_path']);
            if LowerCase(ExtractFileExt(Path)) <> '.schdoc' then begin ErrorText := 'target must be a SchDoc'; Break; end;
            Doc := CloneOpenSheet(Path, ClientDoc);
            if Doc = nil then begin ErrorText := 'sheet must exist or be an open named unsaved SchDoc'; Break; end;
            WasModified := ClientDoc.Modified;
            if Command = 'zoom_schematic' then
            begin
                if (Spec.Values['mode'] <> 'all') and (Spec.Values['mode'] <> 'sheet') then
                begin ErrorText := 'unsupported zoom mode'; Break; end;
                if Spec.Values['mode'] = 'sheet' then
                    Client.SendMessage('SCH:Zoom', 'Action=Document', 255, Client.CurrentView)
                else Client.SendMessage('SCH:Zoom', 'Action=All', 255, Client.CurrentView);
                AddJSONBoolean(Props, 'zoom_requested', True);
                AddJSONProperty(Props, 'mode', Spec.Values['mode']);
                if ClientDoc.Modified <> WasModified then ErrorText := 'zoom changed Modified state';
            end
            else
            begin
                BeforeStyle := Doc.SheetStyle;
                SizeName := Spec.Values['sheet_size'];
                if SizeName <> '' then
                begin
                    Style := SchematicSheetSizeValue(SizeName);
                    if Style < 0 then begin ErrorText := 'unsupported ISO sheet size'; Break; end;
                    if (Style <> BeforeStyle) or Doc.UseCustomSheet then
                    begin
                        if SchServer.ProcessControl.ProcessDepth <> 0 then begin ErrorText := 'schematic editor has an active transaction'; Break; end;
                        SchServer.ProcessControl.PreProcess(Doc, '');
                        Editing := True;
                        Started := True;
                        SchServer.RobotManager.SendMessage(Doc.I_ObjectAddress, c_BroadCast, SCHM_BeginModify, c_NoEventData);
                        try
                            Doc.UseCustomSheet := False;
                            Doc.SheetStyle := Style;
                        finally
                            SchServer.RobotManager.SendMessage(Doc.I_ObjectAddress, c_BroadCast, SCHM_EndModify, c_NoEventData);
                        end;
                        SchServer.ProcessControl.PostProcess(Doc, '');
                        Editing := False;
                        Doc.GraphicallyInvalidate;
                        ClientDoc.Modified := True;
                        Changed := True;
                    end;
                    if Doc.SheetStyle <> Style then ErrorText := 'sheet size readback mismatch';
                end;
                AddJSONProperty(Props, 'sheet_size', SchematicSheetSizeName(Doc.SheetStyle));
                AddJSONInteger(Props, 'sheet_style', Doc.SheetStyle);
                AddJSONBoolean(Props, 'use_custom_sheet', Doc.UseCustomSheet);
                AddJSONProperty(Props, 'previous_sheet_size', SchematicSheetSizeName(BeforeStyle));
            end;
        until True;
      except
        ErrorText := ExceptionMessage;
      end;
      if Editing then SchServer.ProcessControl.PostProcess(Doc, '');
      AddJSONBoolean(Props, 'success', ErrorText = '');
      AddJSONBoolean(Props, 'saved', False);
      AddJSONBoolean(Props, 'project_saved', ProjectSaved);
      AddJSONBoolean(Props, 'changed', Changed or ProjectSaved);
      AddJSONBoolean(Props, 'may_have_partial_changes', Started and (ErrorText <> ''));
      if Command = 'save_project' then AddJSONProperty(Props, 'project_path', Path)
      else
      begin
          AddJSONProperty(Props, 'sheet_path', Path);
          AddJSONBoolean(Props, 'was_modified', WasModified);
          if ClientDoc <> nil then AddJSONBoolean(Props, 'is_modified', ClientDoc.Modified);
      end;
      if ErrorText <> '' then AddJSONProperty(Props, 'error', ErrorText);
      Result := BuildJSONObject(Props);
    finally
        Spec.Free;
        Props.Free;
        Sources.Free;
    end;
end;
