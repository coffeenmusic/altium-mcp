// Exact-library pad overrides and layer-only graphic edits. Never saves.

function FootprintEditLayer(Name: String): Integer;
begin
    Result := PCBLayerFromName(Name);
end;

function FootprintEditValue(Spec: TStringList; Key: String): String;
begin
    Result := JSONUnescape(Spec.Values[Key]);
end;

function FootprintEditNear(Spec: TStringList; Key: String; Actual: Double): Boolean;
begin
    // Getter values are rounded decimal mils. One internal coordinate unit
    // of tolerance handles their conversion without matching nearby geometry.
    Result := Abs(Actual - SafeStrToFloat(Spec.Values[Key])) <= 0.0001;
end;

function FootprintEditMatches(Prim: IPCB_Primitive; Spec: TStringList; Prefix: String;
                              OX, OY: Integer): Boolean;
var
    Kind: String;
begin
    Result := False;
    Kind := Spec.Values[Prefix + 'type'];
    if Prim.Layer <> FootprintEditLayer(FootprintEditValue(Spec, Prefix + 'from_layer')) then Exit;
    if (Kind = 'track') and (Prim.ObjectId = eTrackObject) then
        Result := FootprintEditNear(Spec, Prefix + 'x1', CoordToMils(Prim.x1 - OX)) and
                  FootprintEditNear(Spec, Prefix + 'y1', CoordToMils(Prim.y1 - OY)) and
                  FootprintEditNear(Spec, Prefix + 'x2', CoordToMils(Prim.x2 - OX)) and
                  FootprintEditNear(Spec, Prefix + 'y2', CoordToMils(Prim.y2 - OY)) and
                  FootprintEditNear(Spec, Prefix + 'width', CoordToMils(Prim.Width))
    else if (Kind = 'arc') and (Prim.ObjectId = eArcObject) then
        Result := FootprintEditNear(Spec, Prefix + 'cx', CoordToMils(Prim.XCenter - OX)) and
                  FootprintEditNear(Spec, Prefix + 'cy', CoordToMils(Prim.YCenter - OY)) and
                  FootprintEditNear(Spec, Prefix + 'radius', CoordToMils(Prim.Radius)) and
                  FootprintEditNear(Spec, Prefix + 'start_angle', Prim.StartAngle) and
                  FootprintEditNear(Spec, Prefix + 'end_angle', Prim.EndAngle) and
                  FootprintEditNear(Spec, Prefix + 'width', CoordToMils(Prim.LineWidth))
    else if (Kind = 'fill') and (Prim.ObjectId = eFillObject) then
        Result := FootprintEditNear(Spec, Prefix + 'x1', CoordToMils(Prim.x1Location - OX)) and
                  FootprintEditNear(Spec, Prefix + 'y1', CoordToMils(Prim.y1Location - OY)) and
                  FootprintEditNear(Spec, Prefix + 'x2', CoordToMils(Prim.x2Location - OX)) and
                  FootprintEditNear(Spec, Prefix + 'y2', CoordToMils(Prim.y2Location - OY)) and
                  FootprintEditNear(Spec, Prefix + 'rotation', Prim.Rotation);
end;

function FootprintEditPadSnapshot(Pad: IPCB_Pad): String;
var
    Props: TStringList;
    Cache: IDispatch;
begin
    Props := TStringList.Create;
    try
        Cache := Pad.GetState_Cache;
        AddJSONProperty(Props, 'name', Pad.Name);
        AddJSONInteger(Props, 'mask_mode', Pad.SolderMaskExpansionMode);
        AddJSONInteger(Props, 'paste_mode', Pad.PasteMaskExpansionMode);
        AddJSONNumber(Props, 'mask_expansion_mm', CoordToMils(Pad.SolderMaskExpansion) * 0.0254);
        AddJSONNumber(Props, 'paste_expansion_mm', CoordToMils(Pad.PasteMaskExpansion) * 0.0254);
        AddJSONInteger(Props, 'mask_cache_valid', Cache.SolderMaskExpansionValid);
        AddJSONInteger(Props, 'paste_cache_valid', Cache.PasteMaskExpansionValid);
        Result := BuildJSONObject(Props);
    finally
        Props.Free;
    end;
end;

function FootprintEditPadVerified(Pad: IPCB_Pad; Spec: TStringList): Boolean;
var
    Cache: IDispatch;
begin
    Result := True;
    Cache := Pad.GetState_Cache;
    if Spec.Values['mask_mode'] = '1' then Result := Pad.SolderMaskExpansionMode = 1;
    if Spec.Values['paste_mode'] = '1' then Result := Result and (Pad.PasteMaskExpansionMode = 1);
    if Spec.Values['mask_mode'] = '2' then
        Result := Result and (Pad.SolderMaskExpansionMode = 2) and
                  (Cache.SolderMaskExpansionValid = eCacheManual) and
                  (Abs(Pad.SolderMaskExpansion - MilsToCoord(SafeStrToFloat(Spec.Values['mask_expansion']))) <= 1);
    if Spec.Values['paste_mode'] = '2' then
        Result := Result and (Pad.PasteMaskExpansionMode = 2) and
                  (Cache.PasteMaskExpansionValid = eCacheManual) and
                  (Abs(Pad.PasteMaskExpansion - MilsToCoord(SafeStrToFloat(Spec.Values['paste_expansion']))) <= 1);
end;

function FootprintAPIConstant(Name: String; var Value: Integer): Boolean;
begin
    Result := True;
    if Name = 'eRound' then Value := eRound
    else if Name = 'eRounded' then Value := eRounded
    else if Name = 'eRectangular' then Value := eRectangular
    else if Name = 'eOctagonal' then Value := eOctagonal
    else if Name = 'eRoundedRectangular' then Value := eRoundedRectangular
    else if Name = 'eCacheManual' then Value := eCacheManual
    else if Name = 'eTopLayer' then Value := eTopLayer
    else if Name = 'eBottomLayer' then Value := eBottomLayer
    else if Name = 'eTopOverlay' then Value := eTopOverlay
    else if Name = 'eBottomOverlay' then Value := eBottomOverlay
    else if Name = 'eMechanical13' then Value := eMechanical13
    else if Name = 'eMechanical15' then Value := eMechanical15
    else if Name = 'eMultiLayer' then Value := eMultiLayer
    else if Name = 'eNoLayer' then Value := eNoLayer
    else Result := False;
end;

function FootprintPadPropertiesSnapshot(Pad: IPCB_Pad): String;
var Props: TStringList;
begin
    Props := TStringList.Create;
    try
        AddJSONProperty(Props, 'name', Pad.Name);
        AddJSONBoolean(Props, 'is_testpoint_top', Pad.IsTestpoint_Top);
        AddJSONBoolean(Props, 'is_testpoint_bottom', Pad.IsTestpoint_Bottom);
        AddJSONBoolean(Props, 'locked', not Pad.Moveable);
        AddJSONProperty(Props, 'layer', PCBLayerName(Pad.Layer));
        Props.Add('"mask_settings":' + FootprintEditPadSnapshot(Pad));
        Result := BuildJSONObject(Props);
    finally Props.Free; end;
end;

function FootprintPadPropertiesChanged(Pad: IPCB_Pad; Spec: TStringList): Boolean;
begin
    Result := False;
    if Spec.IndexOfName('is_testpoint_top') >= 0 then
        Result := Pad.IsTestpoint_Top <> (Spec.Values['is_testpoint_top'] = '1');
    if Spec.IndexOfName('is_testpoint_bottom') >= 0 then
        Result := Result or (Pad.IsTestpoint_Bottom <> (Spec.Values['is_testpoint_bottom'] = '1'));
end;

function FootprintPadPropertiesPreflight(Pad: IPCB_Pad; Spec: TStringList): String;
begin
    Result := '';
    if Spec.Values['is_testpoint_top'] = '1' then
        if (Pad.Layer <> eTopLayer) and (Pad.Layer <> eMultiLayer) then
            Result := 'Top testpoint requires a Top Layer or Multi-Layer pad: ' + Pad.Name;
    if Spec.Values['is_testpoint_bottom'] = '1' then
        if (Pad.Layer <> eBottomLayer) and (Pad.Layer <> eMultiLayer) then
            Result := 'Bottom testpoint requires a Bottom Layer or Multi-Layer pad: ' + Pad.Name;
end;

function FootprintHeightMM(Comp: IPCB_LibComponent): Double;
begin
    // CoordToMils rounds to 0.001 mil; retain the native height precision.
    Result := Comp.Height / MilsToCoord(1) * 0.0254;
end;

procedure FootprintPropertySnapshot(Items: TStringList; Comp: IPCB_LibComponent);
begin
    AddJSONNumber(Items, 'height_mm', FootprintHeightMM(Comp));
    AddJSONProperty(Items, 'description', Comp.Description);
end;

function ExecuteFootprintLibraryCommand(Command: String; Request: TStringList): String;
var
    Spec, Props, Addresses, BeforeItems, AfterItems, Constants, Availability, Entry: TStringList;
    PcbLib: IPCB_Library;
    Comp: IPCB_LibComponent;
    Iter: IPCB_GroupIterator;
    Prim: IPCB_Primitive;
    Doc: IServerDocument;
    SpecPath, Path, Name, Prefix, ErrorText, Address, Item, NativeName, NativeError: String;
    I, Count, Matches, OX, OY, Updated, Verified, Value, TargetHeight: Integer;
    HeightMM: Double;
    Editing, IsPad, IsPadProperties, WasModified, HaveProperties, NeedChanges, DoEdit, Available, Resolved: Boolean;
begin
    Spec := TStringList.Create;
    Props := TStringList.Create;
    Addresses := TStringList.Create;
    BeforeItems := TStringList.Create;
    AfterItems := TStringList.Create;
    Constants := TStringList.Create;
    Availability := TStringList.Create;
    Entry := TStringList.Create;
    ErrorText := '';
    Updated := 0;
    Verified := 0;
    Editing := False;
    IsPad := False;
    IsPadProperties := False;
    NeedChanges := False;
    WasModified := False;
    HaveProperties := False;
    Path := '';
    Name := '';
    try
      try
        repeat
            SpecPath := '';
            for I := 0 to Request.Count - 1 do
                if Pos('"spec_file"', Request[I]) > 0 then SpecPath := JSONLineValue(Request[I]);
            if not FileExists(SpecPath) then begin ErrorText := 'Spec file not found'; Break; end;
            Spec.LoadFromFile(SpecPath);
            Count := StrToIntDef(Spec.Values['count'], 0);
            if (Count < 1) or (Count > 1000) then begin ErrorText := 'Invalid edit count'; Break; end;
            if Command = 'get_pcb_api_constants' then
            begin
                for I := 0 to Count - 1 do
                begin
                    Name := FootprintEditValue(Spec, 'name.' + IntToStr(I));
                    NativeName := Name;
                    NativeError := '';
                    Value := 0;
                    Available := False;
                    try
                        Available := FootprintAPIConstant(Name, Value);
                        if not Available then NativeError := 'Unsupported API constant';
                    except NativeError := ExceptionMessage; end;
                    Resolved := Available;
                    // eRound was advertised by this tool, but TShape uses eRounded.
                    if not Resolved and (Name = 'eRound') then
                    begin
                        NativeName := 'eRounded';
                        try Resolved := FootprintAPIConstant(NativeName, Value);
                        except Resolved := False; end;
                    end;
                    Entry.Clear;
                    AddJSONBoolean(Entry, 'available', Available);
                    AddJSONBoolean(Entry, 'resolved', Resolved);
                    AddJSONBoolean(Entry, 'alias', NativeName <> Name);
                    AddJSONProperty(Entry, 'native_name', NativeName);
                    if NativeError <> '' then AddJSONProperty(Entry, 'native_error', NativeError);
                    Availability.Add('"' + JSONEscapeString(Name) + '": ' + BuildJSONObject(Entry));
                    if Resolved then AddJSONInteger(Constants, Name, Value)
                    else ErrorText := 'One or more PCB API constants are unavailable in this Altium version';
                end;
                Props.Add('"constants": ' + BuildJSONObject(Constants));
                Props.Add('"availability": ' + BuildJSONObject(Availability));
                AddJSONBoolean(Props, 'checked_in_running_altium', True);
                Break;
            end;
            IsPadProperties := Command = 'set_footprint_pad_properties';
            IsPad := (Command = 'set_footprint_pad_settings') or IsPadProperties;
            Path := FootprintEditValue(Spec, 'library_path');
            Name := FootprintEditValue(Spec, 'footprint_name');
            if (Path = '') or not FileExists(Path) then begin ErrorText := 'Library file not found'; Break; end;
            Doc := OpenLibraryDocument('PcbLib', Path);
            if Doc = nil then begin ErrorText := 'Cannot open a unique library buffer'; Break; end;
            PcbLib := GetPcbLibSafe(0);
            if PcbLib = nil then begin ErrorText := 'No PCB library is focused'; Break; end;
            if NormalizedPath(PcbLib.Board.FileName) <> NormalizedPath(Path) then
            begin ErrorText := 'Focused library identity mismatch'; Break; end;
            Matches := 0;
            Comp := nil;
            for I := 0 to PcbLib.ComponentCount - 1 do
                if SameText(PcbLib.GetComponent(I).Name, Name) then
                begin Comp := PcbLib.GetComponent(I); Matches := Matches + 1; end;
            if Matches <> 1 then begin ErrorText := 'Footprint missing or ambiguous'; Break; end;
            PcbLib.CurrentComponent := Comp;
            if IsPadProperties then
            begin
                HaveProperties := True;
                WasModified := Doc.Modified;
            end;
            if Command = 'set_footprint_properties' then
            begin
                WasModified := Doc.Modified;
                HaveProperties := True;
                FootprintPropertySnapshot(BeforeItems, Comp);
                TargetHeight := Comp.Height;
                if Spec.IndexOfName('height_mm') >= 0 then
                begin
                    HeightMM := SafeStrToFloat(Spec.Values['height_mm']);
                    if (HeightMM < 0) or (HeightMM > 1000) then
                    begin ErrorText := 'Height must be from 0 to 1000 mm'; Break; end;
                    TargetHeight := MilsToCoord(HeightMM / 0.0254);
                end;
                if TargetHeight <> Comp.Height then
                begin
                    PCBServer.PreProcess;
                    Editing := True;
                    PCBServer.SendMessageToRobots(Comp.I_ObjectAddress, c_Broadcast, PCBM_BeginModify, c_NoEventData);
                    Updated := 1;
                    try Comp.Height := TargetHeight;
                    finally PCBServer.SendMessageToRobots(Comp.I_ObjectAddress, c_Broadcast, PCBM_EndModify, c_NoEventData); end;
                end;
                FootprintPropertySnapshot(AfterItems, Comp);
                if Comp.Height = TargetHeight then Verified := 1
                else ErrorText := 'Native footprint height readback failed';
                Break;
            end;
            OX := PcbLib.Board.XOrigin;
            OY := PcbLib.Board.YOrigin;
            // Resolve every selector before starting an undo group. Keep object
            // addresses so changing a layer cannot retarget a later operation.
            for I := 0 to Count - 1 do
            begin
                ReportProgress;
                Prefix := 'op.' + IntToStr(I) + '.';
                if not IsPad then
                begin
                    Item := FootprintEditValue(Spec, Prefix + 'to_layer');
                    if FootprintEditLayer(Item) = eNoLayer then
                    begin ErrorText := 'Unknown destination layer: ' + Item; Break; end;
                    Item := FootprintEditValue(Spec, Prefix + 'from_layer');
                    if (FootprintEditLayer(Item) = eNoLayer) and not SameText(Item, 'No Layer') then
                    begin ErrorText := 'Unknown selector layer: ' + Item; Break; end;
                end;
                Matches := 0;
                Address := '';
                Iter := Comp.GroupIterator_Create;
                try
                    Prim := Iter.FirstPCBObject;
                    while Prim <> nil do
                    begin
                        ReportProgress;
                        if IsPad then
                        begin
                            if Prim.ObjectId = ePadObject then
                                if SameText(Prim.Name, FootprintEditValue(Spec, 'pad.' + IntToStr(I))) then
                                begin
                                    Address := IntToStr(Prim.I_ObjectAddress); Matches := Matches + 1;
                                    if IsPadProperties then
                                    begin
                                        ErrorText := FootprintPadPropertiesPreflight(Prim, Spec);
                                        NeedChanges := NeedChanges or FootprintPadPropertiesChanged(Prim, Spec);
                                    end;
                                end;
                        end
                        else if FootprintEditMatches(Prim, Spec, Prefix, OX, OY) then
                        begin Address := IntToStr(Prim.I_ObjectAddress); Matches := Matches + 1; end;
                        Prim := Iter.NextPCBObject;
                    end;
                finally
                    Comp.GroupIterator_Destroy(Iter);
                end;
                if Matches <> 1 then begin ErrorText := 'Selector missing or ambiguous at index ' + IntToStr(I); Break; end;
                if ErrorText <> '' then Break;
                if Addresses.IndexOf(Address) >= 0 then begin ErrorText := 'Selectors refer to the same object'; Break; end;
                Addresses.Add(Address);
            end;
            if ErrorText <> '' then Break;
            if not IsPadProperties or NeedChanges then
            begin
                PCBServer.PreProcess;
                Editing := True;
            end;
            for I := 0 to Count - 1 do
            begin
                ReportProgress;
                Prefix := 'op.' + IntToStr(I) + '.';
                Iter := Comp.GroupIterator_Create;
                try
                    Prim := Iter.FirstPCBObject;
                    while Prim <> nil do
                    begin
                        ReportProgress;
                        if IntToStr(Prim.I_ObjectAddress) = Addresses[I] then Break;
                        Prim := Iter.NextPCBObject;
                    end;
                    if Prim = nil then begin ErrorText := 'Preflight object disappeared'; Break; end;
                    if IsPadProperties then BeforeItems.Add(FootprintPadPropertiesSnapshot(Prim))
                    else if IsPad then BeforeItems.Add(FootprintEditPadSnapshot(Prim))
                    else AddJSONProperty(BeforeItems, IntToStr(I), PCBLayerName(Prim.Layer));
                    DoEdit := True;
                    if IsPadProperties then DoEdit := FootprintPadPropertiesChanged(Prim, Spec);
                    if DoEdit then
                    begin
                        PCBServer.SendMessageToRobots(Prim.I_ObjectAddress, c_Broadcast, PCBM_BeginModify, c_NoEventData);
                        Updated := Updated + 1;
                        try
                            if IsPadProperties then
                            begin
                                if Spec.IndexOfName('is_testpoint_top') >= 0 then Prim.IsTestpoint_Top := Spec.Values['is_testpoint_top'] = '1';
                                if Spec.IndexOfName('is_testpoint_bottom') >= 0 then Prim.IsTestpoint_Bottom := Spec.Values['is_testpoint_bottom'] = '1';
                            end
                            else if IsPad then
                                ErrorText := SetPadMaskExpansion(Prim, Spec.Values['paste_mode'], Spec.Values['paste_expansion'],
                                                               Spec.Values['mask_mode'], Spec.Values['mask_expansion'])
                            else Prim.Layer := FootprintEditLayer(FootprintEditValue(Spec, Prefix + 'to_layer'));
                        finally
                            PCBServer.SendMessageToRobots(Prim.I_ObjectAddress, c_Broadcast, PCBM_EndModify, c_NoEventData);
                        end;
                    end;
                    if ErrorText <> '' then Break;
                    if IsPadProperties then
                    begin
                        AfterItems.Add(FootprintPadPropertiesSnapshot(Prim));
                        if not FootprintPadPropertiesChanged(Prim, Spec) then Verified := Verified + 1;
                    end
                    else if IsPad then
                    begin
                        AfterItems.Add(FootprintEditPadSnapshot(Prim));
                        if FootprintEditPadVerified(Prim, Spec) then Verified := Verified + 1;
                    end
                    else
                    begin
                        AddJSONProperty(AfterItems, IntToStr(I), PCBLayerName(Prim.Layer));
                        if Prim.Layer = FootprintEditLayer(FootprintEditValue(Spec, Prefix + 'to_layer')) then
                            Verified := Verified + 1;
                    end;
                finally
                    Comp.GroupIterator_Destroy(Iter);
                end;
                if ErrorText <> '' then Break;
            end;
            if (ErrorText = '') and (Verified <> Count) then ErrorText := 'Native edit readback failed';
        until True;
      except
        ErrorText := 'Footprint library API failure: ' + ExceptionMessage;
      end;
      if Editing then
      begin
          PCBServer.PostProcess;
          Editing := False;
      end;
      if Updated > 0 then
      begin
          if HaveProperties then PcbLib.Board.SetState_DocumentHasChanged;
          MarkDocumentModified(Path);
          PcbLib.Board.ViewManager_FullUpdate;
      end;
      AddJSONBoolean(Props, 'success', ErrorText = '');
      AddJSONBoolean(Props, 'saved', False);
      if HaveProperties then
      begin
          AddJSONBoolean(Props, 'was_modified', WasModified);
          AddJSONBoolean(Props, 'is_modified', Doc.Modified);
          AddJSONBoolean(Props, 'changed', Updated > 0);
      end;
      AddJSONBoolean(Props, 'may_have_partial_changes', (ErrorText <> '') and (Updated > 0));
      if ErrorText <> '' then AddJSONProperty(Props, 'error', ErrorText);
      if Command <> 'get_pcb_api_constants' then
      begin
          AddJSONProperty(Props, 'library_path', Path);
          AddJSONProperty(Props, 'footprint_name', Name);
          AddJSONInteger(Props, 'updated_count', Updated);
          AddJSONInteger(Props, 'verified_count', Verified);
          if IsPad then
          begin
              Props.Add(BuildJSONArray(BeforeItems, 'before'));
              Props.Add(BuildJSONArray(AfterItems, 'after'));
          end
          else
          begin
              Props.Add('"before": ' + BuildJSONObject(BeforeItems));
              Props.Add('"after": ' + BuildJSONObject(AfterItems));
          end;
      end;
      Result := BuildJSONObject(Props);
    finally
        if Editing then PCBServer.PostProcess;
        Spec.Free;
        Props.Free;
        Addresses.Free;
        BeforeItems.Free;
        AfterItems.Free;
        Constants.Free;
        Availability.Free;
        Entry.Free;
    end;
end;

// Validate every layer before creating any object, even in a later footprint.
function ValidateFootprintBatchLayers(Lines: TStringList): String;
var
    I, Field, Side, Layer: Integer;
    Kind, Name, Flag: String;
begin
    Result := '';
    for I := 0 to Lines.Count - 1 do
    begin
        Kind := UpperCase(Trim(GetFieldFromPipeString(Lines[I], 0)));
        Field := -1;
        if Kind = 'PAD' then Field := 5
        else if (Kind = 'TRACK') or (Kind = 'FILL') or (Kind = 'TEXT') then Field := 6
        else if Kind = 'ARC' then Field := 7
        else if Kind = 'REGION' then Field := 1
        else if Kind = 'MODEL3D' then Field := 2
        else if Kind = 'VIA' then Field := 5;
        if Field >= 0 then
        begin
            Name := Trim(GetFieldFromPipeString(Lines[I], Field));
            if FootprintEditLayer(Name) = eNoLayer then
            begin Result := 'Unknown or No Layer at line ' + IntToStr(I + 1) + ': ' + Name; Exit; end;
            if Kind = 'VIA' then
                if FootprintEditLayer(Trim(GetFieldFromPipeString(Lines[I], 6))) = eNoLayer then
                begin Result := 'Unknown via end layer at line ' + IntToStr(I + 1); Exit; end;
            if Kind='PAD' then
            begin
                Layer := FootprintEditLayer(Name);
                for Side := 26 to 27 do
                begin
                    Flag := Trim(GetFieldFromPipeString(Lines[I],Side));
                    if (Flag<>'') and (Flag<>'0') and (Flag<>'1') then
                    begin Result := 'Testpoint flag must be 0, 1 or blank at line '+IntToStr(I+1); Exit; end;
                    if Flag='1' then
                    begin
                        if (Side=26) and (Layer<>eTopLayer) and (Layer<>eMultiLayer) then
                        begin Result := 'Top testpoint requires Top Layer or Multi-Layer at line '+IntToStr(I+1); Exit; end;
                        if (Side=27) and (Layer<>eBottomLayer) and (Layer<>eMultiLayer) then
                        begin Result := 'Bottom testpoint requires Bottom Layer or Multi-Layer at line '+IntToStr(I+1); Exit; end;
                    end;
                end;
            end;
        end;
    end;
end;
