// Existing SchLib objects only: no factory, delete, replicate or save calls.

var
    // Sch I_ObjectAddress is an opaque handle; keep request-local interface IDs.
    UPObjects: TInterfaceList;

function UPIdentity(Obj: ISch_GraphicalObject): String;
var I: Integer;
begin
    I := UPObjects.IndexOf(Obj);
    if I < 0 then I := UPObjects.Add(Obj);
    Result := IntToStr(I);
end;

function UPType(Obj: ISch_GraphicalObject): String;
begin
    Result := 'unsupported';
    case Obj.ObjectId of
        eLabel: Result := 'label';
        eLine: Result := 'line';
        eRectangle: Result := 'rectangle';
        ePolyline: Result := 'polyline';
        ePolygon: Result := 'polygon';
        eArc: Result := 'arc';
        eEllipse: Result := 'ellipse';
        ePin: Result := 'pin';
    end;
end;

function UPFloat(Value: Double): String;
begin
    Result := StringReplace(FloatToStr(Value), ',', '.', REPLACEALL);
end;

function UPCoord(Value: Integer): String;
begin
    Result := UPFloat(Value / MilsToCoord(1));
end;

function UPBool(Value: Boolean): String;
begin
    Result := '0';
    if Value then Result := '1';
end;

function UPStringKey(Key: String): Boolean;
begin
    Result := (Key = 'type') or (Key = '_id') or (Key = 'unique_id') or
              (Key = 'text') or (Key = 'pin_name') or (Key = 'pin_number') or
              (Key = 'description') or (Key = 'font_name') or
              (Key = 'symbol_inside') or (Key = 'symbol_inside_edge') or
              (Key = 'symbol_outside_edge') or (Key = 'symbol_outside');
end;

function UPBoolKey(Key: String): Boolean;
begin
    Result := (Key = 'is_solid') or (Key = 'is_hidden') or (Key = 'show_name') or
              (Key = 'show_designator') or (Key = 'font_bold') or (Key = 'font_italic') or
              (Key = 'font_underline') or (Key = 'font_strikeout');
end;

function UPState(Obj: ISch_GraphicalObject): TStringList;
var
    S, PinProps: TStringList;
    Kind, Step: String;
    R: ISch_Rectangle;
    L: ISch_Line;
    P: ISch_Polyline;
    A: ISch_Arc;
    E: ISch_Ellipse;
    T: ISch_Label;
    Pin: ISch_Pin;
    I, F: Integer;
begin
    S := TStringList.Create;
    try
        Kind := UPType(Obj);
        Step := 'identity';
        S.Values['type'] := Kind;
        S.Values['_id'] := UPIdentity(Obj);
        if Kind = 'unsupported' then S.Values['native_type'] := IntToStr(Obj.ObjectId)
        else
        begin
            S.Add('unique_id=' + Obj.UniqueId);
            S.Values['owner_part_id'] := IntToStr(Obj.OwnerPartId);
            S.Values['display_mode'] := IntToStr(Obj.OwnerPartDisplayMode);
            S.Values['color'] := IntToStr(Obj.Color);
            if (Kind = 'line') or (Kind = 'rectangle') then
            begin
                S.Values['x1'] := UPCoord(Obj.Location.X);
                S.Values['y1'] := UPCoord(Obj.Location.Y);
                // Record-valued Corner must be read through the concrete interface.
                if Kind = 'rectangle' then
                begin
                    R := Obj;
                    S.Values['x2'] := UPCoord(R.Corner.X); S.Values['y2'] := UPCoord(R.Corner.Y);
                end
                else
                begin
                    L := Obj;
                    S.Values['x2'] := UPCoord(L.Corner.X); S.Values['y2'] := UPCoord(L.Corner.Y);
                end;
            end;
            if (Kind = 'polyline') or (Kind = 'polygon') then
            begin
                P := Obj;
                S.Values['vertex_count'] := IntToStr(P.VerticesCount);
                for I := 1 to P.VerticesCount do
                begin
                    S.Values['vertex' + IntToStr(I-1) + '.x'] := UPCoord(P.Vertex[I].X);
                    S.Values['vertex' + IntToStr(I-1) + '.y'] := UPCoord(P.Vertex[I].Y);
                end;
            end;
            if Kind = 'arc' then
            begin
                A := Obj;
                S.Values['radius'] := UPCoord(A.Radius);
                S.Values['start_angle'] := UPFloat(A.StartAngle);
                S.Values['end_angle'] := UPFloat(A.EndAngle);
            end;
            if Kind = 'ellipse' then
            begin
                E := Obj;
                S.Values['radius'] := UPCoord(E.Radius);
                S.Values['secondary_radius'] := UPCoord(E.SecondaryRadius);
            end;
            if (Kind = 'arc') or (Kind = 'ellipse') then
            begin
                S.Values['cx'] := UPCoord(Obj.Location.X); S.Values['cy'] := UPCoord(Obj.Location.Y);
            end;
            if (Kind <> 'label') and (Kind <> 'pin') then S.Values['line_width'] := IntToStr(Obj.LineWidth);
            if (Kind = 'rectangle') or (Kind = 'polygon') or (Kind = 'ellipse') then
            begin
                Step := 'is_solid';
                S.Values['is_solid'] := UPBool(Obj.IsSolid);
                S.Values['area_color'] := IntToStr(Obj.AreaColor);
            end;
            if Kind = 'label' then
            begin
                T := Obj;
                F := T.FontId;
                S.Add('text=' + T.Text);
                S.Values['x'] := UPCoord(T.Location.X); S.Values['y'] := UPCoord(T.Location.Y);
                S.Values['orientation'] := IntToStr(T.Orientation);
                S.Values['justification'] := IntToStr(T.Justification);
                S.Values['font_name'] := SchServer.FontManager.FontName[F];
                S.Values['font_size'] := IntToStr(SchServer.FontManager.Size[F]);
                Step := 'font_bold';
                S.Values['font_bold'] := UPBool(SchServer.FontManager.Bold[F]);
                S.Values['font_italic'] := UPBool(SchServer.FontManager.Italic[F]);
                S.Values['font_underline'] := UPBool(SchServer.FontManager.UnderLine[F]);
                S.Values['font_strikeout'] := UPBool(SchServer.FontManager.StrikeOut[F]);
            end;
            if Kind = 'pin' then
            begin
                Step := 'pin';
                Pin := Obj;
                S.Add('pin_number=' + Pin.Designator); S.Add('pin_name=' + Pin.Name);
                S.Add('description=' + Pin.Description);
                S.Values['x'] := UPCoord(Pin.Location.X); S.Values['y'] := UPCoord(Pin.Location.Y);
                S.Values['length'] := UPCoord(Pin.PinLength);
                S.Values['orientation'] := IntToStr(Pin.Orientation);
                S.Values['electrical'] := IntToStr(Pin.Electrical);
                S.Values['show_name'] := UPBool(Pin.ShowName);
                S.Values['show_designator'] := UPBool(Pin.ShowDesignator);
                S.Values['is_hidden'] := UPBool(Pin.IsHidden);
                S.Add('symbol_inside=' + IeeeSymbolName(Pin.Symbol_Inner));
                S.Add('symbol_inside_edge=' + IeeeSymbolName(Pin.Symbol_InnerEdge));
                S.Add('symbol_outside_edge=' + IeeeSymbolName(Pin.Symbol_OuterEdge));
                S.Add('symbol_outside=' + IeeeSymbolName(Pin.Symbol_Outer));
                PinProps := TStringList.Create;
                try
                    AddPinParametersJSON(PinProps, Pin);
                    S.Values['pin_parameters'] := BuildJSONObject(PinProps);
                finally PinProps.Free; end;
            end;
        end;
        Result := S;
    except
        LogScriptError('update_symbol_primitives: read ' + Kind + ' ' + Step + ': ' + ExceptionMessage);
        S.Free;
        raise;
    end;
end;

function UPStateJSON(S: TStringList): String;
var Props, Vertices, PointProps: TStringList; I, N: Integer; Key: String;
begin
    Props := TStringList.Create;
    Vertices := TStringList.Create;
    try
        for I := 0 to S.Count - 1 do
        begin
            Key := S.Names[I];
            if Pos('vertex', Key) <> 1 then
            begin
                if Key = 'pin_parameters' then Props.Add('"pin_parameters":' + S.ValueFromIndex[I])
                else if UPBoolKey(Key) then AddJSONBoolean(Props, Key, S.ValueFromIndex[I] = '1')
                else AddJSONProperty(Props, Key, S.ValueFromIndex[I], UPStringKey(Key));
            end;
        end;
        if S.IndexOfName('vertex_count') >= 0 then
        begin
            for N := 0 to StrToInt(S.Values['vertex_count']) - 1 do
            begin
                PointProps := TStringList.Create;
                try
                    AddJSONProperty(PointProps, 'x', S.Values['vertex' + IntToStr(N) + '.x'], False);
                    AddJSONProperty(PointProps, 'y', S.Values['vertex' + IntToStr(N) + '.y'], False);
                    Vertices.Add(BuildJSONObject(PointProps));
                finally PointProps.Free; end;
            end;
            Props.Add('"vertices":' + BuildJSONArray(Vertices));
        end;
        Result := BuildJSONObject(Props);
    finally Props.Free; Vertices.Free; end;
end;

function UPSnapshot(Comp: ISch_Component): String;
var Iter: ISch_Iterator; Obj: ISch_GraphicalObject; Items, S: TStringList;
begin
    Items := TStringList.Create;
    try
        Iter := Comp.SchIterator_Create;
        try
            Obj := Iter.FirstSchObject;
            while Obj <> nil do
            begin
                ReportProgress;
                S := UPState(Obj);
                try Items.Add(UPStateJSON(S)); finally S.Free; end;
                Obj := Iter.NextSchObject;
            end;
        finally Comp.SchIterator_Destroy(Iter); end;
        Result := BuildJSONArray(Items);
    finally Items.Free; end;
end;

function UPMatches(S, Spec: TStringList; Prefix: String): Boolean;
var I: Integer; Key, Expected, Actual: String;
begin
    Result := True;
    for I := 0 to Spec.Count - 1 do
        if Pos(Prefix, Spec.Names[I]) = 1 then
        begin
            Key := Copy(Spec.Names[I], Length(Prefix)+1, Length(Spec.Names[I]));
            if S.IndexOfName(Key) < 0 then begin Result := False; Exit; end;
            Expected := JSONUnescape(Spec.ValueFromIndex[I]); Actual := S.Values[Key];
            if Key = 'font_name' then Result := SameText(Actual, Expected)
            else if UPStringKey(Key) or UPBoolKey(Key) then Result := Actual = Expected
            else Result := Abs(SafeStrToFloat(Actual) - SafeStrToFloat(Expected)) < 0.00001;
            if not Result then Exit;
        end;
end;

function UPFind(Comp: ISch_Component; Spec: TStringList; Prefix, Address: String; var Matches: Integer): ISch_GraphicalObject;
var Iter: ISch_Iterator; Obj: ISch_GraphicalObject; S: TStringList; Found: Boolean;
begin
    Result := nil; Matches := 0;
    Iter := Comp.SchIterator_Create;
    try
        Obj := Iter.FirstSchObject;
        while Obj <> nil do
        begin
            ReportProgress;
            Found := False;
            if Address <> '' then Found := UPIdentity(Obj) = Address
            else if UPType(Obj) <> 'unsupported' then
            begin
                S := UPState(Obj);
                try Found := UPMatches(S, Spec, Prefix); finally S.Free; end;
            end;
            if Found then begin Result := Obj; Matches := Matches + 1; end;
            Obj := Iter.NextSchObject;
        end;
    finally Comp.SchIterator_Destroy(Iter); end;
end;

procedure UPApply(Obj: ISch_GraphicalObject; Spec: TStringList; P: String);
var
    S: TStringList; Kind: String; I, F: Integer;
    R: ISch_Rectangle; L: ISch_Line; Poly: ISch_Polyline;
    A: ISch_Arc; E: ISch_Ellipse; T: ISch_Label; Pin: ISch_Pin;
begin
    Kind := UPType(Obj);
    if EditHas(Spec, P+'color') then Obj.Color := StrToInt(Spec.Values[P+'color']);
    if EditHas(Spec, P+'area_color') then Obj.AreaColor := StrToInt(Spec.Values[P+'area_color']);
    if EditHas(Spec, P+'line_width') then Obj.LineWidth := StrToInt(Spec.Values[P+'line_width']);
    if EditHas(Spec, P+'is_solid') then Obj.IsSolid := Spec.Values[P+'is_solid'] = '1';
    if EditHas(Spec, P+'x') then Obj.Location := Point(MilsToCoord(SafeStrToFloat(Spec.Values[P+'x'])),MilsToCoord(SafeStrToFloat(Spec.Values[P+'y'])));
    if EditHas(Spec, P+'cx') then Obj.Location := Point(MilsToCoord(SafeStrToFloat(Spec.Values[P+'cx'])),MilsToCoord(SafeStrToFloat(Spec.Values[P+'cy'])));
    if EditHas(Spec, P+'x1') then Obj.Location := Point(MilsToCoord(SafeStrToFloat(Spec.Values[P+'x1'])),MilsToCoord(SafeStrToFloat(Spec.Values[P+'y1'])));
    if EditHas(Spec, P+'x2') then
    begin
        if Kind = 'line' then
        begin L := Obj; L.Corner := Point(MilsToCoord(SafeStrToFloat(Spec.Values[P+'x2'])),MilsToCoord(SafeStrToFloat(Spec.Values[P+'y2']))); end
        else begin R := Obj; R.Corner := Point(MilsToCoord(SafeStrToFloat(Spec.Values[P+'x2'])),MilsToCoord(SafeStrToFloat(Spec.Values[P+'y2']))); end;
    end;
    if EditHas(Spec, P+'vertex_count') then
    begin
        Poly := Obj; Poly.VerticesCount := StrToInt(Spec.Values[P+'vertex_count']);
        for I := 1 to Poly.VerticesCount do Poly.Vertex[I] := Point(
            MilsToCoord(SafeStrToFloat(Spec.Values[P+'vertex'+IntToStr(I-1)+'.x'])),
            MilsToCoord(SafeStrToFloat(Spec.Values[P+'vertex'+IntToStr(I-1)+'.y'])));
    end;
    if Kind = 'arc' then
    begin
        A := Obj;
        if EditHas(Spec,P+'radius') then A.Radius := MilsToCoord(SafeStrToFloat(Spec.Values[P+'radius']));
        if EditHas(Spec,P+'start_angle') then A.StartAngle := SafeStrToFloat(Spec.Values[P+'start_angle']);
        if EditHas(Spec,P+'end_angle') then A.EndAngle := SafeStrToFloat(Spec.Values[P+'end_angle']);
    end;
    if Kind = 'ellipse' then
    begin
        E := Obj;
        if EditHas(Spec,P+'radius') then E.Radius := MilsToCoord(SafeStrToFloat(Spec.Values[P+'radius']));
        if EditHas(Spec,P+'secondary_radius') then E.SecondaryRadius := MilsToCoord(SafeStrToFloat(Spec.Values[P+'secondary_radius']));
    end;
    if Kind = 'pin' then
    begin
        Pin := Obj;
        if EditHas(Spec,P+'pin_name') then Pin.Name := EditValue(Spec,P+'pin_name');
        if EditHas(Spec,P+'description') then Pin.Description := EditValue(Spec,P+'description');
        if EditHas(Spec,P+'length') then Pin.PinLength := MilsToCoord(SafeStrToFloat(Spec.Values[P+'length']));
        if EditHas(Spec,P+'orientation') then Pin.Orientation := StrToInt(Spec.Values[P+'orientation']);
        if EditHas(Spec,P+'electrical') then Pin.Electrical := StrToInt(Spec.Values[P+'electrical']);
        if EditHas(Spec,P+'show_name') then Pin.ShowName := Spec.Values[P+'show_name']='1';
        if EditHas(Spec,P+'show_designator') then Pin.ShowDesignator := Spec.Values[P+'show_designator']='1';
        if EditHas(Spec,P+'is_hidden') then Pin.IsHidden := Spec.Values[P+'is_hidden']='1';
    end;
    if Kind = 'label' then
    begin
        T := Obj;
        if EditHas(Spec,P+'text') then T.Text := EditValue(Spec,P+'text');
        if EditHas(Spec,P+'orientation') then T.Orientation := StrToInt(Spec.Values[P+'orientation']);
        if EditHas(Spec,P+'justification') then T.Justification := StrToInt(Spec.Values[P+'justification']);
        if EditHas(Spec,P+'font_name') or EditHas(Spec,P+'font_size') or EditHas(Spec,P+'font_bold') or
           EditHas(Spec,P+'font_italic') or EditHas(Spec,P+'font_underline') or EditHas(Spec,P+'font_strikeout') then
        begin
            S := UPState(T);
            try
                F := T.FontId;
                for I := 0 to Spec.Count - 1 do
                    if Pos(P+'font_',Spec.Names[I])=1 then S.Values[Copy(Spec.Names[I],Length(P)+1,Length(Spec.Names[I]))] := JSONUnescape(Spec.ValueFromIndex[I]);
                T.FontId := SchServer.FontManager.GetFontID(StrToInt(S.Values['font_size']),SchServer.FontManager.Rotation[F],
                    S.Values['font_underline']='1',S.Values['font_italic']='1',S.Values['font_bold']='1',S.Values['font_strikeout']='1',S.Values['font_name']);
            finally S.Free; end;
        end;
    end;
end;

function ExecuteUpdateSymbolPrimitives(Request: TStringList): String;
var
    Spec, Props, Addresses, Results, S, Row: TStringList;
    Lib: ISch_Lib;
    Comp, Candidate: ISch_Component;
    Doc: IServerDocument;
    LibIterator: ISch_Iterator;
    Obj: ISch_GraphicalObject;
    Path, Name, SpecPath, ErrorText, BeforeJSON, BeforeMeta, AfterJSON, AfterMeta, BeforeUID, BeforeAddress, Prefix: String;
    I, Count, Matches, Updated, FailedIndex: Integer;
    Editing, Started, WasModified, NeedChanges, HaveSnapshot: Boolean;
begin
    Spec := TStringList.Create; Props := TStringList.Create;
    Addresses := TStringList.Create; Results := TStringList.Create;
    UPObjects := TInterfaceList.Create;
    Lib := nil; Comp := nil; Doc := nil;
    Path := ''; Name := ''; ErrorText := ''; BeforeJSON := '[]'; BeforeMeta := '{}'; BeforeUID := ''; BeforeAddress := '';
    Updated := 0; FailedIndex := -1; Editing := False; Started := False; WasModified := False; NeedChanges := False;
    HaveSnapshot := False;
    try
      try
        repeat
            SpecPath := '';
            for I := 0 to Request.Count - 1 do if Pos('"spec_file"',Request[I])>0 then SpecPath := JSONLineValue(Request[I]);
            if not FileExists(SpecPath) then begin ErrorText := 'Spec file not found'; Break; end;
            Spec.LoadFromFile(SpecPath); Path := EditValue(Spec,'library_path'); Name := EditValue(Spec,'symbol_name');
            if not FileExists(Path) then begin ErrorText := 'Library file not found'; Break; end;
            Doc := OpenLibraryDocument('SchLib',Path);
            if Doc = nil then begin ErrorText := 'Cannot open a unique library buffer'; Break; end;
            Lib := SchServer.GetSchDocumentByPath(Path);
            if Lib = nil then begin ErrorText := 'Target SchLib is unavailable'; Break; end;
            if (Lib.ObjectId<>33) or (NormalizedPath(Lib.DocumentName)<>NormalizedPath(Path)) or
               (NormalizedPath(Doc.FileName)<>NormalizedPath(Path)) then begin ErrorText := 'Library identity mismatch'; Break; end;
            if SchServer.ProcessControl.ProcessDepth<>0 then begin ErrorText := 'Schematic editor has an active transaction'; Break; end;
            Matches := 0;
            LibIterator := Lib.SchLibIterator_Create;
            LibIterator.AddFilter_ObjectSet(MkSet(eSchComponent));
            try
                Candidate := LibIterator.FirstSchObject;
                while Candidate<>nil do
                begin
                    if SameText(Candidate.LibReference,Name) then begin Comp := Candidate; Matches := Matches+1; end;
                    Candidate := LibIterator.NextSchObject;
                end;
            finally
                Lib.SchIterator_Destroy(LibIterator);
            end;
            if Matches<>1 then begin Comp := nil; ErrorText := 'Symbol missing or ambiguous'; Break; end;
            Lib.CurrentSchComponent := Comp;
            Comp := Lib.CurrentSchComponent;
            if Comp = nil then begin ErrorText := 'Focused symbol is unavailable'; Break; end;
            if not SameText(Comp.LibReference,Name) then begin ErrorText := 'Focused symbol identity mismatch'; Break; end;
            WasModified := Doc.Modified;
            BeforeUID := Comp.UniqueId;
            BeforeAddress := UPIdentity(Comp);
            BeforeMeta := LibraryComponentSnapshot(Comp);
            BeforeJSON := UPSnapshot(Comp);
            HaveSnapshot := True;
            Count := StrToInt(Spec.Values['count']);
            for I := 0 to Count-1 do
            begin
                FailedIndex := I; Prefix := IntToStr(I)+'.';
                Obj := UPFind(Comp,Spec,Prefix+'target.','',Matches);
                if Matches<>1 then begin ErrorText := 'Target must match exactly once; found '+IntToStr(Matches); Break; end;
                if Addresses.IndexOf(UPIdentity(Obj))>=0 then begin ErrorText := 'Same object targeted twice'; Break; end;
                Addresses.Add(UPIdentity(Obj));
                S := UPState(Obj);
                try NeedChanges := NeedChanges or not UPMatches(S,Spec,Prefix+'properties.'); finally S.Free; end;
            end;
            if ErrorText<>'' then Break;
            if NeedChanges then begin SchServer.ProcessControl.PreProcess(Lib,''); Editing := True; end;
            for I := 0 to Count-1 do
            begin
                FailedIndex := I; Prefix := IntToStr(I)+'.';
                Obj := UPFind(Comp,Spec,'',Addresses[I],Matches);
                if Matches<>1 then begin ErrorText := 'Preflight object disappeared'; Break; end;
                S := UPState(Obj);
                try
                    if not UPMatches(S,Spec,Prefix+'properties.') then
                    begin
                        Started := True;
                        SchServer.RobotManager.SendMessage(Obj.I_ObjectAddress,c_BroadCast,SCHM_BeginModify,0);
                        try UPApply(Obj,Spec,Prefix+'properties.'); Updated := Updated+1;
                        finally SchServer.RobotManager.SendMessage(Obj.I_ObjectAddress,c_BroadCast,SCHM_EndModify,0); end;
                    end;
                finally S.Free; end;
                Row := TStringList.Create;
                try
                    AddJSONInteger(Row,'index',I); AddJSONProperty(Row,'_id',Addresses[I]);
                    AddJSONProperty(Row,'type',UPType(Obj)); AddJSONProperty(Row,'unique_id',Obj.UniqueId);
                    Results.Add(BuildJSONObject(Row));
                finally Row.Free; end;
            end;
        until True;
      except ErrorText := ExceptionMessage; end;
      if Editing then begin SchServer.ProcessControl.PostProcess(Lib,''); Editing := False; end;
      if Started then begin MarkDocumentModified(Path); Lib.GraphicallyInvalidate; end;
      AfterMeta := BeforeMeta; AfterJSON := BeforeJSON;
      if HaveSnapshot then
      begin
          try
              AfterMeta := LibraryComponentSnapshot(Comp);
              AfterJSON := UPSnapshot(Comp);
          except ErrorText := 'After snapshot failed: ' + ExceptionMessage; end;
      end;
      AddJSONBoolean(Props,'success',ErrorText=''); AddJSONBoolean(Props,'saved',False);
      AddJSONProperty(Props,'library_path',Path); AddJSONProperty(Props,'symbol_name',Name);
      AddJSONBoolean(Props,'changed',Updated>0); AddJSONInteger(Props,'updated_count',Updated);
      AddJSONBoolean(Props,'was_modified',WasModified);
      if Doc<>nil then AddJSONBoolean(Props,'is_modified',Doc.Modified);
      AddJSONBoolean(Props,'may_have_partial_changes',(ErrorText<>'') and Started);
      if ErrorText<>'' then begin AddJSONProperty(Props,'error',ErrorText); AddJSONInteger(Props,'failed_index',FailedIndex); end;
      AddJSONProperty(Props,'component_uid_before',BeforeUID);
      AddJSONProperty(Props,'_component_id_before',BeforeAddress);
      Props.Add('"metadata_before":'+BeforeMeta); Props.Add('"objects_before":'+BeforeJSON);
      if Comp<>nil then
      begin
          AddJSONProperty(Props,'component_uid_after',Comp.UniqueId);
          AddJSONProperty(Props,'_component_id_after',UPIdentity(Comp));
          Props.Add('"metadata_after":'+AfterMeta); Props.Add('"objects_after":'+AfterJSON);
      end;
      Props.Add('"results":'+BuildJSONArray(Results));
      Result := BuildJSONObject(Props);
    finally
        if Editing then SchServer.ProcessControl.PostProcess(Lib,'');
        Spec.Free; Props.Free; Addresses.Free; Results.Free;
        UPObjects.Free;
    end;
end;
