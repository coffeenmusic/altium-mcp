// Read-only paged metadata. Never selects a member, edits objects or saves.

function LCRequested(Spec: TStringList; Name: String): Boolean;
var I,N: Integer;
begin
    N := StrToInt(Spec.Values['name_count']); Result := N=0;
    for I := 0 to N-1 do if SameText(EditValue(Spec,'name.'+IntToStr(I)),Name) then Result := True;
end;

function LCPins(Comp: ISch_Component; var Count: Integer): String;
var Iter: ISch_Iterator; Pin: ISch_Pin; Items,S: TStringList;
begin
    Items := TStringList.Create; Count := 0;
    Iter := Comp.SchIterator_Create; Iter.AddFilter_ObjectSet(MkSet(ePin));
    try
        Pin := Iter.FirstSchObject;
        while Pin<>nil do
        begin
            // Batch reads do not need cross-snapshot interface identities.
            UPObjects.Clear;
            ReportProgress; S := UPState(Pin);
            try Items.Add(UPStateJSON(S)); finally S.Free; end;
            Count := Count+1; Pin := Iter.NextSchObject;
        end;
        Result := BuildJSONArray(Items);
    finally Comp.SchIterator_Destroy(Iter); Items.Free; end;
end;

function LCSchRow(Lib: ISch_Lib; Name: String; Index: Integer; Spec: TStringList): String;
var Comp: ISch_Component; Props,Params,Entry: TStringList; Count: Integer; Pins: String;
    Iter: ISch_Iterator; Param: ISch_Parameter;
begin
    Comp := FindLibSymbol(Lib,Name);
    if Lib.CurrentSchComponent<>nil then
        if SameText(Lib.CurrentSchComponent.LibReference,Name) then Comp := Lib.CurrentSchComponent;
    Props := TStringList.Create;
    try
        AddJSONInteger(Props,'library_index',Index); AddJSONProperty(Props,'component_name',Comp.LibReference);
        Props.Add('"symbol_data":'+LibraryComponentSnapshot(Comp));
        AddJSONInteger(Props,'display_mode_count',Comp.DisplayModeCount);
        AddJSONInteger(Props,'current_part_id',Comp.CurrentPartID); AddJSONInteger(Props,'display_mode',Comp.DisplayMode);
        if Spec.Values['validation']='1' then
        begin
            Params := TStringList.Create; Entry := TStringList.Create;
            Iter := Comp.SchIterator_Create; Iter.AddFilter_ObjectSet(MkSet(eParameter));
            try
                Param := Iter.FirstSchObject;
                while Param<>nil do
                begin
                    if IsOwnParameter(Param,Comp) then
                    begin
                        Entry.Clear; AddJSONProperty(Entry,'name',Param.Name); AddJSONProperty(Entry,'value',Param.Text);
                        Params.Add(BuildJSONObject(Entry));
                    end;
                    Param := Iter.NextSchObject;
                end;
                Props.Add('"parameter_records":'+BuildJSONArray(Params));
            finally Comp.SchIterator_Destroy(Iter); Params.Free; Entry.Free; end;
        end;
        if Spec.Values['field.pins']='1' then
        begin
            Pins := LCPins(Comp,Count); Props.Add('"pins":'+Pins); AddJSONInteger(Props,'pin_count',Count);
        end;
        Result := BuildJSONObject(Props);
    finally Props.Free; end;
end;

function LCValidationPad(Pad: IPCB_Pad; OX,OY: Integer): String;
var P,Sides,Side: TStringList; I: Integer; ShapeName: String; XSize,YSize: Double; Shape: Integer;
begin
    P := TStringList.Create; Sides := TStringList.Create; Side := TStringList.Create;
    try
        AddJSONProperty(P,'name',Pad.Name); AddJSONProperty(P,'layer',PCBLayerName(Pad.Layer));
        AddJSONNumber(P,'x_mm',CoordToMils(Pad.X-OX)*0.0254); AddJSONNumber(P,'y_mm',CoordToMils(Pad.Y-OY)*0.0254);
        AddJSONNumber(P,'rotation',Pad.Rotation); AddJSONBoolean(P,'plated',Pad.Plated);
        AddJSONInteger(P,'mode',Pad.Mode); AddJSONNumber(P,'hole_size_mm',CoordToMils(Pad.HoleSize)*0.0254);
        AddJSONInteger(P,'hole_type',Pad.HoleType); AddJSONNumber(P,'hole_width_mm',CoordToMils(Pad.HoleWidth)*0.0254);
        AddJSONNumber(P,'hole_rotation',Pad.HoleRotation);
        AddJSONBoolean(P,'is_testpoint_top',Pad.IsTestpoint_Top); AddJSONBoolean(P,'is_testpoint_bottom',Pad.IsTestpoint_Bottom);
        AddJSONBoolean(P,'tenting_top',Pad.IsTenting_Top); AddJSONBoolean(P,'tenting_bottom',Pad.IsTenting_Bottom);
        P.Add('"settings":'+FootprintEditPadSnapshot(Pad));
        // Top/middle/bottom only. Full stacks are explicitly deferred by Python.
        for I := 0 to 2 do
        begin
            Side.Clear;
            XSize := Pad.TopXSize; YSize := Pad.TopYSize; Shape := Pad.TopShape;
            if Pad.Mode<>ePadMode_Simple then
            begin
                if I=1 then begin XSize := Pad.MidXSize; YSize := Pad.MidYSize; Shape := Pad.MidShape; end;
                if I=2 then begin XSize := Pad.BotXSize; YSize := Pad.BotYSize; Shape := Pad.BotShape; end;
            end;
            ShapeName := 'unsupported';
            if Shape=eRounded then ShapeName := 'round';
            if Shape=eRectangular then ShapeName := 'rectangle';
            if Shape=eOctagonal then ShapeName := 'octagonal';
            if Shape=eRoundedRectangular then ShapeName := 'rounded_rectangle';
            AddJSONProperty(Side,'shape',ShapeName); AddJSONInteger(Side,'shape_code',Shape);
            AddJSONNumber(Side,'width_mm',CoordToMils(XSize)*0.0254); AddJSONNumber(Side,'height_mm',CoordToMils(YSize)*0.0254);
            Sides.Add(BuildJSONObject(Side));
        end;
        P.Add('"stack":'+BuildJSONArray(Sides)); Result := BuildJSONObject(P);
    finally P.Free; Sides.Free; Side.Free; end;
end;

function LCPcbRow(Lib: IPCB_Library; Comp: IPCB_LibComponent; Index: Integer; Spec: TStringList): String;
var Props,Properties,Pads,PadProps,Details: TStringList; Iter: IPCB_GroupIterator; Prim: IPCB_Primitive; Pad: IPCB_Pad;
    Count,OtherCopper,OtherApertures,OX,OY: Integer;
begin
    Props := TStringList.Create; Properties := TStringList.Create; Pads := TStringList.Create; Details := TStringList.Create;
    Count := 0; OtherCopper := 0; OtherApertures := 0; OX := 0; OY := 0;
    if Lib.CurrentComponent<>nil then
        if Lib.CurrentComponent.I_ObjectAddress=Comp.I_ObjectAddress then begin OX := Lib.Board.XOrigin; OY := Lib.Board.YOrigin; end;
    try
        AddJSONInteger(Props,'library_index',Index); AddJSONProperty(Props,'component_name',Comp.Name);
        if Spec.Values['field.properties']='1' then
        begin
            AddJSONProperty(Properties,'description',Comp.Description); AddJSONNumber(Properties,'height_mm',FootprintHeightMM(Comp));
            Props.Add('"properties":'+BuildJSONObject(Properties));
        end;
        if Spec.Values['field.pads']='1' then
        begin
            Iter := Comp.GroupIterator_Create;
            try
                Prim := Iter.FirstPCBObject;
                while Prim<>nil do
                begin
                    ReportProgress;
                    if Prim.ObjectId=ePadObject then
                    begin
                        Pad := Prim; PadProps := TStringList.Create;
                        try
                            AddJSONProperty(PadProps,'name',Pad.Name); AddJSONProperty(PadProps,'layer',PCBLayerName(Pad.Layer));
                            AddJSONBoolean(PadProps,'is_testpoint_top',Pad.IsTestpoint_Top);
                            AddJSONBoolean(PadProps,'is_testpoint_bottom',Pad.IsTestpoint_Bottom);
                            Pads.Add(BuildJSONObject(PadProps)); Count := Count+1;
                            if Spec.Values['validation']='1' then Details.Add(LCValidationPad(Pad,OX,OY));
                        finally PadProps.Free; end;
                    end
                    else if Spec.Values['validation']='1' then
                        if (Prim.ObjectId<>eComponentBodyObject) and not Prim.IsKeepout then
                            if ((Prim.Layer>=eTopLayer) and (Prim.Layer<=eBottomLayer)) or (Prim.Layer=eMultiLayer) or
                               (Prim.Layer=eTopSolder) or (Prim.Layer=eBottomSolder) or (Prim.Layer=eTopPaste) or (Prim.Layer=eBottomPaste) then
                            begin
                                OtherCopper := OtherCopper+1;
                                if (Prim.Layer=eTopSolder) or (Prim.Layer=eBottomSolder) or (Prim.Layer=eTopPaste) or (Prim.Layer=eBottomPaste) then OtherApertures := OtherApertures+1;
                            end;
                    Prim := Iter.NextPCBObject;
                end;
            finally Comp.GroupIterator_Destroy(Iter); end;
            Props.Add('"pads":'+BuildJSONArray(Pads)); AddJSONInteger(Props,'pad_count',Count);
            if Spec.Values['validation']='1' then
            begin
                Props.Add('"pad_details":'+BuildJSONArray(Details)); AddJSONInteger(Props,'other_copper_count',OtherCopper);
                AddJSONInteger(Props,'other_aperture_count',OtherApertures);
            end;
        end;
        Result := BuildJSONObject(Props);
    finally Props.Free; Properties.Free; Pads.Free; Details.Free; end;
end;

function ExecuteGetLibraryComponentsData(Request: TStringList): String;
var
    Spec,Props,Libraries,Rows,Errors,ComponentErrors,Names,SortedNames,Info,Issue,FoundNames: TStringList;
    Doc,PreviousDoc: IServerDocument; Lib: ISch_Lib; PcbLib: IPCB_Library; Comp: ISch_Component;
    Iter: ISch_Iterator;
    SpecPath,Path,Kind,Name,ErrorText,LibraryError,BeforeName,AfterName,Item,PreviousPath,ActivePath: String;
    I,J,K,Matches,N,Offset,Limit,Total,PageCount,MatchedHere,BeforePart,BeforeDisplay,AfterPart,AfterDisplay: Integer;
    WasModified,AlreadyOpen,Restored: Boolean;
begin
    Spec := TStringList.Create; Props := TStringList.Create; Libraries := TStringList.Create;
    Rows := TStringList.Create; Errors := TStringList.Create; ComponentErrors := TStringList.Create;
    Names := TStringList.Create; SortedNames := TStringList.Create; Info := TStringList.Create;
    Issue := TStringList.Create; FoundNames := TStringList.Create; UPObjects := TInterfaceList.Create;
    Total := 0; PageCount := 0; ErrorText := ''; PreviousPath := ''; ActivePath := ''; PreviousDoc := nil; Restored := False;
    try
      try
        repeat
            SpecPath := '';
            for I := 0 to Request.Count-1 do if Pos('"spec_file"',Request[I])>0 then SpecPath := JSONLineValue(Request[I]);
            if not FileExists(SpecPath) then begin ErrorText := 'Spec file not found'; Break; end;
            Spec.LoadFromFile(SpecPath); N := StrToInt(Spec.Values['path_count']);
            Offset := StrToInt(Spec.Values['offset']); Limit := StrToInt(Spec.Values['limit']);
            if SchServer<>nil then
                if SchServer.ProcessControl.ProcessDepth<>0 then begin ErrorText := 'Schematic transaction is active; batch read refused'; Break; end;
            if Client.CurrentView<>nil then PreviousDoc := Client.CurrentView.OwnerDocument;
            if PreviousDoc<>nil then PreviousPath := PreviousDoc.FileName;
            for I := 0 to N-1 do
            begin
                ReportProgress; Info.Clear; Names.Clear; SortedNames.Clear; Doc := nil; Lib := nil; PcbLib := nil;
                Path := EditValue(Spec,'path.'+IntToStr(I)); Kind := DocumentKindForPath(Path);
                LibraryError := ''; BeforeName := ''; AfterName := ''; BeforePart := -1; BeforeDisplay := -1;
                MatchedHere := 0; WasModified := False;
                AddJSONInteger(Info,'index',I); AddJSONProperty(Info,'library_path',Path); AddJSONProperty(Info,'kind',Kind);
                try
                  repeat
                    if (Kind<>'SCHLIB') and (Kind<>'PCBLIB') then begin LibraryError := 'Expected SchLib or PcbLib'; Break; end;
                    if (FindOpenDocument(Path)=nil) and not FileExists(Path) then begin LibraryError := 'Library is neither saved nor open'; Break; end;
                    AlreadyOpen := FindOpenDocument(Path)<>nil;
                    AddJSONBoolean(Info,'already_open',AlreadyOpen);
                    Doc := OpenLibraryDocument(Kind,Path);
                    if Doc=nil then begin LibraryError := 'Cannot open a unique library buffer'; Break; end;
                    AddJSONBoolean(Info,'opened',not AlreadyOpen);
                    WasModified := Doc.Modified;
                    if (NormalizedPath(Doc.FileName)<>NormalizedPath(Path)) or (UpperCase(Doc.Kind)<>Kind) then begin LibraryError := 'Library identity mismatch'; Break; end;
                    if Kind='SCHLIB' then
                    begin
                        Lib := SchServer.GetSchDocumentByPath(Path);
                        if Lib=nil then begin LibraryError := 'SchLib unavailable'; Break; end;
                        if (Lib.ObjectId<>33) or (NormalizedPath(Lib.DocumentName)<>NormalizedPath(Path)) then begin LibraryError := 'SchLib identity mismatch'; Break; end;
                        if Lib.CurrentSchComponent<>nil then
                        begin
                            BeforeName := Lib.CurrentSchComponent.LibReference;
                            BeforePart := Lib.GetState_CurrentSchComponentPartId; BeforeDisplay := Lib.GetState_CurrentSchComponentDisplayMode;
                        end;
                        Iter := Lib.SchLibIterator_Create; Iter.AddFilter_ObjectSet(MkSet(eSchComponent));
                        try
                            Comp := Iter.FirstSchObject;
                            while Comp<>nil do begin ReportProgress; Names.Add(Comp.LibReference); Comp := Iter.NextSchObject; end;
                        finally Lib.SchIterator_Destroy(Iter); end;
                    end
                    else
                    begin
                        PcbLib := GetPcbLibSafe(0);
                        if PcbLib=nil then begin LibraryError := 'PcbLib unavailable'; Break; end;
                        if NormalizedPath(PcbLib.Board.FileName)<>NormalizedPath(Path) then begin LibraryError := 'PcbLib identity mismatch'; Break; end;
                        if PcbLib.CurrentComponent<>nil then BeforeName := PcbLib.CurrentComponent.Name;
                        for J := 0 to PcbLib.ComponentCount-1 do Names.Add(PcbLib.GetComponent(J).Name);
                    end;
                    SortedNames.Assign(Names); SortedNames.Sort;
                    for J := 0 to SortedNames.Count-1 do
                    begin
                        ReportProgress; Name := SortedNames[J];
                        if J>0 then if SameText(Name,SortedNames[J-1]) then Continue;
                        if not LCRequested(Spec,Name) then Continue;
                        if FoundNames.IndexOf(Name)<0 then FoundNames.Add(Name);
                        MatchedHere := MatchedHere+1; Total := Total+1;
                        if (Total<=Offset) or (PageCount>=Limit) then Continue;
                        PageCount := PageCount+1; Matches := 0;
                        for K := 0 to Names.Count-1 do if SameText(Names[K],Name) then Matches := Matches+1;
                        Item := '';
                        try
                            if Matches<>1 then Item := 'Component name is ambiguous'
                            else
                            begin
                                if Kind='SCHLIB' then Rows.Add(LCSchRow(Lib,Name,I,Spec))
                                else Rows.Add(LCPcbRow(PcbLib,PcbLib.GetComponent(Names.IndexOf(Name)),I,Spec));
                            end;
                        except Item := ExceptionMessage; end;
                        if Item<>'' then
                        begin
                            Issue.Clear; AddJSONInteger(Issue,'library_index',I); AddJSONProperty(Issue,'component_name',Name);
                            AddJSONProperty(Issue,'error',Item); ComponentErrors.Add(BuildJSONObject(Issue));
                        end;
                    end;
                  until True;
                except LibraryError := ExceptionMessage; end;
                if Doc<>nil then
                begin
                    if Lib<>nil then
                    begin
                        AfterPart := -1; AfterDisplay := -1;
                        if Lib.CurrentSchComponent<>nil then
                        begin
                            AfterName := Lib.CurrentSchComponent.LibReference;
                            AfterPart := Lib.GetState_CurrentSchComponentPartId; AfterDisplay := Lib.GetState_CurrentSchComponentDisplayMode;
                        end;
                        AddJSONInteger(Info,'part_before',BeforePart); AddJSONInteger(Info,'part_after',AfterPart);
                        AddJSONInteger(Info,'display_before',BeforeDisplay); AddJSONInteger(Info,'display_after',AfterDisplay);
                    end;
                    if PcbLib<>nil then if PcbLib.CurrentComponent<>nil then AfterName := PcbLib.CurrentComponent.Name;
                    AddJSONBoolean(Info,'was_modified',WasModified); AddJSONBoolean(Info,'is_modified',Doc.Modified);
                    AddJSONProperty(Info,'active_before',BeforeName); AddJSONProperty(Info,'active_after',AfterName);
                    if (LibraryError='') and ((Doc.Modified<>WasModified) or (AfterName<>BeforeName)) then LibraryError := 'Batch read changed buffer or selection';
                end;
                AddJSONInteger(Info,'component_count',Names.Count); AddJSONInteger(Info,'matched_count',MatchedHere);
                AddJSONBoolean(Info,'success',LibraryError='');
                if LibraryError<>'' then
                begin
                    AddJSONProperty(Info,'error',LibraryError); Issue.Clear;
                    AddJSONInteger(Issue,'library_index',I); AddJSONProperty(Issue,'error',LibraryError); Errors.Add(BuildJSONObject(Issue));
                end;
                Libraries.Add(BuildJSONObject(Info));
            end;
        until True;
      except ErrorText := ExceptionMessage; end;
      try
          if PreviousDoc<>nil then Client.ShowDocument(PreviousDoc);
          if Client.CurrentView<>nil then
              if Client.CurrentView.OwnerDocument<>nil then ActivePath := Client.CurrentView.OwnerDocument.FileName;
          Restored := (PreviousPath='') or (NormalizedPath(PreviousPath)=NormalizedPath(ActivePath));
          if not Restored then ErrorText := 'Previous document view was not restored';
      except ErrorText := 'View restore failed: '+ExceptionMessage; end;
      AddJSONBoolean(Props,'success',(ErrorText='') and (Errors.Count=0) and (ComponentErrors.Count=0));
      AddJSONBoolean(Props,'saved',False); AddJSONInteger(Props,'matched_count',Total); AddJSONInteger(Props,'page_count',PageCount);
      AddJSONProperty(Props,'previous_document_path',PreviousPath); AddJSONProperty(Props,'active_document_path',ActivePath);
      AddJSONBoolean(Props,'view_restored',Restored);
      if ErrorText<>'' then AddJSONProperty(Props,'error',ErrorText);
      Props.Add('"libraries":'+BuildJSONArray(Libraries)); Props.Add('"rows":'+BuildJSONArray(Rows));
      Props.Add('"library_errors":'+BuildJSONArray(Errors)); Props.Add('"component_errors":'+BuildJSONArray(ComponentErrors));
      Props.Add('"found_names":'+ECStringArray(FoundNames)); Result := BuildJSONObject(Props);
    finally
        Spec.Free; Props.Free; Libraries.Free; Rows.Free; Errors.Free; ComponentErrors.Free;
        Names.Free; SortedNames.Free; Info.Free; Issue.Free; FoundNames.Free; UPObjects.Free;
    end;
end;
