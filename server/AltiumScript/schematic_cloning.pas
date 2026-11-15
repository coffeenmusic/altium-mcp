// Cross-sheet component copies. Never save, compile or alter project membership.

function CloneIsComponent(Obj: ISch_GraphicalObject): Boolean;
begin
    Result := False;
    if Obj.ObjectId <> eSchComponent then Exit;
    if Obj.Container = nil then Exit;
    Result := Obj.Container.ObjectId = 32;
end;

function CloneFindUID(Doc: ISch_Document; UID: String): ISch_Component;
var
    Iter: ISch_Iterator;
    Obj: ISch_GraphicalObject;
begin
    Result := nil;
    Iter := Doc.SchIterator_Create;
    Iter.AddFilter_ObjectSet(MkSet(eSchComponent));
    try
        Obj := Iter.FirstSchObject;
        while Obj <> nil do
        begin
            if CloneIsComponent(Obj) and (Obj.UniqueId = UID) then
            begin
                Result := Obj;
                Exit;
            end;
            Obj := Iter.NextSchObject;
        end;
    finally
        Doc.SchIterator_Destroy(Iter);
    end;
end;

function CloneTopCounts(Doc: ISch_Document): String;
var
    Counts, Props: TStringList;
    Iter: ISch_Iterator;
    Obj: ISch_GraphicalObject;
    Key: String;
    I: Integer;
begin
    Counts := TStringList.Create;
    Props := TStringList.Create;
    try
        Iter := Doc.SchIterator_Create;
        try
            Obj := Iter.FirstSchObject;
            while Obj <> nil do
            begin
                if Obj.Container <> nil then
                    if Obj.Container.ObjectId = 32 then
                    begin
                        Key := IntToStr(Obj.ObjectId);
                        if Counts.IndexOfName(Key) < 0 then Counts.Values[Key] := '0';
                        Counts.Values[Key] := IntToStr(StrToInt(Counts.Values[Key]) + 1);
                    end;
                Obj := Iter.NextSchObject;
            end;
        finally
            Doc.SchIterator_Destroy(Iter);
        end;
        for I := 0 to Counts.Count - 1 do AddJSONInteger(Props, Counts.Names[I], StrToInt(Counts.ValueFromIndex[I]));
        Result := BuildJSONObject(Props);
    finally
        Counts.Free;
        Props.Free;
    end;
end;

function CloneOpenSheet(Path: String; var ClientDoc: IServerDocument): ISch_Document;
begin
    Result := nil;
    ClientDoc := FindOpenDocument(Path);
    if ClientDoc = nil then
    begin
        if not FileExists(Path) then Exit;
        ClientDoc := Client.OpenDocument('SCH', Path);
    end;
    if ClientDoc = nil then Exit;
    if (UpperCase(ClientDoc.Kind) <> 'SCH') or
       (NormalizedPath(ClientDoc.FileName) <> NormalizedPath(Path)) then Exit;
    Client.ShowDocument(ClientDoc);
    Result := SchServer.GetSchDocumentByPath(ClientDoc.FileName);
    if Result = nil then Exit;
    if (Result.ObjectId <> 32) or (NormalizedPath(Result.DocumentName) <> NormalizedPath(Path)) then Result := nil;
end;

function CloneComponentSnapshot(Comp: ISch_Component): String;
var
    Props, State, Counts, Pins, Displays, IDs, Item: TStringList;
    Iter: ISch_Iterator;
    Obj: ISch_GraphicalObject;
    Key: String;
    X, Y, I: Integer;
begin
    Props := TStringList.Create;
    State := TStringList.Create;
    Counts := TStringList.Create;
    Pins := TStringList.Create;
    Displays := TStringList.Create;
    IDs := TStringList.Create;
    try
        AddJSONProperty(Props, 'unique_id', Comp.UniqueId);
        AddJSONProperty(State, 'designator', Comp.Designator.Text);
        AddJSONInteger(State, 'part_id', Comp.CurrentPartID);
        AddJSONInteger(State, 'display_mode', Comp.DisplayMode);
        AddJSONNumber(State, 'x', CoordToMils(Comp.Location.X));
        AddJSONNumber(State, 'y', CoordToMils(Comp.Location.Y));
        AddJSONInteger(State, 'orientation', Comp.Orientation);
        AddJSONBoolean(State, 'mirror', Comp.IsMirrored);
        AddJSONBoolean(State, 'designator_locked', Comp.DesignatorLocked);
        AddJSONBoolean(State, 'part_locked', Comp.PartIdLocked);
        AddJSONProperty(State, 'library_path', Comp.LibraryPath);
        AddJSONProperty(State, 'source_library_name', Comp.SourceLibraryName);
        State.Add('"metadata": ' + LibraryComponentSnapshot(Comp));
        Iter := Comp.SchIterator_Create;
        try
            Obj := Iter.FirstSchObject;
            while Obj <> nil do
            begin
                Key := IntToStr(Obj.ObjectId);
                if Counts.IndexOfName(Key) < 0 then Counts.Values[Key] := '0';
                Counts.Values[Key] := IntToStr(StrToInt(Counts.Values[Key]) + 1);
                if Obj.UniqueId <> '' then IDs.Add('"' + JSONEscapeString(Obj.UniqueId) + '"');
                if (Obj.ObjectId = ePin) or (Obj.ObjectId = eParameter) or (Obj.ObjectId = 25) then
                begin
                    Item := TStringList.Create;
                    try
                        AddJSONNumber(Item, 'x', CoordToMils(Obj.Location.X));
                        AddJSONNumber(Item, 'y', CoordToMils(Obj.Location.Y));
                        AddJSONInteger(Item, 'orientation', Obj.Orientation);
                        AddJSONInteger(Item, 'owner_part_id', Obj.OwnerPartId);
                        AddJSONInteger(Item, 'owner_display_mode', Obj.OwnerPartDisplayMode);
                        AddJSONBoolean(Item, 'hidden', Obj.IsHidden);
                        if Obj.ObjectId = ePin then
                        begin
                            AddJSONProperty(Item, 'designator', Obj.Designator);
                            AddJSONProperty(Item, 'name', Obj.Name);
                            AddJSONInteger(Item, 'electrical', Obj.Electrical);
                            AddJSONNumber(Item, 'length', CoordToMils(Obj.PinLength));
                            GetPinHotEnd(Obj, X, Y);
                            AddJSONNumber(Item, 'hot_x', CoordToMils(X));
                            AddJSONNumber(Item, 'hot_y', CoordToMils(Y));
                            Pins.Add(BuildJSONObject(Item));
                        end
                        else
                        begin
                            AddJSONProperty(Item, 'name', Obj.Name);
                            AddJSONProperty(Item, 'text', Obj.Text);
                            AddJSONBoolean(Item, 'show_name', Obj.ShowName);
                            Displays.Add(BuildJSONObject(Item));
                        end;
                    finally
                        Item.Free;
                    end;
                end;
                Obj := Iter.NextSchObject;
            end;
        finally
            Comp.SchIterator_Destroy(Iter);
        end;
        Pins.Sort;
        Displays.Sort;
        Item := TStringList.Create;
        try
            for I := 0 to Counts.Count - 1 do AddJSONInteger(Item, Counts.Names[I], StrToInt(Counts.ValueFromIndex[I]));
            State.Add('"primitive_counts": ' + BuildJSONObject(Item));
        finally
            Item.Free;
        end;
        State.Add('"pins": ' + BuildJSONArray(Pins));
        State.Add('"parameter_display": ' + BuildJSONArray(Displays));
        Props.Add('"state": ' + BuildJSONObject(State));
        Props.Add('"child_uids": ' + BuildJSONArray(IDs));
        Result := BuildJSONObject(Props);
    finally
        Props.Free;
        State.Free;
        Counts.Free;
        Pins.Free;
        Displays.Free;
        IDs.Free;
    end;
end;

procedure CloneCollectUIDs(Doc: ISch_Document; Used: TStringList);
var
    Iter: ISch_Iterator;
    Obj: ISch_GraphicalObject;
begin
    Iter := Doc.SchIterator_Create;
    try
        Obj := Iter.FirstSchObject;
        while Obj <> nil do
        begin
            if (Obj.UniqueId <> '') and (Used.IndexOf(Obj.UniqueId) < 0) then Used.Add(Obj.UniqueId);
            Obj := Iter.NextSchObject;
        end;
    finally
        Doc.SchIterator_Destroy(Iter);
    end;
end;

function CloneFreshUID(Used: TStringList): String;
var
    I: Integer;
begin
    for I := 1 to 10 do
    begin
        Result := GetWorkspace.DM_GenerateUniqueID;
        if (Result <> '') and (Used.IndexOf(Result) < 0) then
        begin
            Used.Add(Result);
            Exit;
        end;
    end;
    Result := '';
end;

function CloneRenewUID(Obj: ISch_GraphicalObject; Used, Mapping: TStringList): Boolean;
var
    NewUID: String;
    Props: TStringList;
begin
    Result := True;
    if Obj.UniqueId = '' then Exit;
    NewUID := CloneFreshUID(Used);
    Result := NewUID <> '';
    if not Result then Exit;
    Props := TStringList.Create;
    try
        AddJSONProperty(Props, 'source_uid', Obj.UniqueId);
        AddJSONProperty(Props, 'unique_id', NewUID);
        AddJSONInteger(Props, 'object_id', Obj.ObjectId);
        Obj.UniqueId := NewUID;
        Mapping.Add(BuildJSONObject(Props));
    finally
        Props.Free;
    end;
end;

function CloneSelectionState(Doc: ISch_Document; Selected: TStringList): String;
var
    Items: TStringList;
    Comp: ISch_Component;
    I: Integer;
begin
    Items := TStringList.Create;
    try
        for I := 0 to Selected.Count - 1 do
        begin
            ReportProgress;
            Comp := CloneFindUID(Doc, Selected[I]);
            if Comp = nil then Items.Add('null') else Items.Add(CloneComponentSnapshot(Comp));
        end;
        Result := BuildJSONArray(Items);
    finally
        Items.Free;
    end;
end;

function CloneSchematicFromSpec(SpecPath: String): String;
var
    Spec, Selected, Parts, Used, Mapping, Copies, Props, Item, Registered: TStringList;
    SourceDoc, TargetDoc: ISch_Document;
    SourceClient, TargetClient: IServerDocument;
    Comp, Replica: ISch_Component;
    Iter, ChildIter: ISch_Iterator;
    Obj, Child: ISch_GraphicalObject;
    SourcePath, TargetPath, Name, Key, ErrorText, SourceBefore, SourceAfter, CompBefore, TargetBeforeCounts: String;
    I, Requested, Found, Applied, Failed: Integer;
    Editing, Started, SourceModified, TargetModified, SourceUnchanged: Boolean;
begin
    Result := 'ERROR: clone spec file not found';
    if not FileExists(SpecPath) then Exit;
    Spec := TStringList.Create;
    Selected := TStringList.Create;
    Parts := TStringList.Create;
    Used := TStringList.Create;
    Mapping := TStringList.Create;
    Copies := TStringList.Create;
    Registered := TStringList.Create;
    Props := TStringList.Create;
    SourceClient := nil;
    TargetClient := nil;
    SourceDoc := nil;
    TargetDoc := nil;
    Editing := False;
    Started := False;
    SourceModified := False;
    TargetModified := False;
    SourceUnchanged := True;
    SourceBefore := '[]';
    TargetBeforeCounts := '{}';
    Applied := 0;
    Failed := -1;
    ErrorText := '';
    try
        Spec.LoadFromFile(SpecPath);
        SourcePath := EditValue(Spec, 'source_sheet');
        TargetPath := EditValue(Spec, 'target_sheet');
        try
          repeat
            if (LowerCase(ExtractFileExt(SourcePath)) <> '.schdoc') or
               (LowerCase(ExtractFileExt(TargetPath)) <> '.schdoc') or
               (NormalizedPath(SourcePath) = NormalizedPath(TargetPath)) then
                begin ErrorText := 'source and target must be different exact SchDocs'; Break; end;
            LogScriptError('clone_schematic_components: resolve source ' + SourcePath);
            SourceDoc := CloneOpenSheet(SourcePath, SourceClient);
            if SourceDoc = nil then begin ErrorText := 'source must exist or be an open named SchDoc'; Break; end;
            SourceModified := SourceClient.Modified;
            LogScriptError('clone_schematic_components: resolve target ' + TargetPath);
            TargetDoc := CloneOpenSheet(TargetPath, TargetClient);
            if TargetDoc = nil then begin ErrorText := 'target must exist or be an open named SchDoc'; Break; end;
            TargetModified := TargetClient.Modified;
            TargetBeforeCounts := CloneTopCounts(TargetDoc);
            if SchServer.ProcessControl.ProcessDepth <> 0 then
                begin ErrorText := 'schematic editor has an active transaction'; Break; end;
            Requested := StrToInt(Spec.Values['count']);
            if (Requested < 1) or (Requested > 500) then
                begin ErrorText := 'expected 1 to 500 designators'; Break; end;
            for I := 0 to Requested - 1 do
            begin
                Failed := I;
                Name := EditValue(Spec, 'd' + IntToStr(I));
                Parts.Clear;
                Found := 0;
                Iter := SourceDoc.SchIterator_Create;
                Iter.AddFilter_ObjectSet(MkSet(eSchComponent));
                try
                    Obj := Iter.FirstSchObject;
                    while Obj <> nil do
                    begin
                        if CloneIsComponent(Obj) then
                            if SameText(Obj.Designator.Text, Name) then
                            begin
                                Comp := Obj;
                                Key := IntToStr(Comp.CurrentPartID);
                                if (Parts.IndexOf(Key) >= 0) or (Comp.UniqueId = '') or (Selected.IndexOf(Comp.UniqueId) >= 0) then
                                    ErrorText := 'ambiguous source part or component UID: ' + Name;
                                Parts.Add(Key);
                                Selected.Add(Comp.UniqueId);
                                Inc(Found);
                            end;
                        Obj := Iter.NextSchObject;
                    end;
                finally
                    SourceDoc.SchIterator_Destroy(Iter);
                end;
                if Found = 0 then ErrorText := 'source designator not found: ' + Name;
                if Selected.Count > 1000 then ErrorText := 'request expands to more than 1000 placed parts';
                if ErrorText <> '' then Break;
                Iter := TargetDoc.SchIterator_Create;
                Iter.AddFilter_ObjectSet(MkSet(eSchComponent));
                try
                    Obj := Iter.FirstSchObject;
                    while Obj <> nil do
                    begin
                        if CloneIsComponent(Obj) then
                            if SameText(Obj.Designator.Text, Name) then ErrorText := 'target designator already exists: ' + Name;
                        Obj := Iter.NextSchObject;
                    end;
                finally
                    TargetDoc.SchIterator_Destroy(Iter);
                end;
                if ErrorText <> '' then Break;
            end;
            if ErrorText <> '' then Break;
            SourceBefore := CloneSelectionState(SourceDoc, Selected);
            CloneCollectUIDs(SourceDoc, Used);
            CloneCollectUIDs(TargetDoc, Used);
            Client.ShowDocument(TargetClient);
            SchServer.ProcessControl.PreProcess(TargetDoc, '');
            Editing := True;
            for I := 0 to Selected.Count - 1 do
            begin
                ReportProgress;
                Failed := I;
                Comp := CloneFindUID(SourceDoc, Selected[I]);
                if Comp = nil then begin ErrorText := 'source component disappeared after preflight'; Break; end;
                CompBefore := CloneComponentSnapshot(Comp);
                Mapping.Clear;
                Replica := Comp.Replicate;
                if Replica = nil then begin ErrorText := 'component replication failed'; Break; end;
                if not CloneRenewUID(Replica, Used, Mapping) then
                    begin ErrorText := 'could not allocate a fresh component UID'; Break; end;
                ChildIter := Replica.SchIterator_Create;
                try
                    Child := ChildIter.FirstSchObject;
                    while Child <> nil do
                    begin
                        if not CloneRenewUID(Child, Used, Mapping) then
                            begin ErrorText := 'could not allocate a fresh child UID'; Break; end;
                        Child := ChildIter.NextSchObject;
                    end;
                finally
                    Replica.SchIterator_Destroy(ChildIter);
                end;
                if ErrorText <> '' then Break;
                LogScriptError('clone_schematic_components: register ' + Comp.Designator.Text + ' part ' + IntToStr(Comp.CurrentPartID));
                Started := True;
                TargetDoc.RegisterSchObjectInContainer(Replica);
                Inc(Applied);
                Registered.Add('"' + JSONEscapeString(Replica.UniqueId) + '"');
                SchServer.RobotManager.SendMessage(TargetDoc.I_ObjectAddress, c_BroadCast,
                    SCHM_PrimitiveRegistration, Replica.I_ObjectAddress);
                Item := TStringList.Create;
                try
                    Item.Add('"source": ' + CompBefore);
                    Item.Add('"clone": ' + CloneComponentSnapshot(Replica));
                    Item.Add('"uid_map": ' + BuildJSONArray(Mapping));
                    Copies.Add(BuildJSONObject(Item));
                finally
                    Item.Free;
                end;
            end;
            LogScriptError('clone_schematic_components: copy snapshots complete');
          until True;
        except
            ErrorText := ExceptionMessage;
        end;
        if Editing then
        begin
            LogScriptError('clone_schematic_components: finish target undo group');
            SchServer.ProcessControl.PostProcess(TargetDoc, '');
            Editing := False;
            LogScriptError('clone_schematic_components: target undo group finished');
        end;
        ReportProgress;
        if Started then
        begin
            TargetClient.SetModified(True);
            TargetDoc.GraphicallyInvalidate;
        end;
        if SourceDoc <> nil then
        begin
            if SourceBefore <> '[]' then
            begin
                SourceAfter := CloneSelectionState(SourceDoc, Selected);
                SourceUnchanged := SourceBefore = SourceAfter;
            end;
            SourceUnchanged := SourceUnchanged and (SourceClient.Modified = SourceModified);
        end;
        LogScriptError('clone_schematic_components: source readback complete');
        if not SourceUnchanged and (ErrorText = '') then ErrorText := 'source state changed during cloning';
        if ErrorText = '' then Failed := -1;
        AddJSONBoolean(Props, 'success', ErrorText = '');
        AddJSONProperty(Props, 'source_sheet', SourcePath);
        AddJSONProperty(Props, 'target_sheet', TargetPath);
        AddJSONBoolean(Props, 'saved', False);
        AddJSONBoolean(Props, 'source_unchanged', SourceUnchanged);
        AddJSONBoolean(Props, 'source_was_modified', SourceModified);
        if SourceClient <> nil then AddJSONBoolean(Props, 'source_is_modified', SourceClient.Modified);
        AddJSONBoolean(Props, 'target_was_modified', TargetModified);
        if TargetClient <> nil then AddJSONBoolean(Props, 'target_is_modified', TargetClient.Modified);
        AddJSONInteger(Props, 'cloned_count', Applied);
        AddJSONInteger(Props, 'failed_index', Failed);
        AddJSONBoolean(Props, 'may_have_partial_changes', (ErrorText <> '') and Started);
        Props.Add('"components": ' + BuildJSONArray(Copies));
        Props.Add('"registered_component_uids": ' + BuildJSONArray(Registered));
        Props.Add('"target_counts_before": ' + TargetBeforeCounts);
        if TargetDoc <> nil then Props.Add('"target_counts_after": ' + CloneTopCounts(TargetDoc));
        AddJSONInteger(Props, 'process_depth', SchServer.ProcessControl.ProcessDepth);
        if ErrorText <> '' then AddJSONProperty(Props, 'error', ErrorText);
        // JSONEscapeString already emits ASCII Unicode escapes per value.
        // Re-scanning a large multipart result with DocumentJSON is quadratic.
        Result := BuildJSONObject(Props);
        LogScriptError('clone_schematic_components: response ready');
    finally
        if Editing then SchServer.ProcessControl.PostProcess(TargetDoc, '');
        Spec.Free;
        Selected.Free;
        Parts.Free;
        Used.Free;
        Mapping.Free;
        Copies.Free;
        Registered.Free;
        Props.Free;
    end;
end;

function ExecuteCloneSchematicComponents(Request: TStringList): String;
var
    I: Integer;
begin
    Result := 'ERROR: clone spec_file is required';
    for I := 0 to Request.Count - 1 do
        if Pos('"spec_file":', Request[I]) > 0 then
        begin
            Result := CloneSchematicFromSpec(JSONLineValue(Request[I]));
            Exit;
        end;
end;
