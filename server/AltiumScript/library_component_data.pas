// Exact-path SchLib metadata updates. No document save or project update.

function LibraryDataASCIIJSON(Value: String): String;
var
    I: Integer;
begin
    // TStringList.SaveToFile uses the system ANSI code page. Escape Unicode
    // after JSON construction so the bridge cannot replace it with '?'.
    Result := '';
    for I := 1 to Length(Value) do
        if Ord(Value[I]) > 127 then Result := Result + '\u' + IntToHex(Ord(Value[I]), 4)
        else Result := Result + Value[I];
end;

function LibraryDataParameter(Comp: ISch_Component; Name: String; var Matches: Integer): ISch_Parameter;
var
    Iter: ISch_Iterator;
    Param: ISch_Parameter;
begin
    Result := nil;
    Matches := 0;
    Iter := Comp.SchIterator_Create;
    Iter.AddFilter_ObjectSet(MkSet(eParameter));
    try
        Param := Iter.FirstSchObject;
        while Param <> nil do
        begin
            if SameText(Param.Name, Name) then
            begin
                Result := Param;
                Matches := Matches + 1;
            end;
            Param := Iter.NextSchObject;
        end;
    finally
        Comp.SchIterator_Destroy(Iter);
    end;
end;

function LibraryDataModel(Comp: ISch_Component; Name: String; var Matches: Integer): ISch_Implementation;
var
    Iter: ISch_Iterator;
    Model: ISch_Implementation;
begin
    Result := nil;
    Matches := 0;
    Iter := Comp.SchIterator_Create;
    Iter.AddFilter_ObjectSet(MkSet(eImplementation));
    try
        Model := Iter.FirstSchObject;
        while Model <> nil do
        begin
            if SameText(Model.ModelType, 'PCBLIB') and SameText(Model.ModelName, Name) then
            begin
                Result := Model;
                Matches := Matches + 1;
            end;
            Model := Iter.NextSchObject;
        end;
    finally
        Comp.SchIterator_Destroy(Iter);
    end;
end;

function LibraryComponentKindValue(Name: String): Integer;
begin
    Result := -1;
    if Name = 'standard' then Result := eComponentKind_Standard;
    if Name = 'mechanical' then Result := eComponentKind_Mechanical;
    if Name = 'graphical' then Result := eComponentKind_Graphical;
    if Name = 'net_tie_bom' then Result := eComponentKind_NetTie_BOM;
    if Name = 'net_tie_no_bom' then Result := eComponentKind_NetTie_NoBOM;
    if Name = 'standard_no_bom' then Result := eComponentKind_Standard_NoBOM;
end;

function LibraryComponentKindName(Value: Integer): String;
begin
    Result := 'unknown';
    if Value = eComponentKind_Standard then Result := 'standard';
    if Value = eComponentKind_Mechanical then Result := 'mechanical';
    if Value = eComponentKind_Graphical then Result := 'graphical';
    if Value = eComponentKind_NetTie_BOM then Result := 'net_tie_bom';
    if Value = eComponentKind_NetTie_NoBOM then Result := 'net_tie_no_bom';
    if Value = eComponentKind_Standard_NoBOM then Result := 'standard_no_bom';
end;

function LibraryComponentSnapshot(Comp: ISch_Component): String;
var
    Iter: ISch_Iterator;
    Param: ISch_Parameter;
    Model: ISch_Implementation;
    Link: ISch_ModelDatafileLink;
    Props, Parameters, Models, ModelProps, Links, LinkProps: TStringList;
    I: Integer;
begin
    Props := TStringList.Create;
    Parameters := TStringList.Create;
    Models := TStringList.Create;
    try
        AddJSONProperty(Props, 'symbol_name', Comp.LibReference);
        AddJSONProperty(Props, 'designator_template', Comp.Designator.Text);
        AddJSONInteger(Props, 'designator_read_only_state', Comp.Designator.ReadOnlyState);
        AddJSONProperty(Props, 'component_kind', LibraryComponentKindName(Comp.ComponentKind));
        AddJSONInteger(Props, 'component_kind_code', Comp.ComponentKind);
        AddJSONProperty(Props, 'comment', Comp.Comment.Text);
        AddJSONProperty(Props, 'description', Comp.ComponentDescription);
        AddJSONInteger(Props, 'part_count', Comp.PartCount);
        Iter := Comp.SchIterator_Create;
        Iter.AddFilter_ObjectSet(MkSet(eParameter));
        try
            Param := Iter.FirstSchObject;
            while Param <> nil do
            begin
                AddJSONProperty(Parameters, Param.Name, Param.Text);
                Param := Iter.NextSchObject;
            end;
        finally
            Comp.SchIterator_Destroy(Iter);
        end;
        Iter := Comp.SchIterator_Create;
        Iter.AddFilter_ObjectSet(MkSet(eImplementation));
        try
            Model := Iter.FirstSchObject;
            while Model <> nil do
            begin
                ModelProps := TStringList.Create;
                Links := TStringList.Create;
                try
                    AddJSONProperty(ModelProps, 'model_name', Model.ModelName);
                    AddJSONProperty(ModelProps, 'model_type', Model.ModelType);
                    AddJSONProperty(ModelProps, 'description', Model.Description);
                    AddJSONBoolean(ModelProps, 'is_current', Model.IsCurrent);
                    AddJSONBoolean(ModelProps, 'use_component_library', Model.UseComponentLibrary);
                    AddJSONBoolean(ModelProps, 'datalinks_locked', Model.DatalinksLocked);
                    AddJSONBoolean(ModelProps, 'database_model', Model.DatabaseModel);
                    AddJSONBoolean(ModelProps, 'integrated_model', Model.IntegratedModel);
                    AddJSONProperty(ModelProps, 'map_as_string', Model.MapAsString);
                    for I := 0 to Model.DatafileLinkCount - 1 do
                    begin
                        Link := Model.GetState_SchDatafileLink(I);
                        LinkProps := TStringList.Create;
                        try
                            AddJSONProperty(LinkProps, 'entity_name', Link.EntityName);
                            AddJSONProperty(LinkProps, 'location', Link.Location);
                            AddJSONProperty(LinkProps, 'file_kind', Link.FileKind);
                            Links.Add(BuildJSONObject(LinkProps));
                        finally
                            LinkProps.Free;
                        end;
                    end;
                    ModelProps.Add('"datafile_links": ' + BuildJSONArray(Links));
                    Models.Add(BuildJSONObject(ModelProps));
                finally
                    ModelProps.Free;
                    Links.Free;
                end;
                Model := Iter.NextSchObject;
            end;
        finally
            Comp.SchIterator_Destroy(Iter);
        end;
        Props.Add('"parameters": ' + BuildJSONObject(Parameters));
        Props.Add('"models": ' + BuildJSONArray(Models));
        Result := BuildJSONObject(Props);
    finally
        Props.Free;
        Parameters.Free;
        Models.Free;
    end;
end;

function LibraryCanonicalMap(Value: String): String;
var
    Rows, Pads: TStringList;
    Group: String;
    CloseAt, ColonAt: Integer;
begin
    // Input identifiers cannot contain whitespace or map delimiters.
    Value := StringReplace(Value, ' ', '', REPLACEALL);
    Rows := TStringList.Create;
    Pads := TStringList.Create;
    try
        Pads.Delimiter := ',';
        while Value <> '' do
        begin
            CloseAt := Pos(')', Value);
            if (Copy(Value, 1, 1) <> '(') or (CloseAt = 0) then
            begin
                Result := Value;
                Exit;
            end;
            Group := Copy(Value, 2, CloseAt - 2);
            ColonAt := Pos(':', Group);
            Pads.DelimitedText := Copy(Group, ColonAt + 1, Length(Group));
            Pads.Sort;
            Rows.Add(Copy(Group, 1, ColonAt) + Pads.DelimitedText);
            Value := Copy(Value, CloseAt + 1, Length(Value));
            if Copy(Value, 1, 1) = ',' then Value := Copy(Value, 2, Length(Value));
        end;
        Rows.Sort;
        Result := Rows.Text;
    finally
        Rows.Free;
        Pads.Free;
    end;
end;

function LibraryValidateFootprint(Spec: TStringList; Prefix: String; Pins: TStringList): String;
var
    Lib: IPCB_Library;
    Footprint, Found: IPCB_LibComponent;
    Iter: IPCB_GroupIterator;
    Pad: IPCB_Pad;
    ServerDoc: IServerDocument;
    Pads: TStringList;
    Path, Name, Key, PinName, PadName: String;
    I, J, Matches: Integer;
begin
    Result := '';
    Path := EditValue(Spec, Prefix + 'library');
    Name := EditValue(Spec, Prefix + 'name');
    ServerDoc := OpenLibraryDocument('PcbLib', Path);
    if ServerDoc = nil then
    begin
        Result := 'could not open footprint library: ' + Path;
        Exit;
    end;
    Lib := GetPcbLibSafe(0);
    if Lib = nil then
    begin
        Result := 'PCB library server is unavailable';
        Exit;
    end;
    if not SameText(Lib.Board.FileName, Path) then
    begin
        Result := 'PCB library focus does not match requested path';
        Exit;
    end;
    Matches := 0;
    Found := nil;
    for I := 0 to Lib.ComponentCount - 1 do
    begin
        Footprint := Lib.GetComponent(I);
        if SameText(Footprint.Name, Name) then
        begin
            Found := Footprint;
            Matches := Matches + 1;
        end;
    end;
    if Matches <> 1 then
    begin
        Result := 'footprint must match exactly once; found ' + IntToStr(Matches) + ': ' + Name;
        Exit;
    end;
    Pads := TStringList.Create;
    Pads.CaseSensitive := True;
    try
        Iter := Found.GroupIterator_Create;
        Iter.AddFilter_ObjectSet(MkSet(ePadObject));
        try
            Pad := Iter.FirstPCBObject;
            while Pad <> nil do
            begin
                if Pads.IndexOf(Pad.Name) < 0 then Pads.Add(Pad.Name);
                Pad := Iter.NextPCBObject;
            end;
        finally
            Found.GroupIterator_Destroy(Iter);
        end;
        if StrToInt(EditValue(Spec, Prefix + 'pin_count')) <> Pins.Count then
        begin
            Result := 'pin_map must cover every distinct pin number across all symbol parts';
            Exit;
        end;
        for I := 0 to StrToInt(EditValue(Spec, Prefix + 'pin_count')) - 1 do
        begin
            Key := Prefix + 'pin' + IntToStr(I) + '.';
            PinName := EditValue(Spec, Key + 'name');
            if Pins.IndexOf(PinName) < 0 then
            begin
                Result := 'symbol pin not found: ' + PinName;
                Exit;
            end;
            for J := 0 to StrToInt(EditValue(Spec, Key + 'pad_count')) - 1 do
            begin
                PadName := EditValue(Spec, Key + 'pad' + IntToStr(J));
                if Pads.IndexOf(PadName) < 0 then
                begin
                    Result := 'footprint pad not found: ' + PadName;
                    Exit;
                end;
            end;
        end;
    finally
        Pads.Free;
    end;
end;

function SetLibraryComponentData(SpecPath: String): String;
var
    Spec, Pins, Props: TStringList;
    Lib: ISch_Lib;
    Comp, Candidate: ISch_Component;
    Iter: ISch_Iterator;
    Pin: ISch_Pin;
    Param: ISch_Parameter;
    Model: ISch_Implementation;
    Link: ISch_ModelDatafileLink;
    ClientDoc: IServerDocument;
    Path, SymbolName, Prefix, Name, BeforeState, AfterState, ErrorText, CurrentName: String;
    I, Matches: Integer;
    WasModified, NeedChanges, Editing, Started, Created: Boolean;
begin
    Result := 'ERROR: library component spec not found';
    if not FileExists(SpecPath) then Exit;
    Spec := TStringList.Create;
    Pins := TStringList.Create;
    Pins.CaseSensitive := True;
    Editing := False;
    Started := False;
    NeedChanges := False;
    ErrorText := '';
    CurrentName := '';
    try
        Spec.LoadFromFile(SpecPath);
        Path := EditValue(Spec, 'library_path');
        SymbolName := EditValue(Spec, 'symbol_name');
        if (not FileExists(Path)) or (LowerCase(ExtractFileExt(Path)) <> '.schlib') then
        begin
            Result := 'ERROR: target must be an existing .SchLib';
            Exit;
        end;
        ClientDoc := OpenLibraryDocument('SchLib', Path);
        if ClientDoc = nil then
        begin
            Result := 'ERROR: could not open target SchLib';
            Exit;
        end;
        Lib := SchServer.GetSchDocumentByPath(Path);
        if Lib = nil then
        begin
            Result := 'ERROR: target SchLib is unavailable';
            Exit;
        end;
        if Lib.ObjectId <> 33 then
        begin
            Result := 'ERROR: target is not a SchLib';
            Exit;
        end;
        if (NormalizedPath(ClientDoc.FileName) <> NormalizedPath(Path)) or
           (NormalizedPath(Lib.DocumentName) <> NormalizedPath(Path)) then
        begin
            Result := 'ERROR: opened SchLib does not match the exact requested path';
            Exit;
        end;
        if SchServer.ProcessControl.ProcessDepth <> 0 then
        begin
            Result := 'ERROR: schematic editor has an active transaction';
            Exit;
        end;
        Comp := nil;
        Matches := 0;
        Iter := Lib.SchLibIterator_Create;
        Iter.AddFilter_ObjectSet(MkSet(eSchComponent));
        try
            Candidate := Iter.FirstSchObject;
            while Candidate <> nil do
            begin
                if SameText(Candidate.LibReference, SymbolName) then
                begin
                    Comp := Candidate;
                    Matches := Matches + 1;
                end;
                Candidate := Iter.NextSchObject;
            end;
        finally
            Lib.SchIterator_Destroy(Iter);
        end;
        if Matches <> 1 then
        begin
            Result := 'ERROR: symbol must match exactly once; found ' + IntToStr(Matches);
            Exit;
        end;
        WasModified := ClientDoc.Modified;
        BeforeState := LibraryComponentSnapshot(Comp);
        Iter := Comp.SchIterator_Create;
        Iter.AddFilter_ObjectSet(MkSet(ePin));
        try
            Pin := Iter.FirstSchObject;
            while Pin <> nil do
            begin
                if Pins.IndexOf(Pin.Designator) < 0 then Pins.Add(Pin.Designator);
                Pin := Iter.NextSchObject;
            end;
        finally
            Comp.SchIterator_Destroy(Iter);
        end;
        try
            LogScriptError('set_library_component_data: preflight ' + SymbolName);
            if EditHas(Spec, 'comment') then
                if Comp.Comment.ReadOnlyState <> eReadOnly_None then ErrorText := 'Comment is read-only';
            if EditHas(Spec, 'comment') then NeedChanges := Comp.Comment.Text <> EditValue(Spec, 'comment');
            if EditHas(Spec, 'description') then
                NeedChanges := NeedChanges or (Comp.ComponentDescription <> EditValue(Spec, 'description'));
            if EditHas(Spec, 'designator_template') then
            begin
                // Native Designator normally protects its name, not its value.
                if Comp.Designator.Text <> EditValue(Spec, 'designator_template') then
                begin
                    if (Comp.Designator.ReadOnlyState <> eReadOnly_None) and
                       (Comp.Designator.ReadOnlyState <> eReadOnly_Name) then ErrorText := 'Designator value is read-only';
                    NeedChanges := True;
                end;
            end;
            if EditHas(Spec, 'component_kind') then
            begin
                if LibraryComponentKindValue(EditValue(Spec, 'component_kind')) < 0 then ErrorText := 'unsupported component kind';
                NeedChanges := NeedChanges or (Comp.ComponentKind <> LibraryComponentKindValue(EditValue(Spec, 'component_kind')));
            end;
            for I := 0 to StrToInt(EditValue(Spec, 'parameter_count')) - 1 do
            begin
                if ErrorText <> '' then Break;
                Prefix := 'p' + IntToStr(I) + '.';
                Param := LibraryDataParameter(Comp, EditValue(Spec, Prefix + 'name'), Matches);
                if Matches > 1 then
                begin
                    ErrorText := 'parameter name is ambiguous';
                    Break;
                end;
                if Matches = 1 then
                begin
                    if Param.ReadOnlyState <> eReadOnly_None then
                    begin
                        ErrorText := 'parameter is read-only: ' + Param.Name;
                        Break;
                    end;
                    NeedChanges := NeedChanges or (Param.Text <> EditValue(Spec, Prefix + 'value'));
                end
                else NeedChanges := True;
            end;
            for I := 0 to StrToInt(EditValue(Spec, 'model_count')) - 1 do
            begin
                if ErrorText <> '' then Break;
                Prefix := 'm' + IntToStr(I) + '.';
                Model := LibraryDataModel(Comp, EditValue(Spec, Prefix + 'name'), Matches);
                if Matches > 1 then
                begin
                    ErrorText := 'footprint model name is ambiguous';
                    Break;
                end;
                if Matches = 1 then
                begin
                    if Model.DatalinksLocked or Model.DatabaseModel or Model.IntegratedModel then
                    begin
                        ErrorText := 'footprint model is locked or managed';
                        Break;
                    end;
                    NeedChanges := NeedChanges or Model.UseComponentLibrary;
                    NeedChanges := NeedChanges or (LibraryCanonicalMap(Model.MapAsString) <>
                        LibraryCanonicalMap(EditValue(Spec, Prefix + 'map')));
                    if Model.DatafileLinkCount <> 1 then NeedChanges := True
                    else
                    begin
                        Link := Model.GetState_SchDatafileLink(0);
                        NeedChanges := NeedChanges or (not SameText(Link.Location, EditValue(Spec, Prefix + 'library'))) or
                            (not SameText(Link.EntityName, EditValue(Spec, Prefix + 'name'))) or (not SameText(Link.FileKind, 'PCBLIB'));
                    end;
                    if EditHas(Spec, Prefix + 'current') then
                        NeedChanges := NeedChanges or (Model.IsCurrent <> (EditValue(Spec, Prefix + 'current') = '1'));
                    if EditHas(Spec, Prefix + 'description') then
                        NeedChanges := NeedChanges or (Model.Description <> EditValue(Spec, Prefix + 'description'));
                end
                else NeedChanges := True;
                if EditValue(Spec, Prefix + 'current') = '1' then CurrentName := EditValue(Spec, Prefix + 'name');
                ErrorText := LibraryValidateFootprint(Spec, Prefix, Pins);
            end;

            if (ErrorText = '') and (CurrentName <> '') then
            begin
                Iter := Comp.SchIterator_Create;
                Iter.AddFilter_ObjectSet(MkSet(eImplementation));
                try
                    Model := Iter.FirstSchObject;
                    while Model <> nil do
                    begin
                        if SameText(Model.ModelType, 'PCBLIB') and Model.IsCurrent and
                           (not SameText(Model.ModelName, CurrentName)) then
                        begin
                            if Model.DatalinksLocked or Model.DatabaseModel or Model.IntegratedModel then
                                ErrorText := 'cannot deselect a locked or managed current footprint';
                            NeedChanges := True;
                        end;
                        Model := Iter.NextSchObject;
                    end;
                finally
                    Comp.SchIterator_Destroy(Iter);
                end;
            end;

            if (ErrorText = '') and NeedChanges then
            begin
                Client.ShowDocument(ClientDoc);
                SchServer.ProcessControl.PreProcess(Lib, '');
                Editing := True;
                Started := True;
                SchServer.RobotManager.SendMessage(Comp.I_ObjectAddress, c_BroadCast, SCHM_BeginModify, 0);
                try
                    if EditHas(Spec, 'comment') then Comp.Comment.Text := EditValue(Spec, 'comment');
                    if EditHas(Spec, 'description') then Comp.ComponentDescription := EditValue(Spec, 'description');
                    if EditHas(Spec, 'designator_template') then
                        if Comp.Designator.Text <> EditValue(Spec, 'designator_template') then
                        begin
                            SchServer.RobotManager.SendMessage(Comp.Designator.I_ObjectAddress, c_BroadCast, SCHM_BeginModify, 0);
                            try Comp.Designator.Text := EditValue(Spec, 'designator_template');
                            finally SchServer.RobotManager.SendMessage(Comp.Designator.I_ObjectAddress, c_BroadCast, SCHM_EndModify, 0); end;
                        end;
                    if EditHas(Spec, 'component_kind') then
                        Comp.ComponentKind := LibraryComponentKindValue(EditValue(Spec, 'component_kind'));
                    for I := 0 to StrToInt(EditValue(Spec, 'parameter_count')) - 1 do
                    begin
                        Prefix := 'p' + IntToStr(I) + '.';
                        Param := LibraryDataParameter(Comp, EditValue(Spec, Prefix + 'name'), Matches);
                        if Param = nil then
                        begin
                            Param := SchServer.SchObjectFactory(eParameter, eCreate_Default);
                            Param.Name := EditValue(Spec, Prefix + 'name');
                            Param.OwnerPartId := 0;
                            Param.IsHidden := True;
                            Param.ParamType := eParameterType_String;
                            Param.ReadOnlyState := eReadOnly_None;
                            Comp.AddSchObject(Param);
                            SchServer.RobotManager.SendMessage(Comp.I_ObjectAddress, c_BroadCast,
                                SCHM_PrimitiveRegistration, Param.I_ObjectAddress);
                        end;
                        Param.Text := EditValue(Spec, Prefix + 'value');
                    end;
                    if CurrentName <> '' then
                    begin
                        Iter := Comp.SchIterator_Create;
                        Iter.AddFilter_ObjectSet(MkSet(eImplementation));
                        try
                            Model := Iter.FirstSchObject;
                            while Model <> nil do
                            begin
                                if SameText(Model.ModelType, 'PCBLIB') and Model.IsCurrent then Model.IsCurrent := False;
                                Model := Iter.NextSchObject;
                            end;
                        finally
                            Comp.SchIterator_Destroy(Iter);
                        end;
                    end;
                    for I := 0 to StrToInt(EditValue(Spec, 'model_count')) - 1 do
                    begin
                        Prefix := 'm' + IntToStr(I) + '.';
                        Model := LibraryDataModel(Comp, EditValue(Spec, Prefix + 'name'), Matches);
                        Created := Model = nil;
                        if Created then Model := Comp.AddSchImplementation;
                        Model.ModelType := 'PCBLIB';
                        Model.ModelName := EditValue(Spec, Prefix + 'name');
                        Model.UseComponentLibrary := False;
                        Model.ClearAllDatafileLinks;
                        Model.AddDataFileLink(Model.ModelName, EditValue(Spec, Prefix + 'library'), 'PCBLib');
                        Model.MapAsString := EditValue(Spec, Prefix + 'map');
                        if EditHas(Spec, Prefix + 'description') then Model.Description := EditValue(Spec, Prefix + 'description');
                        if EditHas(Spec, Prefix + 'current') then Model.IsCurrent := EditValue(Spec, Prefix + 'current') = '1'
                        else if Created then Model.IsCurrent := False;
                        if Created then
                            SchServer.RobotManager.SendMessage(Comp.I_ObjectAddress, c_BroadCast,
                                SCHM_PrimitiveRegistration, Model.I_ObjectAddress);
                    end;
                finally
                    SchServer.RobotManager.SendMessage(Comp.I_ObjectAddress, c_BroadCast, SCHM_EndModify, 0);
                end;
            end;
        except
            ErrorText := ExceptionMessage;
        end;
        if Editing then
        begin
            SchServer.ProcessControl.PostProcess(Lib, '');
            Editing := False;
            Lib.GraphicallyInvalidate;
        end;
        Client.ShowDocument(ClientDoc);
        // SetModified can be ignored for metadata-only/empty symbols.
        if Started then MarkDocumentModified(Path);
        AfterState := LibraryComponentSnapshot(Comp);
        Props := TStringList.Create;
        try
            AddJSONBoolean(Props, 'success', ErrorText = '');
            AddJSONProperty(Props, 'library_path', Path);
            AddJSONProperty(Props, 'symbol_name', Comp.LibReference);
            AddJSONBoolean(Props, 'saved', False);
            AddJSONBoolean(Props, 'changed', BeforeState <> AfterState);
            AddJSONBoolean(Props, 'was_modified', WasModified);
            AddJSONBoolean(Props, 'is_modified', ClientDoc.Modified);
            AddJSONBoolean(Props, 'may_have_partial_changes', (ErrorText <> '') and Started);
            if ErrorText <> '' then AddJSONProperty(Props, 'error', ErrorText);
            Props.Add('"before": ' + BeforeState);
            Props.Add('"after": ' + AfterState);
            Result := LibraryDataASCIIJSON(BuildJSONObject(Props));
        finally
            Props.Free;
        end;
    finally
        if Editing then SchServer.ProcessControl.PostProcess(Lib, '');
        Spec.Free;
        Pins.Free;
    end;
end;

function ExecuteLibraryComponentData(Request: TStringList): String;
var
    I: Integer;
begin
    Result := 'ERROR: no library component spec_file';
    for I := 0 to Request.Count - 1 do
        if Pos('"spec_file":', Request[I]) > 0 then
        begin
            Result := SetLibraryComponentData(JSONLineValue(Request[I]));
            Exit;
        end;
end;
