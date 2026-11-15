// Native sheet hierarchy and non-electrical drawing. Exact target, no saves.

function DiagramSide(Name: String): Integer;
begin
    Result := eLeftSide;
    if Name = 'right' then Result := eRightSide;
    if Name = 'top' then Result := eTopSide;
    if Name = 'bottom' then Result := eBottomSide;
end;

function DiagramSideName(Value: Integer): String;
begin
    Result := 'left';
    if Value = eRightSide then Result := 'right';
    if Value = eTopSide then Result := 'top';
    if Value = eBottomSide then Result := 'bottom';
end;

function DiagramIO(Name: String): Integer;
begin
    Result := ePortUnspecified;
    if Name = 'input' then Result := ePortInput;
    if Name = 'output' then Result := ePortOutput;
    if Name = 'bidirectional' then Result := ePortBidirectional;
end;

function DiagramIOName(Value: Integer): String;
begin
    Result := 'unspecified';
    if Value = ePortInput then Result := 'input';
    if Value = ePortOutput then Result := 'output';
    if Value = ePortBidirectional then Result := 'bidirectional';
end;

function DiagramFind(Doc: ISch_Document; UID: String; var Matches: Integer): ISch_SheetSymbol;
var Iter: ISch_Iterator; Obj: ISch_SheetSymbol;
begin
    Result := nil;
    Matches := 0;
    Iter := Doc.SchIterator_Create;
    Iter.AddFilter_ObjectSet(MkSet(eSheetSymbol));
    try
        Obj := Iter.FirstSchObject;
        while Obj <> nil do
        begin
            if Obj.UniqueId = UID then begin Result := Obj; Matches := Matches + 1; end;
            Obj := Iter.NextSchObject;
        end;
    finally
        Doc.SchIterator_Destroy(Iter);
    end;
end;

function DiagramSymbolJSON(Symbol: ISch_SheetSymbol): String;
var
    Props, Entries, P: TStringList;
    Iter: ISch_Iterator;
    Entry: ISch_SheetEntry;
    Owner: ISch_SheetSymbol;
begin
    Props := TStringList.Create;
    Entries := TStringList.Create;
    try
        AddJSONProperty(Props, 'type', 'sheet_symbol');
        AddJSONProperty(Props, 'unique_id', Symbol.UniqueId);
        AddJSONProperty(Props, 'designator', Symbol.SheetName.Text);
        AddJSONProperty(Props, 'filename', Symbol.SheetFileName.Text);
        AddJSONNumber(Props, 'x', CoordToMils(Symbol.Location.X));
        // Native sheet-symbol origin is top-left; public coordinates are lower-left.
        AddJSONNumber(Props, 'y', CoordToMils(Symbol.Location.Y - Symbol.YSize));
        AddJSONNumber(Props, 'width', CoordToMils(Symbol.XSize));
        AddJSONNumber(Props, 'height', CoordToMils(Symbol.YSize));
        Iter := Symbol.SchIterator_Create;
        Iter.AddFilter_ObjectSet(MkSet(eSheetEntry));
        try
            Entry := Iter.FirstSchObject;
            while Entry <> nil do
            begin
                P := TStringList.Create;
                try
                    AddJSONProperty(P, 'unique_id', Entry.UniqueId);
                    AddJSONProperty(P, 'name', Entry.Name);
                    AddJSONProperty(P, 'side', DiagramSideName(Entry.Side));
                    AddJSONProperty(P, 'io_type', DiagramIOName(Entry.IOType));
                    AddJSONNumber(P, 'offset', CoordToMils(Entry.DistanceFromTop));
                    AddJSONNumber(P, 'x', CoordToMils(Entry.Location.X));
                    AddJSONNumber(P, 'y', CoordToMils(Entry.Location.Y));
                    Owner := Entry.OwnerSheetSymbol;
                    if Owner <> nil then AddJSONProperty(P, 'owner_unique_id', Owner.UniqueId);
                    Entries.Add(BuildJSONObject(P));
                finally P.Free; end;
                Entry := Iter.NextSchObject;
            end;
        finally Symbol.SchIterator_Destroy(Iter); end;
        Props.Add('"entries":' + BuildJSONArray(Entries));
        Result := BuildJSONObject(Props);
    finally Props.Free; Entries.Free; end;
end;

function DiagramSnapshot(Doc: ISch_Document; SymbolsOnly: Boolean): String;
var
    Iter: ISch_Iterator;
    Obj: ISch_GraphicalObject;
    Rect: ISch_Rectangle;
    Poly: ISch_Polyline;
    LabelObj: ISch_Label;
    Items, P, Points: TStringList;
    J: Integer;
begin
    Items := TStringList.Create;
    try
        Iter := Doc.SchIterator_Create;
        if SymbolsOnly then Iter.AddFilter_ObjectSet(MkSet(eSheetSymbol))
        else Iter.AddFilter_ObjectSet(MkSet(eSheetSymbol, eRectangle, ePolyline, eLabel));
        try
            Obj := Iter.FirstSchObject;
            while Obj <> nil do
            begin
                if Obj.ObjectId = eSheetSymbol then Items.Add(DiagramSymbolJSON(Obj))
                else
                begin
                    P := TStringList.Create;
                    try
                        AddJSONProperty(P, 'unique_id', Obj.UniqueId);
                        AddJSONNumber(P, 'x', CoordToMils(Obj.Location.X));
                        AddJSONNumber(P, 'y', CoordToMils(Obj.Location.Y));
                        if Obj.ObjectId = eRectangle then
                        begin
                            Rect := Obj;
                            AddJSONProperty(P, 'type', 'rectangle');
                            AddJSONNumber(P, 'width', CoordToMils(Rect.Corner.X - Rect.Location.X));
                            AddJSONNumber(P, 'height', CoordToMils(Rect.Corner.Y - Rect.Location.Y));
                        end;
                        if Obj.ObjectId = eLabel then
                        begin
                            LabelObj := Obj;
                            AddJSONProperty(P, 'type', 'text');
                            AddJSONProperty(P, 'text', LabelObj.Text);
                        end;
                        if Obj.ObjectId = ePolyline then
                        begin
                            Poly := Obj;
                            AddJSONProperty(P, 'type', 'polyline');
                            Points := TStringList.Create;
                            try
                                for J := 1 to Poly.VerticesCount do
                                    Points.Add('[' + StringReplace(FloatToStr(CoordToMils(Poly.Vertex[J].X)), ',', '.', REPLACEALL) + ',' +
                                        StringReplace(FloatToStr(CoordToMils(Poly.Vertex[J].Y)), ',', '.', REPLACEALL) + ']');
                                P.Add('"points":' + BuildJSONArray(Points));
                            finally Points.Free; end;
                        end;
                        Items.Add(BuildJSONObject(P));
                    finally P.Free; end;
                end;
                Obj := Iter.NextSchObject;
            end;
        finally Doc.SchIterator_Destroy(Iter); end;
        Result := BuildJSONArray(Items);
    finally Items.Free; end;
end;

procedure DiagramPositionEntry(Symbol: ISch_SheetSymbol; Entry: ISch_SheetEntry);
var X, Y: Integer;
begin
    X := Symbol.Location.X;
    Y := Symbol.Location.Y;
    if (Entry.Side = eLeftSide) or (Entry.Side = eRightSide) then
    begin
        Y := Y - Entry.DistanceFromTop;
        if Entry.Side = eRightSide then X := X + Symbol.XSize;
    end
    else
    begin
        X := X + Entry.DistanceFromTop;
        if Entry.Side = eBottomSide then Y := Y - Symbol.YSize;
    end;
    Entry.Location := Point(X, Y);
end;

procedure DiagramSetSymbol(Symbol: ISch_SheetSymbol; Spec: TStringList; P: String);
var
    Iter: ISch_Iterator;
    Entry, NextEntry: ISch_SheetEntry;
    EP: String;
    I, X, Bottom: Integer;
    Geometry: Boolean;
begin
    X := Symbol.Location.X;
    Bottom := Symbol.Location.Y - Symbol.YSize;
    Geometry := EditHas(Spec, P + 'x') or EditHas(Spec, P + 'width') or EditHas(Spec, P + 'height');
    if EditHas(Spec, P + 'x') then
    begin
        X := MilsToCoord(SafeStrToFloat(EditValue(Spec, P + 'x')));
        Bottom := MilsToCoord(SafeStrToFloat(EditValue(Spec, P + 'y')));
    end;
    if EditHas(Spec, P + 'width') then Symbol.XSize := MilsToCoord(SafeStrToFloat(EditValue(Spec, P + 'width')));
    if EditHas(Spec, P + 'height') then Symbol.YSize := MilsToCoord(SafeStrToFloat(EditValue(Spec, P + 'height')));
    if Geometry then Symbol.MoveByXY(X - Symbol.Location.X, Bottom + Symbol.YSize - Symbol.Location.Y);
    if EditHas(Spec, P + 'designator') then Symbol.SheetName.Text := EditValue(Spec, P + 'designator');
    if EditHas(Spec, P + 'filename') then Symbol.SheetFileName.Text := EditValue(Spec, P + 'filename');
    if Geometry then
    begin
        Symbol.SheetName.Location := Point(Symbol.Location.X, Symbol.Location.Y + MilsToCoord(100));
        Symbol.SheetFileName.Location := Point(Symbol.Location.X, Symbol.Location.Y - Symbol.YSize - MilsToCoord(150));
    end;
    if Spec.Values[P + 'op'] = 'add' then
    begin
        Symbol.SheetName.IsHidden := False;
        Symbol.SheetFileName.IsHidden := False;
    end;
    if not EditHas(Spec, P + 'entry_count') then
    begin
        if Geometry then
        begin
            Iter := Symbol.SchIterator_Create;
            Iter.AddFilter_ObjectSet(MkSet(eSheetEntry));
            try
                Entry := Iter.FirstSchObject;
                while Entry <> nil do
                begin
                    SchServer.RobotManager.SendMessage(Entry.I_ObjectAddress, c_BroadCast, SCHM_BeginModify, 0);
                    try DiagramPositionEntry(Symbol, Entry);
                    finally SchServer.RobotManager.SendMessage(Entry.I_ObjectAddress, c_BroadCast, SCHM_EndModify, 0); end;
                    Entry := Iter.NextSchObject;
                end;
            finally Symbol.SchIterator_Destroy(Iter); end;
        end;
        Exit;
    end;
    Iter := Symbol.SchIterator_Create;
    Iter.AddFilter_ObjectSet(MkSet(eSheetEntry));
    try
        Entry := Iter.FirstSchObject;
        while Entry <> nil do
        begin
            NextEntry := Iter.NextSchObject;
            Symbol.RemoveSchObject(Entry);
            SchServer.RobotManager.SendMessage(Symbol.I_ObjectAddress, c_BroadCast,
                SCHM_PrimitiveRegistration, Entry.I_ObjectAddress);
            Entry := NextEntry;
        end;
    finally Symbol.SchIterator_Destroy(Iter); end;
    for I := 0 to StrToInt(Spec.Values[P + 'entry_count']) - 1 do
    begin
        EP := P + 'entry' + IntToStr(I) + '.';
        Entry := SchServer.SchObjectFactory(eSheetEntry, eCreate_Default);
        Entry.UniqueId := GetWorkspace.DM_GenerateUniqueID;
        Entry.Name := EditValue(Spec, EP + 'name');
        Entry.Side := DiagramSide(Spec.Values[EP + 'side']);
        Entry.DistanceFromTop := MilsToCoord(SafeStrToFloat(Spec.Values[EP + 'offset']));
        Entry.IOType := DiagramIO(Spec.Values[EP + 'io_type']);
        Symbol.AddSchObject(Entry);
        DiagramPositionEntry(Symbol, Entry);
        SchServer.RobotManager.SendMessage(Symbol.I_ObjectAddress, c_BroadCast,
            SCHM_PrimitiveRegistration, Entry.I_ObjectAddress);
    end;
end;

function ExecuteSchematicDiagram(Command: String; Request: TStringList): String;
var
    Spec, Props, Results, P, Names: TStringList;
    Path, SpecPath, ErrorText, Prefix, Action, Kind, UID, Name, EP: String;
    Doc: ISch_Document;
    ClientDoc: IServerDocument;
    Symbol, Existing: ISch_SheetSymbol;
    Obj: ISch_GraphicalObject;
    Rect: ISch_Rectangle;
    Poly: ISch_Polyline;
    LabelObj: ISch_Label;
    Entry: ISch_SheetEntry;
    Iter: ISch_Iterator;
    Count, I, J, Matches, Applied, FailedIndex: Integer;
    W, H, X, Y, Limit, Offset: Double;
    Editing, Started, WasModified: Boolean;
begin
    Spec := TStringList.Create;
    Props := TStringList.Create;
    Results := TStringList.Create;
    Names := TStringList.Create;
    Doc := nil; ClientDoc := nil;
    Path := ''; ErrorText := ''; Applied := 0; FailedIndex := -1;
    Editing := False; Started := False; WasModified := False;
    try
      try
        repeat
            SpecPath := '';
            for I := 0 to Request.Count - 1 do
                if Pos('"spec_file"', Request[I]) > 0 then SpecPath := JSONLineValue(Request[I]);
            if not FileExists(SpecPath) then begin ErrorText := 'spec file not found'; Break; end;
            Spec.LoadFromFile(SpecPath);
            Path := EditValue(Spec, 'sheet_path');
            Doc := CloneOpenSheet(Path, ClientDoc);
            if Doc = nil then begin ErrorText := 'exact target SchDoc could not be opened'; Break; end;
            WasModified := ClientDoc.Modified;
            Count := StrToInt(Spec.Values['count']);
            // Preflight all targets, names and resulting entry bounds before mutation.
            for I := 0 to Count - 1 do
            begin
                FailedIndex := I;
                Prefix := IntToStr(I) + '.';
                Action := Spec.Values[Prefix + 'op'];
                Kind := Spec.Values[Prefix + 'type'];
                if Command = 'edit_sheet_symbols' then
                begin
                    Symbol := nil;
                    if Action <> 'add' then
                    begin
                        Symbol := DiagramFind(Doc, EditValue(Spec, Prefix + 'unique_id'), Matches);
                        if Matches <> 1 then begin ErrorText := 'symbol UID must match exactly once'; Break; end;
                    end;
                    if Action <> 'delete' then
                    begin
                        W := 0; H := 0; X := 0; Y := 0;
                        if Symbol <> nil then
                        begin
                            W := CoordToMils(Symbol.XSize); H := CoordToMils(Symbol.YSize);
                            X := CoordToMils(Symbol.Location.X); Y := CoordToMils(Symbol.Location.Y - Symbol.YSize);
                        end;
                        if EditHas(Spec, Prefix + 'width') then W := SafeStrToFloat(Spec.Values[Prefix + 'width']);
                        if EditHas(Spec, Prefix + 'height') then H := SafeStrToFloat(Spec.Values[Prefix + 'height']);
                        if EditHas(Spec, Prefix + 'x') then
                        begin
                            X := SafeStrToFloat(Spec.Values[Prefix + 'x']); Y := SafeStrToFloat(Spec.Values[Prefix + 'y']);
                        end;
                        if (W <= 0) or (H <= 0) or (Abs(X + W) > 200000) or (Abs(Y + H) > 200000) then
                            ErrorText := 'resulting symbol bounds exceed supported coordinate range';
                        if EditHas(Spec, Prefix + 'entry_count') then
                        begin
                            for J := 0 to StrToInt(Spec.Values[Prefix + 'entry_count']) - 1 do
                            begin
                                EP := Prefix + 'entry' + IntToStr(J) + '.';
                                Limit := W;
                                if (Spec.Values[EP + 'side'] = 'left') or (Spec.Values[EP + 'side'] = 'right') then Limit := H;
                                Offset := SafeStrToFloat(Spec.Values[EP + 'offset']);
                                if (Offset <= 0) or (Offset >= Limit) then ErrorText := 'entry offset is outside resulting symbol edge';
                            end;
                        end
                        else if Symbol <> nil then
                        begin
                            Iter := Symbol.SchIterator_Create;
                            Iter.AddFilter_ObjectSet(MkSet(eSheetEntry));
                            try
                                Entry := Iter.FirstSchObject;
                                while Entry <> nil do
                                begin
                                    Limit := W;
                                    if (Entry.Side = eLeftSide) or (Entry.Side = eRightSide) then Limit := H;
                                    if CoordToMils(Entry.DistanceFromTop) >= Limit then ErrorText := 'resize would strand an existing entry';
                                    Entry := Iter.NextSchObject;
                                end;
                            finally Symbol.SchIterator_Destroy(Iter); end;
                        end;
                        if EditHas(Spec, Prefix + 'designator') then
                        begin
                            Name := UpperCase(EditValue(Spec, Prefix + 'designator'));
                            if Names.IndexOf(Name) >= 0 then ErrorText := 'duplicate sheet symbol designator in batch';
                            Names.Add(Name);
                            Iter := Doc.SchIterator_Create;
                            Iter.AddFilter_ObjectSet(MkSet(eSheetSymbol));
                            try
                                Existing := Iter.FirstSchObject;
                                while Existing <> nil do
                                begin
                                    if UpperCase(Existing.SheetName.Text) = Name then
                                        if (Symbol = nil) or (Existing.UniqueId <> Symbol.UniqueId) then ErrorText := 'sheet symbol designator already exists';
                                    Existing := Iter.NextSchObject;
                                end;
                            finally Doc.SchIterator_Destroy(Iter); end;
                        end;
                    end;
                end
                else if (Action <> 'add') or ((Kind <> 'rectangle') and (Kind <> 'polyline') and (Kind <> 'text')) then
                    ErrorText := 'unsupported drawing operation';
                if ErrorText <> '' then Break;
            end;
            if ErrorText <> '' then Break;
            if Count = 0 then Break;
            SchServer.ProcessControl.PreProcess(Doc, '');
            Editing := True;
            for I := 0 to Count - 1 do
            begin
                FailedIndex := I;
                ReportProgress;
                Prefix := IntToStr(I) + '.';
                Action := Spec.Values[Prefix + 'op'];
                Kind := Spec.Values[Prefix + 'type'];
                Started := True;
                if Kind = 'sheet_symbol' then
                begin
                    if Action = 'add' then
                    begin
                        Symbol := SchServer.SchObjectFactory(eSheetSymbol, eCreate_Default);
                        Symbol.UniqueId := GetWorkspace.DM_GenerateUniqueID;
                        Symbol.SheetName.UniqueId := GetWorkspace.DM_GenerateUniqueID;
                        Symbol.SheetFileName.UniqueId := GetWorkspace.DM_GenerateUniqueID;
                        Doc.RegisterSchObjectInContainer(Symbol);
                        SchServer.RobotManager.SendMessage(Doc.I_ObjectAddress, c_BroadCast,
                            SCHM_PrimitiveRegistration, Symbol.I_ObjectAddress);
                    end
                    else Symbol := DiagramFind(Doc, EditValue(Spec, Prefix + 'unique_id'), Matches);
                    UID := Symbol.UniqueId;
                    if Action = 'delete' then
                    begin
                        Doc.RemoveSchObject(Symbol);
                        SchServer.RobotManager.SendMessage(Doc.I_ObjectAddress, c_BroadCast,
                            SCHM_PrimitiveRegistration, Symbol.I_ObjectAddress);
                    end
                    else
                    begin
                        SchServer.RobotManager.SendMessage(Symbol.I_ObjectAddress, c_BroadCast, SCHM_BeginModify, 0);
                        try DiagramSetSymbol(Symbol, Spec, Prefix);
                        finally SchServer.RobotManager.SendMessage(Symbol.I_ObjectAddress, c_BroadCast, SCHM_EndModify, 0); end;
                    end;
                end
                else
                begin
                    Obj := nil;
                    if Kind = 'rectangle' then
                    begin
                        Rect := SchServer.SchObjectFactory(eRectangle, eCreate_Default);
                        Rect.Location := Point(MilsToCoord(SafeStrToFloat(Spec.Values[Prefix + 'x'])), MilsToCoord(SafeStrToFloat(Spec.Values[Prefix + 'y'])));
                        Rect.Corner := Point(Rect.Location.X + MilsToCoord(SafeStrToFloat(Spec.Values[Prefix + 'width'])), Rect.Location.Y + MilsToCoord(SafeStrToFloat(Spec.Values[Prefix + 'height'])));
                        Rect.Color := $800000; Rect.AreaColor := $FFFFFF; Rect.IsSolid := False; Rect.LineWidth := eSmall;
                        Obj := Rect;
                    end;
                    if Kind = 'text' then
                    begin
                        LabelObj := SchServer.SchObjectFactory(eLabel, eCreate_Default);
                        LabelObj.Location := Point(MilsToCoord(SafeStrToFloat(Spec.Values[Prefix + 'x'])), MilsToCoord(SafeStrToFloat(Spec.Values[Prefix + 'y'])));
                        LabelObj.Text := EditValue(Spec, Prefix + 'text');
                        LabelObj.FontId := FontIDFromSpec('Arial:10');
                        LabelObj.Color := $800000; LabelObj.Orientation := eRotate0;
                        LabelObj.Justification := 0;
                        if Spec.Values[Prefix + 'centered'] = '1' then LabelObj.Justification := 4;
                        Obj := LabelObj;
                    end;
                    if Kind = 'polyline' then
                    begin
                        Poly := SchServer.SchObjectFactory(ePolyline, eCreate_Default);
                        Poly.VerticesCount := StrToInt(Spec.Values[Prefix + 'point_count']);
                        for J := 1 to Poly.VerticesCount do
                            Poly.Vertex[J] := Point(MilsToCoord(SafeStrToFloat(Spec.Values[Prefix + 'point' + IntToStr(J-1) + '.x'])),
                                                    MilsToCoord(SafeStrToFloat(Spec.Values[Prefix + 'point' + IntToStr(J-1) + '.y'])));
                        Poly.Color := $800000; Poly.LineWidth := eSmall;
                        Obj := Poly;
                    end;
                    Obj.UniqueId := GetWorkspace.DM_GenerateUniqueID;
                    UID := Obj.UniqueId;
                    Doc.RegisterSchObjectInContainer(Obj);
                    SchServer.RobotManager.SendMessage(Doc.I_ObjectAddress, c_BroadCast,
                        SCHM_PrimitiveRegistration, Obj.I_ObjectAddress);
                end;
                Applied := Applied + 1;
                P := TStringList.Create;
                try
                    AddJSONInteger(P, 'index', I);
                    AddJSONProperty(P, 'op', Action);
                    AddJSONProperty(P, 'type', Kind);
                    AddJSONProperty(P, 'unique_id', UID);
                    if EditHas(Spec, Prefix + 'role') then AddJSONProperty(P, 'role', EditValue(Spec, Prefix + 'role'));
                    Results.Add(BuildJSONObject(P));
                finally P.Free; end;
            end;
        until True;
      except ErrorText := ExceptionMessage; end;
      if Editing then
      begin
          SchServer.ProcessControl.PostProcess(Doc, ''); Editing := False;
          ClientDoc.SetModified(True); Doc.GraphicallyInvalidate;
      end;
      AddJSONBoolean(Props, 'success', ErrorText = '');
      AddJSONProperty(Props, 'sheet_path', Path);
      AddJSONProperty(Props, 'coordinate_units', 'mils');
      AddJSONBoolean(Props, 'saved', False);
      AddJSONBoolean(Props, 'was_modified', WasModified);
      if ClientDoc <> nil then AddJSONBoolean(Props, 'is_modified', ClientDoc.Modified);
      AddJSONInteger(Props, 'applied_count', Applied);
      if ErrorText <> '' then
      begin
          AddJSONProperty(Props, 'error', ErrorText);
          AddJSONInteger(Props, 'failed_index', FailedIndex);
          AddJSONBoolean(Props, 'may_have_partial_changes', Started);
      end;
      Props.Add('"results":' + BuildJSONArray(Results));
      if Doc <> nil then Props.Add('"objects":' + DiagramSnapshot(Doc, Command = 'edit_sheet_symbols'));
      Result := BuildJSONObject(Props);
    finally
        if Editing then SchServer.ProcessControl.PostProcess(Doc, '');
        Spec.Free; Props.Free; Results.Free; Names.Free;
    end;
end;
