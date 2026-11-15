// Fail-closed content checks and exact-member removal; never saves a library.

var ECObjects: TInterfaceList;

function ECIdentity(Obj: IDispatch): String;
var I: Integer;
begin
    I := ECObjects.IndexOf(Obj);
    if I < 0 then I := ECObjects.Add(Obj);
    Result := IntToStr(I);
end;

function ECDefaultText(Value: String): Boolean;
begin
    Result := (Value = '') or (Value = '*') or (Value = '?');
end;

function ECSchChildren(Comp: ISch_Component; Blockers: TStringList): Integer;
var Iter: ISch_Iterator; Obj: ISch_GraphicalObject; DefaultID, CommentID, ID: String;
begin
    Result := 0;
    DefaultID := ECIdentity(Comp.Designator); CommentID := ECIdentity(Comp.Comment);
    Iter := Comp.SchIterator_Create;
    try
        Obj := Iter.FirstSchObject;
        while Obj <> nil do
        begin
            ReportProgress;
            Result := Result + 1;
            ID := ECIdentity(Obj);
            if (ID <> DefaultID) and (ID <> CommentID) then
                Blockers.Add('child object type ' + IntToStr(Obj.ObjectId));
            Obj := Iter.NextSchObject;
        end;
    finally Comp.SchIterator_Destroy(Iter); end;
end;

function ECPcbChildren(Comp: IPCB_LibComponent): Integer;
var Iter: IPCB_GroupIterator; Obj: IPCB_Primitive;
begin
    Result := 0;
    Iter := Comp.GroupIterator_Create;
    try
        Obj := Iter.FirstPCBObject;
        while Obj <> nil do
        begin
            ReportProgress; Result := Result + 1;
            Obj := Iter.NextPCBObject;
        end;
    finally Comp.GroupIterator_Destroy(Iter); end;
end;

function ECSchAssessment(Comp: ISch_Component; Blockers: TStringList): String;
var Props: TStringList; Count: Integer;
begin
    Props := TStringList.Create;
    try
        Blockers.Clear;
        Count := ECSchChildren(Comp,Blockers);
        if Comp.ComponentDescription <> '' then Blockers.Add('description is set');
        if not ECDefaultText(Comp.Designator.Text) then Blockers.Add('designator template is set');
        if not ECDefaultText(Comp.Comment.Text) then Blockers.Add('Comment is set');
        if Comp.PartCount <> 1 then Blockers.Add('multipart symbol');
        if Comp.DisplayModeCount <> 1 then Blockers.Add('alternate display modes');
        if Comp.ComponentKind <> eComponentKind_Standard then Blockers.Add('component kind is set');
        if Comp.DesignItemId <> '' then Blockers.Add('design item ID is set');
        if not ECDefaultText(Comp.LibraryIdentifier) and (Comp.LibraryIdentifier <> Comp.LibReference) then Blockers.Add('library identifier is set');
        AddJSONProperty(Props,'library_identifier',Comp.LibraryIdentifier);
        AddJSONProperty(Props,'design_item_id',Comp.DesignItemId);
        AddJSONInteger(Props,'child_count',Count);
        AddJSONInteger(Props,'display_mode_count',Comp.DisplayModeCount);
        Props.Add('"metadata":' + LibraryComponentSnapshot(Comp));
        AddJSONBoolean(Props,'empty',Blockers.Count=0);
        // Blocker strings are fixed ASCII messages, including native type numbers.
        Props.Add('"blockers":' + ECStringArray(Blockers));
        Result := BuildJSONObject(Props);
    finally Props.Free; end;
end;

function ECStringArray(Values: TStringList): String;
var I: Integer; Items, Props: TStringList;
begin
    Items := TStringList.Create; Props := TStringList.Create;
    try
        for I := 0 to Values.Count-1 do
        begin
            Props.Clear; AddJSONProperty(Props,'value',Values[I]);
            // Extract the escaped string value from this one-property object.
            Items.Add(Copy(Props[0],Pos(':',Props[0])+1,Length(Props[0])));
        end;
        Result := BuildJSONArray(Items);
    finally Items.Free; Props.Free; end;
end;

function ECPcbAssessment(Comp: IPCB_LibComponent; Blockers: TStringList): String;
var Props: TStringList; Count: Integer;
begin
    Props := TStringList.Create;
    try
        Blockers.Clear;
        Count := ECPcbChildren(Comp);
        if Count <> 0 then Blockers.Add('footprint has child objects');
        if Comp.Description <> '' then Blockers.Add('description is set');
        if Comp.Height <> 0 then Blockers.Add('footprint height is set');
        AddJSONInteger(Props,'child_count',Count);
        AddJSONProperty(Props,'description',Comp.Description);
        AddJSONNumber(Props,'height_mm',FootprintHeightMM(Comp));
        AddJSONBoolean(Props,'empty',Blockers.Count=0);
        Props.Add('"blockers":' + ECStringArray(Blockers));
        Result := BuildJSONObject(Props);
    finally Props.Free; end;
end;

function ECSchInventory(Lib: ISch_Lib; Name: String; var Target, Other: ISch_Component; var Matches: Integer): String;
var Iter: ISch_Iterator; Comp: ISch_Component; Items, Props, Blockers: TStringList;
begin
    Items := TStringList.Create; Props := TStringList.Create; Blockers := TStringList.Create;
    Target := nil; Other := nil; Matches := 0;
    Iter := Lib.SchLibIterator_Create;
    Iter.AddFilter_ObjectSet(MkSet(eSchComponent));
    try
        Comp := Iter.FirstSchObject;
        while Comp <> nil do
        begin
            ReportProgress; Props.Clear; Blockers.Clear;
            AddJSONProperty(Props,'name',Comp.LibReference); AddJSONProperty(Props,'_id',ECIdentity(Comp));
            AddJSONInteger(Props,'child_count',ECSchChildren(Comp,Blockers));
            Props.Add('"metadata":' + LibraryComponentSnapshot(Comp));
            Items.Add(BuildJSONObject(Props));
            if SameText(Comp.LibReference,Name) then begin Target := Comp; Matches := Matches+1; end
            else if Other = nil then Other := Comp;
            Comp := Iter.NextSchObject;
        end;
        Result := BuildJSONArray(Items);
    finally Lib.SchIterator_Destroy(Iter); Items.Free; Props.Free; Blockers.Free; end;
end;

function ECPcbInventory(Lib: IPCB_Library; Name: String; var Target, Other: IPCB_LibComponent; var Matches: Integer): String;
var I: Integer; Comp: IPCB_LibComponent; Items, Props: TStringList;
begin
    Items := TStringList.Create; Props := TStringList.Create;
    Target := nil; Other := nil; Matches := 0;
    try
        for I := 0 to Lib.ComponentCount-1 do
        begin
            ReportProgress; Props.Clear; Comp := Lib.GetComponent(I);
            AddJSONProperty(Props,'name',Comp.Name); AddJSONProperty(Props,'_id',ECIdentity(Comp));
            AddJSONInteger(Props,'child_count',ECPcbChildren(Comp));
            AddJSONProperty(Props,'description',Comp.Description); AddJSONInteger(Props,'height',Comp.Height);
            Items.Add(BuildJSONObject(Props));
            if SameText(Comp.Name,Name) then begin Target := Comp; Matches := Matches+1; end
            else if Other = nil then Other := Comp;
        end;
        Result := BuildJSONArray(Items);
    finally Items.Free; Props.Free; end;
end;

function ExecuteRemoveEmptyLibraryComponent(Request: TStringList): String;
var
    Spec, Props, Blockers: TStringList;
    Doc: IServerDocument;
    SchLib: ISch_Lib; SchComp, SchOther: ISch_Component;
    PcbLib: IPCB_Library; PcbComp, PcbOther: IPCB_LibComponent;
    Path, Name, Kind, SpecPath, ErrorText, BeforeJSON, AfterJSON, Assessment: String;
    I, Matches: Integer;
    DryRun, Found, Removable, Started, Removed, Editing, WasModified, IsSch, HaveInventory: Boolean;
begin
    Spec := TStringList.Create; Props := TStringList.Create; Blockers := TStringList.Create;
    ECObjects := TInterfaceList.Create;
    Doc := nil; SchLib := nil; PcbLib := nil;
    Path := ''; Name := ''; ErrorText := ''; BeforeJSON := '[]'; AfterJSON := '[]'; Assessment := '{}';
    DryRun := False; Found := False; Removable := False; Started := False; Removed := False;
    Editing := False; WasModified := False; IsSch := False; HaveInventory := False;
    try
      try
        repeat
            SpecPath := '';
            for I := 0 to Request.Count-1 do if Pos('"spec_file"',Request[I])>0 then SpecPath := JSONLineValue(Request[I]);
            if not FileExists(SpecPath) then begin ErrorText := 'Spec file not found'; Break; end;
            Spec.LoadFromFile(SpecPath);
            Path := EditValue(Spec,'library_path'); Name := EditValue(Spec,'component_name');
            DryRun := Spec.Values['dry_run']='1'; Kind := DocumentKindForPath(Path);
            if (Name='') or ((Kind<>'SCHLIB') and (Kind<>'PCBLIB')) then begin ErrorText := 'Expected a named SchLib or PcbLib member'; Break; end;
            if (FindOpenDocument(Path)=nil) and not FileExists(Path) then begin ErrorText := 'Library is neither saved nor open'; Break; end;
            Doc := OpenLibraryDocument(Kind,Path);
            if Doc=nil then begin ErrorText := 'Cannot open a unique library buffer'; Break; end;
            if (NormalizedPath(Doc.FileName)<>NormalizedPath(Path)) or (UpperCase(Doc.Kind)<>Kind) then begin ErrorText := 'Library identity mismatch'; Break; end;
            WasModified := Doc.Modified; IsSch := Kind='SCHLIB';
            if IsSch then
            begin
                SchLib := SchServer.GetSchDocumentByPath(Path);
                if SchLib=nil then begin ErrorText := 'SchLib unavailable'; Break; end;
                if (SchLib.ObjectId<>33) or (NormalizedPath(SchLib.DocumentName)<>NormalizedPath(Path)) then begin ErrorText := 'SchLib identity mismatch'; Break; end;
                if SchServer.ProcessControl.ProcessDepth<>0 then begin ErrorText := 'Schematic transaction is active'; Break; end;
                BeforeJSON := ECSchInventory(SchLib,Name,SchComp,SchOther,Matches); HaveInventory := True;
                if Matches>1 then begin ErrorText := 'Component name is ambiguous'; Break; end;
                Found := Matches=1;
                if not Found then Break;
                SchLib.CurrentSchComponent := SchComp; SchComp := SchLib.CurrentSchComponent;
                if SchComp=nil then begin ErrorText := 'Focused symbol unavailable'; Break; end;
                if not SameText(SchComp.LibReference,Name) then begin ErrorText := 'Focused symbol mismatch'; Break; end;
                Assessment := ECSchAssessment(SchComp,Blockers);
                Removable := (Blockers.Count=0) and (SchOther<>nil);
                if DryRun then Break;
                if Blockers.Count<>0 then begin ErrorText := 'Component contains data; removal refused'; Break; end;
                if SchOther=nil then begin ErrorText := 'Refusing to remove the last library component; create its replacement first'; Break; end;
                // Switch away, flush the editor buffer and recheck the stored target.
                SchLib.CurrentSchComponent := SchOther;
                BeforeJSON := ECSchInventory(SchLib,Name,SchComp,SchOther,Matches);
                if Matches<>1 then begin ErrorText := 'Component changed during preflight'; Break; end;
                Assessment := ECSchAssessment(SchComp,Blockers);
                if Blockers.Count<>0 then begin ErrorText := 'Component contains data after editor flush'; Removable := False; Break; end;
                SchServer.ProcessControl.PreProcess(SchLib,''); Editing := True;
                Started := True; SchLib.RemoveSchComponent(SchComp); Removed := True;
            end
            else
            begin
                PcbLib := GetPcbLibSafe(0);
                if PcbLib=nil then begin ErrorText := 'PcbLib unavailable'; Break; end;
                if NormalizedPath(PcbLib.Board.FileName)<>NormalizedPath(Path) then begin ErrorText := 'PcbLib identity mismatch'; Break; end;
                BeforeJSON := ECPcbInventory(PcbLib,Name,PcbComp,PcbOther,Matches); HaveInventory := True;
                if Matches>1 then begin ErrorText := 'Component name is ambiguous'; Break; end;
                Found := Matches=1;
                if not Found then Break;
                Assessment := ECPcbAssessment(PcbComp,Blockers);
                Removable := (Blockers.Count=0) and (PcbOther<>nil);
                if DryRun then Break;
                if Blockers.Count<>0 then begin ErrorText := 'Component contains data; removal refused'; Break; end;
                if PcbOther=nil then begin ErrorText := 'Refusing to remove the last library component; create its replacement first'; Break; end;
                PcbLib.CurrentComponent := PcbOther;
                BeforeJSON := ECPcbInventory(PcbLib,Name,PcbComp,PcbOther,Matches);
                if Matches<>1 then begin ErrorText := 'Component changed during preflight'; Break; end;
                Assessment := ECPcbAssessment(PcbComp,Blockers);
                if Blockers.Count<>0 then begin ErrorText := 'Component contains data after editor flush'; Removable := False; Break; end;
                PCBServer.PreProcess; Editing := True;
                Started := True; PcbLib.RemoveComponent(PcbComp); Removed := True;
            end;
        until True;
      except ErrorText := ExceptionMessage; end;
      if Editing then
      begin
          if IsSch then SchServer.ProcessControl.PostProcess(SchLib,'') else PCBServer.PostProcess;
          Editing := False;
      end;
      if Started then
      begin
          if not IsSch then PcbLib.Board.SetState_DocumentHasChanged;
          MarkDocumentModified(Path);
          if IsSch then SchLib.GraphicallyInvalidate else PcbLib.Board.ViewManager_FullUpdate;
      end;
      AfterJSON := BeforeJSON;
      if HaveInventory then
      begin
          try
              if IsSch then AfterJSON := ECSchInventory(SchLib,Name,SchComp,SchOther,Matches)
              else AfterJSON := ECPcbInventory(PcbLib,Name,PcbComp,PcbOther,Matches);
              if Removed and (Matches<>0) then ErrorText := 'Removed component is still present';
          except ErrorText := 'After inventory failed: '+ExceptionMessage; end;
      end;
      AddJSONBoolean(Props,'success',ErrorText=''); AddJSONBoolean(Props,'saved',False);
      AddJSONProperty(Props,'library_path',Path); AddJSONProperty(Props,'component_name',Name); AddJSONProperty(Props,'document_kind',Kind);
      AddJSONBoolean(Props,'dry_run',DryRun); AddJSONBoolean(Props,'found',Found); AddJSONBoolean(Props,'removable',Removable);
      AddJSONBoolean(Props,'removed',Removed); AddJSONBoolean(Props,'changed',Removed);
      AddJSONBoolean(Props,'was_modified',WasModified);
      if Doc<>nil then AddJSONBoolean(Props,'is_modified',Doc.Modified);
      AddJSONBoolean(Props,'may_have_partial_changes',(ErrorText<>'') and Started);
      if ErrorText<>'' then AddJSONProperty(Props,'error',ErrorText);
      Props.Add('"assessment":'+Assessment); Props.Add('"components_before":'+BeforeJSON); Props.Add('"components_after":'+AfterJSON);
      Result := BuildJSONObject(Props);
    finally
        if Editing then
            if IsSch then SchServer.ProcessControl.PostProcess(SchLib,'') else PCBServer.PostProcess;
        Spec.Free; Props.Free; Blockers.Free; ECObjects.Free;
    end;
end;
