// edit_schematic: exact-path edits in memory. Never save or clear Modified.
// The Python boundary validates fields/types before this escaped spec is read.

function EditValue(Spec: TStringList; Key: String): String;
begin
    Result := JSONUnescape(Spec.Values[Key]);
end;

function EditHas(Spec: TStringList; Key: String): Boolean;
begin
    Result := Spec.IndexOfName(Key) >= 0;
end;

function EditKind(Obj: ISch_GraphicalObject): String;
begin
    Result := '';
    case Obj.ObjectId of
        eSchComponent: Result := 'component';
        eWire: Result := 'wire';
        eNetLabel: Result := 'net_label';
        ePort: Result := 'port';
        ePowerObject: Result := 'power_port';
        eJunction: Result := 'junction';
        eLabel: Result := 'text';
    end;
end;

// Never match across sheets or fall back to the currently focused document.
function EditFind(Doc: ISch_Document; Kind, UID, Designator: String;
                  var Matches: Integer): ISch_GraphicalObject;
var
    Iter: ISch_Iterator;
    Obj: ISch_GraphicalObject;
    Match: Boolean;
begin
    Result := nil;
    Matches := 0;
    Iter := Doc.SchIterator_Create;
    try
        Obj := Iter.FirstSchObject;
        while Obj <> nil do
        begin
            if EditKind(Obj) = Kind then
            begin
                Match := False;
                if UID <> '' then Match := Obj.UniqueId = UID
                else if Kind = 'component' then
                    Match := UpperCase(Obj.Designator.Text) = UpperCase(Designator);
                if Match then
                begin
                    Result := Obj;
                    Matches := Matches + 1;
                end;
            end;
            Obj := Iter.NextSchObject;
        end;
    finally
        Doc.SchIterator_Destroy(Iter);
    end;
end;

function EditSource(Doc: ISch_Document; Spec: TStringList; Prefix: String;
                    var Matches: Integer): ISch_Component;
var
    Lib: ISch_Document;
    ClientDoc: IServerDocument;
    Iter: ISch_Iterator;
    Obj: ISch_Component;
    Path: String;
begin
    Result := nil;
    Matches := 0;
    if EditHas(Spec, Prefix + 'source_designator') then
    begin
        Result := EditFind(Doc, 'component', '', EditValue(Spec, Prefix + 'source_designator'), Matches);
        Exit;
    end;
    Path := EditValue(Spec, Prefix + 'symbol_library');
    // OpenDocument reuses the in-memory library. It is never modified/saved.
    if not FileExists(Path) then Exit;
    ClientDoc := Client.OpenDocument('SchLib', Path);
    if ClientDoc = nil then Exit;
    Client.ShowDocument(ClientDoc);
    Lib := SchServer.GetSchDocumentByPath(Path);
    if Lib = nil then Exit;
    if Lib.ObjectId <> 33 then Exit;
    Iter := Lib.SchLibIterator_Create;
    Iter.AddFilter_ObjectSet(MkSet(eSchComponent));
    try
        Obj := Iter.FirstSchObject;
        while Obj <> nil do
        begin
            if Obj.LibReference = EditValue(Spec, Prefix + 'symbol') then
            begin
                Result := Obj;
                Matches := Matches + 1;
            end;
            Obj := Iter.NextSchObject;
        end;
    finally
        Lib.SchIterator_Destroy(Iter);
    end;
end;

function EditSnapshot(Doc: ISch_Document): String;
var
    Iter, ChildIter: ISch_Iterator;
    Obj, Child: ISch_GraphicalObject;
    Items, Props, Values, Pins, PinProps: TStringList;
    Points: String;
    V, X, Y: Integer;
begin
    Items := TStringList.Create;
    try
        Iter := Doc.SchIterator_Create;
        try
            Obj := Iter.FirstSchObject;
            while Obj <> nil do
            begin
                if EditKind(Obj) <> '' then
                begin
                    Props := TStringList.Create;
                    try
                        AddJSONProperty(Props, 'type', EditKind(Obj));
                        AddJSONProperty(Props, 'unique_id', Obj.UniqueId);
                        AddJSONNumber(Props, 'x', CoordToMils(Obj.Location.X));
                        AddJSONNumber(Props, 'y', CoordToMils(Obj.Location.Y));
                        if EditKind(Obj) = 'wire' then
                        begin
                            Points := '';
                            for V := 1 to Obj.VerticesCount do
                            begin
                                if Points <> '' then Points := Points + ',';
                                Points := Points + StringReplace(FloatToStr(CoordToMils(Obj.Vertex[V].X)), ',', '.', REPLACEALL) + ',' +
                                    StringReplace(FloatToStr(CoordToMils(Obj.Vertex[V].Y)), ',', '.', REPLACEALL);
                            end;
                            Props.Add('"points": [' + Points + ']');
                        end
                        else if EditKind(Obj) = 'component' then
                        begin
                            AddJSONProperty(Props, 'designator', Obj.Designator.Text);
                            AddJSONProperty(Props, 'symbol', Obj.LibReference);
                            AddJSONProperty(Props, 'comment', Obj.Comment.Text);
                            AddJSONProperty(Props, 'description', Obj.ComponentDescription);
                            AddJSONProperty(Props, 'design_item_id', Obj.DesignItemID);
                            AddJSONInteger(Props, 'orientation', Obj.Orientation);
                            AddJSONBoolean(Props, 'mirror', Obj.IsMirrored);
                            Values := TStringList.Create;
                            Pins := TStringList.Create;
                            try
                                ChildIter := Obj.SchIterator_Create;
                                try
                                    Child := ChildIter.FirstSchObject;
                                    while Child <> nil do
                                    begin
                                        if Child.ObjectId = eParameter then
                                            AddJSONProperty(Values, Child.Name, Child.Text);
                                        if Child.ObjectId = ePin then
                                        begin
                                            GetPinHotEnd(Child, X, Y);
                                            PinProps := TStringList.Create;
                                            try
                                                AddJSONProperty(PinProps, 'designator', Child.Designator);
                                                AddJSONProperty(PinProps, 'name', Child.Name);
                                                AddJSONNumber(PinProps, 'x', CoordToMils(X));
                                                AddJSONNumber(PinProps, 'y', CoordToMils(Y));
                                                Pins.Add(BuildJSONObject(PinProps));
                                            finally
                                                PinProps.Free;
                                            end;
                                        end;
                                        Child := ChildIter.NextSchObject;
                                    end;
                                finally
                                    Obj.SchIterator_Destroy(ChildIter);
                                end;
                                Props.Add('"parameters": ' + BuildJSONObject(Values));
                                Props.Add('"pins": ' + BuildJSONArray(Pins));
                            finally
                                Values.Free;
                                Pins.Free;
                            end;
                        end
                        else if EditKind(Obj) = 'port' then
                        begin
                            AddJSONProperty(Props, 'name', Obj.Name);
                            AddJSONInteger(Props, 'io_type', Obj.IOType);
                            AddJSONInteger(Props, 'style', Obj.Style);
                            AddJSONNumber(Props, 'width', CoordToMils(Obj.Width));
                        end
                        else if EditKind(Obj) <> 'junction' then
                        begin
                            AddJSONProperty(Props, 'text', Obj.Text);
                            AddJSONInteger(Props, 'orientation', Obj.Orientation);
                            if EditKind(Obj) = 'power_port' then
                            begin
                                AddJSONInteger(Props, 'style', Obj.Style);
                                AddJSONBoolean(Props, 'show_net_name', Obj.ShowNetName);
                            end;
                        end;
                        Items.Add(BuildJSONObject(Props));
                    finally
                        Props.Free;
                    end;
                end;
                Obj := Iter.NextSchObject;
            end;
        finally
            Doc.SchIterator_Destroy(Iter);
        end;
        Result := BuildJSONArray(Items);
    finally
        Items.Free;
    end;
end;

procedure EditProperties(Obj: ISch_GraphicalObject; Spec: TStringList; Prefix, Kind: String);
var
    Iter: ISch_Iterator;
    Param, Found: ISch_Parameter;
    V, Count: Integer;
    Name, Key: String;
begin
    if EditHas(Spec, Prefix + 'orientation') then Obj.Orientation := StrToInt(EditValue(Spec, Prefix + 'orientation'));
    if EditHas(Spec, Prefix + 'x') then
    begin
        if Kind = 'component' then
            Obj.MoveByXY(MilsToCoord(SafeStrToFloat(EditValue(Spec, Prefix + 'x'))) - Obj.Location.X,
                         MilsToCoord(SafeStrToFloat(EditValue(Spec, Prefix + 'y'))) - Obj.Location.Y)
        else Obj.Location := Point(MilsToCoord(SafeStrToFloat(EditValue(Spec, Prefix + 'x'))),
                                   MilsToCoord(SafeStrToFloat(EditValue(Spec, Prefix + 'y'))));
    end;
    if Kind = 'component' then
    begin
        if EditHas(Spec, Prefix + 'mirror') then
            if Obj.IsMirrored <> (EditValue(Spec, Prefix + 'mirror') = '1') then Obj.Mirror(Obj.Location);
        if EditHas(Spec, Prefix + 'designator') then Obj.Designator.Text := EditValue(Spec, Prefix + 'designator');
        if EditHas(Spec, Prefix + 'comment') then Obj.Comment.Text := EditValue(Spec, Prefix + 'comment');
        if EditHas(Spec, Prefix + 'description') then Obj.ComponentDescription := EditValue(Spec, Prefix + 'description');
        if EditHas(Spec, Prefix + 'design_item_id') then Obj.DesignItemID := EditValue(Spec, Prefix + 'design_item_id');
        if EditHas(Spec, Prefix + 'parameter_count') then
            for V := 0 to StrToInt(EditValue(Spec, Prefix + 'parameter_count')) - 1 do
            begin
                Key := Prefix + 'parameter' + IntToStr(V) + '.';
                Name := EditValue(Spec, Key + 'name');
                Found := nil;
                Iter := Obj.SchIterator_Create;
                Iter.AddFilter_ObjectSet(MkSet(eParameter));
                try
                    Param := Iter.FirstSchObject;
                    while Param <> nil do
                    begin
                        if UpperCase(Param.Name) = UpperCase(Name) then Found := Param;
                        Param := Iter.NextSchObject;
                    end;
                finally
                    Obj.SchIterator_Destroy(Iter);
                end;
                if Found = nil then
                begin
                    Found := SchServer.SchObjectFactory(eParameter, eCreate_Default);
                    Found.Name := Name;
                    Found.IsHidden := True;
                    Found.ParamType := eParameterType_String;
                    Obj.AddSchObject(Found);
                    SchServer.RobotManager.SendMessage(Obj.I_ObjectAddress, c_BroadCast,
                        SCHM_PrimitiveRegistration, Found.I_ObjectAddress);
                end;
                Found.Text := EditValue(Spec, Key + 'value');
            end;
    end;
    if Kind = 'wire' then
    begin
        Count := StrToInt(EditValue(Spec, Prefix + 'point_count'));
        Obj.VerticesCount := Count;
        for V := 0 to Count - 1 do
            Obj.SetState_Vertex(V + 1, Point(
                MilsToCoord(SafeStrToFloat(EditValue(Spec, Prefix + 'point' + IntToStr(V * 2)))),
                MilsToCoord(SafeStrToFloat(EditValue(Spec, Prefix + 'point' + IntToStr(V * 2 + 1))))));
    end;
    if EditHas(Spec, Prefix + 'text') then Obj.Text := EditValue(Spec, Prefix + 'text');
    if EditHas(Spec, Prefix + 'name') then Obj.Name := EditValue(Spec, Prefix + 'name');
    if EditHas(Spec, Prefix + 'style') then Obj.Style := StrToInt(EditValue(Spec, Prefix + 'style'));
    if EditHas(Spec, Prefix + 'io_type') then Obj.IOType := StrToInt(EditValue(Spec, Prefix + 'io_type'));
    if EditHas(Spec, Prefix + 'width') then Obj.Width := MilsToCoord(SafeStrToFloat(EditValue(Spec, Prefix + 'width')));
    if EditHas(Spec, Prefix + 'show_net_name') then Obj.ShowNetName := EditValue(Spec, Prefix + 'show_net_name') = '1';
end;

function EditSchematicFromSpec(SpecPath: String): String;
var
    Spec, UsedTargets, Designators, Results, Props: TStringList;
    Doc: ISch_Document;
    ClientDoc: IServerDocument;
    Obj: ISch_GraphicalObject;
    Source: ISch_Component;
    Iter: ISch_Iterator;
    Child: ISch_GraphicalObject;
    Path, Prefix, Action, Kind, UID, ErrorText: String;
    I, Count, Matches, Applied, FailedIndex: Integer;
    Editing, Started, WasModified: Boolean;
begin
    Result := 'ERROR: edit_schematic spec file not found';
    if not FileExists(SpecPath) then Exit;
    Spec := TStringList.Create;
    UsedTargets := TStringList.Create;
    Designators := TStringList.Create;
    Results := TStringList.Create;
    Applied := 0;
    FailedIndex := -1;
    Editing := False;
    Started := False;
    ErrorText := '';
    try
        LogScriptError('edit_schematic: read spec');
        Spec.LoadFromFile(SpecPath);
        Path := EditValue(Spec, 'sheet_path');
        if LowerCase(ExtractFileExt(Path)) <> '.schdoc' then
        begin
            Result := 'ERROR: target must be a SchDoc';
            Exit;
        end;
        LogScriptError('edit_schematic: open exact target ' + Path);
        ClientDoc := FindOpenDocument(Path);
        if (ClientDoc = nil) and FileExists(Path) then ClientDoc := Client.OpenDocument('SCH', Path);
        if ClientDoc = nil then
        begin
            Result := 'ERROR: could not open target schematic';
            Exit;
        end;
        Client.ShowDocument(ClientDoc);
        Doc := SchServer.GetSchDocumentByPath(Path);
        if Doc = nil then
        begin
            Result := 'ERROR: target schematic not available';
            Exit;
        end;
        LogScriptError('edit_schematic: target kind ' + IntToStr(Doc.ObjectId));
        if Doc.ObjectId <> 32 then
        begin
            Result := 'ERROR: refusing to edit a library or other document kind';
            Exit;
        end;
        if NormalizedPath(Doc.DocumentName) <> NormalizedPath(Path) then
        begin
            Result := 'ERROR: focused schematic identity mismatch';
            Exit;
        end;
        WasModified := ClientDoc.Modified;
        LogScriptError('edit_schematic: preflight');
        Count := StrToInt(EditValue(Spec, 'count'));
        try
            if (Count > 0) and (SchServer.ProcessControl.ProcessDepth <> 0) then
                ErrorText := 'schematic editor has an active transaction; finish the current action first';
            // Resolve ALL targets and symbol sources before changing the sheet.
            for I := 0 to Count - 1 do
            begin
                if ErrorText <> '' then Break;
                FailedIndex := I;
                Prefix := IntToStr(I) + '.';
                Action := EditValue(Spec, Prefix + 'op');
                Kind := EditValue(Spec, Prefix + 'type');
                if Action <> 'add' then
                begin
                    Obj := EditFind(Doc, Kind, EditValue(Spec, Prefix + 'target_unique_id'),
                        EditValue(Spec, Prefix + 'target_designator'), Matches);
                    if Matches <> 1 then
                    begin
                        ErrorText := 'target must match exactly one ' + Kind + '; found ' + IntToStr(Matches);
                        Break;
                    end;
                    UID := Obj.UniqueId;
                    if UID = '' then
                    begin
                        ErrorText := 'target has no unique_id; refusing an unsafe edit';
                        Break;
                    end;
                    if UsedTargets.IndexOf(UID) >= 0 then
                    begin
                        ErrorText := 'a target may be edited only once per batch';
                        Break;
                    end;
                    UsedTargets.Add(UID);
                    Spec.Values[Prefix + 'resolved_uid'] := UID;
                end;
                if (Kind = 'component') and (Action = 'add') then
                begin
                    Source := EditSource(Doc, Spec, Prefix, Matches);
                    if Matches <> 1 then
                    begin
                        ErrorText := 'component source must match exactly once; found ' + IntToStr(Matches);
                        Break;
                    end;
                end;
                if EditHas(Spec, Prefix + 'designator') then
                begin
                    UID := '';
                    if Action = 'update' then UID := EditValue(Spec, Prefix + 'resolved_uid');
                    Obj := EditFind(Doc, 'component', '', EditValue(Spec, Prefix + 'designator'), Matches);
                    if Matches = 1 then
                        if (UID <> '') and (Obj.UniqueId = UID) then Matches := 0;
                    if (Matches <> 0) or (Designators.IndexOf(UpperCase(EditValue(Spec, Prefix + 'designator'))) >= 0) then
                    begin
                        ErrorText := 'component designator already exists or is reserved by this batch';
                        Break;
                    end;
                    Designators.Add(UpperCase(EditValue(Spec, Prefix + 'designator')));
                end;
            end;

            if (ErrorText = '') and (Count > 0) then
            begin
                Client.ShowDocument(ClientDoc);
                SchServer.ProcessControl.PreProcess(Doc, '');
                Editing := True;
                for I := 0 to Count - 1 do
                begin
                    FailedIndex := I;
                    Prefix := IntToStr(I) + '.';
                    Action := EditValue(Spec, Prefix + 'op');
                    Kind := EditValue(Spec, Prefix + 'type');
                    LogScriptError('edit_schematic: operation ' + IntToStr(I) + ' ' + Action + ' ' + Kind);
                    Started := True;
                    if Action = 'add' then
                    begin
                        Obj := nil;
                        if Kind = 'component' then
                        begin
                            Source := EditSource(Doc, Spec, Prefix, Matches);
                            Obj := ReplicateLibSymbol(Source);
                            Iter := Obj.SchIterator_Create;
                            try
                                Child := Iter.FirstSchObject;
                                while Child <> nil do
                                begin
                                    Child.UniqueId := GetWorkspace.DM_GenerateUniqueID;
                                    Child := Iter.NextSchObject;
                                end;
                            finally
                                Obj.SchIterator_Destroy(Iter);
                            end;
                        end;
                        if Kind = 'wire' then Obj := SchServer.SchObjectFactory(eWire, eCreate_GlobalCopy);
                        if Kind = 'net_label' then Obj := SchServer.SchObjectFactory(eNetLabel, eCreate_GlobalCopy);
                        if Kind = 'port' then Obj := SchServer.SchObjectFactory(ePort, eCreate_GlobalCopy);
                        if Kind = 'power_port' then Obj := SchServer.SchObjectFactory(ePowerObject, eCreate_GlobalCopy);
                        if Kind = 'junction' then Obj := SchServer.SchObjectFactory(eJunction, eCreate_GlobalCopy);
                        if Kind = 'text' then Obj := SchServer.SchObjectFactory(eLabel, eCreate_GlobalCopy);
                        Obj.UniqueId := GetWorkspace.DM_GenerateUniqueID;
                        EditProperties(Obj, Spec, Prefix, Kind);
                        Doc.RegisterSchObjectInContainer(Obj);
                        SchServer.RobotManager.SendMessage(Doc.I_ObjectAddress, c_BroadCast,
                            SCHM_PrimitiveRegistration, Obj.I_ObjectAddress);
                        UID := Obj.UniqueId;
                    end
                    else
                    begin
                        UID := EditValue(Spec, Prefix + 'resolved_uid');
                        Obj := EditFind(Doc, Kind, UID, '', Matches);
                        if Action = 'delete' then
                        begin
                            // Remove keeps the object available to the undo system.
                            // UnRegisterSchObjectFromContainer frees it immediately.
                            Doc.RemoveSchObject(Obj);
                            SchServer.RobotManager.SendMessage(Doc.I_ObjectAddress, c_BroadCast,
                                SCHM_PrimitiveRegistration, Obj.I_ObjectAddress);
                        end
                        else
                        begin
                            SchServer.RobotManager.SendMessage(Obj.I_ObjectAddress, c_BroadCast, SCHM_BeginModify, 0);
                            try
                                EditProperties(Obj, Spec, Prefix, Kind);
                            finally
                                SchServer.RobotManager.SendMessage(Obj.I_ObjectAddress, c_BroadCast, SCHM_EndModify, 0);
                            end;
                        end;
                    end;
                    Applied := Applied + 1;
                    Props := TStringList.Create;
                    try
                        AddJSONInteger(Props, 'index', I);
                        AddJSONProperty(Props, 'op', Action);
                        AddJSONProperty(Props, 'type', Kind);
                        AddJSONProperty(Props, 'unique_id', UID);
                        Results.Add(BuildJSONObject(Props));
                    finally
                        Props.Free;
                    end;
                end;
            end;
        except
            ErrorText := ExceptionMessage;
        end;
        if Editing then
        begin
            SchServer.ProcessControl.PostProcess(Doc, '');
            Editing := False;
            ClientDoc.SetModified(True);
            Doc.GraphicallyInvalidate;
            Client.ShowDocument(ClientDoc);
        end;
        Props := TStringList.Create;
        try
            AddJSONBoolean(Props, 'success', ErrorText = '');
            AddJSONProperty(Props, 'sheet_path', Path);
            AddJSONProperty(Props, 'coordinate_units', 'mils');
            AddJSONBoolean(Props, 'saved', False);
            AddJSONBoolean(Props, 'was_modified', WasModified);
            AddJSONInteger(Props, 'applied_count', Applied);
            if ErrorText <> '' then
            begin
                AddJSONProperty(Props, 'error', ErrorText);
                AddJSONInteger(Props, 'failed_index', FailedIndex);
                AddJSONBoolean(Props, 'may_have_partial_changes', Started);
            end;
            Props.Add('"results": ' + BuildJSONArray(Results));
            LogScriptError('edit_schematic: snapshot');
            Props.Add('"objects": ' + EditSnapshot(Doc));
            LogScriptError('edit_schematic: complete');
            Result := BuildJSONObject(Props);
        finally
            Props.Free;
        end;
    finally
        if Editing then SchServer.ProcessControl.PostProcess(Doc, '');
        Spec.Free;
        UsedTargets.Free;
        Designators.Free;
        Results.Free;
    end;
end;

function ExecuteEditSchematic(Request: TStringList): String;
var
    I: Integer;
begin
    Result := 'ERROR: no edit_schematic spec_file';
    for I := 0 to Request.Count - 1 do
        if Pos('"spec_file":', Request[I]) > 0 then
        begin
            Result := EditSchematicFromSpec(JSONLineValue(Request[I]));
            Exit;
        end;
end;
