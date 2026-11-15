// Edit one existing STEP body in an exact PcbLib buffer. Never saves.

function FootprintModelMM(Value: Integer): Double;
begin
    Result := Value / MilsToCoord(1) * 0.0254;
end;

function FootprintModelSnapshot(Body: IPCB_ComponentBody; OX, OY: Integer): String;
var
    Props: TStringList;
    Model: IPCB_Model;
    Bounds: TCoordRect;
begin
    Props := TStringList.Create;
    try
        AddJSONProperty(Props, 'body_address', IntToStr(Body.I_ObjectAddress));
        AddJSONProperty(Props, 'layer', PCBLayerName(Body.Layer));
        AddJSONNumber(Props, 'standoff_height_mm', FootprintModelMM(Body.StandoffHeight));
        AddJSONNumber(Props, 'overall_height_mm', FootprintModelMM(Body.OverallHeight));
        Bounds := Body.BoundingRectangle;
        AddJSONNumber(Props, 'left_mm', FootprintModelMM(Bounds.Left - OX));
        AddJSONNumber(Props, 'right_mm', FootprintModelMM(Bounds.Right - OX));
        AddJSONNumber(Props, 'bottom_mm', FootprintModelMM(Bounds.Bottom - OY));
        AddJSONNumber(Props, 'top_mm', FootprintModelMM(Bounds.Top - OY));
        Model := Body.Model;
        AddJSONBoolean(Props, 'has_model', Model <> nil);
        if Model <> nil then
        begin
            AddJSONProperty(Props, 'model_file', Model.FileName);
            AddJSONBoolean(Props, 'embed', Model.Embed);
            AddJSONInteger(Props, 'model_type', Model.ModelType);
            AddJSONNumber(Props, 'x_mm', FootprintModelMM(Model.Origin.X - OX));
            AddJSONNumber(Props, 'y_mm', FootprintModelMM(Model.Origin.Y - OY));
        end;
        Result := BuildJSONObject(Props);
    finally Props.Free; end;
end;

procedure FootprintModelInventory(Comp: IPCB_LibComponent; Items: TStringList; OX, OY: Integer);
var
    Iter: IPCB_GroupIterator;
    Body: IPCB_ComponentBody;
begin
    Items.Clear;
    Iter := Comp.GroupIterator_Create;
    try
        Iter.AddFilter_ObjectSet(MkSet(eComponentBodyObject));
        Body := Iter.FirstPCBObject;
        while Body <> nil do
        begin
            ReportProgress;
            Items.Add(FootprintModelSnapshot(Body, OX, OY));
            Body := Iter.NextPCBObject;
        end;
    finally Comp.GroupIterator_Destroy(Iter); end;
end;

function ExecuteFootprintModel3D(Request: TStringList): String;
var
    Spec, Props, BeforeItems, AfterItems, PoseProps: TStringList;
    Doc: IServerDocument;
    Lib: IPCB_Library;
    Comp: IPCB_LibComponent;
    Iter: IPCB_GroupIterator;
    Body: IPCB_ComponentBody;
    Model, NewModel: IPCB_Model;
    Path, Name, SpecPath, ErrorText, BeforeJSON, AfterJSON, ModelPath: String;
    I, Matches, Index, OX, OY, X, Y, DZ: Integer;
    RX, RY, RZ, TargetX, TargetY: Double;
    Editing, Changed, WasModified, Mutating, Deleting, HavePose, Embed, NeedEdit, Replaced, HaveInventory: Boolean;
begin
    Spec := TStringList.Create;
    Props := TStringList.Create;
    BeforeItems := TStringList.Create;
    AfterItems := TStringList.Create;
    PoseProps := TStringList.Create;
    Editing := False; Changed := False; Replaced := False; WasModified := False;
    HaveInventory := False;
    ErrorText := ''; Path := ''; Name := ''; Index := -1; Doc := nil; Lib := nil; Comp := nil;
    BeforeJSON := '{}'; AfterJSON := '{}';
    try
      try
        repeat
            SpecPath := '';
            for I := 0 to Request.Count - 1 do
                if Pos('"spec_file"', Request[I]) > 0 then SpecPath := JSONLineValue(Request[I]);
            if not FileExists(SpecPath) then begin ErrorText := 'Spec file missing'; Break; end;
            Spec.LoadFromFile(SpecPath);
            Path := FootprintEditValue(Spec, 'library_path');
            Name := FootprintEditValue(Spec, 'footprint_name');
            if (Path = '') or (Name = '') or (UpperCase(ExtractFileExt(Path)) <> '.PCBLIB') then
            begin ErrorText := 'An explicit PcbLib path and footprint name are required'; Break; end;
            // OpenLibraryDocument reuses exactly one open buffer and never reloads it.
            if (FindOpenDocument(Path) = nil) and not FileExists(Path) then
            begin ErrorText := 'Library is neither open nor present on disk'; Break; end;
            Doc := OpenLibraryDocument('PcbLib', Path);
            if Doc = nil then begin ErrorText := 'Cannot open a unique PCB library buffer'; Break; end;
            WasModified := Doc.Modified;
            Lib := PCBServer.GetCurrentPCBLibrary;
            if Lib = nil then begin ErrorText := 'No PCB library is focused'; Break; end;
            if NormalizedPath(Lib.Board.FileName) <> NormalizedPath(Path) then
            begin ErrorText := 'Focused library identity mismatch'; Break; end;
            Matches := 0;
            for I := 0 to Lib.ComponentCount - 1 do
                if SameText(Lib.GetComponent(I).Name, Name) then
                begin Comp := Lib.GetComponent(I); Matches := Matches + 1; end;
            if Matches <> 1 then begin ErrorText := 'Footprint missing or ambiguous'; Break; end;
            if Lib.CurrentComponent = nil then Lib.CurrentComponent := Comp
            else if Lib.CurrentComponent.I_ObjectAddress <> Comp.I_ObjectAddress then Lib.CurrentComponent := Comp;
            OX := Lib.Board.XOrigin; OY := Lib.Board.YOrigin;
            FootprintModelInventory(Comp, BeforeItems, OX, OY);
            HaveInventory := True;
            BeforeJSON := GetFootprintPrimitivesJSON('', Path, Name);
            Mutating := Spec.Values['mutating'] = '1';
            Deleting := Spec.Values['delete'] = '1';
            HavePose := Spec.IndexOfName('rotation_x') >= 0;
            if (not Mutating) and (Spec.IndexOfName('body_index') < 0) and
               (Spec.IndexOfName('body_id') < 0) and
               (Spec.IndexOfName('expected_model_file') < 0) then Break;
            if Spec.IndexOfName('body_id') >= 0 then
            begin
                Iter := Comp.GroupIterator_Create;
                try
                    Iter.AddFilter_ObjectSet(MkSet(eComponentBodyObject));
                    Body := Iter.FirstPCBObject;
                    I := 0;
                    while Body <> nil do
                    begin
                        ReportProgress;
                        if IntToStr(Body.I_ObjectAddress) = Spec.Values['body_id'] then begin Index := I; Break; end;
                        I := I + 1;
                        Body := Iter.NextPCBObject;
                    end;
                finally Comp.GroupIterator_Destroy(Iter); end;
                if Index < 0 then begin ErrorText := 'body_id is stale or does not belong to this footprint'; Break; end;
                if Spec.IndexOfName('body_index') >= 0 then
                    if Index <> StrToInt(Spec.Values['body_index']) then
                    begin ErrorText := 'body_index does not match body_id'; Break; end;
            end
            else if Spec.IndexOfName('body_index') >= 0 then Index := StrToInt(Spec.Values['body_index'])
            else if BeforeItems.Count = 1 then Index := 0
            else begin ErrorText := 'Specify body_id or body_index when the footprint does not have exactly one body'; Break; end;
            if (Index < 0) or (Index >= BeforeItems.Count) then
            begin ErrorText := 'body_index is outside the current body inventory'; Break; end;
            Iter := Comp.GroupIterator_Create;
            try
                Iter.AddFilter_ObjectSet(MkSet(eComponentBodyObject));
                Body := Iter.FirstPCBObject;
                for I := 1 to Index do Body := Iter.NextPCBObject;
            finally Comp.GroupIterator_Destroy(Iter); end;
            Model := Body.Model;
            if Spec.IndexOfName('expected_model_file') >= 0 then
            begin
                if Model = nil then begin ErrorText := 'Selected body has no model'; Break; end;
                if Model.FileName <> FootprintEditValue(Spec, 'expected_model_file') then
                begin ErrorText := 'expected_model_file does not match the selected body'; Break; end;
            end;
            if not Mutating then Break;
            if not Deleting then
            begin
                if Model = nil then begin ErrorText := 'Selected body has no model'; Break; end;
                if Model.ModelType <> e3DModelType_Generic then
                begin ErrorText := 'Only generic STEP model bodies can be updated or replaced'; Break; end;
                X := Model.Origin.X; Y := Model.Origin.Y;
                TargetX := X; TargetY := Y;
                if Spec.IndexOfName('x_mm') >= 0 then TargetX := OX + SafeStrToFloat(Spec.Values['x_mm']) / 0.0254 * MilsToCoord(1);
                if Spec.IndexOfName('y_mm') >= 0 then TargetY := OY + SafeStrToFloat(Spec.Values['y_mm']) / 0.0254 * MilsToCoord(1);
                if (Abs(TargetX) > 2147480000) or (Abs(TargetY) > 2147480000) or
                   (Abs(TargetX - X) > 2147480000) or (Abs(TargetY - Y) > 2147480000) then
                begin ErrorText := 'Requested position/displacement exceeds native coordinate limits'; Break; end;
                if Spec.IndexOfName('x_mm') >= 0 then X := OX + MilsToCoord(SafeStrToFloat(Spec.Values['x_mm']) / 0.0254);
                if Spec.IndexOfName('y_mm') >= 0 then Y := OY + MilsToCoord(SafeStrToFloat(Spec.Values['y_mm']) / 0.0254);
                Embed := Model.Embed;
                if Spec.IndexOfName('embed') >= 0 then Embed := Spec.Values['embed'] = '1';
                ModelPath := FootprintEditValue(Spec, 'model_path');
                if HavePose or (ModelPath <> '') then
                begin
                    if (Spec.IndexOfName('rotation_x') < 0) or (Spec.IndexOfName('rotation_y') < 0) or
                       (Spec.IndexOfName('rotation_z') < 0) or (Spec.IndexOfName('z_mm') < 0) then
                    begin ErrorText := 'A complete rotation_x/rotation_y/rotation_z/z_mm pose is required'; Break; end;
                    RX := SafeStrToFloat(Spec.Values['rotation_x']);
                    RY := SafeStrToFloat(Spec.Values['rotation_y']);
                    RZ := SafeStrToFloat(Spec.Values['rotation_z']);
                    DZ := MilsToCoord(SafeStrToFloat(Spec.Values['z_mm']) / 0.0254);
                    AddJSONNumber(PoseProps, 'rotation_x', RX);
                    AddJSONNumber(PoseProps, 'rotation_y', RY);
                    AddJSONNumber(PoseProps, 'rotation_z', RZ);
                    AddJSONNumber(PoseProps, 'z_mm', SafeStrToFloat(Spec.Values['z_mm']));
                end;
                if ModelPath <> '' then
                begin
                    if not FileExists(ModelPath) then begin ErrorText := 'Replacement STEP file missing'; Break; end;
                    // Loading is preflight: do not touch the original body until this succeeds.
                    NewModel := Body.ModelFactory_FromFilename(ModelPath, False);
                    if NewModel = nil then begin ErrorText := 'Altium could not load the replacement STEP'; Break; end;
                end;
                if (not Embed) and (ModelPath = '') then
                    if (not FileExists(Model.FileName)) or (ExtractFileDrive(Model.FileName) = '') then
                    begin ErrorText := 'Linking requires an existing absolute source; supply model_path and a complete pose'; Break; end;
                NeedEdit := HavePose or (ModelPath <> '') or (X <> Model.Origin.X) or
                            (Y <> Model.Origin.Y) or (Embed <> Model.Embed);
            end
            else NeedEdit := True;
            if not NeedEdit then Break;
            PCBServer.PreProcess; Editing := True;
            Changed := True;
            if Deleting then
            begin
                // Detach from both the footprint group and its displayed board.
                Comp.RemovePCBObject(Body);
                Body.Component := nil;
                Lib.Board.RemovePCBObject(Body);
                PCBServer.SendMessageToRobots(Lib.Board.I_ObjectAddress, c_Broadcast,
                                             PCBM_BoardRegisteration, Body.I_ObjectAddress);
                PCBServer.DestroyPCBObject(Body);
            end
            else
            begin
                PCBServer.SendMessageToRobots(Body.I_ObjectAddress, c_Broadcast, PCBM_BeginModify, c_NoEventData);
                try
                    if ModelPath <> '' then Model := NewModel;
                    Model.Embed := Embed;
                    if HavePose then Model.SetState(RX, RY, RZ, DZ);
                    if HavePose or (ModelPath <> '') then
                    begin
                        Body.Model := Model;
                        Body.SetState_FromModel;
                    end;
                    Model := Body.Model;
                    Body.MoveByXY(X - Model.Origin.X, Y - Model.Origin.Y);
                    Replaced := ModelPath <> '';
                finally
                    PCBServer.SendMessageToRobots(Body.I_ObjectAddress, c_Broadcast, PCBM_EndModify, c_NoEventData);
                end;
            end;
            PCBServer.PostProcess; Editing := False;
        until True;
      except ErrorText := '3D model API failure: ' + ExceptionMessage; end;
      if Editing then begin PCBServer.PostProcess; Editing := False; end;
      if Changed then
      begin
          Lib.Board.SetState_DocumentHasChanged;
          MarkDocumentModified(Path);
          Lib.Board.ViewManager_FullUpdate;
      end;
      if HaveInventory then
      begin
          try
              FootprintModelInventory(Comp, AfterItems, OX, OY);
              AfterJSON := GetFootprintPrimitivesJSON('', Path, Name);
          except ErrorText := ErrorText + ' After snapshot failed: ' + ExceptionMessage; end;
      end;
      AddJSONBoolean(Props, 'success', ErrorText = '');
      AddJSONBoolean(Props, 'saved', False);
      AddJSONBoolean(Props, 'changed', Changed);
      AddJSONBoolean(Props, 'was_modified', WasModified);
      if Doc <> nil then AddJSONBoolean(Props, 'is_modified', Doc.Modified);
      AddJSONBoolean(Props, 'may_have_partial_changes', Changed and (ErrorText <> ''));
      AddJSONBoolean(Props, 'replacement_loaded', Replaced);
      AddJSONProperty(Props, 'library_path', Path);
      AddJSONProperty(Props, 'footprint_name', Name);
      if Index >= 0 then AddJSONInteger(Props, 'body_index', Index);
      if ErrorText <> '' then AddJSONProperty(Props, 'error', ErrorText);
      Props.Add(BuildJSONArray(BeforeItems, 'before'));
      Props.Add(BuildJSONArray(AfterItems, 'after'));
      Props.Add('"footprint_before": ' + BeforeJSON);
      Props.Add('"footprint_after": ' + AfterJSON);
      if Changed and (PoseProps.Count > 0) then Props.Add('"pose_applied": ' + BuildJSONObject(PoseProps));
      Result := BuildJSONObject(Props);
    finally
        if Editing then PCBServer.PostProcess;
        Spec.Free; Props.Free; BeforeItems.Free; AfterItems.Free; PoseProps.Free;
    end;
end;
