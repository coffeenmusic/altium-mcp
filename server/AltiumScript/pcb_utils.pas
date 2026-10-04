// Nil-safe access to the current PCB board. When no PCB document has ever
// been opened this session, PCBServer itself is nil and calling
// GetCurrentPCBBoard on it throws an access violation that leaves the
// script paused in the debugger - silently blocking every later script run.
// All board lookups must go through this helper.
function GetBoardSafe(Dummy: Integer): IPCB_Board;
begin
    Result := nil;
    if (PCBServer <> nil) then
        Result := PCBServer.GetCurrentPCBBoard;
end;

// Nil-safe access to the current PCB library (same hazard as GetBoardSafe)
function GetPcbLibSafe(Dummy: Integer): IPCB_Library;
begin
    Result := nil;
    if (PCBServer <> nil) then
        Result := PCBServer.GetCurrentPCBLibrary;
end;

// Create many footprints in a single script run from a spec file (plain
// text, pipe-delimited; coords in mils, layers as names, shapes/hole types
// as raw enum ints - symmetric with get_footprint_primitives):
//   FPLIB|<path to .PcbLib>              (optional first line - focus/open)
//   FOOTPRINT|<name>|<description>
//   PAD|name|x|y|rot|layer|plated|hole_size|hole_type|hole_width|hole_rot|top_x|top_y|top_shape[|corner_pct[|mode|mid_x|mid_y|mid_shape|bot_x|bot_y|bot_shape]]
//   TRACK|x1|y1|x2|y2|width|layer
//   ARC|cx|cy|radius|start_angle|end_angle|width|layer
//   FILL|x1|y1|x2|y2|rotation|layer
//   TEXT|x|y|size|width|rotation|layer|mirror|ttf|text
function CreateFootprintsBatch(SpecFilePath: String): String;
var
    PcbLib      : IPCB_Library;
    LibComp     : IPCB_LibComponent;
    ServerDoc   : IServerDocument;
    Lines       : TStringList;
    FailedArray : TStringList;
    ResultProps : TStringList;
    Pad         : IPCB_Pad;
    Track       : IPCB_Track;
    Arc         : IPCB_Arc;
    Fill        : IPCB_Fill;
    Text        : IPCB_Text;
    Region      : IPCB_Region;
    Contour     : IPCB_Contour;
    Via         : IPCB_Via;
    Line, Kind  : String;
    LibPath     : String;
    FieldValue  : String;
    CreatedCount: Integer;
    PrimErrors  : Integer;
    i, V        : Integer;


begin
    if not FileExists(SpecFilePath) then
    begin
        Result := 'ERROR: Spec file not found: ' + SpecFilePath;
        Exit;
    end;

    Lines := TStringList.Create;
    FailedArray := TStringList.Create;
    ResultProps := TStringList.Create;
    LibComp := nil;
    CreatedCount := 0;
    PrimErrors := 0;

    try
        Lines.LoadFromFile(SpecFilePath);

        for i := 0 to Lines.Count - 1 do
        begin
            Line := Lines[i];
            Kind := UpperCase(Trim(GetFieldFromPipeString(Line, 0)));

            try
                if (Kind = 'FPLIB') then
                begin
                    LibPath := GetFieldFromPipeString(Line, 1);
                    if (LibPath <> '') and FileExists(LibPath) then
                    begin
                        if Client.IsDocumentOpen(LibPath) then
                            ServerDoc := Client.GetDocumentByPath(LibPath)
                        else
                            ServerDoc := Client.OpenDocument('PcbLib', LibPath);
                        if (ServerDoc <> Nil) then
                        begin
                            Client.ShowDocument(ServerDoc);
                            Sleep(500);
                        end;
                    end;
                end
                else if (Kind = 'FOOTPRINT') then
                begin
                    PcbLib := GetPcbLibSafe(0);
                    // Focus can drift between chunks - retry via the opened
                    // document before giving up
                    if (PcbLib = nil) and (ServerDoc <> nil) then
                    begin
                        Client.ShowDocument(ServerDoc);
                        Sleep(1000);
                        PcbLib := GetPcbLibSafe(0);
                    end;
                    if (PcbLib = nil) then
                    begin
                        Result := 'ERROR: No PCB library document is active';
                        Exit;
                    end;
                    LibComp := PCBServer.CreatePCBLibComp;
                    LibComp.Name := Trim(GetFieldFromPipeString(Line, 1));
                    LibComp.Description := GetFieldFromPipeString(Line, 2);
                    PcbLib.RegisterComponent(LibComp);
                    CreatedCount := CreatedCount + 1;
                end
                else if (LibComp <> nil) and (Kind = 'PAD') then
                begin
                    Pad := PCBServer.PCBObjectFactory(ePadObject, eNoDimension, eCreate_Default);
                    // Pad name is NOT trimmed - round-trip fidelity preserves
                    // whitespace and even control characters found in source data
                    Pad.Name := GetFieldFromPipeString(Line, 1);
                    // Mode first so per-stack sizes land correctly
                    FieldValue := Trim(GetFieldFromPipeString(Line, 15));
                    if (FieldValue <> '') then
                        Pad.Mode := StrToInt(FieldValue)
                    else
                        Pad.Mode := ePadMode_Simple;
                    Pad.x := MilsToCoord(SafeStrToFloat(Trim(GetFieldFromPipeString(Line, 2))));
                    Pad.y := MilsToCoord(SafeStrToFloat(Trim(GetFieldFromPipeString(Line, 3))));
                    Pad.Layer := String2Layer(Trim(GetFieldFromPipeString(Line, 5)));
                    Pad.Plated := (Trim(GetFieldFromPipeString(Line, 6)) = '1');
                    Pad.HoleSize := MilsToCoord(SafeStrToFloat(Trim(GetFieldFromPipeString(Line, 7))));
                    Pad.HoleType := StrToInt(Trim(GetFieldFromPipeString(Line, 8)));
                    Pad.HoleWidth := MilsToCoord(SafeStrToFloat(Trim(GetFieldFromPipeString(Line, 9))));
                    Pad.HoleRotation := SafeStrToFloat(Trim(GetFieldFromPipeString(Line, 10)));
                    Pad.TopXSize := MilsToCoord(SafeStrToFloat(Trim(GetFieldFromPipeString(Line, 11))));
                    Pad.TopYSize := MilsToCoord(SafeStrToFloat(Trim(GetFieldFromPipeString(Line, 12))));
                    Pad.TopShape := StrToInt(Trim(GetFieldFromPipeString(Line, 13)));
                    FieldValue := Trim(GetFieldFromPipeString(Line, 14));
                    if (FieldValue <> '') then
                        Pad.StackCRPctOnLayer[eTopLayer] := StrToInt(FieldValue);
                    if (Pad.Mode <> ePadMode_Simple) then
                    begin
                        Pad.MidXSize := MilsToCoord(SafeStrToFloat(Trim(GetFieldFromPipeString(Line, 16))));
                        Pad.MidYSize := MilsToCoord(SafeStrToFloat(Trim(GetFieldFromPipeString(Line, 17))));
                        Pad.MidShape := StrToInt(Trim(GetFieldFromPipeString(Line, 18)));
                        Pad.BotXSize := MilsToCoord(SafeStrToFloat(Trim(GetFieldFromPipeString(Line, 19))));
                        Pad.BotYSize := MilsToCoord(SafeStrToFloat(Trim(GetFieldFromPipeString(Line, 20))));
                        Pad.BotShape := StrToInt(Trim(GetFieldFromPipeString(Line, 21)));
                    end;
                    // Rotation last: it rotates the pad about its location
                    Pad.Rotation := SafeStrToFloat(Trim(GetFieldFromPipeString(Line, 4)));
                    LibComp.AddPCBObject(Pad);
                    PCBServer.SendMessageToRobots(Pad.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, c_NoEventData);
                end
                else if (LibComp <> nil) and (Kind = 'TRACK') then
                begin
                    Track := PCBServer.PCBObjectFactory(eTrackObject, eNoDimension, eCreate_Default);
                    Track.x1 := MilsToCoord(SafeStrToFloat(Trim(GetFieldFromPipeString(Line, 1))));
                    Track.y1 := MilsToCoord(SafeStrToFloat(Trim(GetFieldFromPipeString(Line, 2))));
                    Track.x2 := MilsToCoord(SafeStrToFloat(Trim(GetFieldFromPipeString(Line, 3))));
                    Track.y2 := MilsToCoord(SafeStrToFloat(Trim(GetFieldFromPipeString(Line, 4))));
                    Track.Width := MilsToCoord(SafeStrToFloat(Trim(GetFieldFromPipeString(Line, 5))));
                    Track.Layer := String2Layer(Trim(GetFieldFromPipeString(Line, 6)));
                    LibComp.AddPCBObject(Track);
                    PCBServer.SendMessageToRobots(Track.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, c_NoEventData);
                end
                else if (LibComp <> nil) and (Kind = 'ARC') then
                begin
                    Arc := PCBServer.PCBObjectFactory(eArcObject, eNoDimension, eCreate_Default);
                    Arc.XCenter := MilsToCoord(SafeStrToFloat(Trim(GetFieldFromPipeString(Line, 1))));
                    Arc.YCenter := MilsToCoord(SafeStrToFloat(Trim(GetFieldFromPipeString(Line, 2))));
                    Arc.Radius := MilsToCoord(SafeStrToFloat(Trim(GetFieldFromPipeString(Line, 3))));
                    Arc.StartAngle := SafeStrToFloat(Trim(GetFieldFromPipeString(Line, 4)));
                    Arc.EndAngle := SafeStrToFloat(Trim(GetFieldFromPipeString(Line, 5)));
                    Arc.LineWidth := MilsToCoord(SafeStrToFloat(Trim(GetFieldFromPipeString(Line, 6))));
                    Arc.Layer := String2Layer(Trim(GetFieldFromPipeString(Line, 7)));
                    LibComp.AddPCBObject(Arc);
                    PCBServer.SendMessageToRobots(Arc.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, c_NoEventData);
                end
                else if (LibComp <> nil) and (Kind = 'FILL') then
                begin
                    Fill := PCBServer.PCBObjectFactory(eFillObject, eNoDimension, eCreate_Default);
                    Fill.x1Location := MilsToCoord(SafeStrToFloat(Trim(GetFieldFromPipeString(Line, 1))));
                    Fill.y1Location := MilsToCoord(SafeStrToFloat(Trim(GetFieldFromPipeString(Line, 2))));
                    Fill.x2Location := MilsToCoord(SafeStrToFloat(Trim(GetFieldFromPipeString(Line, 3))));
                    Fill.y2Location := MilsToCoord(SafeStrToFloat(Trim(GetFieldFromPipeString(Line, 4))));
                    Fill.Rotation := SafeStrToFloat(Trim(GetFieldFromPipeString(Line, 5)));
                    Fill.Layer := String2Layer(Trim(GetFieldFromPipeString(Line, 6)));
                    LibComp.AddPCBObject(Fill);
                    PCBServer.SendMessageToRobots(Fill.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, c_NoEventData);
                end
                else if (LibComp <> nil) and (Kind = 'VIA') then
                begin
                    Via := PCBServer.PCBObjectFactory(eViaObject, eNoDimension, eCreate_Default);
                    Via.x := MilsToCoord(SafeStrToFloat(Trim(GetFieldFromPipeString(Line, 1))));
                    Via.y := MilsToCoord(SafeStrToFloat(Trim(GetFieldFromPipeString(Line, 2))));
                    Via.Size := MilsToCoord(SafeStrToFloat(Trim(GetFieldFromPipeString(Line, 3))));
                    Via.HoleSize := MilsToCoord(SafeStrToFloat(Trim(GetFieldFromPipeString(Line, 4))));
                    Via.LowLayer := String2Layer(Trim(GetFieldFromPipeString(Line, 5)));
                    Via.HighLayer := String2Layer(Trim(GetFieldFromPipeString(Line, 6)));
                    LibComp.AddPCBObject(Via);
                    PCBServer.SendMessageToRobots(Via.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, c_NoEventData);
                end
                else if (LibComp <> nil) and (Kind = 'REGION') then
                begin
                    Region := PCBServer.PCBObjectFactory(eRegionObject, eNoDimension, eCreate_Default);
                    Contour := PCBServer.PCBContourFactory;
                    V := 3;
                    while (Trim(GetFieldFromPipeString(Line, V)) <> '') do
                    begin
                        Contour.AddPoint(MilsToCoord(SafeStrToFloat(Trim(GetFieldFromPipeString(Line, V)))),
                                         MilsToCoord(SafeStrToFloat(Trim(GetFieldFromPipeString(Line, V + 1)))));
                        V := V + 2;
                    end;
                    Region.SetOutlineContour(Contour);
                    Region.Layer := String2Layer(Trim(GetFieldFromPipeString(Line, 1)));
                    Region.Kind := StrToInt(Trim(GetFieldFromPipeString(Line, 2)));
                    LibComp.AddPCBObject(Region);
                    PCBServer.SendMessageToRobots(Region.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, c_NoEventData);
                end
                else if (LibComp <> nil) and (Kind = 'TEXT') then
                begin
                    Text := PCBServer.PCBObjectFactory(eTextObject, eNoDimension, eCreate_Default);
                    Text.XLocation := MilsToCoord(SafeStrToFloat(Trim(GetFieldFromPipeString(Line, 1))));
                    Text.YLocation := MilsToCoord(SafeStrToFloat(Trim(GetFieldFromPipeString(Line, 2))));
                    Text.Size := MilsToCoord(SafeStrToFloat(Trim(GetFieldFromPipeString(Line, 3))));
                    Text.Width := MilsToCoord(SafeStrToFloat(Trim(GetFieldFromPipeString(Line, 4))));
                    Text.Layer := String2Layer(Trim(GetFieldFromPipeString(Line, 6)));
                    Text.MirrorFlag := (Trim(GetFieldFromPipeString(Line, 7)) = '1');
                    Text.UseTTFonts := (Trim(GetFieldFromPipeString(Line, 8)) = '1');
                    // <NL> placeholder carries embedded newlines through the
                    // line-based spec file
                    Text.Text := StringReplace(GetFieldFromPipeString(Line, 9), '<NL>', #13#10, REPLACEALL);
                    Text.Rotation := SafeStrToFloat(Trim(GetFieldFromPipeString(Line, 5)));
                    LibComp.AddPCBObject(Text);
                    PCBServer.SendMessageToRobots(Text.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, c_NoEventData);
                end;
            except
                PrimErrors := PrimErrors + 1;
                if (Kind = 'FOOTPRINT') then
                    FailedArray.Add('"' + JSONEscapeString(Trim(GetFieldFromPipeString(Line, 1))) + '"');
            end;
        end;

        AddJSONInteger(ResultProps, 'created', CreatedCount);
        AddJSONInteger(ResultProps, 'primitive_errors', PrimErrors);
        if (FailedArray.Count > 0) then
            ResultProps.Add(BuildJSONArray(FailedArray, 'failed'))
        else
            ResultProps.Add('"failed": []');

        Result := BuildJSONObject(ResultProps);
    finally
        Lines.Free;
        FailedArray.Free;
        ResultProps.Free;
    end;
end;

// Get the graphic/copper primitives of footprints in a PCB library.
// FootprintName = '' -> inventory (per-footprint primitive counts);
// '*' -> full dump of every footprint; name -> dump that footprint.
// Coordinates in mils, layers as names, shapes/hole types as raw enum ints
// (pass-through symmetric with create_footprints_batch).
function GetFootprintPrimitives(ROOT_DIR: String; LibraryPath: String; FootprintName: String): String;
var
    PcbLib      : IPCB_Library;
    LibComp     : IPCB_LibComponent;
    GrpIter     : IPCB_GroupIterator;
    Prim        : IPCB_Primitive;
    ServerDoc   : IServerDocument;
    ResultProps : TStringList;
    FPArray     : TStringList;
    FPProps     : TStringList;
    PrimsArray  : TStringList;
    PrimProps   : TStringList;
    PointsArray : TStringList;
    PProps      : TStringList;
    Counts      : TStringList;
    OutputLines : TStringList;
    TypeName    : String;
    i, C, V     : Integer;
    Found       : Boolean;
begin
    Result := '';

    if (LibraryPath <> '') then
    begin
        if not FileExists(LibraryPath) then
        begin
            Result := 'ERROR: Library file not found: ' + LibraryPath;
            Exit;
        end;
        // Never re-open an open document (reload discards unsaved changes)
        if Client.IsDocumentOpen(LibraryPath) then
            ServerDoc := Client.GetDocumentByPath(LibraryPath)
        else
            ServerDoc := Client.OpenDocument('PcbLib', LibraryPath);
        if ServerDoc = Nil then
        begin
            Result := 'ERROR: Failed to open library: ' + LibraryPath;
            Exit;
        end;
        Client.ShowDocument(ServerDoc);
        Sleep(500);
    end;

    PcbLib := GetPcbLibSafe(0);
    if (PcbLib = nil) then
    begin
        Result := 'ERROR: No PCB library is open (provide library_path or open a .PcbLib)';
        Exit;
    end;

    ResultProps := TStringList.Create;
    FPArray := TStringList.Create;
    Found := False;

    try
        AddJSONProperty(ResultProps, 'library_name', ExtractFileName(PcbLib.Board.FileName));

        for C := 0 to PcbLib.ComponentCount - 1 do
        begin
            LibComp := PcbLib.GetComponent(C);

            if (FootprintName = '') then
            begin
                // Inventory mode
                Counts := TStringList.Create;
                FPProps := TStringList.Create;
                try
                    GrpIter := LibComp.GroupIterator_Create;
                    Prim := GrpIter.FirstPCBObject;
                    while (Prim <> nil) do
                    begin
                        case Prim.ObjectId of
                            ePadObject:           TypeName := 'pads';
                            eTrackObject:         TypeName := 'tracks';
                            eArcObject:           TypeName := 'arcs';
                            eFillObject:          TypeName := 'fills';
                            eTextObject:          TypeName := 'texts';
                            eRegionObject:        TypeName := 'regions';
                            eViaObject:           TypeName := 'vias';
                            eComponentBodyObject: TypeName := 'component_bodies';
                        else
                            TypeName := 'other';
                        end;
                        i := Counts.IndexOfName(TypeName);
                        if (i < 0) then
                            Counts.Add(TypeName + '=1')
                        else
                            Counts[i] := TypeName + '=' + IntToStr(StrToInt(Counts.ValueFromIndex[i]) + 1);
                        Prim := GrpIter.NextPCBObject;
                    end;
                    LibComp.GroupIterator_Destroy(GrpIter);

                    AddJSONProperty(FPProps, 'name', LibComp.Name);
                    AddJSONProperty(FPProps, 'description', LibComp.Description);
                    for i := 0 to Counts.Count - 1 do
                        AddJSONInteger(FPProps, Counts.Names[i], StrToInt(Counts.ValueFromIndex[i]));
                    FPArray.Add(BuildJSONObject(FPProps, 1));
                finally
                    Counts.Free;
                    FPProps.Free;
                end;
            end
            else if (FootprintName = '*') or (UpperCase(LibComp.Name) = UpperCase(FootprintName)) then
            begin
                // Dump mode
                Found := True;
                FPProps := TStringList.Create;
                PrimsArray := TStringList.Create;
                try
                    AddJSONProperty(FPProps, 'footprint_name', LibComp.Name);
                    AddJSONProperty(FPProps, 'description', LibComp.Description);

                    GrpIter := LibComp.GroupIterator_Create;
                    Prim := GrpIter.FirstPCBObject;
                    while (Prim <> nil) do
                    begin
                        PrimProps := TStringList.Create;
                        try
                          // Armor: an unreadable primitive degrades to a
                          // reported entry instead of crashing the script
                          try
                            case Prim.ObjectId of
                                ePadObject:
                                begin
                                    AddJSONProperty(PrimProps, 'type', 'pad');
                                    AddJSONProperty(PrimProps, 'name', Prim.Name);
                                    AddJSONNumber(PrimProps, 'x', CoordToMils(Prim.x));
                                    AddJSONNumber(PrimProps, 'y', CoordToMils(Prim.y));
                                    AddJSONNumber(PrimProps, 'rotation', Prim.Rotation);
                                    AddJSONProperty(PrimProps, 'layer', Layer2String(Prim.Layer));
                                    AddJSONBoolean(PrimProps, 'plated', Prim.Plated);
                                    AddJSONInteger(PrimProps, 'mode', Prim.Mode);
                                    AddJSONNumber(PrimProps, 'top_x_size', CoordToMils(Prim.TopXSize));
                                    AddJSONNumber(PrimProps, 'top_y_size', CoordToMils(Prim.TopYSize));
                                    AddJSONInteger(PrimProps, 'top_shape', Prim.TopShape);
                                    AddJSONNumber(PrimProps, 'hole_size', CoordToMils(Prim.HoleSize));
                                    AddJSONInteger(PrimProps, 'hole_type', Prim.HoleType);
                                    AddJSONNumber(PrimProps, 'hole_width', CoordToMils(Prim.HoleWidth));
                                    AddJSONNumber(PrimProps, 'hole_rotation', Prim.HoleRotation);
                                    if (Prim.Mode <> ePadMode_Simple) then
                                    begin
                                        AddJSONNumber(PrimProps, 'mid_x_size', CoordToMils(Prim.MidXSize));
                                        AddJSONNumber(PrimProps, 'mid_y_size', CoordToMils(Prim.MidYSize));
                                        AddJSONInteger(PrimProps, 'mid_shape', Prim.MidShape);
                                        AddJSONNumber(PrimProps, 'bot_x_size', CoordToMils(Prim.BotXSize));
                                        AddJSONNumber(PrimProps, 'bot_y_size', CoordToMils(Prim.BotYSize));
                                        AddJSONInteger(PrimProps, 'bot_shape', Prim.BotShape);
                                    end;
                                    if (Prim.TopShape = eRoundedRectangular) then
                                        AddJSONInteger(PrimProps, 'corner_pct', Prim.StackCRPctOnLayer[eTopLayer]);
                                end;
                                eTrackObject:
                                begin
                                    AddJSONProperty(PrimProps, 'type', 'track');
                                    AddJSONNumber(PrimProps, 'x1', CoordToMils(Prim.x1));
                                    AddJSONNumber(PrimProps, 'y1', CoordToMils(Prim.y1));
                                    AddJSONNumber(PrimProps, 'x2', CoordToMils(Prim.x2));
                                    AddJSONNumber(PrimProps, 'y2', CoordToMils(Prim.y2));
                                    AddJSONNumber(PrimProps, 'width', CoordToMils(Prim.Width));
                                    AddJSONProperty(PrimProps, 'layer', Layer2String(Prim.Layer));
                                end;
                                eArcObject:
                                begin
                                    AddJSONProperty(PrimProps, 'type', 'arc');
                                    AddJSONNumber(PrimProps, 'cx', CoordToMils(Prim.XCenter));
                                    AddJSONNumber(PrimProps, 'cy', CoordToMils(Prim.YCenter));
                                    AddJSONNumber(PrimProps, 'radius', CoordToMils(Prim.Radius));
                                    AddJSONNumber(PrimProps, 'start_angle', Prim.StartAngle);
                                    AddJSONNumber(PrimProps, 'end_angle', Prim.EndAngle);
                                    AddJSONNumber(PrimProps, 'width', CoordToMils(Prim.LineWidth));
                                    AddJSONProperty(PrimProps, 'layer', Layer2String(Prim.Layer));
                                end;
                                eFillObject:
                                begin
                                    AddJSONProperty(PrimProps, 'type', 'fill');
                                    AddJSONNumber(PrimProps, 'x1', CoordToMils(Prim.x1Location));
                                    AddJSONNumber(PrimProps, 'y1', CoordToMils(Prim.y1Location));
                                    AddJSONNumber(PrimProps, 'x2', CoordToMils(Prim.x2Location));
                                    AddJSONNumber(PrimProps, 'y2', CoordToMils(Prim.y2Location));
                                    AddJSONNumber(PrimProps, 'rotation', Prim.Rotation);
                                    AddJSONProperty(PrimProps, 'layer', Layer2String(Prim.Layer));
                                end;
                                eTextObject:
                                begin
                                    AddJSONProperty(PrimProps, 'type', 'text');
                                    AddJSONProperty(PrimProps, 'text', Prim.Text);
                                    AddJSONNumber(PrimProps, 'x', CoordToMils(Prim.XLocation));
                                    AddJSONNumber(PrimProps, 'y', CoordToMils(Prim.YLocation));
                                    AddJSONNumber(PrimProps, 'size', CoordToMils(Prim.Size));
                                    AddJSONNumber(PrimProps, 'width', CoordToMils(Prim.Width));
                                    AddJSONNumber(PrimProps, 'rotation', Prim.Rotation);
                                    AddJSONProperty(PrimProps, 'layer', Layer2String(Prim.Layer));
                                    AddJSONBoolean(PrimProps, 'mirror', Prim.MirrorFlag);
                                    AddJSONBoolean(PrimProps, 'ttf', Prim.UseTTFonts);
                                end;
                                eRegionObject:
                                begin
                                    AddJSONProperty(PrimProps, 'type', 'region');
                                    AddJSONProperty(PrimProps, 'layer', Layer2String(Prim.Layer));
                                    AddJSONInteger(PrimProps, 'kind', Prim.Kind);
                                    PointsArray := TStringList.Create;
                                    try
                                        for V := 1 to Prim.MainContour.Count do
                                        begin
                                            PProps := TStringList.Create;
                                            try
                                                AddJSONNumber(PProps, 'x', CoordToMils(Prim.MainContour.x[V]));
                                                AddJSONNumber(PProps, 'y', CoordToMils(Prim.MainContour.y[V]));
                                                PointsArray.Add(BuildJSONObject(PProps, 3));
                                            finally
                                                PProps.Free;
                                            end;
                                        end;
                                        PrimProps.Add(BuildJSONArray(PointsArray, 'vertices', 2));
                                    finally
                                        PointsArray.Free;
                                    end;
                                    AddJSONInteger(PrimProps, 'hole_count', Prim.HoleCount);
                                end;
                                eViaObject:
                                begin
                                    AddJSONProperty(PrimProps, 'type', 'via');
                                    AddJSONNumber(PrimProps, 'x', CoordToMils(Prim.x));
                                    AddJSONNumber(PrimProps, 'y', CoordToMils(Prim.y));
                                    AddJSONNumber(PrimProps, 'size', CoordToMils(Prim.Size));
                                    AddJSONNumber(PrimProps, 'hole_size', CoordToMils(Prim.HoleSize));
                                    AddJSONProperty(PrimProps, 'low_layer', Layer2String(Prim.LowLayer));
                                    AddJSONProperty(PrimProps, 'high_layer', Layer2String(Prim.HighLayer));
                                end;
                                eComponentBodyObject:
                                    // 3D bodies are models, not 2D primitives -
                                    // excluded from the graphics round-trip
                                    AddJSONProperty(PrimProps, 'type', '');
                            else
                            begin
                                AddJSONProperty(PrimProps, 'type', 'unknown');
                                AddJSONInteger(PrimProps, 'object_id', Prim.ObjectId);
                            end;
                            end;

                            if (PrimProps.Count > 0) then
                                if (Pos('"type": ""', PrimProps[0]) = 0) then
                                    PrimsArray.Add(BuildJSONObject(PrimProps, 1));
                          except
                            PrimProps.Clear;
                            AddJSONProperty(PrimProps, 'type', 'unreadable');
                            PrimsArray.Add(BuildJSONObject(PrimProps, 1));
                          end;
                        finally
                            PrimProps.Free;
                        end;

                        Prim := GrpIter.NextPCBObject;
                    end;
                    LibComp.GroupIterator_Destroy(GrpIter);

                    FPProps.Add(BuildJSONArray(PrimsArray, 'primitives', 1));

                    if (FootprintName = '*') then
                        FPArray.Add(BuildJSONObject(FPProps, 1))
                    else
                        for i := 0 to FPProps.Count - 1 do
                            ResultProps.Add(FPProps[i]);
                finally
                    FPProps.Free;
                    PrimsArray.Free;
                end;
            end;
        end;

        if (FootprintName = '') or (FootprintName = '*') then
        begin
            AddJSONInteger(ResultProps, 'footprint_count', FPArray.Count);
            ResultProps.Add(BuildJSONArray(FPArray, 'footprints', 1));
        end
        else if not Found then
        begin
            Result := 'ERROR: Footprint not found in library: ' + FootprintName;
            Exit;
        end;

        OutputLines := TStringList.Create;
        try
            OutputLines.Text := BuildJSONObject(ResultProps);
            Result := WriteJSONToFile(OutputLines, ROOT_DIR + '\temp_footprint_primitives.json');
        finally
            OutputLines.Free;
        end;
    finally
        ResultProps.Free;
        FPArray.Free;
    end;
end;


// Function to get all unique net names from the current PCB document
function GetAllNets(ROOT_DIR: String): String;
var
    Board       : IPCB_Board;
    Net         : IPCB_Net;
    Iterator    : IPCB_BoardIterator;
    NetsArray   : TStringList; 
    OutputLines : TStringList;
begin
    // Initialize empty array result in case no board is found
    Result := '[]';
    
    // Retrieve the current board
    Board := GetBoardSafe(0);
    if Board = nil then Exit;

    // Create array for storing unique nets
    NetsArray := TStringList.Create;
    // Set Duplicates property to prevent duplicate net names
    NetsArray.Duplicates := dupIgnore;
    NetsArray.Sorted := True;
    
    try
        // Create the iterator that will look for Net objects only
        Iterator := Board.BoardIterator_Create;
        Iterator.AddFilter_ObjectSet(MkSet(eNetObject));
        Iterator.AddFilter_LayerSet(AllLayers);
        Iterator.AddFilter_Method(eProcessAll);

        // Search for Net objects and get their Net Name values
        Net := Iterator.FirstPCBObject;
        while (Net <> nil) do
        begin
            // Add each net name to the list, duplicates will be ignored
            NetsArray.Add('"' + JSONEscapeString(Net.Name) + '"');
            Net := Iterator.NextPCBObject;
        end;
        Board.BoardIterator_Destroy(Iterator);
        
        // Build the final JSON array
        OutputLines := TStringList.Create;
        try
            OutputLines.Text := BuildJSONArray(NetsArray);
            Result := WriteJSONToFile(OutputLines, ROOT_DIR+'\temp_nets_data.json');
        finally
            OutputLines.Free;
        end;
    finally
        NetsArray.Free;
    end;
end;

// Function to create a net class and add nets to it
function CreateNetClass(ClassName: String; NetNames: TStringList): String;
var
    Board       : IPCB_Board;
    ClassExists : Boolean;
    NetClass    : IPCB_ObjectClass;
    ClassIterator : IPCB_BoardIterator;
    i           : Integer;
    ResultProps : TStringList;
    AddedCount  : Integer;
    OutputLines : TStringList;
begin
    // Initialize result
    ResultProps := TStringList.Create;
    AddedCount := 0;
    ClassExists := False;
    
    try
        // Retrieve the current board
        Board := GetBoardSafe(0);
        if (Board = nil) then
        begin
            AddJSONBoolean(ResultProps, 'success', False);
            AddJSONProperty(ResultProps, 'error', 'No PCB document is currently active');
            
            OutputLines := TStringList.Create;
            try
                OutputLines.Text := BuildJSONObject(ResultProps);
                Result := OutputLines.Text;
            finally
                OutputLines.Free;
            end;
            Exit;
        end;
        
        // Search for existing class with the same name
        ClassIterator := Board.BoardIterator_Create;
        ClassIterator.SetState_FilterAll;
        ClassIterator.AddFilter_ObjectSet(MkSet(eClassObject));
        
        NetClass := ClassIterator.FirstPCBObject;
        while (NetClass <> nil) do
        begin
            if (NetClass.MemberKind = eClassMemberKind_Net) and (NetClass.Name = ClassName) then
            begin
                ClassExists := True;
                Break;
            end;
            NetClass := ClassIterator.NextPCBObject;
        end;
        
        // If class doesn't exist, create it
        if not ClassExists then
        begin
            PCBServer.PreProcess;
            NetClass := PCBServer.PCBClassFactoryByClassMember(eClassMemberKind_Net);
            NetClass.SuperClass := False;
            NetClass.Name := ClassName;
            Board.AddPCBObject(NetClass);
            PCBServer.PostProcess;
        end;
        
        // Add nets to the class
        PCBServer.PreProcess;
        for i := 0 to NetNames.Count - 1 do
        begin
            // Add each net to the class
            if NetClass.AddMemberByName(NetNames[i]) then
                AddedCount := AddedCount + 1;
        end;
        PCBServer.PostProcess;
        
        // Clean up iterator
        Board.BoardIterator_Destroy(ClassIterator);
        
        // Build result JSON
        AddJSONBoolean(ResultProps, 'success', True);
        AddJSONProperty(ResultProps, 'class_name', ClassName);
        AddJSONBoolean(ResultProps, 'class_created', not ClassExists);
        AddJSONInteger(ResultProps, 'nets_added', AddedCount);
        
        OutputLines := TStringList.Create;
        try
            OutputLines.Text := BuildJSONObject(ResultProps);
            Result := OutputLines.Text;
        finally
            OutputLines.Free;
        end;
    finally
        ResultProps.Free;
    end;
end;

// Function to get detailed layer stackup information
function GetPCBLayerStackup(ROOT_DIR): String;
var
    Board           : IPCB_Board;
    LayerIterator   : IPCB_LayerObjectIterator;
    LayerObject     : IPCB_LayerObject;
    StackupArray    : TStringList;
    LayerProps      : TStringList;
    OutputLines     : TStringList;
    TotalThickness  : Double;
    LayerCount      : Integer;
begin
    Result := '';

    // Retrieve the current board
    Board := GetBoardSafe(0);
    if (Board = nil) then
    begin
        Result := '{"error": "No PCB document is currently active"}';
        Exit;
    end;

    // Create arrays for stackup data
    StackupArray := TStringList.Create;
    TotalThickness := 0;
    LayerCount := 0;
    
    try
        // Get the electrical layer iterator
        LayerIterator := Board.ElectricalLayerIterator;
        
        // Process each electrical layer
        while LayerIterator.Next do
        begin
            LayerObject := LayerIterator.LayerObject;
            
            // Create layer properties
            LayerProps := TStringList.Create;
            try
                // Basic layer information
                AddJSONProperty(LayerProps, 'layer_name', LayerObject.Name);
                AddJSONProperty(LayerProps, 'layer_id', Layer2String(LayerObject.LayerID));
                AddJSONProperty(LayerProps, 'material_type', 'Copper');
                AddJSONNumber(LayerProps, 'copper_thickness_mils', LayerObject.CopperThickness / 10000);
                AddJSONNumber(LayerProps, 'copper_thickness_um', LayerObject.CopperThickness / 254);
                
                // Add copper thickness to total
                TotalThickness := TotalThickness + (LayerObject.CopperThickness / 10000);
                
                // Dielectric information (if present)
                if LayerObject.Dielectric.DielectricType <> eNoDielectric then
                begin
                    case LayerObject.Dielectric.DielectricType of
                        eCore: AddJSONProperty(LayerProps, 'dielectric_type', 'Core');
                        ePrePreg: AddJSONProperty(LayerProps, 'dielectric_type', 'PrePreg');
                        eSurfaceMaterial: AddJSONProperty(LayerProps, 'dielectric_type', 'Surface Material');
                    else
                        AddJSONProperty(LayerProps, 'dielectric_type', 'Unknown');
                    end;
                    
                    AddJSONProperty(LayerProps, 'dielectric_material', LayerObject.Dielectric.DielectricMaterial);
                    AddJSONNumber(LayerProps, 'dielectric_height_mils', LayerObject.Dielectric.DielectricHeight / 10000);
                    AddJSONNumber(LayerProps, 'dielectric_height_um', LayerObject.Dielectric.DielectricHeight / 254);
                    AddJSONNumber(LayerProps, 'dielectric_constant', LayerObject.Dielectric.DielectricConstant);
                    
                    // Add dielectric thickness to total
                    TotalThickness := TotalThickness + (LayerObject.Dielectric.DielectricHeight / 10000);
                end
                else
                begin
                    AddJSONProperty(LayerProps, 'dielectric_type', 'No Dielectric');
                    AddJSONProperty(LayerProps, 'dielectric_material', '');
                    AddJSONNumber(LayerProps, 'dielectric_height_mils', 0);
                    AddJSONNumber(LayerProps, 'dielectric_height_um', 0);
                    AddJSONNumber(LayerProps, 'dielectric_constant', 0);
                end;
                
                // Add layer order
                AddJSONInteger(LayerProps, 'layer_order', LayerCount + 1);
                
                // Add to stackup array
                StackupArray.Add(BuildJSONObject(LayerProps, 1));
                LayerCount := LayerCount + 1;
            finally
                LayerProps.Free;
            end;
        end;
        
        // Create final stackup object with summary
        LayerProps := TStringList.Create;
        try
            AddJSONInteger(LayerProps, 'total_layers', LayerCount);
            AddJSONNumber(LayerProps, 'total_thickness_mils', TotalThickness);
            AddJSONNumber(LayerProps, 'total_thickness_mm', TotalThickness * 0.0254);
            AddJSONProperty(LayerProps, 'board_name', ExtractFileName(Board.FileName));
            
            // Add the layers array
            LayerProps.Add(BuildJSONArray(StackupArray, 'layers'));
            
            // Build the final JSON
            OutputLines := TStringList.Create;
            try
                OutputLines.Text := BuildJSONObject(LayerProps);
                Result := WriteJSONToFile(OutputLines, ROOT_DIR+'\temp_stackup_data.json');
            finally
                OutputLines.Free;
            end;
        finally
            LayerProps.Free;
        end;
    finally
        StackupArray.Free;
    end;
end;

// Function to get all layer information from the PCB
function GetPCBLayers(ROOT_DIR: String): String;
var
    Board           : IPCB_Board;
    TheLayerStack   : IPCB_LayerStack_V7;
    LayerObj        : IPCB_LayerObject;
    MechLayer       : IPCB_MechanicalLayer;
    AllLayersArray  : TStringList;
    CopperArray     : TStringList;
    MechArray       : TStringList;
    OtherArray      : TStringList;
    LayerProps      : TStringList;
    i               : Integer;
    OutputLines     : TStringList;
begin
    Result := '';

    // Retrieve the current board
    Board := GetBoardSafe(0);
    if (Board = nil) then
    begin
        Result := '[]';
        Exit;
    end;
    
    // Get the layer stack
    TheLayerStack := Board.LayerStack_V7;
    if (TheLayerStack = nil) then
    begin
        Result := '[]';
        Exit;
    end;

    // Create arrays for different layer categories
    AllLayersArray := TStringList.Create;
    CopperArray := TStringList.Create;
    MechArray := TStringList.Create;
    OtherArray := TStringList.Create;
    
    try
        // Process copper (electrical) layers
        LayerObj := TheLayerStack.FirstLayer;
        while (LayerObj <> nil) do
        begin
            // Create layer properties
            LayerProps := TStringList.Create;
            try
                // Add properties
                AddJSONProperty(LayerProps, 'name', LayerObj.Name);
                AddJSONProperty(LayerProps, 'layer_id', IntToStr(LayerObj.V6_LayerID));
                AddJSONProperty(LayerProps, 'layer_type', 'copper');

                if LayerSet.SignalLayers.Contains(LayerObj.V6_LayerID) then
                    AddJSONProperty(LayerProps, 'is_signal', 'true', False)
                else
                    AddJSONProperty(LayerProps, 'is_signal', 'false', False);

                if not LayerSet.SignalLayers.Contains(LayerObj.V6_LayerID) then
                    AddJSONProperty(LayerProps, 'is_plane', 'true', False)
                else
                    AddJSONProperty(LayerProps, 'is_plane', 'false', False);

                AddJSONBoolean(LayerProps, 'is_displayed', LayerObj.IsDisplayed[Board]);
                AddJSONBoolean(LayerProps, 'is_enabled', True);
                AddJSONProperty(LayerProps, 'color', ColorToString(Board.LayerColor[LayerObj.LayerID]));
                
                // Add to copper array
                CopperArray.Add(BuildJSONObject(LayerProps, 1));
            finally
                LayerProps.Free;
            end;
            
            LayerObj := TheLayerStack.NextLayer(LayerObj);
        end;
        
        // Process mechanical layers
        for i := 1 to 32 do
        begin
            MechLayer := TheLayerStack.LayerObject_V7[ILayer.MechanicalLayer(i)];
            
            if MechLayer.MechanicalLayerEnabled then
            begin
                // Create layer properties
                LayerProps := TStringList.Create;
                try
                    // Add properties
                    AddJSONProperty(LayerProps, 'name', MechLayer.Name);
                    AddJSONProperty(LayerProps, 'layer_id', IntToStr(MechLayer.V6_LayerID));
                    AddJSONProperty(LayerProps, 'layer_type', 'mechanical');
                    AddJSONProperty(LayerProps, 'mechanical_number', IntToStr(i));
                    AddJSONBoolean(LayerProps, 'is_displayed', MechLayer.IsDisplayed[Board]);
                    AddJSONBoolean(LayerProps, 'is_enabled', MechLayer.MechanicalLayerEnabled);
                    AddJSONBoolean(LayerProps, 'link_to_sheet', MechLayer.LinkToSheet);
                    AddJSONBoolean(LayerProps, 'is_paired', Board.MechanicalPairs.LayerUsed(ILayer.MechanicalLayer(i)));
                    AddJSONProperty(LayerProps, 'color', ColorToString(PCBServer.SystemOptions.LayerColors[MechLayer.V6_LayerID]));
                    
                    // If layer is paired, add the pair information
                    if Board.MechanicalPairs.LayerUsed(ILayer.MechanicalLayer(i)) then
                    begin
                        // Could add pair info here if Altium API provides it
                    end;
                    
                    // Add to mechanical array
                    MechArray.Add(BuildJSONObject(LayerProps, 1));
                finally
                    LayerProps.Free;
                end;
            end;
        end;
        
        // Process other special layers
        // Top Overlay
        LayerProps := TStringList.Create;
        try
            AddJSONProperty(LayerProps, 'name', 'Top Overlay');
            AddJSONProperty(LayerProps, 'layer_id', IntToStr(String2Layer('Top Overlay')));
            AddJSONProperty(LayerProps, 'layer_type', 'special');
            AddJSONProperty(LayerProps, 'special_type', 'overlay');
            AddJSONBoolean(LayerProps, 'is_displayed', Board.LayerIsDisplayed[String2Layer('Top Overlay')]);
            AddJSONProperty(LayerProps, 'color', ColorToString(Board.LayerColor[String2Layer('Top Overlay')]));
            OtherArray.Add(BuildJSONObject(LayerProps, 1));
        finally
            LayerProps.Free;
        end;
        
        // Bottom Overlay
        LayerProps := TStringList.Create;
        try
            AddJSONProperty(LayerProps, 'name', 'Bottom Overlay');
            AddJSONProperty(LayerProps, 'layer_id', IntToStr(String2Layer('Bottom Overlay')));
            AddJSONProperty(LayerProps, 'layer_type', 'special');
            AddJSONProperty(LayerProps, 'special_type', 'overlay');
            AddJSONBoolean(LayerProps, 'is_displayed', Board.LayerIsDisplayed[String2Layer('Bottom Overlay')]);
            AddJSONProperty(LayerProps, 'color', ColorToString(Board.LayerColor[String2Layer('Bottom Overlay')]));
            OtherArray.Add(BuildJSONObject(LayerProps, 1));
        finally
            LayerProps.Free;
        end;
        
        // Top Solder Mask
        LayerProps := TStringList.Create;
        try
            AddJSONProperty(LayerProps, 'name', 'Top Solder Mask');
            AddJSONProperty(LayerProps, 'layer_id', IntToStr(String2Layer('Top Solder Mask')));
            AddJSONProperty(LayerProps, 'layer_type', 'special');
            AddJSONProperty(LayerProps, 'special_type', 'solder_mask');
            AddJSONBoolean(LayerProps, 'is_displayed', Board.LayerIsDisplayed[String2Layer('Top Solder Mask')]);
            AddJSONProperty(LayerProps, 'color', ColorToString(Board.LayerColor[String2Layer('Top Solder Mask')]));
            OtherArray.Add(BuildJSONObject(LayerProps, 1));
        finally
            LayerProps.Free;
        end;
        
        // Bottom Solder Mask
        LayerProps := TStringList.Create;
        try
            AddJSONProperty(LayerProps, 'name', 'Bottom Solder Mask');
            AddJSONProperty(LayerProps, 'layer_id', IntToStr(String2Layer('Bottom Solder Mask')));
            AddJSONProperty(LayerProps, 'layer_type', 'special');
            AddJSONProperty(LayerProps, 'special_type', 'solder_mask');
            AddJSONBoolean(LayerProps, 'is_displayed', Board.LayerIsDisplayed[String2Layer('Bottom Solder Mask')]);
            AddJSONProperty(LayerProps, 'color', ColorToString(Board.LayerColor[String2Layer('Bottom Solder Mask')]));
            OtherArray.Add(BuildJSONObject(LayerProps, 1));
        finally
            LayerProps.Free;
        end;
        
        // Top Paste
        LayerProps := TStringList.Create;
        try
            AddJSONProperty(LayerProps, 'name', 'Top Paste');
            AddJSONProperty(LayerProps, 'layer_id', IntToStr(String2Layer('Top Paste')));
            AddJSONProperty(LayerProps, 'layer_type', 'special');
            AddJSONProperty(LayerProps, 'special_type', 'paste');
            AddJSONBoolean(LayerProps, 'is_displayed', Board.LayerIsDisplayed[String2Layer('Top Paste')]);
            AddJSONProperty(LayerProps, 'color', ColorToString(Board.LayerColor[String2Layer('Top Paste')]));
            OtherArray.Add(BuildJSONObject(LayerProps, 1));
        finally
            LayerProps.Free;
        end;
        
        // Bottom Paste
        LayerProps := TStringList.Create;
        try
            AddJSONProperty(LayerProps, 'name', 'Bottom Paste');
            AddJSONProperty(LayerProps, 'layer_id', IntToStr(String2Layer('Bottom Paste')));
            AddJSONProperty(LayerProps, 'layer_type', 'special');
            AddJSONProperty(LayerProps, 'special_type', 'paste');
            AddJSONBoolean(LayerProps, 'is_displayed', Board.LayerIsDisplayed[String2Layer('Bottom Paste')]);
            AddJSONProperty(LayerProps, 'color', ColorToString(Board.LayerColor[String2Layer('Bottom Paste')]));
            OtherArray.Add(BuildJSONObject(LayerProps, 1));
        finally
            LayerProps.Free;
        end;
        
        // Drill Guide
        LayerProps := TStringList.Create;
        try
            AddJSONProperty(LayerProps, 'name', 'Drill Guide');
            AddJSONProperty(LayerProps, 'layer_id', IntToStr(String2Layer('Drill Guide')));
            AddJSONProperty(LayerProps, 'layer_type', 'special');
            AddJSONProperty(LayerProps, 'special_type', 'drill');
            AddJSONBoolean(LayerProps, 'is_displayed', Board.LayerIsDisplayed[String2Layer('Drill Guide')]);
            AddJSONProperty(LayerProps, 'color', ColorToString(Board.LayerColor[String2Layer('Drill Guide')]));
            OtherArray.Add(BuildJSONObject(LayerProps, 1));
        finally
            LayerProps.Free;
        end;
        
        // Drill Drawing
        LayerProps := TStringList.Create;
        try
            AddJSONProperty(LayerProps, 'name', 'Drill Drawing');
            AddJSONProperty(LayerProps, 'layer_id', IntToStr(String2Layer('Drill Drawing')));
            AddJSONProperty(LayerProps, 'layer_type', 'special');
            AddJSONProperty(LayerProps, 'special_type', 'drill');
            AddJSONBoolean(LayerProps, 'is_displayed', Board.LayerIsDisplayed[String2Layer('Drill Drawing')]);
            AddJSONProperty(LayerProps, 'color', ColorToString(Board.LayerColor[String2Layer('Drill Drawing')]));
            OtherArray.Add(BuildJSONObject(LayerProps, 1));
        finally
            LayerProps.Free;
        end;
        
        // Multi Layer
        LayerProps := TStringList.Create;
        try
            AddJSONProperty(LayerProps, 'name', 'Multi Layer');
            AddJSONProperty(LayerProps, 'layer_id', IntToStr(String2Layer('Multi Layer')));
            AddJSONProperty(LayerProps, 'layer_type', 'special');
            AddJSONProperty(LayerProps, 'special_type', 'multi');
            AddJSONBoolean(LayerProps, 'is_displayed', Board.LayerIsDisplayed[String2Layer('Multi Layer')]);
            AddJSONProperty(LayerProps, 'color', ColorToString(Board.LayerColor[String2Layer('Multi Layer')]));
            OtherArray.Add(BuildJSONObject(LayerProps, 1));
        finally
            LayerProps.Free;
        end;
        
        // Keep Out Layer
        LayerProps := TStringList.Create;
        try
            AddJSONProperty(LayerProps, 'name', 'Keep Out Layer');
            AddJSONProperty(LayerProps, 'layer_id', IntToStr(String2Layer('Keep Out Layer')));
            AddJSONProperty(LayerProps, 'layer_type', 'special');
            AddJSONProperty(LayerProps, 'special_type', 'keepout');
            AddJSONBoolean(LayerProps, 'is_displayed', Board.LayerIsDisplayed[String2Layer('Keep Out Layer')]);
            AddJSONProperty(LayerProps, 'color', ColorToString(Board.LayerColor[String2Layer('Keep Out Layer')]));
            OtherArray.Add(BuildJSONObject(LayerProps, 1));
        finally
            LayerProps.Free;
        end;
        
        // Add additional info for the complete layer response
        LayerProps := TStringList.Create;
        try
            // Add summary information
            AddJSONInteger(LayerProps, 'copper_layers_count', TheLayerStack.LayersInStackCount);
            AddJSONInteger(LayerProps, 'signal_layers_count', TheLayerStack.SignalLayerCount);
            AddJSONInteger(LayerProps, 'internal_planes_count', TheLayerStack.LayersInStackCount - TheLayerStack.SignalLayerCount);
            
            // Get the number of enabled mechanical layers
            i := 0;
            for i := 1 to 32 do
                if TheLayerStack.LayerObject_V7[ILayer.MechanicalLayer(i)].MechanicalLayerEnabled then
                    i := i + 1;
            AddJSONInteger(LayerProps, 'mechanical_layers_count', i);
            
            // Add the layer arrays
            LayerProps.Add(BuildJSONArray(CopperArray, 'copper_layers'));
            LayerProps.Add(BuildJSONArray(MechArray, 'mechanical_layers'));
            LayerProps.Add(BuildJSONArray(OtherArray, 'special_layers'));
            
            // Build the final JSON
            OutputLines := TStringList.Create;
            try
                OutputLines.Text := BuildJSONObject(LayerProps);
                Result := WriteJSONToFile(OutputLines, ROOT_DIR+'\temp_layers_data.json');
            finally
                OutputLines.Free;
            end;
        finally
            LayerProps.Free;
        end;
    finally
        AllLayersArray.Free;
        CopperArray.Free;
        MechArray.Free;
        OtherArray.Free;
    end;
end;

// Function to set layer visibility (only specified layers visible)
// Function to set layer visibility with two modes:
// - visible=true: Show only specified layers, hide all others
// - visible=false: Hide specified layers, leave others unchanged
function SetPCBLayerVisibility(LayerNamesList: TStringList; Visible: Boolean): String;
var
    Board          : IPCB_Board;
    TheLayerStack  : IPCB_LayerStack_V7;
    LayerObj       : IPCB_LayerObject;
    MechLayer      : IPCB_MechanicalLayer;
    ResultProps    : TStringList;
    OutputLines    : TStringList;
    i, j           : Integer;
    LayerName      : String;
    LayerID        : TLayer;
    FoundCount     : Integer;
    NotFoundList   : TStringList;
    FoundLayers    : TStringList;
begin
    // Retrieve the current board
    Board := GetBoardSafe(0);
    if (Board = nil) then
    begin
        Result := '{"success": false, "error": "No PCB document is currently active"}';
        Exit;
    end;
    
    // Get the layer stack
    TheLayerStack := Board.LayerStack_V7;
    if (TheLayerStack = nil) then
    begin
        Result := '{"success": false, "error": "Failed to retrieve layer stack"}';
        Exit;
    end;
    
    // Create lists for tracking results
    ResultProps := TStringList.Create;
    NotFoundList := TStringList.Create;
    FoundLayers := TStringList.Create;
    FoundCount := 0;
    
    try
        // First phase: identify all specified layers
        for i := 0 to LayerNamesList.Count - 1 do
        begin
            LayerName := LayerNamesList[i];
            
            // Try to find the layer by name
            // First check special layers (since they have specific names)
            if (LayerName = 'Top Overlay') or 
               (LayerName = 'Bottom Overlay') or
               (LayerName = 'Top Solder Mask') or
               (LayerName = 'Bottom Solder Mask') or
               (LayerName = 'Top Paste') or
               (LayerName = 'Bottom Paste') or
               (LayerName = 'Drill Guide') or
               (LayerName = 'Drill Drawing') or
               (LayerName = 'Multi Layer') or
               (LayerName = 'Keep Out Layer') then
            begin
                // Get layer ID from name
                LayerID := String2Layer(LayerName);
                if (LayerID <> eNoLayer) then
                begin
                    FoundLayers.Add(IntToStr(LayerID));
                    FoundCount := FoundCount + 1;
                end
                else
                    NotFoundList.Add('"' + JSONEscapeString(LayerName) + '"');
                
                continue;
            end;
            
            // Check copper layers
            LayerObj := TheLayerStack.FirstLayer;
            j := 1;
            
            while (LayerObj <> nil) do
            begin
                if (LayerObj.Name = LayerName) then
                begin
                    FoundLayers.Add(IntToStr(LayerObj.V6_LayerID));
                    FoundCount := FoundCount + 1;
                    break;
                end;
                
                Inc(j);
                LayerObj := TheLayerStack.NextLayer(LayerObj);
            end;
            
            // If we found the layer in copper layers, continue to next layer name
            if (LayerObj <> nil) then
                continue;
            
            // Check mechanical layers (they can have custom names)
            for j := 1 to 32 do
            begin
                MechLayer := TheLayerStack.LayerObject_V7[ILayer.MechanicalLayer(j)];
                
                if MechLayer.MechanicalLayerEnabled and (MechLayer.Name = LayerName) then
                begin
                    FoundLayers.Add(IntToStr(MechLayer.V6_LayerID));
                    FoundCount := FoundCount + 1;
                    break;
                end;
            end;
            
            // If we've checked all layer types and didn't find a match, add to not found list
            if j > 32 then
                NotFoundList.Add('"' + JSONEscapeString(LayerName) + '"');
        end;
        
        // Second phase: set visibility for all layers based on mode
        if Visible then
        begin
            // Visibility mode: show only specified layers, hide all others
            
            // For copper layers
            LayerObj := TheLayerStack.FirstLayer;
            while (LayerObj <> nil) do
            begin
                // Check if this layer is in our found list
                if (FoundLayers.IndexOf(IntToStr(LayerObj.V6_LayerID)) >= 0) then
                    LayerObj.IsDisplayed[Board] := True
                else
                    LayerObj.IsDisplayed[Board] := False;
                
                LayerObj := TheLayerStack.NextLayer(LayerObj);
            end;
            
            // For mechanical layers
            for j := 1 to 32 do
            begin
                MechLayer := TheLayerStack.LayerObject_V7[ILayer.MechanicalLayer(j)];
                
                if MechLayer.MechanicalLayerEnabled then
                begin
                    if (FoundLayers.IndexOf(IntToStr(MechLayer.V6_LayerID)) >= 0) then
                        MechLayer.IsDisplayed[Board] := True
                    else
                        MechLayer.IsDisplayed[Board] := False;
                end;
            end;
            
            // For special layers
            for j := 1 to 10 do
            begin
                case j of
                    1: LayerID := String2Layer('Top Overlay');
                    2: LayerID := String2Layer('Bottom Overlay');
                    3: LayerID := String2Layer('Top Solder Mask');
                    4: LayerID := String2Layer('Bottom Solder Mask');
                    5: LayerID := String2Layer('Top Paste');
                    6: LayerID := String2Layer('Bottom Paste');
                    7: LayerID := String2Layer('Drill Guide');
                    8: LayerID := String2Layer('Drill Drawing');
                    9: LayerID := String2Layer('Multi Layer');
                    10: LayerID := String2Layer('Keep Out Layer');
                end;
                
                if (FoundLayers.IndexOf(IntToStr(LayerID)) >= 0) then
                    Board.LayerIsDisplayed[LayerID] := True
                else
                    Board.LayerIsDisplayed[LayerID] := False;
            end;
        end
        else
        begin
            // Hide mode: only hide specified layers, leave others unchanged
            
            // For copper layers
            LayerObj := TheLayerStack.FirstLayer;
            while (LayerObj <> nil) do
            begin
                // Check if this layer is in our found list
                if (FoundLayers.IndexOf(IntToStr(LayerObj.V6_LayerID)) >= 0) then
                    LayerObj.IsDisplayed[Board] := False;
                
                LayerObj := TheLayerStack.NextLayer(LayerObj);
            end;
            
            // For mechanical layers
            for j := 1 to 32 do
            begin
                MechLayer := TheLayerStack.LayerObject_V7[ILayer.MechanicalLayer(j)];
                
                if MechLayer.MechanicalLayerEnabled then
                begin
                    if (FoundLayers.IndexOf(IntToStr(MechLayer.V6_LayerID)) >= 0) then
                        MechLayer.IsDisplayed[Board] := False;
                end;
            end;
            
            // For special layers
            for j := 1 to 10 do
            begin
                case j of
                    1: LayerID := String2Layer('Top Overlay');
                    2: LayerID := String2Layer('Bottom Overlay');
                    3: LayerID := String2Layer('Top Solder Mask');
                    4: LayerID := String2Layer('Bottom Solder Mask');
                    5: LayerID := String2Layer('Top Paste');
                    6: LayerID := String2Layer('Bottom Paste');
                    7: LayerID := String2Layer('Drill Guide');
                    8: LayerID := String2Layer('Drill Drawing');
                    9: LayerID := String2Layer('Multi Layer');
                    10: LayerID := String2Layer('Keep Out Layer');
                end;
                
                if (FoundLayers.IndexOf(IntToStr(LayerID)) >= 0) then
                    Board.LayerIsDisplayed[LayerID] := False;
            end;
        end;
        
        // Update the display
        Board.ViewManager_FullUpdate;
        Board.ViewManager_UpdateLayerTabs;
        
        // Create result JSON
        AddJSONBoolean(ResultProps, 'success', True);
        AddJSONInteger(ResultProps, 'updated_count', FoundCount);
        
        // Add missing layers array
        if (NotFoundList.Count > 0) then
            ResultProps.Add(BuildJSONArray(NotFoundList, 'not_found_layers'))
        else
            ResultProps.Add('"not_found_layers": []');
        
        // Build final JSON
        OutputLines := TStringList.Create;
        try
            OutputLines.Text := BuildJSONObject(ResultProps);
            Result := OutputLines.Text;
        finally
            OutputLines.Free;
        end;
    finally
        ResultProps.Free;
        NotFoundList.Free;
        FoundLayers.Free;
    end;
end;

// Function to get all PCB rules
function GetPCBRules(ROOT_DIR: String): String;
Var
    Board         : IPCB_Board;
    Rule          : IPCB_Rule;
    BoardIterator : IPCB_BoardIterator;
    RulesArray    : TStringList;
    RuleProps     : TStringList;
    OutputLines   : TStringList;
begin
    // Retrieve the current board
    Board := GetBoardSafe(0);
    if (Board = Nil) then
    begin
        Result := '[]';
        Exit;
    end;

    // Create array for rules
    RulesArray := TStringList.Create;
    
    try
        // Retrieve the iterator
        BoardIterator := Board.BoardIterator_Create;
        BoardIterator.AddFilter_ObjectSet(MkSet(eRuleObject));
        BoardIterator.AddFilter_LayerSet(AllLayers);
        BoardIterator.AddFilter_Method(eProcessAll);

        // Process each rule
        Rule := BoardIterator.FirstPCBObject;
        while (Rule <> Nil) do
        begin
            // Create rule properties
            RuleProps := TStringList.Create;
            try
                // Add rule descriptor
                AddJSONProperty(RuleProps, 'descriptor', Rule.Descriptor);
                AddJSONProperty(RuleProps, 'rule_kind', Rule.GetState_ShortDescriptorString);
                AddJSONProperty(RuleProps, 'filter1', Rule.Scope1Expression);
                AddJSONProperty(RuleProps, 'filter2', Rule.Scope2Expression);

                // Add to rules array
                RulesArray.Add(BuildJSONObject(RuleProps, 1));
            finally
                RuleProps.Free;
            end;
            
            // Move to next rule
            Rule := BoardIterator.NextPCBObject;
        end;

        // Clean up the iterator
        Board.BoardIterator_Destroy(BoardIterator);
        
        // Build the final JSON array
        OutputLines := TStringList.Create;
        try
            OutputLines.Text := BuildJSONArray(RulesArray);
            Result := WriteJSONToFile(OutputLines, ROOT_DIR+'\temp_rules_data.json');
        finally
            OutputLines.Free;
        end;
    finally
        RulesArray.Free;
    end;
end;

// Function to get all component data from the PCB
function GetAllComponentData(ROOT_DIR: String, SelectedOnly: Boolean = False): String;
var
    Board       : IPCB_Board;
    Iterator    : IPCB_BoardIterator;
    Component   : IPCB_Component;
    ComponentsArray : TStringList;
    ComponentProps : TStringList;
    Rect        : TCoordRect;
    xorigin, yorigin : Integer;
    i           : Integer;
    ComponentCount : Integer;
    OutputLines : TStringList;
begin
    // Retrieve the current board
    Board := GetBoardSafe(0);
    if (Board = nil) then
    begin
        Result := '[]';
        Exit;
    end;
    
    // Get board origin coordinates
    xorigin := Board.XOrigin;
    yorigin := Board.YOrigin;

    // Create array for components
    ComponentsArray := TStringList.Create;
    
    try
        // Create an iterator to find all components
        Iterator := Board.BoardIterator_Create;
        Iterator.AddFilter_ObjectSet(MkSet(eComponentObject));
        Iterator.AddFilter_IPCB_LayerSet(LayerSet.AllLayers);
        Iterator.AddFilter_Method(eProcessAll);

        // Process each component
        Component := Iterator.FirstPCBObject;
        while (Component <> Nil) do
        begin
            // Process either all components or only selected ones
            if ((not SelectedOnly) or (SelectedOnly and Component.Selected)) then
            begin
                // Create component properties
                ComponentProps := TStringList.Create;
                try
                    // Get bounds
                    Rect := Component.BoundingRectangleNoNameComment;
                    
                    // Add properties
                    AddJSONProperty(ComponentProps, 'designator', Component.Name.Text);
                    AddJSONProperty(ComponentProps, 'name', Component.Identifier);
                    AddJSONProperty(ComponentProps, 'description', Component.SourceDescription);
                    AddJSONProperty(ComponentProps, 'footprint', Component.Pattern);
                    AddJSONProperty(ComponentProps, 'layer', Layer2String(Component.Layer));
                    AddJSONNumber(ComponentProps, 'x', CoordToMils(Component.x - xorigin));
                    AddJSONNumber(ComponentProps, 'y', CoordToMils(Component.y - yorigin));
                    AddJSONNumber(ComponentProps, 'width', CoordToMils(Rect.Right - Rect.Left));
                    AddJSONNumber(ComponentProps, 'height', CoordToMils(Rect.Top - Rect.Bottom));
                    AddJSONNumber(ComponentProps, 'rotation', Component.Rotation);

                    // Add to components array
                    ComponentsArray.Add(BuildJSONObject(ComponentProps, 1));
                finally
                    ComponentProps.Free;
                end;
            end;
            
            // Move to next component
            Component := Iterator.NextPCBObject;
        end;

        // Clean up the iterator
        Board.BoardIterator_Destroy(Iterator);
        
        // Build the final JSON array
        OutputLines := TStringList.Create;
        try
            OutputLines.Text := BuildJSONArray(ComponentsArray);
            Result := WriteJSONToFile(OutputLines, ROOT_DIR+'\temp_component_data.json');
        finally
            OutputLines.Free;
        end;
    finally
        ComponentsArray.Free;
    end;
end;

// Example refactored function using the new JSON utilities
function GetSelectedComponentsCoordinates(ROOT_DIR: String): String;
var
    Board       : IPCB_Board;
    Component   : IPCB_Component;
    Rect        : TCoordRect;
    xorigin, yorigin : Integer;
    ComponentsArray : TStringList;
    ComponentProps : TStringList;
    OutputLines : TStringList;
    i : Integer;
begin
    Result := '';

    // Retrieve the current board
    Board := GetBoardSafe(0);
    if Board = nil then Exit;

    // Get board origin coordinates
    xorigin := Board.XOrigin;
    yorigin := Board.YOrigin;

    // Create output and components array
    OutputLines := TStringList.Create;
    ComponentsArray := TStringList.Create;
    
    try
        // Process each selected component
        for i := 0 to Board.SelectecObjectCount - 1 do
        begin
            // Only process selected components
            if Board.SelectecObject[i].ObjectId = eComponentObject then
            begin
                // Cast to component type
                Component := Board.SelectecObject[i];
                
                // Get component bounds
                Rect := Component.BoundingRectangleNoNameComment;
                
                // Create component properties
                ComponentProps := TStringList.Create;
                try
                    // Add component properties
                    AddJSONProperty(ComponentProps, 'designator', Component.Name.Text);
                    AddJSONProperty(ComponentProps, 'layer', Layer2String(Component.Layer));
                    AddJSONProperty(ComponentProps, 'footprint', Component.Pattern);
                    AddJSONNumber(ComponentProps, 'x', CoordToMils(Component.x - xorigin));
                    AddJSONNumber(ComponentProps, 'y', CoordToMils(Component.y - yorigin));
                    AddJSONNumber(ComponentProps, 'width', CoordToMils(Rect.Right - Rect.Left));
                    AddJSONNumber(ComponentProps, 'height', CoordToMils(Rect.Top - Rect.Bottom));
                    AddJSONNumber(ComponentProps, 'rotation', Component.Rotation);
                    
                    // Add component JSON to array
                    ComponentsArray.Add(BuildJSONObject(ComponentProps, 1));
                finally
                    ComponentProps.Free;
                end;
            end;
        end;
        
        // If components found, build array
        if ComponentsArray.Count > 0 then
            Result := BuildJSONArray(ComponentsArray)
        else
            Result := '[]';
            
        // For consistency with existing code, write to file and read back
        OutputLines.Text := Result;
        Result := WriteJSONToFile(OutputLines, ROOT_DIR+'\temp_selected_components.json');
    finally
        ComponentsArray.Free;
        OutputLines.Free;
    end;
end;

// Function to get pin data for specified components
function GetComponentPinsFromList(ROOT_DIR: String; DesignatorsList: TStringList): String;
var
    Board           : IPCB_Board;
    Component       : IPCB_Component;
    ComponentsArray : TStringList;
    CompProps       : TStringList;
    PinsArray       : TStringList;
    GrpIter         : IPCB_GroupIterator;
    Pad             : IPCB_Pad;
    NetName         : String;
    xorigin, yorigin : Integer;
    PinProps        : TStringList;
    PinCount, PinsProcessed : Integer;
    Designator      : String;
    i               : Integer;
    OutputLines     : TStringList;
    CompX, CompY    : Double;
    CompRad         : Double;
    AbsDX, AbsDY    : Double;
    RelDX, RelDY    : Double;
begin
    // Retrieve the current board
    Board := GetBoardSafe(0);
    if (Board = nil) then
    begin
        Result := '[]';
        Exit;
    end;
    
    // Get board origin coordinates
    xorigin := Board.XOrigin;
    yorigin := Board.YOrigin;

    // Create array for components
    ComponentsArray := TStringList.Create;
    
    try
        // Process each designator
        for i := 0 to DesignatorsList.Count - 1 do
        begin
            Designator := Trim(DesignatorsList[i]);
            
            // Use direct function to get component by designator
            Component := Board.GetPcbComponentByRefDes(Designator);
            
            if (Component <> Nil) then
            begin
                // Create component properties
                CompProps := TStringList.Create;
                PinsArray := TStringList.Create;
                
                try
                    // Add designator to component
                    AddJSONProperty(CompProps, 'designator', Component.Name.Text);

                    // Component placement info so pin data is self-contained
                    CompX := CoordToMils(Component.x - xorigin);
                    CompY := CoordToMils(Component.y - yorigin);
                    CompRad := Component.Rotation * Pi / 180;
                    AddJSONNumber(CompProps, 'x', CompX);
                    AddJSONNumber(CompProps, 'y', CompY);
                    AddJSONNumber(CompProps, 'rotation', Component.Rotation);
                    AddJSONProperty(CompProps, 'layer', Layer2String(Component.Layer));

                    // Create pad iterator
                    GrpIter := Component.GroupIterator_Create;
                    GrpIter.SetState_FilterAll;
                    GrpIter.AddFilter_ObjectSet(MkSet(ePadObject));
                    
                    // Count pins
                    PinCount := 0;
                    Pad := GrpIter.FirstPCBObject;
                    while (Pad <> Nil) do
                    begin
                        if Pad.InComponent then
                            PinCount := PinCount + 1;
                        Pad := GrpIter.NextPCBObject;
                    end;
                    
                    // Reset iterator
                    Component.GroupIterator_Destroy(GrpIter);
                    GrpIter := Component.GroupIterator_Create;
                    GrpIter.SetState_FilterAll;
                    GrpIter.AddFilter_ObjectSet(MkSet(ePadObject));
                    
                    // Process each pad
                    PinsProcessed := 0;
                    Pad := GrpIter.FirstPCBObject;
                    while (Pad <> Nil) do
                    begin
                        if Pad.InComponent then
                        begin
                            // Get net name if connected
                            if (Pad.Net <> Nil) then
                                NetName := Pad.Net.Name
                            else
                                NetName := '';
                                
                            // Create pin properties
                            PinProps := TStringList.Create;
                            try
                                AddJSONProperty(PinProps, 'name', Pad.Name);
                                AddJSONProperty(PinProps, 'net', NetName);
                                AddJSONNumber(PinProps, 'x', CoordToMils(Pad.x - xorigin));
                                AddJSONNumber(PinProps, 'y', CoordToMils(Pad.y - yorigin));

                                // Pad offset from the component origin in the
                                // footprint's rotation-0 frame: un-rotate the
                                // current offset, and un-mirror X for parts on
                                // the bottom side. Predicted pad position after
                                // placement = origin + (mirror-x if bottom,
                                // then rotate CCW by rotation) applied to dx/dy.
                                AbsDX := CoordToMils(Pad.x - xorigin) - CompX;
                                AbsDY := CoordToMils(Pad.y - yorigin) - CompY;
                                RelDX := AbsDX * Cos(CompRad) + AbsDY * Sin(CompRad);
                                RelDY := -AbsDX * Sin(CompRad) + AbsDY * Cos(CompRad);
                                if (Component.Layer = eBottomLayer) then
                                begin
                                    RelDX := -(AbsDX * Cos(CompRad) - AbsDY * Sin(CompRad));
                                    RelDY := AbsDX * Sin(CompRad) + AbsDY * Cos(CompRad);
                                end;
                                // Round away trig noise (0.0001 mil resolution)
                                RelDX := Round(RelDX * 10000) / 10000;
                                RelDY := Round(RelDY * 10000) / 10000;
                                AddJSONNumber(PinProps, 'dx', RelDX);
                                AddJSONNumber(PinProps, 'dy', RelDY);

                                AddJSONNumber(PinProps, 'rotation', Pad.Rotation);
                                AddJSONProperty(PinProps, 'layer', Layer2String(Pad.Layer));
                                AddJSONNumber(PinProps, 'width', CoordToMils(Pad.XSizeOnLayer[Pad.Layer]));
                                AddJSONNumber(PinProps, 'height', CoordToMils(Pad.YSizeOnLayer[Pad.Layer]));
                                AddJSONProperty(PinProps, 'shape', ShapeToString(Pad.ShapeOnLayer[Pad.Layer]));
                                
                                // Add to pins array
                                PinsArray.Add(BuildJSONObject(PinProps, 3));
                                
                                // Increment counter
                                PinsProcessed := PinsProcessed + 1;
                            finally
                                PinProps.Free;
                            end;
                        end;
                        
                        Pad := GrpIter.NextPCBObject;
                    end;
                    
                    // Clean up iterator
                    Component.GroupIterator_Destroy(GrpIter);
                    
                    // Add pins array to component
                    CompProps.Add(BuildJSONArray(PinsArray, 'pins', 1));
                    
                    // Add to components array
                    ComponentsArray.Add(BuildJSONObject(CompProps, 1));
                finally
                    CompProps.Free;
                    PinsArray.Free;
                end;
            end
            else
            begin
                // Component not found, add empty component
                CompProps := TStringList.Create;
                try
                    AddJSONProperty(CompProps, 'designator', Designator);
                    CompProps.Add('"pins": []');
                    
                    // Add to components array
                    ComponentsArray.Add(BuildJSONObject(CompProps, 1));
                finally
                    CompProps.Free;
                end;
            end;
        end;
        
        // Build the final JSON array
        OutputLines := TStringList.Create;
        try
            OutputLines.Text := BuildJSONArray(ComponentsArray);
            Result := WriteJSONToFile(OutputLines, ROOT_DIR+'\temp_pins_data.json');
        finally
            OutputLines.Free;
        end;
    finally
        ComponentsArray.Free;
    end;
end;

// Set absolute position of a single component
function SetComponentPosition(Designator: String; NewX, NewY: Float; Rotation: Float): String;
var
    Board: IPCB_Board;
    Component: IPCB_Component;
    ResultProps: TStringList;
    xorigin, yorigin: TCoord;
begin
    Board := GetBoardSafe(0);
    if (Board = nil) then
    begin
        Result := '{"success": false, "error": "No PCB document is currently active"}';
        Exit;
    end;
    
    Component := Board.GetPcbComponentByRefDes(Designator);
    if (Component = nil) then
    begin
        Result := '{"success": false, "error": "Component not found: ' + Designator + '"}';
        Exit;
    end;
    
    // Get board origin
    xorigin := Board.XOrigin;
    yorigin := Board.YOrigin;
    
    ResultProps := TStringList.Create;
    try
        PCBServer.PreProcess;
        PCBServer.SendMessageToRobots(Component.I_ObjectAddress, c_Broadcast, PCBM_BeginModify, c_NoEventData);
        
        // Set absolute position using MoveToXY
        // Add origin back since input coordinates are relative to origin
        Component.MoveToXY(MilsToCoord(NewX) + xorigin, MilsToCoord(NewY) + yorigin);
        
        // Set rotation if specified (use -1 to keep current)
        if (Rotation >= 0) then
            Component.Rotation := Rotation;
        
        PCBServer.SendMessageToRobots(Component.I_ObjectAddress, c_Broadcast, PCBM_EndModify, c_NoEventData);
        PCBServer.PostProcess;
        
        Client.SendMessage('PCB:Zoom', 'Action=Redraw', 255, Client.CurrentView);
        
        AddJSONProperty(ResultProps, 'designator', Designator);
        AddJSONProperty(ResultProps, 'new_x', FloatToStr(NewX), False);
        AddJSONProperty(ResultProps, 'new_y', FloatToStr(NewY), False);
        AddJSONProperty(ResultProps, 'rotation', FloatToStr(Component.Rotation), False);
        
        Result := '{"success": true, "result": ' + BuildJSONObject(ResultProps) + '}';
    finally
        ResultProps.Free;
    end;
end;

// Create a PCB footprint (SMD pads + silkscreen + courtyard) in the active PcbLib
function CreatePCBFootprint(FootprintName: String; Description: String; PadsList: TStringList; CourtyardXMM: Double; CourtyardYMM: Double): String;
var
    PcbLib      : IPCB_Library;
    LibComp     : IPCB_Component;
    Pad         : IPCB_Pad;
    Track       : IPCB_Track;
    ResultProps : TStringList;
    OutputLines : TStringList;
    i, j        : Integer;
    PadData     : String;
    PadNum      : String;
    XMM, YMM    : Double;
    WMM, HMM    : Double;
    ShapeStr    : String;
    PadShape    : TShape;
    PadCount    : Integer;
    MaxX, MaxY  : Double;
    MinX, MinY  : Double;
    CrtX1, CrtY1, CrtX2, CrtY2 : Double;
    TrackWidth  : TCoord;
    FieldStart  : Integer;
    Fields      : TStringList;
    SilkLayer   : TLayer;
begin
    PcbLib := GetPcbLibSafe(0);
    if PcbLib = nil then
    begin
        Result := '{"success": false, "error": "No PCB library document is currently active. Open a .PcbLib file first."}';
        Exit;
    end;

    ResultProps := TStringList.Create;
    Fields := TStringList.Create;
    PadCount := 0;
    MaxX := -1e9; MaxY := -1e9;
    MinX :=  1e9; MinY :=  1e9;
    SilkLayer := String2Layer('Top Overlay');

    try
        LibComp := PCBServer.CreatePCBLibComp;
        LibComp.Name := FootprintName;

        PcbLib.RegisterComponent(LibComp);

        for i := 0 to PadsList.Count - 1 do
        begin
            PadData := Trim(PadsList[i]);
            if (PadData = '') then continue;

            // Parse pipe-delimited fields manually
            Fields.Clear;
            FieldStart := 1;
            for j := 1 to Length(PadData) + 1 do
            begin
                if (j > Length(PadData)) or (PadData[j] = '|') then
                begin
                    Fields.Add(Trim(Copy(PadData, FieldStart, j - FieldStart)));
                    FieldStart := j + 1;
                end;
            end;

            if Fields.Count < 5 then continue;

            PadNum := Fields[0];
            XMM := SafeStrToFloat(Fields[1]);
            YMM := SafeStrToFloat(Fields[2]);
            WMM := SafeStrToFloat(Fields[3]);
            HMM := SafeStrToFloat(Fields[4]);

            if Fields.Count >= 6 then
                ShapeStr := Fields[5]
            else
                ShapeStr := 'Rect';

            if ShapeStr = 'Round' then
                PadShape := eRounded
            else if ShapeStr = 'Oval' then
                PadShape := eRoundedRectangle
            else
                PadShape := eRectangular;

            Pad := PCBServer.PCBObjectFactory(ePadObject, eNoDimension, eCreate_Default);
            Pad.Name := PadNum;
            Pad.Mode := ePadMode_Simple;
            Pad.HoleSize := 0;
            Pad.x := MMsToCoord(XMM);
            Pad.y := MMsToCoord(YMM);
            Pad.Layer := eTopLayer;
            Pad.TopXSize := MMsToCoord(WMM);
            Pad.TopYSize := MMsToCoord(HMM);
            Pad.TopShape := PadShape;

            LibComp.AddPCBObject(Pad);
            PCBServer.SendMessageToRobots(Pad.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, c_NoEventData);

            if (XMM - WMM/2) < MinX then MinX := XMM - WMM/2;
            if (YMM - HMM/2) < MinY then MinY := YMM - HMM/2;
            if (XMM + WMM/2) > MaxX then MaxX := XMM + WMM/2;
            if (YMM + HMM/2) > MaxY then MaxY := YMM + HMM/2;

            PadCount := PadCount + 1;
        end;

        // Compute courtyard extents
        if (CourtyardXMM > 0) and (CourtyardYMM > 0) then
        begin
            CrtX1 := -CourtyardXMM; CrtX2 :=  CourtyardXMM;
            CrtY1 := -CourtyardYMM; CrtY2 :=  CourtyardYMM;
        end
        else
        begin
            CrtX1 := MinX - 0.25; CrtX2 := MaxX + 0.25;
            CrtY1 := MinY - 0.25; CrtY2 := MaxY + 0.25;
        end;

        TrackWidth := MMsToCoord(0.1);

        // Courtyard on Mechanical 15
        Track := PCBServer.PCBObjectFactory(eTrackObject, eNoDimension, eCreate_Default);
        Track.Layer := ILayer.MechanicalLayer(15);
        Track.x1 := MMsToCoord(CrtX1); Track.y1 := MMsToCoord(CrtY1);
        Track.x2 := MMsToCoord(CrtX2); Track.y2 := MMsToCoord(CrtY1);
        Track.Width := TrackWidth;
        LibComp.AddPCBObject(Track);
        PCBServer.SendMessageToRobots(Track.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, c_NoEventData);

        Track := PCBServer.PCBObjectFactory(eTrackObject, eNoDimension, eCreate_Default);
        Track.Layer := ILayer.MechanicalLayer(15);
        Track.x1 := MMsToCoord(CrtX1); Track.y1 := MMsToCoord(CrtY2);
        Track.x2 := MMsToCoord(CrtX2); Track.y2 := MMsToCoord(CrtY2);
        Track.Width := TrackWidth;
        LibComp.AddPCBObject(Track);
        PCBServer.SendMessageToRobots(Track.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, c_NoEventData);

        Track := PCBServer.PCBObjectFactory(eTrackObject, eNoDimension, eCreate_Default);
        Track.Layer := ILayer.MechanicalLayer(15);
        Track.x1 := MMsToCoord(CrtX1); Track.y1 := MMsToCoord(CrtY1);
        Track.x2 := MMsToCoord(CrtX1); Track.y2 := MMsToCoord(CrtY2);
        Track.Width := TrackWidth;
        LibComp.AddPCBObject(Track);
        PCBServer.SendMessageToRobots(Track.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, c_NoEventData);

        Track := PCBServer.PCBObjectFactory(eTrackObject, eNoDimension, eCreate_Default);
        Track.Layer := ILayer.MechanicalLayer(15);
        Track.x1 := MMsToCoord(CrtX2); Track.y1 := MMsToCoord(CrtY1);
        Track.x2 := MMsToCoord(CrtX2); Track.y2 := MMsToCoord(CrtY2);
        Track.Width := TrackWidth;
        LibComp.AddPCBObject(Track);
        PCBServer.SendMessageToRobots(Track.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, c_NoEventData);

        // Silkscreen on TopOverlay (inset 0.1mm from courtyard)
        Track := PCBServer.PCBObjectFactory(eTrackObject, eNoDimension, eCreate_Default);
        Track.Layer := SilkLayer;
        Track.x1 := MMsToCoord(CrtX1+0.1); Track.y1 := MMsToCoord(CrtY1+0.1);
        Track.x2 := MMsToCoord(CrtX2-0.1); Track.y2 := MMsToCoord(CrtY1+0.1);
        Track.Width := TrackWidth;
        LibComp.AddPCBObject(Track);
        PCBServer.SendMessageToRobots(Track.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, c_NoEventData);

        Track := PCBServer.PCBObjectFactory(eTrackObject, eNoDimension, eCreate_Default);
        Track.Layer := SilkLayer;
        Track.x1 := MMsToCoord(CrtX1+0.1); Track.y1 := MMsToCoord(CrtY2-0.1);
        Track.x2 := MMsToCoord(CrtX2-0.1); Track.y2 := MMsToCoord(CrtY2-0.1);
        Track.Width := TrackWidth;
        LibComp.AddPCBObject(Track);
        PCBServer.SendMessageToRobots(Track.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, c_NoEventData);

        // Left silk — split to mark pin 1 (gap at top-left corner for pin 1 indicator)
        Track := PCBServer.PCBObjectFactory(eTrackObject, eNoDimension, eCreate_Default);
        Track.Layer := SilkLayer;
        Track.x1 := MMsToCoord(CrtX1+0.1); Track.y1 := MMsToCoord(CrtY1+0.1);
        Track.x2 := MMsToCoord(CrtX1+0.1); Track.y2 := MMsToCoord(CrtY2-0.6);
        Track.Width := TrackWidth;
        LibComp.AddPCBObject(Track);
        PCBServer.SendMessageToRobots(Track.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, c_NoEventData);

        // Right silk
        Track := PCBServer.PCBObjectFactory(eTrackObject, eNoDimension, eCreate_Default);
        Track.Layer := SilkLayer;
        Track.x1 := MMsToCoord(CrtX2-0.1); Track.y1 := MMsToCoord(CrtY1+0.1);
        Track.x2 := MMsToCoord(CrtX2-0.1); Track.y2 := MMsToCoord(CrtY2-0.1);
        Track.Width := TrackWidth;
        LibComp.AddPCBObject(Track);
        PCBServer.SendMessageToRobots(Track.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, c_NoEventData);

        // Register with library board, navigate, and refresh
        PCBServer.SendMessageToRobots(PcbLib.Board.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, LibComp.I_ObjectAddress);
        PcbLib.CurrentComponent := LibComp;
        PcbLib.Board.ViewManager_FullUpdate;

        AddJSONBoolean(ResultProps, 'success', True);
        AddJSONProperty(ResultProps, 'footprint_name', FootprintName);
        AddJSONInteger(ResultProps, 'pad_count', PadCount);
        AddJSONNumber(ResultProps, 'courtyard_width_mm', CrtX2 - CrtX1);
        AddJSONNumber(ResultProps, 'courtyard_height_mm', CrtY2 - CrtY1);

        OutputLines := TStringList.Create;
        try
            OutputLines.Text := BuildJSONObject(ResultProps);
            Result := OutputLines.Text;
        finally
            OutputLines.Free;
        end;
    finally
        ResultProps.Free;
        Fields.Free;
    end;
end;

// Function to move components by X and Y offsets and set rotation
function MoveComponentsByDesignators(DesignatorsList: TStringList; XOffset, YOffset: TCoord; Rotation: TAngle): String;
var
    Board          : IPCB_Board;
    Component      : IPCB_Component;
    ResultProps    : TStringList;
    MissingArray   : TStringList;
    Designator     : String;
    i              : Integer;
    MovedCount     : Integer;
    OutputLines    : TStringList;
begin
    // Retrieve the current board
    Board := GetBoardSafe(0);
    if (Board = nil) then
    begin
        Result := 'ERROR: No PCB document is currently active';
        Exit;
    end;
    
    // Create output properties
    ResultProps := TStringList.Create;
    MissingArray := TStringList.Create;
    MovedCount := 0;
    
    try
        // Start transaction
        PCBServer.PreProcess;
        
        // Process each designator
        for i := 0 to DesignatorsList.Count - 1 do
        begin
            Designator := Trim(DesignatorsList[i]);
            
            // Use direct function to get component by designator
            Component := Board.GetPcbComponentByRefDes(Designator);
            
            if (Component <> Nil) then
            begin
                // Begin modify
                PCBServer.SendMessageToRobots(Component.I_ObjectAddress, c_Broadcast, PCBM_BeginModify, c_NoEventData);
                
                // Move the component by the specified offsets
                Component.MoveByXY(XOffset, YOffset);
                
                // Set rotation if specified (non-zero)
                if (Rotation <> 0) then
                    Component.Rotation := Rotation;
                
                // End modify
                PCBServer.SendMessageToRobots(Component.I_ObjectAddress, c_Broadcast, PCBM_EndModify, c_NoEventData);
                
                MovedCount := MovedCount + 1;
            end
            else
            begin
                // Add to missing designators list
                MissingArray.Add('"' + JSONEscapeString(Designator) + '"');
            end;
        end;
        
        // End transaction
        PCBServer.PostProcess;
        
        // Update PCB document
        Client.SendMessage('PCB:Zoom', 'Action=Redraw', 255, Client.CurrentView);
        
        // Create result JSON
        AddJSONInteger(ResultProps, 'moved_count', MovedCount);
        
        // Add missing designators array
        if (MissingArray.Count > 0) then
            ResultProps.Add(BuildJSONArray(MissingArray, 'missing_designators'))
        else
            ResultProps.Add('"missing_designators": []');
        
        // Build final JSON
        OutputLines := TStringList.Create;
        try
            OutputLines.Text := BuildJSONObject(ResultProps);
            Result := OutputLines.Text;
        finally
            OutputLines.Free;
        end;
    finally
        ResultProps.Free;
        MissingArray.Free;
    end;
end;

// Minimum primitive-to-primitive distance between two components in mils,
// ignoring text primitives (designator/comment strings float over neighbors
// and would poison the measurement). 0 = touching or overlapping.
function ComponentMinDistance(Board: IPCB_Board; CompA, CompB: IPCB_Component): Double;
var
    ItA, ItB : IPCB_GroupIterator;
    PA, PB   : IPCB_Primitive;
    D, MinD  : Integer;
begin
    MinD := 2147483647;

    ItA := CompA.GroupIterator_Create;
    ItA.SetState_FilterAll;
    PA := ItA.FirstPCBObject;
    while (PA <> nil) do
    begin
        if (PA.ObjectId <> eTextObject) then
        begin
            ItB := CompB.GroupIterator_Create;
            ItB.SetState_FilterAll;
            PB := ItB.FirstPCBObject;
            while (PB <> nil) do
            begin
                if (PB.ObjectId <> eTextObject) then
                begin
                    D := Board.PrimPrimDistance(PA, PB);
                    if (D < MinD) then MinD := D;
                end;

                // Early exit once touching - it cannot get closer
                if (MinD = 0) then
                    PB := nil
                else
                    PB := ItB.NextPCBObject;
            end;
            CompB.GroupIterator_Destroy(ItB);
        end;

        if (MinD = 0) then
            PA := nil
        else
            PA := ItA.NextPCBObject;
    end;
    CompA.GroupIterator_Destroy(ItA);

    Result := CoordToMils(MinD);
end;

// Check component placement for overlaps and clearance violations.
// Targets are the given designators (or the current selection when the list
// is empty); each target is checked against every other component on the same
// side of the board. Bounding boxes act as a fast prefilter; close pairs are
// measured precisely with Board.PrimPrimDistance (minimum distance between
// any two primitives of the components, 0 = touching/overlapping).
function CheckPlacement(DesignatorsList: TStringList; ClearanceMils: Double): String;
var
    Board           : IPCB_Board;
    Iterator        : IPCB_BoardIterator;
    Target, Other   : IPCB_Component;
    Targets         : TStringList;
    Processed       : TStringList;
    MissingArray    : TStringList;
    ViolationsArray : TStringList;
    VProps          : TStringList;
    ResultProps     : TStringList;
    RectA, RectB    : TCoordRect;
    Designator      : String;
    i               : Integer;
    OverlapX, OverlapY : Double;
    Separation      : Double;
    DistMils        : Double;
    PairsChecked    : Integer;
    IsOverlap       : Boolean;
begin
    Board := GetBoardSafe(0);
    if (Board = nil) then
    begin
        Result := 'ERROR: No PCB document is currently active';
        Exit;
    end;

    if (ClearanceMils <= 0) then
        ClearanceMils := 6;

    Targets := TStringList.Create;
    Processed := TStringList.Create;
    MissingArray := TStringList.Create;
    ViolationsArray := TStringList.Create;
    ResultProps := TStringList.Create;
    PairsChecked := 0;

    try
        // Build the target list: explicit designators, or current selection
        if (DesignatorsList.Count > 0) then
        begin
            for i := 0 to DesignatorsList.Count - 1 do
            begin
                Designator := Trim(DesignatorsList[i]);
                if (Board.GetPcbComponentByRefDes(Designator) <> nil) then
                    Targets.Add(Designator)
                else
                    MissingArray.Add('"' + JSONEscapeString(Designator) + '"');
            end;
        end
        else
        begin
            for i := 0 to Board.SelectecObjectCount - 1 do
                if (Board.SelectecObject[i].ObjectId = eComponentObject) then
                    Targets.Add(Board.SelectecObject[i].Name.Text);
        end;

        if (Targets.Count = 0) then
        begin
            Result := 'ERROR: No components to check (no designators given and no components selected)';
            Exit;
        end;

        // Check each target against every other component on the same side
        for i := 0 to Targets.Count - 1 do
        begin
            Target := Board.GetPcbComponentByRefDes(Targets[i]);
            RectA := Target.BoundingRectangleNoNameComment;

            Iterator := Board.BoardIterator_Create;
            Iterator.AddFilter_ObjectSet(MkSet(eComponentObject));
            Iterator.AddFilter_IPCB_LayerSet(LayerSet.AllLayers);
            Iterator.AddFilter_Method(eProcessAll);

            Other := Iterator.FirstPCBObject;
            while (Other <> nil) do
            begin
                if (Other.Name.Text <> Target.Name.Text) and
                   (Other.Layer = Target.Layer) and
                   (Processed.IndexOf(Other.Name.Text) < 0) then
                begin
                    RectB := Other.BoundingRectangleNoNameComment;

                    // Bounding-box overlap/separation in mils (negative = gap)
                    OverlapX := CoordToMils(Min(RectA.Right, RectB.Right) - Max(RectA.Left, RectB.Left));
                    OverlapY := CoordToMils(Min(RectA.Top, RectB.Top) - Max(RectA.Bottom, RectB.Bottom));

                    if (OverlapX > 0) and (OverlapY > 0) then
                        Separation := 0
                    else
                        Separation := Max(-OverlapX, -OverlapY);

                    // Only measure precisely when the prefilter says "close"
                    if (Separation < ClearanceMils + 25) then
                    begin
                        PairsChecked := PairsChecked + 1;
                        DistMils := ComponentMinDistance(Board, Target, Other);
                        IsOverlap := (OverlapX > 0) and (OverlapY > 0);

                        if (IsOverlap) or (DistMils < ClearanceMils) then
                        begin
                            VProps := TStringList.Create;
                            try
                                AddJSONProperty(VProps, 'a', Target.Name.Text);
                                AddJSONProperty(VProps, 'b', Other.Name.Text);
                                AddJSONProperty(VProps, 'layer', Layer2String(Target.Layer));
                                if IsOverlap then
                                    AddJSONProperty(VProps, 'type', 'bounding_box_overlap')
                                else
                                    AddJSONProperty(VProps, 'type', 'clearance');
                                AddJSONNumber(VProps, 'distance_mils', Round(DistMils * 100) / 100);
                                if IsOverlap then
                                begin
                                    AddJSONNumber(VProps, 'overlap_x_mils', Round(OverlapX * 100) / 100);
                                    AddJSONNumber(VProps, 'overlap_y_mils', Round(OverlapY * 100) / 100);
                                end;
                                AddJSONNumber(VProps, 'b_x', CoordToMils(Other.x - Board.XOrigin));
                                AddJSONNumber(VProps, 'b_y', CoordToMils(Other.y - Board.YOrigin));
                                ViolationsArray.Add(BuildJSONObject(VProps, 2));
                            finally
                                VProps.Free;
                            end;
                        end;
                    end;
                end;

                Other := Iterator.NextPCBObject;
            end;

            Board.BoardIterator_Destroy(Iterator);
            Processed.Add(Target.Name.Text);
        end;

        AddJSONInteger(ResultProps, 'checked_count', Targets.Count);
        AddJSONNumber(ResultProps, 'clearance_mils', ClearanceMils);
        AddJSONInteger(ResultProps, 'close_pairs_measured', PairsChecked);
        AddJSONInteger(ResultProps, 'violation_count', ViolationsArray.Count);

        if (MissingArray.Count > 0) then
            ResultProps.Add(BuildJSONArray(MissingArray, 'missing_designators'))
        else
            ResultProps.Add('"missing_designators": []');

        if (ViolationsArray.Count > 0) then
            ResultProps.Add(BuildJSONArray(ViolationsArray, 'violations', 1))
        else
            ResultProps.Add('"violations": []');

        Result := BuildJSONObject(ResultProps);
    finally
        Targets.Free;
        Processed.Free;
        MissingArray.Free;
        ViolationsArray.Free;
        ResultProps.Free;
    end;
end;

// Get all pads on every net touched by the given components (or the current
// selection when the list is empty). Returns a flat board-wide pad list -
// including pads of components outside the target set - so the caller can
// group by net and compute airline lengths. Coordinates in mils relative to
// the board origin.
function GetNetConnections(ROOT_DIR: String; DesignatorsList: TStringList): String;
var
    Board       : IPCB_Board;
    Component   : IPCB_Component;
    Iterator    : IPCB_BoardIterator;
    GrpIter     : IPCB_GroupIterator;
    Pad         : IPCB_Pad;
    NetNames    : TStringList;
    TargetNames : TStringList;
    PadsArray   : TStringList;
    PadProps    : TStringList;
    ResultProps : TStringList;
    NamesArray  : TStringList;
    OutputLines : TStringList;
    Designator  : String;
    xorigin, yorigin : Integer;
    i           : Integer;
begin
    Board := GetBoardSafe(0);
    if (Board = nil) then
    begin
        Result := 'ERROR: No PCB document is currently active';
        Exit;
    end;

    xorigin := Board.XOrigin;
    yorigin := Board.YOrigin;

    NetNames := TStringList.Create;
    TargetNames := TStringList.Create;
    PadsArray := TStringList.Create;
    ResultProps := TStringList.Create;
    NamesArray := TStringList.Create;

    try
        // Build the target component list: explicit designators, or selection
        if (DesignatorsList.Count > 0) then
        begin
            for i := 0 to DesignatorsList.Count - 1 do
            begin
                Designator := Trim(DesignatorsList[i]);
                if (Board.GetPcbComponentByRefDes(Designator) <> nil) then
                    TargetNames.Add(Designator);
            end;
        end
        else
        begin
            for i := 0 to Board.SelectecObjectCount - 1 do
                if (Board.SelectecObject[i].ObjectId = eComponentObject) then
                    TargetNames.Add(Board.SelectecObject[i].Name.Text);
        end;

        if (TargetNames.Count = 0) then
        begin
            Result := 'ERROR: No components found (no designators given and no components selected)';
            Exit;
        end;

        // Collect the set of nets touched by the target components
        for i := 0 to TargetNames.Count - 1 do
        begin
            Component := Board.GetPcbComponentByRefDes(TargetNames[i]);
            GrpIter := Component.GroupIterator_Create;
            GrpIter.SetState_FilterAll;
            GrpIter.AddFilter_ObjectSet(MkSet(ePadObject));

            Pad := GrpIter.FirstPCBObject;
            while (Pad <> nil) do
            begin
                if (Pad.Net <> nil) then
                    if (NetNames.IndexOf(Pad.Net.Name) < 0) then
                        NetNames.Add(Pad.Net.Name);
                Pad := GrpIter.NextPCBObject;
            end;

            Component.GroupIterator_Destroy(GrpIter);
        end;

        // One board-wide pass: emit every pad on any of those nets
        Iterator := Board.BoardIterator_Create;
        Iterator.AddFilter_ObjectSet(MkSet(ePadObject));
        Iterator.AddFilter_IPCB_LayerSet(LayerSet.AllLayers);
        Iterator.AddFilter_Method(eProcessAll);

        Pad := Iterator.FirstPCBObject;
        while (Pad <> nil) do
        begin
            if (Pad.Net <> nil) then
            begin
                if (NetNames.IndexOf(Pad.Net.Name) >= 0) then
                begin
                    PadProps := TStringList.Create;
                    try
                        AddJSONProperty(PadProps, 'net', Pad.Net.Name);
                        if (Pad.Component <> nil) then
                            AddJSONProperty(PadProps, 'designator', Pad.Component.Name.Text)
                        else
                            AddJSONProperty(PadProps, 'designator', '');
                        AddJSONProperty(PadProps, 'pin', Pad.Name);
                        AddJSONNumber(PadProps, 'x', CoordToMils(Pad.x - xorigin));
                        AddJSONNumber(PadProps, 'y', CoordToMils(Pad.y - yorigin));
                        PadsArray.Add(BuildJSONObject(PadProps, 2));
                    finally
                        PadProps.Free;
                    end;
                end;
            end;
            Pad := Iterator.NextPCBObject;
        end;

        Board.BoardIterator_Destroy(Iterator);

        // Build the result - include the resolved targets so the caller
        // knows which components were analyzed when using the selection
        for i := 0 to TargetNames.Count - 1 do
            NamesArray.Add('"' + JSONEscapeString(TargetNames[i]) + '"');
        ResultProps.Add(BuildJSONArray(NamesArray, 'targets'));
        NamesArray.Clear;

        for i := 0 to NetNames.Count - 1 do
            NamesArray.Add('"' + JSONEscapeString(NetNames[i]) + '"');

        if (NamesArray.Count > 0) then
            ResultProps.Add(BuildJSONArray(NamesArray, 'net_names'))
        else
            ResultProps.Add('"net_names": []');

        if (PadsArray.Count > 0) then
            ResultProps.Add(BuildJSONArray(PadsArray, 'pads', 1))
        else
            ResultProps.Add('"pads": []');

        // Potentially large (plane nets) - go through the temp-file path
        OutputLines := TStringList.Create;
        try
            OutputLines.Text := BuildJSONObject(ResultProps);
            Result := WriteJSONToFile(OutputLines, ROOT_DIR+'\temp_net_connections.json');
        finally
            OutputLines.Free;
        end;
    finally
        NetNames.Free;
        TargetNames.Free;
        PadsArray.Free;
        ResultProps.Free;
        NamesArray.Free;
    end;
end;

// Place multiple components at absolute positions in a single transaction.
// Each entry in PlacementsList is 'Designator|X|Y|Rotation|Layer' where X/Y
// are mils relative to the board origin, Rotation is degrees CCW (-1 = keep
// current) and Layer is 'top', 'bottom' or '' (keep current side).
function PlaceComponentsFromList(PlacementsList: TStringList): String;
var
    Board            : IPCB_Board;
    Component        : IPCB_Component;
    ResultProps      : TStringList;
    MissingArray     : TStringList;
    PlacedArray      : TStringList;
    CompProps        : TStringList;
    Entry            : String;
    Designator       : String;
    FieldValue       : String;
    LayerStr         : String;
    NewX, NewY       : Double;
    Rotation         : Double;
    xorigin, yorigin : TCoord;
    i                : Integer;
    PlacedCount      : Integer;
begin
    Board := GetBoardSafe(0);
    if (Board = nil) then
    begin
        Result := 'ERROR: No PCB document is currently active';
        Exit;
    end;

    xorigin := Board.XOrigin;
    yorigin := Board.YOrigin;

    ResultProps := TStringList.Create;
    MissingArray := TStringList.Create;
    PlacedArray := TStringList.Create;
    PlacedCount := 0;

    try
        // Single transaction for the whole batch (one undo step)
        PCBServer.PreProcess;

        for i := 0 to PlacementsList.Count - 1 do
        begin
            Entry := Trim(PlacementsList[i]);
            if (Entry <> '') then
            begin
                Designator := Trim(GetFieldFromPipeString(Entry, 0));
                NewX := SafeStrToFloat(GetFieldFromPipeString(Entry, 1));
                NewY := SafeStrToFloat(GetFieldFromPipeString(Entry, 2));

                // Rotation is optional: missing/empty field means keep current
                FieldValue := Trim(GetFieldFromPipeString(Entry, 3));
                if (FieldValue <> '') then
                    Rotation := SafeStrToFloat(FieldValue)
                else
                    Rotation := -1;

                LayerStr := LowerCase(Trim(GetFieldFromPipeString(Entry, 4)));

                Component := Board.GetPcbComponentByRefDes(Designator);

                if (Component <> nil) then
                begin
                    PCBServer.SendMessageToRobots(Component.I_ObjectAddress, c_Broadcast, PCBM_BeginModify, c_NoEventData);

                    // Change layer first: flipping mirrors the footprint, so
                    // position and rotation are applied afterwards to keep the
                    // requested values authoritative
                    if (LayerStr = 'top') and (Component.Layer = eBottomLayer) then
                        Component.Layer := eTopLayer
                    else if (LayerStr = 'bottom') and (Component.Layer = eTopLayer) then
                        Component.Layer := eBottomLayer;

                    Component.MoveToXY(MilsToCoord(NewX) + xorigin, MilsToCoord(NewY) + yorigin);

                    if (Rotation >= 0) then
                        Component.Rotation := Rotation;

                    PCBServer.SendMessageToRobots(Component.I_ObjectAddress, c_Broadcast, PCBM_EndModify, c_NoEventData);

                    // Report the final state as Altium sees it
                    CompProps := TStringList.Create;
                    try
                        AddJSONProperty(CompProps, 'designator', Component.Name.Text);
                        AddJSONNumber(CompProps, 'x', CoordToMils(Component.x - xorigin));
                        AddJSONNumber(CompProps, 'y', CoordToMils(Component.y - yorigin));
                        AddJSONNumber(CompProps, 'rotation', Component.Rotation);
                        AddJSONProperty(CompProps, 'layer', Layer2String(Component.Layer));
                        PlacedArray.Add(BuildJSONObject(CompProps, 2));
                    finally
                        CompProps.Free;
                    end;

                    PlacedCount := PlacedCount + 1;
                end
                else
                begin
                    MissingArray.Add('"' + JSONEscapeString(Designator) + '"');
                end;
            end;
        end;

        PCBServer.PostProcess;

        // Update PCB document
        Client.SendMessage('PCB:Zoom', 'Action=Redraw', 255, Client.CurrentView);

        AddJSONInteger(ResultProps, 'placed_count', PlacedCount);

        if (MissingArray.Count > 0) then
            ResultProps.Add(BuildJSONArray(MissingArray, 'missing_designators'))
        else
            ResultProps.Add('"missing_designators": []');

        if (PlacedArray.Count > 0) then
            ResultProps.Add(BuildJSONArray(PlacedArray, 'components', 1))
        else
            ResultProps.Add('"components": []');

        Result := BuildJSONObject(ResultProps);
    finally
        ResultProps.Free;
        MissingArray.Free;
        PlacedArray.Free;
    end;
end;

// ---------------------------------------------------------------------------
// Silkscreen designator placement
//
// The placement itself is solved in Python (server/silkscreen.py) from a
// geometry dump. These routines export that geometry, apply the solved
// designator positions, and verify the result with Altium's own silk rules.
// ---------------------------------------------------------------------------

// Locale-safe mils string (3 decimals) for the silk dump
function SilkNum(Value: Double): String;
begin
    Result := StringReplace(FloatToStr(Round(Value * 1000) / 1000), ',', '.', REPLACEALL);
end;

function SilkLen(C: Integer): String;
begin
    Result := SilkNum(CoordToMils(C));
end;

// 'T' / 'B' for an overlay or solder mask layer, '' for any other layer
function SilkSideOfLayer(Layer: Integer): String;
begin
    Result := '';
    if (Layer = eTopOverlay) or (Layer = eTopSolder) then
        Result := 'T'
    else if (Layer = eBottomOverlay) or (Layer = eBottomSolder) then
        Result := 'B';
end;

function SilkRectStr(R: TCoordRect; XO, YO: Integer): String;
begin
    Result := SilkLen(R.Left - XO) + '|' + SilkLen(R.Bottom - YO) + '|' +
              SilkLen(R.Right - XO) + '|' + SilkLen(R.Top - YO);
end;

// True for designator/comment strings that are switched off on the component
function SilkTextHidden(Txt: IPCB_Text): Boolean;
begin
    Result := False;
    if Txt.IsDesignator then
    begin
        if (Txt.Component <> nil) then
            Result := not Txt.Component.NameOn;
    end
    else if Txt.IsComment then
    begin
        if (Txt.Component <> nil) then
            Result := not Txt.Component.CommentOn;
    end;
end;

// A pad's solder mask opening on MaskLayer as 'l|b|r|t|flag', or '' when it
// has none there. BoundingRectangleOnLayer can come back shifted from the pad
// for some shapes (seen on an offset SOT-89 tab, TopShape 10, 55 mil off), so
// the result is its union with the pad's own rectangle. flag 1 marks pads
// where the two disagree; the solver treats those as plain rectangles.
function SilkPadMaskStr(Pad: IPCB_Pad; MaskLayer: Integer; XO, YO: Integer): String;
var
    R, C        : TCoordRect;
    L, B, Rt, T : Integer;
    Flag        : String;
begin
    Result := '';
    R := Pad.BoundingRectangleOnLayer(MaskLayer);
    if (R.Right <= R.Left) then
        Exit;
    C := Pad.BoundingRectangle;
    L := R.Left;
    B := R.Bottom;
    Rt := R.Right;
    T := R.Top;
    Flag := '0';
    if (C.Left < L) then begin L := C.Left; Flag := '1'; end;
    if (C.Bottom < B) then begin B := C.Bottom; Flag := '1'; end;
    if (C.Right > Rt) then begin Rt := C.Right; Flag := '1'; end;
    if (C.Top > T) then begin T := C.Top; Flag := '1'; end;
    Result := SilkLen(L - XO) + '|' + SilkLen(B - YO) + '|' + SilkLen(Rt - XO) + '|' +
              SilkLen(T - YO) + '|' + Flag;
end;

// Export everything the silkscreen solver needs to ROOT_DIR\silk_data.txt,
// one pipe-delimited line per object, mils relative to the board origin:
//   B|file                                   the board's file path
//   RULE|S2S|clearance, RULE|S2M|clearance   largest enabled silk rules
//   RULE|EDGE|outline|cutout                 largest text-to-board-edge
//                                            clearances (Board Outline Clearance)
//   O|kind|vx|vy|cx|cy|radius|a1|a2          board outline (kind 0 line, 1 arc)
//   K|x1|y1|x2|y2|...                        board cutout contour
//   C|des|side|x|y|rot|nameon|l|b|r|t|tl|tb|tr|tt|trot|size|width|autopos|mirror|pattern|truetype
//                                            component (bbox without name/comment,
//                                            then the designator text box)
//   Y|owner|l|b|r|t                          3D body extent
//   ST|side|owner|x1|y1|x2|y2|w              silk track
//   SA|side|owner|cx|cy|r|a1|a2|w            silk arc
//   SX|side|owner|kind|l|b|r|t|stroke|tt|rot silk text (D designator, C comment, F free)
//                                            free text adds |text
//   SB|side|owner|l|b|r|t                    silk fill / region extent
//   MP|side|owner|pin|x|y|rot|shape|xs|ys|l|b|r|t|shifted   pad solder mask opening
//   MV|side|owner|x|y|diameter               untented via opening
//   MT|side|owner|x1|y1|x2|y2|w, MB|side|owner|l|b|r|t   solder mask layer objects
function ExportSilkscreenData(ROOT_DIR: String): String;
var
    Board     : IPCB_Board;
    Iterator  : IPCB_BoardIterator;
    Prim      : IPCB_Primitive;
    Comp      : IPCB_Component;
    Lines     : TStringList;
    R, T      : TCoordRect;
    XO, YO    : Integer;
    I         : Integer;
    Side      : String;
    Owner     : String;
    Kind      : String;
    Flag      : String;
    Flag2     : String;
    Detail    : String;
    ObjId     : Integer;
    PrimLayer : Integer;
    Wanted    : Boolean;
    FileName  : String;
    S2S, S2M  : Double;
    EdgeOut   : Double;
    EdgeCut   : Double;
    IsSilk    : Boolean;
    IsMask    : Boolean;
    Diameter  : Integer;
begin
    Board := GetBoardSafe(0);
    if (Board = nil) then
    begin
        Result := 'ERROR: No PCB document is currently active';
        Exit;
    end;

    XO := Board.XOrigin;
    YO := Board.YOrigin;
    S2S := -1;
    S2M := -1;
    EdgeOut := -1;
    EdgeCut := -1;
    Lines := TStringList.Create;

    try
        Lines.Add('V|1');
        Lines.Add('B|' + Board.FileName);

        // Design rules: the largest enabled silk clearances
        Iterator := Board.BoardIterator_Create;
        Iterator.AddFilter_ObjectSet(MkSet(eRuleObject));
        Iterator.AddFilter_LayerSet(AllLayers);
        Iterator.AddFilter_Method(eProcessAll);
        Prim := Iterator.FirstPCBObject;
        while (Prim <> nil) do
        begin
            if Prim.Enabled then
            begin
                if (Prim.RuleKind = eRule_SilkToSilkClearance) then
                begin
                    if (CoordToMils(Prim.SilkToSilkClearance) > S2S) then
                        S2S := CoordToMils(Prim.SilkToSilkClearance);
                end
                else if (Prim.RuleKind = eRule_SilkToSolderMaskClearance) then
                begin
                    if (CoordToMils(Prim.SilkToMaskGap) > S2M) then
                        S2M := CoordToMils(Prim.SilkToMaskGap);
                end
                else if (Prim.RuleKind = eRule_BoardOutlineClearance) then
                begin
                    // Per-object table; the rule's Gap is only the generic value
                    if (CoordToMils(Prim.GetClearance(eObjectClearanceID_Text, eObjectClearanceID_OutlineEdge)) > EdgeOut) then
                        EdgeOut := CoordToMils(Prim.GetClearance(eObjectClearanceID_Text, eObjectClearanceID_OutlineEdge));
                    if (CoordToMils(Prim.GetClearance(eObjectClearanceID_Text, eObjectClearanceID_CutoutEdge)) > EdgeCut) then
                        EdgeCut := CoordToMils(Prim.GetClearance(eObjectClearanceID_Text, eObjectClearanceID_CutoutEdge));
                end;
            end;
            Prim := Iterator.NextPCBObject;
        end;
        Board.BoardIterator_Destroy(Iterator);
        Lines.Add('RULE|S2S|' + SilkNum(S2S));
        Lines.Add('RULE|S2M|' + SilkNum(S2M));
        if (EdgeOut >= 0) then
            Lines.Add('RULE|EDGE|' + SilkNum(EdgeOut) + '|' + SilkNum(EdgeCut));

        // Board outline, vertex by vertex (arcs start at their vertex)
        for I := 0 to Board.BoardOutline.PointCount - 1 do
            Lines.Add('O|' + IntToStr(Board.BoardOutline.Segments[I].Kind) + '|' +
                SilkLen(Board.BoardOutline.Segments[I].vx - XO) + '|' +
                SilkLen(Board.BoardOutline.Segments[I].vy - YO) + '|' +
                SilkLen(Board.BoardOutline.Segments[I].cx - XO) + '|' +
                SilkLen(Board.BoardOutline.Segments[I].cy - YO) + '|' +
                SilkLen(Board.BoardOutline.Segments[I].Radius) + '|' +
                SilkNum(Board.BoardOutline.Segments[I].Angle1) + '|' +
                SilkNum(Board.BoardOutline.Segments[I].Angle2));

        // Components and their designator text boxes
        Iterator := Board.BoardIterator_Create;
        Iterator.AddFilter_ObjectSet(MkSet(eComponentObject));
        Iterator.AddFilter_LayerSet(AllLayers);
        Iterator.AddFilter_Method(eProcessAll);
        Comp := Iterator.FirstPCBObject;
        while (Comp <> nil) do
        begin
            R := Comp.BoundingRectangleNoNameComment;
            T := Comp.Name.BoundingRectangle;
            if Comp.NameOn then Flag := '1' else Flag := '0';
            Kind := '0';
            if Comp.Name.MirrorFlag then Kind := '1';
            Flag2 := '0';
            if Comp.Name.UseTTFonts then Flag2 := '1';
            Lines.Add('C|' + Comp.Name.Text + '|' + SilkSideOfLayer(Comp.Name.Layer) + '|' +
                SilkLen(Comp.x - XO) + '|' + SilkLen(Comp.y - YO) + '|' +
                SilkNum(Comp.Rotation) + '|' + Flag + '|' +
                SilkRectStr(R, XO, YO) + '|' + SilkRectStr(T, XO, YO) + '|' +
                SilkNum(Comp.Name.Rotation) + '|' + SilkLen(Comp.Name.Size) + '|' +
                SilkLen(Comp.Name.Width) + '|' + IntToStr(Comp.NameAutoPosition) + '|' +
                Kind + '|' + Comp.Pattern + '|' + Flag2);
            Comp := Iterator.NextPCBObject;
        end;
        Board.BoardIterator_Destroy(Iterator);

        // One pass over every other primitive kind the solver cares about
        Iterator := Board.BoardIterator_Create;
        Iterator.AddFilter_ObjectSet(MkSet(eTrackObject, eArcObject, eTextObject, eFillObject,
                                           eRegionObject, ePadObject, eViaObject, eComponentBodyObject));
        Iterator.AddFilter_LayerSet(AllLayers);
        Iterator.AddFilter_Method(eProcessAll);
        Prim := Iterator.FirstPCBObject;
        while (Prim <> nil) do
        begin
            // Every property read leaks a little memory in the script engine
            // (about 40 MB per export on a 40k-object board), so each object's
            // kind and layer are read once, objects that produce no line are
            // skipped before anything else is read, and the owner is read
            // only for the rest
            ObjId := Prim.ObjectId;
            PrimLayer := Prim.Layer;
            IsSilk := (PrimLayer = eTopOverlay) or (PrimLayer = eBottomOverlay);
            IsMask := (PrimLayer = eTopSolder) or (PrimLayer = eBottomSolder);
            Wanted := IsSilk or IsMask or (ObjId = ePadObject) or (ObjId = eViaObject) or
                      (ObjId = eComponentBodyObject) or (ObjId = eRegionObject);
            Owner := '';
            if Wanted then
                if Prim.InComponent then
                begin
                    Comp := Prim.Component;
                    if (Comp <> nil) then
                        Owner := Comp.Name.Text;
                end;
            Side := SilkSideOfLayer(PrimLayer);

            if not Wanted then
                Side := Side        // nothing to export
            else if (ObjId = eTrackObject) then
            begin
                if IsSilk or IsMask then
                begin
                    if IsSilk then Kind := 'ST' else Kind := 'MT';
                    Lines.Add(Kind + '|' + Side + '|' + Owner + '|' +
                        SilkLen(Prim.X1 - XO) + '|' + SilkLen(Prim.Y1 - YO) + '|' +
                        SilkLen(Prim.X2 - XO) + '|' + SilkLen(Prim.Y2 - YO) + '|' +
                        SilkLen(Prim.Width));
                end;
            end
            else if (ObjId = eArcObject) then
            begin
                if IsSilk then
                    Lines.Add('SA|' + Side + '|' + Owner + '|' +
                        SilkLen(Prim.XCenter - XO) + '|' + SilkLen(Prim.YCenter - YO) + '|' +
                        SilkLen(Prim.Radius) + '|' + SilkNum(Prim.StartAngle) + '|' +
                        SilkNum(Prim.EndAngle) + '|' + SilkLen(Prim.LineWidth))
                else if IsMask then
                    Lines.Add('MB|' + Side + '|' + Owner + '|' + SilkRectStr(Prim.BoundingRectangle, XO, YO));
            end
            else if (ObjId = eTextObject) then
            begin
                if IsSilk then
                begin
                    if (not SilkTextHidden(Prim)) then
                    begin
                        if Prim.IsDesignator then Kind := 'D'
                        else if Prim.IsComment then Kind := 'C'
                        else Kind := 'F';
                        Flag := '0';
                        if Prim.UseTTFonts then Flag := '1';
                        Detail := '';
                        if (Kind = 'F') then
                            Detail := '|' + StringReplace(Prim.Text, '|', '/', REPLACEALL);
                        Lines.Add('SX|' + Side + '|' + Owner + '|' + Kind + '|' +
                            SilkRectStr(Prim.BoundingRectangle, XO, YO) + '|' +
                            SilkLen(Prim.Width) + '|' + Flag + '|' + SilkNum(Prim.Rotation) + Detail);
                    end;
                end;
            end
            else if (ObjId = eFillObject) then
            begin
                if IsSilk then
                    Lines.Add('SB|' + Side + '|' + Owner + '|' + SilkRectStr(Prim.BoundingRectangle, XO, YO))
                else if IsMask then
                    Lines.Add('MB|' + Side + '|' + Owner + '|' + SilkRectStr(Prim.BoundingRectangle, XO, YO));
            end
            else if (ObjId = eRegionObject) then
            begin
                if (Prim.Kind = eRegionKind_BoardCutout) then
                begin
                    // Contour vertices; a 'Layer Stack Region' also reports this
                    // kind and spans the whole board - the solver drops those
                    Kind := 'K';
                    for I := 0 to Prim.MainContour.Count - 1 do
                        Kind := Kind + '|' + SilkLen(Prim.MainContour.x[I] - XO) + '|' +
                                SilkLen(Prim.MainContour.y[I] - YO);
                    Lines.Add(Kind);
                end
                else if IsSilk then
                    Lines.Add('SB|' + Side + '|' + Owner + '|' + SilkRectStr(Prim.BoundingRectangle, XO, YO))
                else if IsMask then
                    Lines.Add('MB|' + Side + '|' + Owner + '|' + SilkRectStr(Prim.BoundingRectangle, XO, YO));
            end
            else if (ObjId = ePadObject) then
            begin
                // Mask opening per side (see SilkPadMaskStr)
                if ((PrimLayer = eTopLayer) or (PrimLayer = eMultiLayer)) and (not Prim.IsTenting_Top) then
                begin
                    Kind := SilkPadMaskStr(Prim, eTopSolder, XO, YO);
                    if (Kind <> '') then
                        Lines.Add('MP|T|' + Owner + '|' + Prim.Name + '|' +
                            SilkLen(Prim.x - XO) + '|' + SilkLen(Prim.y - YO) + '|' +
                            SilkNum(Prim.Rotation) + '|' + IntToStr(Prim.TopShape) + '|' +
                            SilkLen(Prim.TopXSize) + '|' + SilkLen(Prim.TopYSize) + '|' + Kind);
                end;
                if ((PrimLayer = eBottomLayer) or (PrimLayer = eMultiLayer)) and (not Prim.IsTenting_Bottom) then
                begin
                    Kind := SilkPadMaskStr(Prim, eBottomSolder, XO, YO);
                    if (Kind <> '') then
                        Lines.Add('MP|B|' + Owner + '|' + Prim.Name + '|' +
                            SilkLen(Prim.x - XO) + '|' + SilkLen(Prim.y - YO) + '|' +
                            SilkNum(Prim.Rotation) + '|' + IntToStr(Prim.BotShape) + '|' +
                            SilkLen(Prim.BotXSize) + '|' + SilkLen(Prim.BotYSize) + '|' + Kind);
                end;
            end
            else if (ObjId = eViaObject) then
            begin
                Diameter := Prim.Size;
                if (Prim.SolderMaskExpansion > 0) then
                    Diameter := Diameter + 2 * Prim.SolderMaskExpansion;
                if Prim.IntersectLayer(eTopLayer) and (not Prim.IsTenting_Top) then
                    Lines.Add('MV|T|' + Owner + '|' + SilkLen(Prim.x - XO) + '|' + SilkLen(Prim.y - YO) + '|' +
                        SilkLen(Diameter));
                if Prim.IntersectLayer(eBottomLayer) and (not Prim.IsTenting_Bottom) then
                    Lines.Add('MV|B|' + Owner + '|' + SilkLen(Prim.x - XO) + '|' + SilkLen(Prim.y - YO) + '|' +
                        SilkLen(Diameter));
            end
            else if (ObjId = eComponentBodyObject) then
            begin
                if (Owner <> '') then
                    Lines.Add('Y|' + Owner + '|' + SilkRectStr(Prim.BoundingRectangle, XO, YO));
            end;

            Prim := Iterator.NextPCBObject;
        end;
        Board.BoardIterator_Destroy(Iterator);

        FileName := ROOT_DIR + 'silk_data.txt';
        Lines.SaveToFile(FileName);
        Result := '{"file": "' + JSONEscapeString(FileName) + '", "line_count": ' + IntToStr(Lines.Count) + '}';
    finally
        Lines.Free;
    end;
end;

// Move designators so their text box is centred on a given point.
// Each entry is 'Designator|CX|CY|Rotation|Height|StrokeWidth|Visible' in
// mils relative to the board origin; an empty field keeps the current value
// (Visible is 1 or 0). Positioning by box centre makes the result independent
// of the text anchor, justification and bottom-side mirroring. The whole
// batch is one undo step.
function PlaceDesignatorsFromList(EntriesList: TStringList): String;
var
    Board        : IPCB_Board;
    Comp         : IPCB_Component;
    Txt          : IPCB_Text;
    R            : TCoordRect;
    Entry        : String;
    Designator   : String;
    FieldValue   : String;
    ResultProps  : TStringList;
    MissingArray : TStringList;
    PlacedArray  : TStringList;
    MovedTexts   : TStringList;
    TextProps    : TStringList;
    XO, YO       : Integer;
    TargetX      : Integer;
    TargetY      : Integer;
    DX, DY       : Integer;
    Pass         : Integer;
    i            : Integer;
begin
    Board := GetBoardSafe(0);
    if (Board = nil) then
    begin
        Result := 'ERROR: No PCB document is currently active';
        Exit;
    end;

    XO := Board.XOrigin;
    YO := Board.YOrigin;
    ResultProps := TStringList.Create;
    MissingArray := TStringList.Create;
    PlacedArray := TStringList.Create;
    MovedTexts := TStringList.Create;

    try
        PCBServer.PreProcess;

        for i := 0 to EntriesList.Count - 1 do
        begin
            Entry := Trim(EntriesList[i]);
            if (Entry <> '') then
            begin
                Designator := Trim(GetFieldFromPipeString(Entry, 0));
                Comp := Board.GetPcbComponentByRefDes(Designator);

                if (Comp = nil) then
                    MissingArray.Add('"' + JSONEscapeString(Designator) + '"')
                else
                begin
                    Txt := Comp.Name;
                    PCBServer.SendMessageToRobots(Comp.I_ObjectAddress, c_Broadcast, PCBM_BeginModify, c_NoEventData);
                    PCBServer.SendMessageToRobots(Txt.I_ObjectAddress, c_Broadcast, PCBM_BeginModify, c_NoEventData);

                    FieldValue := Trim(GetFieldFromPipeString(Entry, 6));
                    if (FieldValue = '0') then
                        Comp.NameOn := False
                    else if (FieldValue = '1') then
                        Comp.NameOn := True;

                    // Manual, or Altium re-autopositions the text on the next move
                    Comp.NameAutoPosition := eAutoPos_Manual;

                    FieldValue := Trim(GetFieldFromPipeString(Entry, 4));
                    if (FieldValue <> '') then
                        Txt.Size := MilsToCoord(SafeStrToFloat(FieldValue));
                    FieldValue := Trim(GetFieldFromPipeString(Entry, 5));
                    if (FieldValue <> '') then
                        Txt.Width := MilsToCoord(SafeStrToFloat(FieldValue));
                    FieldValue := Trim(GetFieldFromPipeString(Entry, 3));
                    if (FieldValue <> '') then
                        Txt.Rotation := SafeStrToFloat(FieldValue);

                    PCBServer.SendMessageToRobots(Txt.I_ObjectAddress, c_Broadcast, PCBM_EndModify, c_NoEventData);
                    PCBServer.SendMessageToRobots(Comp.I_ObjectAddress, c_Broadcast, PCBM_EndModify, c_NoEventData);

                    // Move only after the rotation is committed: mid-modify the
                    // box turns about its centre, but Altium can commit the
                    // turn about another pivot, so measure the committed box.
                    // A second pass corrects any residual shift.
                    FieldValue := Trim(GetFieldFromPipeString(Entry, 1));
                    if (FieldValue <> '') and (Trim(GetFieldFromPipeString(Entry, 2)) <> '') then
                    begin
                        TargetX := MilsToCoord(SafeStrToFloat(FieldValue)) + XO;
                        TargetY := MilsToCoord(SafeStrToFloat(Trim(GetFieldFromPipeString(Entry, 2)))) + YO;
                        for Pass := 1 to 2 do
                        begin
                            R := Txt.BoundingRectangle;
                            DX := TargetX - (R.Left + (R.Right - R.Left) div 2);
                            DY := TargetY - (R.Bottom + (R.Top - R.Bottom) div 2);
                            if (Abs(DX) > 1) or (Abs(DY) > 1) then
                            begin
                                PCBServer.SendMessageToRobots(Txt.I_ObjectAddress, c_Broadcast, PCBM_BeginModify, c_NoEventData);
                                Txt.MoveByXY(DX, DY);
                                PCBServer.SendMessageToRobots(Txt.I_ObjectAddress, c_Broadcast, PCBM_EndModify, c_NoEventData);
                            end;
                        end;
                    end;

                    MovedTexts.AddObject(Designator, Txt);

                    // Report the final text box as Altium sees it
                    R := Txt.BoundingRectangle;
                    TextProps := TStringList.Create;
                    try
                        AddJSONProperty(TextProps, 'designator', Comp.Name.Text);
                        AddJSONNumber(TextProps, 'cx', CoordToMils(R.Left + (R.Right - R.Left) div 2 - XO));
                        AddJSONNumber(TextProps, 'cy', CoordToMils(R.Bottom + (R.Top - R.Bottom) div 2 - YO));
                        AddJSONNumber(TextProps, 'width', CoordToMils(R.Right - R.Left));
                        AddJSONNumber(TextProps, 'height', CoordToMils(R.Top - R.Bottom));
                        AddJSONNumber(TextProps, 'rotation', Txt.Rotation);
                        AddJSONNumber(TextProps, 'text_height', CoordToMils(Txt.Size));
                        AddJSONNumber(TextProps, 'stroke_width', CoordToMils(Txt.Width));
                        AddJSONBoolean(TextProps, 'visible', Comp.NameOn);
                        PlacedArray.Add(BuildJSONObject(TextProps, 2));
                    finally
                        TextProps.Free;
                    end;
                end;
            end;
        end;

        PCBServer.PostProcess;

        // Rebuild the stroke geometry of every moved text: batch DRC checks a
        // cached copy that scripted moves and resizes leave stale, reporting
        // collisions (and missing real ones) at the old spot. Done after the
        // undo group closes: inside it the rebuild does not stick.
        for i := 0 to MovedTexts.Count - 1 do
        begin
            Txt := MovedTexts.Objects[i];
            Txt.BeginModify;
            Txt.SetState_XSizeYSize;
            Txt.EndModify;
            Txt.GraphicallyInvalidate;
        end;
        Client.SendMessage('PCB:Zoom', 'Action=Redraw', 255, Client.CurrentView);

        AddJSONInteger(ResultProps, 'placed_count', PlacedArray.Count);
        if (MissingArray.Count > 0) then
            ResultProps.Add(BuildJSONArray(MissingArray, 'missing_designators'))
        else
            ResultProps.Add('"missing_designators": []');
        if (PlacedArray.Count > 0) then
            ResultProps.Add(BuildJSONArray(PlacedArray, 'designators', 1))
        else
            ResultProps.Add('"designators": []');

        Result := BuildJSONObject(ResultProps);
    finally
        ResultProps.Free;
        MissingArray.Free;
        PlacedArray.Free;
        MovedTexts.Free;
    end;
end;

// Verify designator silkscreen with Altium's own rule engine. Each visible
// designator in the list (all visible designators when the list is empty) is
// tested against nearby silk with the Silk To Silk Clearance rule and against
// pads, vias and solder mask objects with the Silk To Solder Mask Clearance
// rule, and against the board edge with the Board Outline Clearance rule,
// through Rule.ActualCheck - the same test a batch DRC runs.
function CheckSilkscreen(DesignatorsList: TStringList): String;
var
    Board           : IPCB_Board;
    Iterator        : IPCB_BoardIterator;
    SIter           : IPCB_SpatialIterator;
    Comp            : IPCB_Component;
    Txt             : IPCB_Text;
    Prim            : IPCB_Primitive;
    Rule            : IPCB_Rule;
    Targets         : TStringList;
    MissingArray    : TStringList;
    ViolationsArray : TStringList;
    VProps          : TStringList;
    ResultProps     : TStringList;
    R, PR           : TCoordRect;
    XO, YO          : Integer;
    Margin          : Integer;
    SilkLayer       : Integer;
    MaskLayer       : Integer;
    CopperLayer     : Integer;
    i               : Integer;
    RuleName        : String;
    Detail          : String;
    Owner           : String;
    HiddenCount     : Integer;
    CheckedCount    : Integer;
    DirtyCount      : Integer;
    Dirty           : Boolean;
    Skip            : Boolean;
begin
    Board := GetBoardSafe(0);
    if (Board = nil) then
    begin
        Result := 'ERROR: No PCB document is currently active';
        Exit;
    end;

    XO := Board.XOrigin;
    YO := Board.YOrigin;
    // Search radius around each text box; larger than any sane silk clearance
    Margin := MilsToCoord(30);
    HiddenCount := 0;
    CheckedCount := 0;
    DirtyCount := 0;

    Targets := TStringList.Create;
    MissingArray := TStringList.Create;
    ViolationsArray := TStringList.Create;
    ResultProps := TStringList.Create;

    try
        if (DesignatorsList.Count > 0) then
        begin
            for i := 0 to DesignatorsList.Count - 1 do
            begin
                if (Board.GetPcbComponentByRefDes(Trim(DesignatorsList[i])) <> nil) then
                    Targets.Add(Trim(DesignatorsList[i]))
                else
                    MissingArray.Add('"' + JSONEscapeString(Trim(DesignatorsList[i])) + '"');
            end;
        end
        else
        begin
            Iterator := Board.BoardIterator_Create;
            Iterator.AddFilter_ObjectSet(MkSet(eComponentObject));
            Iterator.AddFilter_LayerSet(AllLayers);
            Iterator.AddFilter_Method(eProcessAll);
            Comp := Iterator.FirstPCBObject;
            while (Comp <> nil) do
            begin
                Targets.Add(Comp.Name.Text);
                Comp := Iterator.NextPCBObject;
            end;
            Board.BoardIterator_Destroy(Iterator);
        end;

        for i := 0 to Targets.Count - 1 do
        begin
            Comp := Board.GetPcbComponentByRefDes(Targets[i]);
            Txt := Comp.Name;
            SilkLayer := Txt.Layer;

            if (not Comp.NameOn) or ((SilkLayer <> eTopOverlay) and (SilkLayer <> eBottomOverlay)) then
                HiddenCount := HiddenCount + 1
            else
            begin
                CheckedCount := CheckedCount + 1;
                Dirty := False;
                if (SilkLayer = eTopOverlay) then
                begin
                    MaskLayer := eTopSolder;
                    CopperLayer := eTopLayer;
                end
                else
                begin
                    MaskLayer := eBottomSolder;
                    CopperLayer := eBottomLayer;
                end;

                R := Txt.BoundingRectangle;
                SIter := Board.SpatialIterator_Create;
                SIter.AddFilter_ObjectSet(MkSet(eTrackObject, eArcObject, eTextObject, eFillObject,
                                                eRegionObject, ePadObject, eViaObject));
                SIter.AddFilter_LayerSet(AllLayers);
                SIter.AddFilter_Area(R.Left - Margin, R.Bottom - Margin, R.Right + Margin, R.Top + Margin);

                Prim := SIter.FirstPCBObject;
                while (Prim <> nil) do
                begin
                    RuleName := '';
                    if (Prim.I_ObjectAddress <> Txt.I_ObjectAddress) then
                    begin
                        if (Prim.Layer = SilkLayer) then
                        begin
                            // No short-circuit evaluation in DelphiScript:
                            // only ask text objects whether they are hidden
                            Skip := False;
                            if (Prim.ObjectId = eTextObject) then
                                Skip := SilkTextHidden(Prim);
                            if not Skip then
                            begin
                                Rule := Board.FindDominantRuleForObjectPair(Txt, Prim, eRule_SilkToSilkClearance);
                                if (Rule <> nil) then
                                    if (Rule.ActualCheck(Txt, Prim) <> nil) then
                                        RuleName := 'silk_to_silk';
                            end;
                        end
                        else if (((Prim.ObjectId = ePadObject) and
                                  ((Prim.Layer = CopperLayer) or (Prim.Layer = eMultiLayer))) or
                                 (Prim.ObjectId = eViaObject) or (Prim.Layer = MaskLayer)) then
                        begin
                            Rule := Board.FindDominantRuleForObjectPair(Txt, Prim, eRule_SilkToSolderMaskClearance);
                            if (Rule <> nil) then
                                if (Rule.ActualCheck(Txt, Prim) <> nil) or (Rule.ActualCheck(Prim, Txt) <> nil) then
                                    RuleName := 'silk_to_mask';
                        end;
                    end;

                    if (RuleName <> '') then
                    begin
                        Dirty := True;
                        Owner := '';
                        if Prim.InComponent then
                            if (Prim.Component <> nil) then
                                Owner := Prim.Component.Name.Text;
                        Detail := '';
                        if (Prim.ObjectId = ePadObject) then
                            Detail := Prim.Name
                        else if (Prim.ObjectId = eTextObject) then
                            Detail := Prim.Text;

                        PR := Prim.BoundingRectangle;
                        VProps := TStringList.Create;
                        try
                            AddJSONProperty(VProps, 'designator', Comp.Name.Text);
                            AddJSONProperty(VProps, 'rule', RuleName);
                            AddJSONProperty(VProps, 'object', Prim.ObjectIDString);
                            AddJSONProperty(VProps, 'owner', Owner);
                            AddJSONProperty(VProps, 'detail', Detail);
                            if (RuleName = 'silk_to_silk') then
                                AddJSONNumber(VProps, 'distance_mils', Round(CoordToMils(Board.PrimPrimDistance(Txt, Prim)) * 100) / 100);
                            AddJSONNumber(VProps, 'object_x', CoordToMils(PR.Left + (PR.Right - PR.Left) div 2 - XO));
                            AddJSONNumber(VProps, 'object_y', CoordToMils(PR.Bottom + (PR.Top - PR.Bottom) div 2 - YO));
                            ViolationsArray.Add(BuildJSONObject(VProps, 2));
                        finally
                            VProps.Free;
                        end;
                    end;

                    Prim := SIter.NextPCBObject;
                end;
                Board.SpatialIterator_Destroy(SIter);

                // Board Outline Clearance: text to the board edge and cutouts,
                // measured from the text's bounding box
                Rule := Board.FindDominantRuleForObject(Txt, eRule_BoardOutlineClearance);
                if (Rule <> nil) then
                    if (Rule.ActualCheck(Txt, Board.BoardOutline) <> nil) then
                    begin
                        Dirty := True;
                        VProps := TStringList.Create;
                        try
                            AddJSONProperty(VProps, 'designator', Comp.Name.Text);
                            AddJSONProperty(VProps, 'rule', 'board_edge');
                            AddJSONProperty(VProps, 'object', 'Board outline');
                            AddJSONProperty(VProps, 'owner', '');
                            AddJSONProperty(VProps, 'detail', '');
                            AddJSONNumber(VProps, 'distance_mils', Round(CoordToMils(Board.PrimPrimDistance(Txt, Board.BoardOutline)) * 100) / 100);
                            ViolationsArray.Add(BuildJSONObject(VProps, 2));
                        finally
                            VProps.Free;
                        end;
                    end;

                if Dirty then
                    DirtyCount := DirtyCount + 1;
            end;
        end;

        AddJSONInteger(ResultProps, 'checked_count', CheckedCount);
        AddJSONInteger(ResultProps, 'hidden_count', HiddenCount);
        AddJSONInteger(ResultProps, 'designators_with_violations', DirtyCount);
        AddJSONInteger(ResultProps, 'violation_count', ViolationsArray.Count);
        if (MissingArray.Count > 0) then
            ResultProps.Add(BuildJSONArray(MissingArray, 'missing_designators'))
        else
            ResultProps.Add('"missing_designators": []');
        if (ViolationsArray.Count > 0) then
            ResultProps.Add(BuildJSONArray(ViolationsArray, 'violations', 1))
        else
            ResultProps.Add('"violations": []');

        Result := BuildJSONObject(ResultProps);
    finally
        Targets.Free;
        MissingArray.Free;
        ViolationsArray.Free;
        ResultProps.Free;
    end;
end;

// Check one free silk primitive (a label-block link) with Altium's silk
// rules, as CheckSilkscreen does for designators: one JSON violation per
// problem, tagged with the request item it came from.
procedure SilkGraphicViolations(Board: IPCB_Board; Prim: IPCB_Primitive; ItemIndex: Integer; ViolationsArray: TStringList);
var
    SIter       : IPCB_SpatialIterator;
    Other       : IPCB_Primitive;
    Rule        : IPCB_Rule;
    R           : TCoordRect;
    Margin      : Integer;
    MaskLayer   : Integer;
    CopperLayer : Integer;
    RuleName    : String;
    Owner       : String;
    Detail      : String;
    Skip        : Boolean;
    VProps      : TStringList;
begin
    if (Prim.Layer = eTopOverlay) then
    begin
        MaskLayer := eTopSolder;
        CopperLayer := eTopLayer;
    end
    else
    begin
        MaskLayer := eBottomSolder;
        CopperLayer := eBottomLayer;
    end;
    Margin := MilsToCoord(30);
    R := Prim.BoundingRectangle;
    SIter := Board.SpatialIterator_Create;
    SIter.AddFilter_ObjectSet(MkSet(eTrackObject, eArcObject, eTextObject, eFillObject,
                                    eRegionObject, ePadObject, eViaObject));
    SIter.AddFilter_LayerSet(AllLayers);
    SIter.AddFilter_Area(R.Left - Margin, R.Bottom - Margin, R.Right + Margin, R.Top + Margin);
    Other := SIter.FirstPCBObject;
    while (Other <> nil) do
    begin
        RuleName := '';
        if (Other.I_ObjectAddress <> Prim.I_ObjectAddress) then
        begin
            if (Other.Layer = Prim.Layer) then
            begin
                Skip := False;
                if (Other.ObjectId = eTextObject) then
                    Skip := SilkTextHidden(Other);
                if not Skip then
                begin
                    Rule := Board.FindDominantRuleForObjectPair(Prim, Other, eRule_SilkToSilkClearance);
                    if (Rule <> nil) then
                        if (Rule.ActualCheck(Prim, Other) <> nil) then
                            RuleName := 'silk_to_silk';
                end;
            end
            else if (((Other.ObjectId = ePadObject) and
                      ((Other.Layer = CopperLayer) or (Other.Layer = eMultiLayer))) or
                     (Other.ObjectId = eViaObject) or (Other.Layer = MaskLayer)) then
            begin
                Rule := Board.FindDominantRuleForObjectPair(Prim, Other, eRule_SilkToSolderMaskClearance);
                if (Rule <> nil) then
                    if (Rule.ActualCheck(Prim, Other) <> nil) or (Rule.ActualCheck(Other, Prim) <> nil) then
                        RuleName := 'silk_to_mask';
            end;
        end;
        if (RuleName <> '') then
        begin
            Owner := '';
            if Other.InComponent then
                if (Other.Component <> nil) then
                    Owner := Other.Component.Name.Text;
            Detail := '';
            if (Other.ObjectId = ePadObject) then
                Detail := Other.Name
            else if (Other.ObjectId = eTextObject) then
                Detail := Other.Text;
            VProps := TStringList.Create;
            try
                AddJSONInteger(VProps, 'item', ItemIndex);
                AddJSONProperty(VProps, 'rule', RuleName);
                AddJSONProperty(VProps, 'object', Other.ObjectIDString);
                AddJSONProperty(VProps, 'owner', Owner);
                AddJSONProperty(VProps, 'detail', Detail);
                ViolationsArray.Add(BuildJSONObject(VProps, 2));
            finally
                VProps.Free;
            end;
        end;
        Other := SIter.NextPCBObject;
    end;
    Board.SpatialIterator_Destroy(SIter);

    Rule := Board.FindDominantRuleForObject(Prim, eRule_BoardOutlineClearance);
    if (Rule <> nil) then
        if (Rule.ActualCheck(Prim, Board.BoardOutline) <> nil) then
        begin
            VProps := TStringList.Create;
            try
                AddJSONInteger(VProps, 'item', ItemIndex);
                AddJSONProperty(VProps, 'rule', 'board_edge');
                AddJSONProperty(VProps, 'object', 'Board outline');
                AddJSONProperty(VProps, 'owner', '');
                AddJSONProperty(VProps, 'detail', '');
                ViolationsArray.Add(BuildJSONObject(VProps, 2));
            finally
                VProps.Free;
            end;
        end;
end;

// The free silk primitive matching a '-' item (without the '-'), or nil.
// Tracks match by end points (either direction), arcs by centre, radius and
// angles, text by its string and the centre of its box.
function FindSilkGraphic(Board: IPCB_Board; Entry: String; Layer: Integer): IPCB_Primitive;
var
    Iterator  : IPCB_BoardIterator;
    Prim      : IPCB_Primitive;
    Kind      : String;
    R         : TCoordRect;
    Tol       : Integer;
    X1, Y1    : Integer;
    X2, Y2    : Integer;
    CR        : Integer;
    A1, A2    : Integer;
    TextValue : String;
    Hit       : Boolean;
begin
    Result := nil;
    Tol := MilsToCoord(0.2);
    Kind := GetFieldFromPipeString(Entry, 0);
    X1 := MilsToCoord(SafeStrToFloat(Trim(GetFieldFromPipeString(Entry, 2)))) + Board.XOrigin;
    Y1 := MilsToCoord(SafeStrToFloat(Trim(GetFieldFromPipeString(Entry, 3)))) + Board.YOrigin;
    X2 := 0;
    Y2 := 0;
    CR := 0;
    A1 := 0;
    A2 := 0;
    TextValue := '';
    if (Kind = 'T') then
    begin
        X2 := MilsToCoord(SafeStrToFloat(Trim(GetFieldFromPipeString(Entry, 4)))) + Board.XOrigin;
        Y2 := MilsToCoord(SafeStrToFloat(Trim(GetFieldFromPipeString(Entry, 5)))) + Board.YOrigin;
    end
    else if (Kind = 'A') then
    begin
        CR := MilsToCoord(SafeStrToFloat(Trim(GetFieldFromPipeString(Entry, 4))));
        A1 := Round(SafeStrToFloat(Trim(GetFieldFromPipeString(Entry, 5))) * 100);
        A2 := Round(SafeStrToFloat(Trim(GetFieldFromPipeString(Entry, 6))) * 100);
    end
    else
        TextValue := GetFieldFromPipeString(Entry, 7);

    Iterator := Board.BoardIterator_Create;
    Iterator.AddFilter_ObjectSet(MkSet(eTrackObject, eArcObject, eTextObject));
    Iterator.AddFilter_IPCB_LayerSet(MkSet(Layer));
    Iterator.AddFilter_Method(eProcessAll);
    Prim := Iterator.FirstPCBObject;
    while (Prim <> nil) do
    begin
        Hit := False;
        if not Prim.InComponent then
        begin
            if (Kind = 'T') and (Prim.ObjectId = eTrackObject) then
            begin
                if (Abs(Prim.X1 - X1) <= Tol) and (Abs(Prim.Y1 - Y1) <= Tol) and
                   (Abs(Prim.X2 - X2) <= Tol) and (Abs(Prim.Y2 - Y2) <= Tol) then
                    Hit := True
                else if (Abs(Prim.X1 - X2) <= Tol) and (Abs(Prim.Y1 - Y2) <= Tol) and
                        (Abs(Prim.X2 - X1) <= Tol) and (Abs(Prim.Y2 - Y1) <= Tol) then
                    Hit := True;
            end
            else if (Kind = 'A') and (Prim.ObjectId = eArcObject) then
            begin
                if (Abs(Prim.XCenter - X1) <= Tol) and (Abs(Prim.YCenter - Y1) <= Tol) and
                   (Abs(Prim.Radius - CR) <= Tol) and (Abs(Round(Prim.StartAngle * 100) - A1) <= 1) and
                   (Abs(Round(Prim.EndAngle * 100) - A2) <= 1) then
                    Hit := True;
            end
            else if (Kind = 'X') and (Prim.ObjectId = eTextObject) then
            begin
                if (Prim.Text = TextValue) then
                begin
                    R := Prim.BoundingRectangle;
                    if (Abs((R.Left + R.Right) div 2 - X1) <= MilsToCoord(1)) and
                       (Abs((R.Bottom + R.Top) div 2 - Y1) <= MilsToCoord(1)) then
                        Hit := True;
                end;
            end;
        end;
        if Hit then
        begin
            Result := Prim;
            Break;
        end;
        Prim := Iterator.NextPCBObject;
    end;
    Board.BoardIterator_Destroy(Iterator);
end;

// Add or remove free silkscreen graphics (the links of label blocks) in one
// undo step. Items, coordinates in mils from the board origin:
//   +T|side|x1|y1|x2|y2|width                   track
//   +A|side|cx|cy|radius|start|end|width        arc (degrees, CCW; 0 to 360 = circle)
//   +X|side|cx|cy|height|stroke|rotation|text   stroke text, box centred on cx, cy
// The same with '-' removes the matching free primitive. Every created
// primitive is then checked with Altium's silk rules.
function EditSilkGraphics(ItemsList: TStringList): String;
var
    Board           : IPCB_Board;
    Prim            : IPCB_Primitive;
    CreatedList     : TStringList;
    RemoveList      : TStringList;
    MissingArray    : TStringList;
    ViolationsArray : TStringList;
    ResultProps     : TStringList;
    Entry           : String;
    Op              : String;
    Kind            : String;
    Layer           : Integer;
    XO, YO          : Integer;
    TX, TY          : Integer;
    R               : TCoordRect;
    i               : Integer;
begin
    Board := GetBoardSafe(0);
    if (Board = nil) then
    begin
        Result := 'ERROR: No PCB document is currently active';
        Exit;
    end;
    XO := Board.XOrigin;
    YO := Board.YOrigin;
    CreatedList := TStringList.Create;
    RemoveList := TStringList.Create;
    MissingArray := TStringList.Create;
    ViolationsArray := TStringList.Create;
    ResultProps := TStringList.Create;
    try
        PCBServer.PreProcess;

        // Removals first, so they can never match what this call creates
        for i := 0 to ItemsList.Count - 1 do
        begin
            Entry := Trim(ItemsList[i]);
            if (Copy(Entry, 1, 1) = '-') then
            begin
                Entry := Copy(Entry, 2, Length(Entry) - 1);
                if (GetFieldFromPipeString(Entry, 1) = 'B') then
                    Layer := eBottomOverlay
                else
                    Layer := eTopOverlay;
                Prim := FindSilkGraphic(Board, Entry, Layer);
                if (Prim <> nil) then
                    RemoveList.AddObject(IntToStr(i), Prim)
                else
                    MissingArray.Add('"' + JSONEscapeString(Entry) + '"');
            end;
        end;
        for i := 0 to RemoveList.Count - 1 do
            Board.RemovePCBObject(RemoveList.Objects[i]);

        for i := 0 to ItemsList.Count - 1 do
        begin
            Entry := Trim(ItemsList[i]);
            Op := Copy(Entry, 1, 1);
            Entry := Copy(Entry, 2, Length(Entry) - 1);
            Kind := GetFieldFromPipeString(Entry, 0);
            if (GetFieldFromPipeString(Entry, 1) = 'B') then
                Layer := eBottomOverlay
            else
                Layer := eTopOverlay;
            Prim := nil;
            if (Op = '+') and (Kind = 'T') then
            begin
                Prim := PCBServer.PCBObjectFactory(eTrackObject, eNoDimension, eCreate_Default);
                Prim.Layer := Layer;
                Prim.X1 := MilsToCoord(SafeStrToFloat(Trim(GetFieldFromPipeString(Entry, 2)))) + XO;
                Prim.Y1 := MilsToCoord(SafeStrToFloat(Trim(GetFieldFromPipeString(Entry, 3)))) + YO;
                Prim.X2 := MilsToCoord(SafeStrToFloat(Trim(GetFieldFromPipeString(Entry, 4)))) + XO;
                Prim.Y2 := MilsToCoord(SafeStrToFloat(Trim(GetFieldFromPipeString(Entry, 5)))) + YO;
                Prim.Width := MilsToCoord(SafeStrToFloat(Trim(GetFieldFromPipeString(Entry, 6))));
                Board.AddPCBObject(Prim);
                PCBServer.SendMessageToRobots(Board.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, Prim.I_ObjectAddress);
            end
            else if (Op = '+') and (Kind = 'A') then
            begin
                Prim := PCBServer.PCBObjectFactory(eArcObject, eNoDimension, eCreate_Default);
                Prim.Layer := Layer;
                Prim.XCenter := MilsToCoord(SafeStrToFloat(Trim(GetFieldFromPipeString(Entry, 2)))) + XO;
                Prim.YCenter := MilsToCoord(SafeStrToFloat(Trim(GetFieldFromPipeString(Entry, 3)))) + YO;
                Prim.Radius := MilsToCoord(SafeStrToFloat(Trim(GetFieldFromPipeString(Entry, 4))));
                Prim.StartAngle := SafeStrToFloat(Trim(GetFieldFromPipeString(Entry, 5)));
                Prim.EndAngle := SafeStrToFloat(Trim(GetFieldFromPipeString(Entry, 6)));
                Prim.LineWidth := MilsToCoord(SafeStrToFloat(Trim(GetFieldFromPipeString(Entry, 7))));
                Board.AddPCBObject(Prim);
                PCBServer.SendMessageToRobots(Board.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, Prim.I_ObjectAddress);
            end
            else if (Op = '+') and (Kind = 'X') then
            begin
                TX := MilsToCoord(SafeStrToFloat(Trim(GetFieldFromPipeString(Entry, 2)))) + XO;
                TY := MilsToCoord(SafeStrToFloat(Trim(GetFieldFromPipeString(Entry, 3)))) + YO;
                Prim := PCBServer.PCBObjectFactory(eTextObject, eNoDimension, eCreate_Default);
                Prim.Layer := Layer;
                Prim.UseTTFonts := False;
                Prim.Text := GetFieldFromPipeString(Entry, 7);
                Prim.Size := MilsToCoord(SafeStrToFloat(Trim(GetFieldFromPipeString(Entry, 4))));
                Prim.Width := MilsToCoord(SafeStrToFloat(Trim(GetFieldFromPipeString(Entry, 5))));
                Prim.MirrorFlag := (Layer = eBottomOverlay);
                Prim.Rotation := SafeStrToFloat(Trim(GetFieldFromPipeString(Entry, 6)));
                Prim.XLocation := TX;
                Prim.YLocation := TY;
                Board.AddPCBObject(Prim);
                PCBServer.SendMessageToRobots(Board.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, Prim.I_ObjectAddress);
                // Centre its box on the target, then rebuild the stroke
                // geometry that batch DRC uses
                R := Prim.BoundingRectangle;
                PCBServer.SendMessageToRobots(Prim.I_ObjectAddress, c_Broadcast, PCBM_BeginModify, c_NoEventData);
                Prim.MoveByXY(TX - (R.Left + R.Right) div 2, TY - (R.Bottom + R.Top) div 2);
                Prim.SetState_XSizeYSize;
                PCBServer.SendMessageToRobots(Prim.I_ObjectAddress, c_Broadcast, PCBM_EndModify, c_NoEventData);
                Prim.GraphicallyInvalidate;
            end
            else if (Op <> '-') then
                MissingArray.Add('"' + JSONEscapeString('cannot read ' + ItemsList[i]) + '"');
            if (Prim <> nil) then
                CreatedList.AddObject(IntToStr(i), Prim);
        end;

        PCBServer.PostProcess;

        // As for designators: rebuild new text geometry outside the undo group
        for i := 0 to CreatedList.Count - 1 do
        begin
            Prim := CreatedList.Objects[i];
            if (Prim.ObjectId = eTextObject) then
            begin
                Prim.BeginModify;
                Prim.SetState_XSizeYSize;
                Prim.EndModify;
            end;
        end;
        Board.ViewManager_FullUpdate;

        for i := 0 to CreatedList.Count - 1 do
            SilkGraphicViolations(Board, CreatedList.Objects[i], StrToInt(CreatedList[i]), ViolationsArray);

        AddJSONInteger(ResultProps, 'created_count', CreatedList.Count);
        AddJSONInteger(ResultProps, 'removed_count', RemoveList.Count);
        if (MissingArray.Count > 0) then
            ResultProps.Add(BuildJSONArray(MissingArray, 'not_found'))
        else
            ResultProps.Add('"not_found": []');
        if (ViolationsArray.Count > 0) then
            ResultProps.Add(BuildJSONArray(ViolationsArray, 'violations', 1))
        else
            ResultProps.Add('"violations": []');
        Result := BuildJSONObject(ResultProps);
    finally
        CreatedList.Free;
        RemoveList.Free;
        MissingArray.Free;
        ViolationsArray.Free;
        ResultProps.Free;
    end;
end;
