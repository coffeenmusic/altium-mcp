// Whole-library snapshots and clean close/reopen; only explicit save=1 writes.

function RTSupplement(Comp: ISch_Component): String;
var Items,S: TStringList; Iter: ISch_Iterator; Obj: ISch_GraphicalObject;
begin
    Items := TStringList.Create; Iter := Comp.SchIterator_Create;
    try
        Obj := Iter.FirstSchObject;
        while Obj<>nil do
        begin
            ReportProgress;
            if UPType(Obj)<>'unsupported' then
            begin
                UPObjects.Clear; S := UPState(Obj);
                try Items.Add(UPStateJSON(S)); finally S.Free; end;
            end;
            Obj := Iter.NextSchObject;
        end;
        Result := BuildJSONArray(Items);
    finally Comp.SchIterator_Destroy(Iter); Items.Free; end;
end;

function RTContent(Path,Kind: String): String;
var Props,Members,Item: TStringList; Lib: ISch_Lib; Comp: ISch_Component; Iter: ISch_Iterator; Value: String;
begin
    Props := TStringList.Create; Members := TStringList.Create; Item := TStringList.Create;
    try
        if Kind='SCHLIB' then
        begin
            Value := GetSymbolPrimitivesJSON(ROOT_DIR,'','*');
            if Pos('ERROR:',Value)=1 then begin Result := Value; Exit; end;
            Props.Add('"graphics":'+Value);
            Lib := SchServer.GetSchDocumentByPath(Path);
            if Lib=nil then begin Result := 'ERROR: SchLib unavailable'; Exit; end;
            Iter := Lib.SchLibIterator_Create; Iter.AddFilter_ObjectSet(MkSet(eSchComponent));
            try
                Comp := Iter.FirstSchObject;
                while Comp<>nil do
                begin
                    Item.Clear;
                    AddJSONProperty(Item,'component_name',Comp.LibReference);
                    Item.Add('"metadata":'+LibraryComponentSnapshot(Comp));
                    AddJSONInteger(Item,'display_mode_count',Comp.DisplayModeCount);
                    Item.Add('"supplement":'+RTSupplement(Comp)); Members.Add(BuildJSONObject(Item));
                    Comp := Iter.NextSchObject;
                end;
            finally Lib.SchIterator_Destroy(Iter); end;
            Props.Add('"members":'+BuildJSONArray(Members));
        end
        else
        begin
            Value := GetFootprintPrimitivesJSON(ROOT_DIR,'','*');
            if Pos('ERROR:',Value)=1 then begin Result := Value; Exit; end;
            Props.Add('"graphics":'+Value);
        end;
        Result := BuildJSONObject(Props);
    finally Props.Free; Members.Free; Item.Free; end;
end;

function RTSelection(Path,Kind: String; var Name: String; var Part,Display: Integer): Boolean;
var Lib: ISch_Lib; PcbLib: IPCB_Library;
begin
    Result := False; Name := ''; Part := -1; Display := -1;
    if Kind='SCHLIB' then
    begin
        Lib := SchServer.GetSchDocumentByPath(Path);
        if Lib=nil then Exit;
        if NormalizedPath(Lib.DocumentName)<>NormalizedPath(Path) then Exit;
        if Lib.CurrentSchComponent<>nil then
        begin
            Name := Lib.CurrentSchComponent.LibReference;
            Part := Lib.GetState_CurrentSchComponentPartId; Display := Lib.GetState_CurrentSchComponentDisplayMode;
        end;
    end
    else
    begin
        PcbLib := GetPcbLibSafe(0);
        if PcbLib=nil then Exit;
        if NormalizedPath(PcbLib.Board.FileName)<>NormalizedPath(Path) then Exit;
        if PcbLib.CurrentComponent<>nil then Name := PcbLib.CurrentComponent.Name;
    end;
    Result := True;
end;

function RTRestoreSelection(Path,Kind,Name: String; Part,Display: Integer): Boolean;
var Lib: ISch_Lib; Comp: ISch_Component; PcbLib: IPCB_Library; I: Integer; Actual: String; AP,AD: Integer;
begin
    Result := False;
    if Name<>'' then
    begin
        if Kind='SCHLIB' then
        begin
            Lib := SchServer.GetSchDocumentByPath(Path); if Lib=nil then Exit;
            Comp := FindLibSymbol(Lib,Name); if Comp=nil then Exit;
            if Lib.CurrentSchComponent<>Comp then Lib.CurrentSchComponent := Comp;
            if Part>=0 then if Lib.GetState_CurrentSchComponentPartId<>Part then Lib.SetState_CurrentSchComponentPartId(Part);
            if Display>=0 then if Lib.GetState_CurrentSchComponentDisplayMode<>Display then Lib.SetState_CurrentSchComponentDisplayMode(Display);
        end
        else
        begin
            PcbLib := GetPcbLibSafe(0); if PcbLib=nil then Exit;
            for I := 0 to PcbLib.ComponentCount-1 do
                if SameText(PcbLib.GetComponent(I).Name,Name) then
                    if PcbLib.CurrentComponent<>PcbLib.GetComponent(I) then PcbLib.CurrentComponent := PcbLib.GetComponent(I);
        end;
    end;
    if not RTSelection(Path,Kind,Actual,AP,AD) then Exit;
    Result := SameText(Actual,Name) and (AP=Part) and (AD=Display);
end;

function ExecuteVerifyLibraryRoundtrip(Request: TStringList): String;
var
    Spec,Props,Rows,Entry,Outside,Paths,Names,Parts,Displays,WasOpen,WasDirty,Before,After,
    SavedPaths,Attempted,Reopened,CleanupErrors: TStringList;
    Doc,PreviousDoc: IServerDocument; Project: IProject;
    SpecPath,Path,Kind,ErrorText,BeforeOutside,AfterOutside,PreviousPath,ActivePath,Name,Value,PreviousProject,ActiveProject: String;
    I,N,Part,Display: Integer; SaveRequested,Started,ViewRestored,SelectionsRestored,ContextCaptured: Boolean;
begin
    Spec := TStringList.Create; Props := TStringList.Create; Rows := TStringList.Create; Entry := TStringList.Create;
    Outside := TStringList.Create; Paths := TStringList.Create; Names := TStringList.Create;
    Parts := TStringList.Create; Displays := TStringList.Create; WasOpen := TStringList.Create; WasDirty := TStringList.Create;
    Before := TStringList.Create; After := TStringList.Create; SavedPaths := TStringList.Create;
    Attempted := TStringList.Create; Reopened := TStringList.Create; CleanupErrors := TStringList.Create;
    UPObjects := TInterfaceList.Create;
    ErrorText := ''; PreviousPath := ''; ActivePath := ''; BeforeOutside := '[]'; AfterOutside := '[]'; PreviousProject := ''; ActiveProject := '';
    Started := False; SaveRequested := False; ViewRestored := False; SelectionsRestored := True; Project := nil; ContextCaptured := False;
    try
      try
        repeat
            SpecPath := '';
            for I := 0 to Request.Count-1 do if Pos('"spec_file"',Request[I])>0 then SpecPath := JSONLineValue(Request[I]);
            if not FileExists(SpecPath) then begin ErrorText := 'Spec file not found'; Break; end;
            Spec.LoadFromFile(SpecPath); N := StrToIntDef(Spec.Values['path_count'],0);
            if (N<1) or (N>8) then begin ErrorText := 'Expected 1..8 libraries'; Break; end;
            if (Spec.Values['save']<>'0') and (Spec.Values['save']<>'1') then begin ErrorText := 'save must be explicit 0 or 1'; Break; end;
            SaveRequested := Spec.Values['save']='1';
            if SchServer<>nil then if SchServer.ProcessControl.ProcessDepth<>0 then begin ErrorText := 'Schematic transaction is active'; Break; end;
            if Client.CurrentView<>nil then
                if Client.CurrentView.OwnerDocument<>nil then PreviousPath := Client.CurrentView.OwnerDocument.FileName;
            Project := GetWorkspace.DM_FocusedProject;
            if Project<>nil then PreviousProject := Project.DM_ProjectFullPath;
            ContextCaptured := True;
            for I := 0 to N-1 do Paths.Add(EditValue(Spec,'path.'+IntToStr(I)));
            CloseOutsidePaths(Outside,Paths); BeforeOutside := ExistingOpenDocumentStates(Outside);
            // Preflight ALL targets before a save or close; do not discard buffers.
            for I := 0 to Paths.Count-1 do
            begin
                Path := Paths[I]; Kind := DocumentKindForPath(Path);
                if (Kind<>'SCHLIB') and (Kind<>'PCBLIB') then begin ErrorText := 'Expected library document'; Break; end;
                Doc := FindOpenDocument(Path);
                if Doc<>nil then WasOpen.Add('1') else WasOpen.Add('0');
                if Doc=nil then
                begin
                    if not FileExists(Path) then begin ErrorText := 'Library is neither saved nor open'; Break; end;
                    Doc := OpenLibraryDocument(Kind,Path);
                end
                else Doc := OpenLibraryDocument(Kind,Path);
                if Doc=nil then begin ErrorText := 'Library buffer is missing or ambiguous'; Break; end;
                if Doc.BeingClosed or (NormalizedPath(Doc.FileName)<>NormalizedPath(Path)) or (UpperCase(Doc.Kind)<>Kind) then
                    begin ErrorText := 'Library identity/closing preflight failed'; Break; end;
                if Doc.Modified then WasDirty.Add('1') else WasDirty.Add('0');
                if not SaveRequested and (Doc.Modified or not FileExists(Path)) then
                    begin ErrorText := 'save=False requires saved clean libraries; no changes discarded'; Break; end;
                if SaveRequested and FileExists(Path) then
                    if (FileGetAttr(Path) and 1)<>0 then begin ErrorText := 'Library file is read-only'; Break; end;
                if not RTSelection(Path,Kind,Name,Part,Display) then begin ErrorText := 'Library selection unavailable'; Break; end;
                Names.Add(Name); Parts.Add(IntToStr(Part)); Displays.Add(IntToStr(Display));
                Value := RTContent(Path,Kind);
                if Pos('ERROR:',Value)=1 then begin ErrorText := Value; Break; end;
                Before.Add(Value);
            end;
            if ErrorText<>'' then Break;
            for I := 0 to Paths.Count-1 do
            begin
                ReportProgress; Path := Paths[I]; Kind := DocumentKindForPath(Path);
                Doc := FindOpenDocument(Path); if Doc=nil then begin ErrorText := 'Library disappeared after preflight'; Break; end;
                Started := True;
                if SaveRequested then
                begin
                    Attempted.Add(Path);
                    if not Doc.DoFileSave('') then begin ErrorText := 'Altium refused/cancelled saving'; Break; end;
                    SavedPaths.Add(Path);
                end;
                if Doc.Modified or not FileExists(Path) then begin ErrorText := 'Library is not saved and clean before close'; Break; end;
                // No stale document/component interfaces survive the close.
                Client.CloseDocument(Doc); Doc := nil;
                if FindOpenDocument(Path)<>nil then begin ErrorText := 'Clean library did not close'; Break; end;
                Doc := OpenLibraryDocument(Kind,Path);
                if Doc=nil then begin ErrorText := 'Saved library did not reopen'; Break; end;
                Reopened.Add(Path);
                if not RTRestoreSelection(Path,Kind,Names[I],StrToInt(Parts[I]),StrToInt(Displays[I])) then
                    begin SelectionsRestored := False; ErrorText := 'Member/section/display restore failed'; Break; end;
                if Doc.Modified then begin ErrorText := 'Reopened library became modified'; Break; end;
                Value := RTContent(Path,Kind);
                if Pos('ERROR:',Value)=1 then begin ErrorText := Value; Break; end;
                After.Add(Value);
            end;
        until True;
      except ErrorText := ExceptionMessage; end;
      // Restore originally closed editors only when clean. Never clear Modified.
      for I := 0 to WasOpen.Count-1 do
        if WasOpen[I]='0' then
        begin
            try
                Doc := FindOpenDocument(Paths[I]);
                if Doc<>nil then
                    if not Doc.Modified then Client.CloseDocument(Doc)
                    else CleanupErrors.Add('Originally closed target is now dirty: '+Paths[I]);
            except CleanupErrors.Add('Target editor restoration failed: '+Paths[I]); end;
        end;
      try
          PreviousDoc := FindOpenDocument(PreviousPath);
          if PreviousDoc<>nil then Client.ShowDocument(PreviousDoc);
          if ContextCaptured then
          begin
              if Project<>nil then Project.DM_SetAsCurrentProject;
              if GetWorkspace.DM_FocusedProject<>nil then ActiveProject := GetWorkspace.DM_FocusedProject.DM_ProjectFullPath;
          end;
          if Client.CurrentView<>nil then
              if Client.CurrentView.OwnerDocument<>nil then ActivePath := Client.CurrentView.OwnerDocument.FileName;
          ViewRestored := (PreviousPath='') or (NormalizedPath(PreviousPath)=NormalizedPath(ActivePath));
      except CleanupErrors.Add('Previous document view restoration failed'); end;
      if not ViewRestored then CleanupErrors.Add('Previous document view was not restored');
      if NormalizedPath(PreviousProject)<>NormalizedPath(ActiveProject) then CleanupErrors.Add('Focused project was not restored');
      AfterOutside := ExistingOpenDocumentStates(Outside);
      if BeforeOutside<>AfterOutside then CleanupErrors.Add('Unrelated open document states changed');
      for I := 0 to Before.Count-1 do
      begin
          Entry.Clear; AddJSONProperty(Entry,'library_path',Paths[I]); AddJSONProperty(Entry,'kind',DocumentKindForPath(Paths[I]));
          AddJSONBoolean(Entry,'was_open',WasOpen[I]='1'); AddJSONBoolean(Entry,'was_modified',WasDirty[I]='1');
          AddJSONProperty(Entry,'active_member',Names[I]); AddJSONInteger(Entry,'part_id',StrToInt(Parts[I])); AddJSONInteger(Entry,'display_mode',StrToInt(Displays[I]));
          Entry.Add('"before":'+Before[I]);
          if I<After.Count then Entry.Add('"after":'+After[I]) else Entry.Add('"after":null');
          Doc := FindOpenDocument(Paths[I]); AddJSONBoolean(Entry,'is_open',Doc<>nil);
          if Doc<>nil then AddJSONBoolean(Entry,'is_modified',Doc.Modified) else AddJSONBoolean(Entry,'is_modified',False);
          Rows.Add(BuildJSONObject(Entry));
      end;
      AddJSONBoolean(Props,'success',(ErrorText='') and (CleanupErrors.Count=0)); AddJSONBoolean(Props,'save_requested',SaveRequested);
      AddJSONBoolean(Props,'saved',SavedPaths.Count>0); AddJSONBoolean(Props,'started',Started);
      Props.Add('"saved_paths":'+ECStringArray(SavedPaths)); Props.Add('"save_attempted_paths":'+ECStringArray(Attempted));
      Props.Add('"reopened_paths":'+ECStringArray(Reopened)); Props.Add('"libraries":'+BuildJSONArray(Rows));
      Props.Add('"restoration_errors":'+ECStringArray(CleanupErrors));
      AddJSONBoolean(Props,'view_restored',ViewRestored); AddJSONBoolean(Props,'selections_restored',SelectionsRestored);
      AddJSONBoolean(Props,'project_restored',NormalizedPath(PreviousProject)=NormalizedPath(ActiveProject));
      AddJSONProperty(Props,'previous_project_path',PreviousProject); AddJSONProperty(Props,'active_project_path',ActiveProject);
      AddJSONBoolean(Props,'unrelated_documents_preserved',BeforeOutside=AfterOutside);
      AddJSONProperty(Props,'previous_document_path',PreviousPath); AddJSONProperty(Props,'active_document_path',ActivePath);
      if ErrorText<>'' then AddJSONProperty(Props,'error',ErrorText);
      Result := BuildJSONObject(Props);
    finally
        Spec.Free; Props.Free; Rows.Free; Entry.Free; Outside.Free; Paths.Free; Names.Free; Parts.Free; Displays.Free;
        WasOpen.Free; WasDirty.Free; Before.Free; After.Free; SavedPaths.Free; Attempted.Free; Reopened.Free; CleanupErrors.Free; UPObjects.Free;
    end;
end;
