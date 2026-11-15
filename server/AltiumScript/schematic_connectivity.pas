// Read-only schematic connectivity. Net names come from the compiler, never
// from proximity of drawn wires/labels. No save or SetModified calls.

function ReadSchematicConnectivity(Path: String; DoCompile: Boolean): String;
var
    Project, Candidate: IProject;
    Logical, Flat: IDocument;
    ModelComp: IComponent;
    ModelPin: INetItem;
    Sheet: ISch_Document;
    ClientDoc: IServerDocument;
    Comp: ISch_Component;
    Pin: ISch_Pin;
    Iter, PinIter: ISch_Iterator;
    Props, SheetPins, ModelPins, PinProps, NetNames, NetCounts, CountsProps: TStringList;
    P, D, C, K, I, N, Owners, X, Y, Errors, Fatals, Warnings: Integer;
    Name: String;
    WasModified: Boolean;
begin
    Result := 'ERROR: target must be an existing .SchDoc';
    if (not FileExists(Path)) or (LowerCase(ExtractFileExt(Path)) <> '.schdoc') then Exit;
    Project := nil;
    Owners := 0;
    // Resolve the owning project from the exact sheet path, not UI focus.
    for P := 0 to GetWorkspace.DM_ProjectCount - 1 do
    begin
        Candidate := GetWorkspace.DM_Projects(P);
        if LowerCase(ExtractFileExt(Candidate.DM_ProjectFullPath)) = '.prjpcb' then
            for D := 0 to Candidate.DM_LogicalDocumentCount - 1 do
            begin
                Logical := Candidate.DM_LogicalDocuments(D);
                if SameText(Logical.DM_FullPath, Path) then
                begin
                    Project := Candidate;
                    Owners := Owners + 1;
                end;
            end;
    end;
    if Owners <> 1 then
    begin
        Result := 'ERROR: sheet must belong to exactly one open PCB project; found ' + IntToStr(Owners);
        Exit;
    end;
    ClientDoc := Client.GetDocumentByPath(Path);
    if ClientDoc = nil then ClientDoc := Client.OpenDocument('SCH', Path);
    if ClientDoc = nil then
    begin
        Result := 'ERROR: could not open target schematic';
        Exit;
    end;
    Client.ShowDocument(ClientDoc);
    Sheet := SchServer.GetSchDocumentByPath(Path);
    if Sheet = nil then
    begin
        Result := 'ERROR: schematic server has no target document';
        Exit;
    end;
    if Sheet.ObjectId <> 32 then
    begin
        Result := 'ERROR: target is not a schematic sheet';
        Exit;
    end;
    if SchServer.ProcessControl.ProcessDepth <> 0 then
    begin
        Result := 'ERROR: schematic editor has an active transaction; finish the current action first';
        Exit;
    end;
    WasModified := ClientDoc.Modified;
    if DoCompile then
    begin
        LogScriptError('get_schematic_connectivity: compile ' + Project.DM_ProjectFullPath);
        Project.DM_Compile;
    end;
    Flat := Project.DM_DocumentFlattened;
    if Flat = nil then
    begin
        Result := 'ERROR: no compiled project model; call with compiled=true';
        Exit;
    end;

    Props := TStringList.Create;
    SheetPins := TStringList.Create;
    ModelPins := TStringList.Create;
    NetNames := TStringList.Create;
    NetCounts := TStringList.Create;
    CountsProps := TStringList.Create;
    try
        // Inventory the actual placed parts too: a compiler model may exclude
        // a part. Such a gap must not silently hide its pins from the response.
        LogScriptError('get_schematic_connectivity: placed pin inventory');
        Iter := Sheet.SchIterator_Create;
        Iter.AddFilter_ObjectSet(MkSet(eSchComponent));
        try
            Comp := Iter.FirstSchObject;
            while Comp <> nil do
            begin
                PinIter := Comp.SchIterator_Create;
                PinIter.AddFilter_ObjectSet(MkSet(ePin));
                try
                    Pin := PinIter.FirstSchObject;
                    while Pin <> nil do
                    begin
                        if ((Pin.OwnerPartId = 0) or (Pin.OwnerPartId = Comp.CurrentPartID)) and
                           (Pin.OwnerPartDisplayMode = Comp.DisplayMode) then
                        begin
                            GetPinHotEnd(Pin, X, Y);
                            PinProps := TStringList.Create;
                            try
                                AddJSONProperty(PinProps, 'component_uid', Comp.UniqueId);
                                AddJSONProperty(PinProps, 'designator', Comp.Designator.Text);
                                AddJSONProperty(PinProps, 'logical_designator', Comp.Designator.Text);
                                AddJSONProperty(PinProps, 'pin_number', Pin.Designator);
                                AddJSONProperty(PinProps, 'pin_name', Pin.Name);
                                AddJSONInteger(PinProps, 'part_id', Pin.OwnerPartId);
                                AddJSONNumber(PinProps, 'x', CoordToMils(X));
                                AddJSONNumber(PinProps, 'y', CoordToMils(Y));
                                SheetPins.Add(BuildJSONObject(PinProps));
                            finally
                                PinProps.Free;
                            end;
                        end;
                        Pin := PinIter.NextSchObject;
                    end;
                finally
                    Comp.SchIterator_Destroy(PinIter);
                end;
                Comp := Iter.NextSchObject;
            end;
        finally
            Sheet.SchIterator_Destroy(Iter);
        end;

        LogScriptError('get_schematic_connectivity: compiled pin inventory');
        for C := 0 to Flat.DM_ComponentCount - 1 do
        begin
            ModelComp := Flat.DM_Components(C);
            for K := 0 to ModelComp.DM_PinCount - 1 do
            begin
                ModelPin := ModelComp.DM_Pins(K);
                Name := ModelPin.DM_FlattenedNetName;
                if (Name <> '') and (Name <> '?') then
                begin
                    N := NetNames.IndexOf(Name);
                    if N < 0 then
                    begin
                        NetNames.Add(Name);
                        NetCounts.Add('1');
                    end
                    else NetCounts[N] := IntToStr(StrToInt(NetCounts[N]) + 1);
                end;
                // Pin ownership matters for multipart components spanning sheets.
                if SameText(ModelPin.DM_OwnerDocumentFullPath, Path) then
                begin
                    PinProps := TStringList.Create;
                    try
                        AddJSONProperty(PinProps, 'component_uid', ExtractFileName(ModelPin.DM_PartUniqueId));
                        AddJSONProperty(PinProps, 'designator', ModelPin.DM_PhysicalPartDesignator);
                        AddJSONProperty(PinProps, 'logical_designator', ModelPin.DM_LogicalPartDesignator);
                        AddJSONProperty(PinProps, 'pin_number', ModelPin.DM_PinNumber);
                        AddJSONProperty(PinProps, 'pin_name', ModelPin.DM_PinName);
                        AddJSONInteger(PinProps, 'part_id', ModelPin.DM_PartId);
                        AddJSONProperty(PinProps, 'instance_id', ModelPin.DM_FullUniqueId);
                        AddJSONNumber(PinProps, 'x', CoordToMils(ModelPin.DM_LocationX));
                        AddJSONNumber(PinProps, 'y', CoordToMils(ModelPin.DM_LocationY));
                        AddJSONProperty(PinProps, 'net_name', Name);
                        AddJSONProperty(PinProps, 'logical_net_name', ModelPin.DM_NetName);
                        ModelPins.Add(BuildJSONObject(PinProps));
                    finally
                        PinProps.Free;
                    end;
                end;
            end;
        end;
        for I := 0 to NetNames.Count - 1 do
            AddJSONInteger(CountsProps, NetNames[I], StrToInt(NetCounts[I]));
        Errors := 0;
        Fatals := 0;
        Warnings := 0;
        for I := 0 to Project.DM_ViolationCount - 1 do
        begin
            if Project.DM_Violations(I).DM_ErrorLevel = 3 then Fatals := Fatals + 1;
            if Project.DM_Violations(I).DM_ErrorLevel = 2 then Errors := Errors + 1;
            if Project.DM_Violations(I).DM_ErrorLevel = 1 then Warnings := Warnings + 1;
        end;
        AddJSONBoolean(Props, 'success', True);
        AddJSONProperty(Props, 'sheet_path', Path);
        AddJSONProperty(Props, 'project_path', Project.DM_ProjectFullPath);
        AddJSONBoolean(Props, 'compile_performed', DoCompile);
        AddJSONBoolean(Props, 'saved', False);
        AddJSONBoolean(Props, 'was_modified', WasModified);
        AddJSONBoolean(Props, 'is_modified', ClientDoc.Modified);
        AddJSONInteger(Props, 'compile_errors', Errors);
        AddJSONInteger(Props, 'compile_fatals', Fatals);
        AddJSONInteger(Props, 'compile_warnings', Warnings);
        AddJSONInteger(Props, 'placed_pin_count', SheetPins.Count);
        AddJSONInteger(Props, 'model_pin_count', ModelPins.Count);
        Props.Add('"sheet_pins": ' + BuildJSONArray(SheetPins));
        Props.Add('"model_pins": ' + BuildJSONArray(ModelPins));
        Props.Add('"project_net_pin_counts": ' + BuildJSONObject(CountsProps));
        Result := BuildJSONObject(Props);
        LogScriptError('get_schematic_connectivity: complete');
    finally
        Props.Free;
        SheetPins.Free;
        ModelPins.Free;
        NetNames.Free;
        NetCounts.Free;
        CountsProps.Free;
    end;
end;

function ExecuteSchematicConnectivity(Request: TStringList): String;
var
    I: Integer;
    Path: String;
    DoCompile: Boolean;
begin
    Path := '';
    DoCompile := True;
    for I := 0 to Request.Count - 1 do
    begin
        if Pos('"sheet_path":', Request[I]) > 0 then Path := JSONLineValue(Request[I]);
        if Pos('"compiled":', Request[I]) > 0 then DoCompile := JSONLineValue(Request[I]) = 'true';
    end;
    Result := ReadSchematicConnectivity(Path, DoCompile);
end;
