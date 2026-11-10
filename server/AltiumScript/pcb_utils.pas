const
    // Mechanical 1 to 1024: Altium numbers mechanical layers past 32
    MAX_MECH_LAYERS = 1024;

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
//   EDITFOOTPRINT|<name>                 add MODEL3D records to an existing
//                                        footprint (never creates one)
//   PAD|name|x|y|rot|layer|plated|hole_size|hole_type|hole_width|hole_rot|top_x|top_y|top_shape[|corner_pct[|mode|mid_x|mid_y|mid_shape|bot_x|bot_y|bot_shape[|paste_mode|paste_exp|mask_mode|mask_exp]]]
//       (mask modes are TMaskExpansionMode ints: 0 no mask, 1 rule, 2 manual;
//        every pad created is locked)
//   MODEL3D|<STEP file>|<layer>|<rot_x>|<rot_y>|<rot_z>|<z_offset>[|<x>|<y>[|<embed 1/0>]]
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
    Body        : IPCB_ComponentBody;
    Model       : IPCB_Model;
    Line, Kind  : String;
    LibPath     : String;
    FieldValue  : String;
    CurMode     : String;
    ModelPath   : String;
    CreatedCount: Integer;
    EditedCount : Integer;
    ModelCount  : Integer;
    PrimErrors  : Integer;
    OX, OY      : Integer;
    i, V        : Integer;
    Matched     : Boolean;
    Shown       : Boolean;
    Problems    : TStringList;


begin
    if not FileExists(SpecFilePath) then
    begin
        Result := 'ERROR: Spec file not found: ' + SpecFilePath;
        Exit;
    end;

    Lines := TStringList.Create;
    FailedArray := TStringList.Create;
    ResultProps := TStringList.Create;
    Problems := TStringList.Create;
    LibComp := nil;
    PcbLib := nil;
    CurMode := '';
    Shown := False;
    OX := 0;
    OY := 0;
    CreatedCount := 0;
    EditedCount := 0;
    ModelCount := 0;
    PrimErrors := 0;

    try
        Lines.LoadFromFile(SpecFilePath);

        // One undo step for the whole batch; this is also what marks the
        // library as modified (the user saves it)
        PCBServer.PreProcess;

        for i := 0 to Lines.Count - 1 do
        begin
            ReportProgress;
            Line := Lines[i];
            Kind := UpperCase(Trim(GetFieldFromPipeString(Line, 0)));

            try
                if (Kind = 'FPLIB') then
                begin
                    // Stop unless that library is now the focused one -
                    // never work on whatever else is focused
                    LibPath := Trim(GetFieldFromPipeString(Line, 1));
                    if FileExists(LibPath) then
                        ServerDoc := OpenLibraryDocument('PcbLib', LibPath);
                    PcbLib := GetPcbLibSafe(0);
                    Matched := False;
                    if (PcbLib <> nil) then
                        Matched := (NormalizedPath(PcbLib.Board.FileName) = NormalizedPath(LibPath));
                    if not Matched then
                    begin
                        PCBServer.PostProcess;
                        Result := 'ERROR: Could not open and focus ' + LibPath + ' - nothing was created';
                        Exit;
                    end;
                end
                else if (Kind = 'FOOTPRINT') or (Kind = 'EDITFOOTPRINT') then
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
                        PCBServer.PostProcess;
                        Result := 'ERROR: No PCB library document is active';
                        Exit;
                    end;
                    CurMode := Kind;
                    OX := 0;
                    OY := 0;
                    Shown := False;
                    if (Kind = 'EDITFOOTPRINT') then
                    begin
                        LibComp := FindLibFootprint(PcbLib, Trim(GetFieldFromPipeString(Line, 1)));
                        if (LibComp = nil) then
                        begin
                            FailedArray.Add('"' + JSONEscapeString(Trim(GetFieldFromPipeString(Line, 1))) + '"');
                            Problems.Add('"' + JSONEscapeString(Trim(GetFieldFromPipeString(Line, 1)) +
                                ': not in the library (EDITFOOTPRINT never creates a footprint)') + '"');
                        end
                        else
                        begin
                            EditedCount := EditedCount + 1;
                            // The library board holds the footprint shown in
                            // the editor, in library coordinates (offset by the
                            // library origin), and writes it back to the
                            // footprint when another one is shown
                            if (PcbLib.CurrentComponent <> nil) then
                                if (PcbLib.CurrentComponent.I_ObjectAddress = LibComp.I_ObjectAddress) then
                                begin
                                    Shown := True;
                                    OX := PcbLib.Board.XOrigin;
                                    OY := PcbLib.Board.YOrigin;
                                end;
                        end;
                    end
                    else
                    begin
                        LibComp := PCBServer.CreatePCBLibComp;
                        LibComp.Name := Trim(GetFieldFromPipeString(Line, 1));
                        LibComp.Description := GetFieldFromPipeString(Line, 2);
                        PcbLib.RegisterComponent(LibComp);
                        PCBServer.SendMessageToRobots(PcbLib.Board.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, LibComp.I_ObjectAddress);
                        CreatedCount := CreatedCount + 1;
                    end;
                end
                else if (LibComp <> nil) and (Kind = 'MODEL3D') then
                begin
                    ModelPath := Trim(GetFieldFromPipeString(Line, 1));
                    if not FileExists(ModelPath) then
                        Problems.Add('"' + JSONEscapeString(LibComp.Name + ': 3D model file not found: ' + ModelPath) + '"')
                    else
                    begin
                        Body := PCBServer.PCBObjectFactory(eComponentBodyObject, eNoDimension, eCreate_Default);
                        Model := Body.ModelFactory_FromFilename(ModelPath, False);
                        if (Model = nil) then
                            Problems.Add('"' + JSONEscapeString(LibComp.Name + ': could not load 3D model ' + ModelPath) + '"')
                        else
                        begin
                            // Embedded unless the embed field is 0
                            if (Trim(GetFieldFromPipeString(Line, 9)) = '0') then
                                Model.Embed := False;
                            Model.SetState(SafeStrToFloat(Trim(GetFieldFromPipeString(Line, 3))),
                                           SafeStrToFloat(Trim(GetFieldFromPipeString(Line, 4))),
                                           SafeStrToFloat(Trim(GetFieldFromPipeString(Line, 5))),
                                           MilsToCoord(SafeStrToFloat(Trim(GetFieldFromPipeString(Line, 6)))));
                            Body.Model := Model;
                            Body.SetState_FromModel;
                            Body.Layer := String2Layer(Trim(GetFieldFromPipeString(Line, 2)));
                            Body.MoveByXY(OX + MilsToCoord(SafeStrToFloat(Trim(GetFieldFromPipeString(Line, 7)))),
                                          OY + MilsToCoord(SafeStrToFloat(Trim(GetFieldFromPipeString(Line, 8)))));
                            // Added to the footprint itself, a body for the
                            // shown footprint is lost when another is shown
                            if Shown then
                            begin
                                PcbLib.Board.AddPCBObject(Body);
                                PCBServer.SendMessageToRobots(PcbLib.Board.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, Body.I_ObjectAddress);
                            end
                            else
                            begin
                                LibComp.AddPCBObject(Body);
                                PCBServer.SendMessageToRobots(LibComp.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, Body.I_ObjectAddress);
                            end;
                            ModelCount := ModelCount + 1;
                        end;
                    end;
                end
                else if (LibComp <> nil) and (CurMode = 'EDITFOOTPRINT') and (Kind <> '') then
                    Problems.Add('"' + JSONEscapeString('line ' + IntToStr(i + 1) + ': ' + Kind +
                        ' is ignored - only MODEL3D can be added to an existing footprint') + '"')
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
                    // Paste and solder mask expansion; omitted keeps the
                    // default (rule)
                    FieldValue := SetPadMaskExpansion(Pad, Trim(GetFieldFromPipeString(Line, 22)), Trim(GetFieldFromPipeString(Line, 23)),
                                                      Trim(GetFieldFromPipeString(Line, 24)), Trim(GetFieldFromPipeString(Line, 25)));
                    if (FieldValue <> '') then
                        Problems.Add('"' + JSONEscapeString(LibComp.Name + ' pad ' + Pad.Name + ': ' + FieldValue) + '"');
                    // Rotation last: it rotates the pad about its location
                    Pad.Rotation := SafeStrToFloat(Trim(GetFieldFromPipeString(Line, 4)));
                    // Every pad the tool creates is locked
                    Pad.Moveable := False;
                    LibComp.AddPCBObject(Pad);
                    PCBServer.SendMessageToRobots(LibComp.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, Pad.I_ObjectAddress);
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
                    PCBServer.SendMessageToRobots(LibComp.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, Track.I_ObjectAddress);
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
                    PCBServer.SendMessageToRobots(LibComp.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, Arc.I_ObjectAddress);
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
                    PCBServer.SendMessageToRobots(LibComp.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, Fill.I_ObjectAddress);
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
                    PCBServer.SendMessageToRobots(LibComp.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, Via.I_ObjectAddress);
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
                    PCBServer.SendMessageToRobots(LibComp.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, Region.I_ObjectAddress);
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
                    PCBServer.SendMessageToRobots(LibComp.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, Text.I_ObjectAddress);
                end;
            except
                PrimErrors := PrimErrors + 1;
                if (Kind = 'FOOTPRINT') then
                    FailedArray.Add('"' + JSONEscapeString(Trim(GetFieldFromPipeString(Line, 1))) + '"');
            end;
        end;

        PCBServer.PostProcess;
        if (LibComp <> nil) and (PcbLib <> nil) then
        begin
            PcbLib.CurrentComponent := LibComp;
            PcbLib.Board.ViewManager_FullUpdate;
        end;
        if (CreatedCount + EditedCount > 0) and (PcbLib <> nil) then
            MarkDocumentModified(PcbLib.Board.FileName);

        AddJSONInteger(ResultProps, 'created', CreatedCount);
        AddJSONInteger(ResultProps, 'edited', EditedCount);
        AddJSONInteger(ResultProps, 'models_added', ModelCount);
        AddJSONInteger(ResultProps, 'primitive_errors', PrimErrors);
        if (FailedArray.Count > 0) then
            ResultProps.Add(BuildJSONArray(FailedArray, 'failed'))
        else
            ResultProps.Add('"failed": []');
        if (Problems.Count > 0) then
            ResultProps.Add(BuildJSONArray(Problems, 'problems'))
        else
            ResultProps.Add('"problems": []');

        Result := BuildJSONObject(ResultProps);
    finally
        Lines.Free;
        FailedArray.Free;
        ResultProps.Free;
        Problems.Free;
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
    BodiesArray : TStringList;
    BodyProps   : TStringList;
    Model       : IPCB_Model;
    OX, OY      : Integer;
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
        ServerDoc := OpenLibraryDocument('PcbLib', LibraryPath);
        if ServerDoc = Nil then
        begin
            Result := 'ERROR: Failed to open library: ' + LibraryPath;
            Exit;
        end;
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
        AddJSONProperty(ResultProps, 'library_path', PcbLib.Board.FileName);

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
                BodiesArray := TStringList.Create;
                try
                    AddJSONProperty(FPProps, 'footprint_name', LibComp.Name);
                    AddJSONProperty(FPProps, 'description', LibComp.Description);

                    // The footprint shown in the editor is held in library
                    // coordinates, offset by the library origin; report every
                    // footprint relative to its own origin
                    OX := 0;
                    OY := 0;
                    if (PcbLib.CurrentComponent <> nil) then
                        if (PcbLib.CurrentComponent.I_ObjectAddress = LibComp.I_ObjectAddress) then
                        begin
                            OX := PcbLib.Board.XOrigin;
                            OY := PcbLib.Board.YOrigin;
                        end;

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
                                    AddJSONNumber(PrimProps, 'x', CoordToMils(Prim.x - OX));
                                    AddJSONNumber(PrimProps, 'y', CoordToMils(Prim.y - OY));
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
                                    AddJSONInteger(PrimProps, 'paste_mode', Prim.PasteMaskExpansionMode);
                                    AddJSONNumber(PrimProps, 'paste_expansion', CoordToMils(Prim.PasteMaskExpansion));
                                    AddJSONInteger(PrimProps, 'mask_mode', Prim.SolderMaskExpansionMode);
                                    AddJSONNumber(PrimProps, 'mask_expansion', CoordToMils(Prim.SolderMaskExpansion));
                                    AddJSONBoolean(PrimProps, 'locked', not Prim.Moveable);
                                end;
                                eTrackObject:
                                begin
                                    AddJSONProperty(PrimProps, 'type', 'track');
                                    AddJSONNumber(PrimProps, 'x1', CoordToMils(Prim.x1 - OX));
                                    AddJSONNumber(PrimProps, 'y1', CoordToMils(Prim.y1 - OY));
                                    AddJSONNumber(PrimProps, 'x2', CoordToMils(Prim.x2 - OX));
                                    AddJSONNumber(PrimProps, 'y2', CoordToMils(Prim.y2 - OY));
                                    AddJSONNumber(PrimProps, 'width', CoordToMils(Prim.Width));
                                    AddJSONProperty(PrimProps, 'layer', Layer2String(Prim.Layer));
                                end;
                                eArcObject:
                                begin
                                    AddJSONProperty(PrimProps, 'type', 'arc');
                                    AddJSONNumber(PrimProps, 'cx', CoordToMils(Prim.XCenter - OX));
                                    AddJSONNumber(PrimProps, 'cy', CoordToMils(Prim.YCenter - OY));
                                    AddJSONNumber(PrimProps, 'radius', CoordToMils(Prim.Radius));
                                    AddJSONNumber(PrimProps, 'start_angle', Prim.StartAngle);
                                    AddJSONNumber(PrimProps, 'end_angle', Prim.EndAngle);
                                    AddJSONNumber(PrimProps, 'width', CoordToMils(Prim.LineWidth));
                                    AddJSONProperty(PrimProps, 'layer', Layer2String(Prim.Layer));
                                end;
                                eFillObject:
                                begin
                                    AddJSONProperty(PrimProps, 'type', 'fill');
                                    AddJSONNumber(PrimProps, 'x1', CoordToMils(Prim.x1Location - OX));
                                    AddJSONNumber(PrimProps, 'y1', CoordToMils(Prim.y1Location - OY));
                                    AddJSONNumber(PrimProps, 'x2', CoordToMils(Prim.x2Location - OX));
                                    AddJSONNumber(PrimProps, 'y2', CoordToMils(Prim.y2Location - OY));
                                    AddJSONNumber(PrimProps, 'rotation', Prim.Rotation);
                                    AddJSONProperty(PrimProps, 'layer', Layer2String(Prim.Layer));
                                end;
                                eTextObject:
                                begin
                                    AddJSONProperty(PrimProps, 'type', 'text');
                                    AddJSONProperty(PrimProps, 'text', Prim.Text);
                                    AddJSONNumber(PrimProps, 'x', CoordToMils(Prim.XLocation - OX));
                                    AddJSONNumber(PrimProps, 'y', CoordToMils(Prim.YLocation - OY));
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
                                                AddJSONNumber(PProps, 'x', CoordToMils(Prim.MainContour.x[V] - OX));
                                                AddJSONNumber(PProps, 'y', CoordToMils(Prim.MainContour.y[V] - OY));
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
                                    AddJSONNumber(PrimProps, 'x', CoordToMils(Prim.x - OX));
                                    AddJSONNumber(PrimProps, 'y', CoordToMils(Prim.y - OY));
                                    AddJSONNumber(PrimProps, 'size', CoordToMils(Prim.Size));
                                    AddJSONNumber(PrimProps, 'hole_size', CoordToMils(Prim.HoleSize));
                                    AddJSONProperty(PrimProps, 'low_layer', Layer2String(Prim.LowLayer));
                                    AddJSONProperty(PrimProps, 'high_layer', Layer2String(Prim.HighLayer));
                                end;
                                eComponentBodyObject:
                                begin
                                    // 3D bodies are models, not 2D primitives:
                                    // reported apart, so the graphics
                                    // round-trip is unchanged. The model's
                                    // rotations and Z offset are added by the
                                    // server from the saved file:
                                    // Model.GetState returns them through out
                                    // parameters, which DelphiScript never
                                    // receives.
                                    AddJSONProperty(PrimProps, 'type', '');
                                    BodyProps := TStringList.Create;
                                    try
                                        Model := Prim.Model;
                                        if (Model <> nil) then
                                        begin
                                            AddJSONProperty(BodyProps, 'model_file', Model.FileName);
                                            AddJSONBoolean(BodyProps, 'model_embedded', Model.Embed);
                                            AddJSONNumber(BodyProps, 'model_x', CoordToMils(Model.Origin.X - OX));
                                            AddJSONNumber(BodyProps, 'model_y', CoordToMils(Model.Origin.Y - OY));
                                        end
                                        else
                                            AddJSONProperty(BodyProps, 'model_file', '');
                                        AddJSONNumber(BodyProps, 'standoff_height', CoordToMils(Prim.StandoffHeight));
                                        AddJSONNumber(BodyProps, 'overall_height', CoordToMils(Prim.OverallHeight));
                                        AddJSONProperty(BodyProps, 'layer', Layer2String(Prim.Layer));
                                        BodiesArray.Add(BuildJSONObject(BodyProps, 2));
                                    finally
                                        BodyProps.Free;
                                    end;
                                end;
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
                    if (BodiesArray.Count > 0) then
                        FPProps.Add(BuildJSONArray(BodiesArray, 'bodies_3d', 1))
                    else
                        FPProps.Add('"bodies_3d": []');

                    if (FootprintName = '*') then
                        FPArray.Add(BuildJSONObject(FPProps, 1))
                    else
                        for i := 0 to FPProps.Count - 1 do
                            ResultProps.Add(FPProps[i]);
                finally
                    FPProps.Free;
                    PrimsArray.Free;
                    BodiesArray.Free;
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
    BoardNets   : TStringList;
    Members     : TStringList;
    NotFound    : TStringList;
    Net         : IPCB_Net;
    NetIterator : IPCB_BoardIterator;
    NetIndex    : Integer;
    OutputLines : TStringList;
begin
    // Initialize result
    ResultProps := TStringList.Create;
    BoardNets := TStringList.Create;
    Members := TStringList.Create;
    NotFound := TStringList.Create;
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
        
        // AddMemberByName returns nothing and takes any name, and IsMember
        // takes a net object: compare names. A net already in the class is
        // skipped (AddMemberByName would list it twice), and so is a name
        // the board has no net for.
        NetIterator := Board.BoardIterator_Create;
        NetIterator.AddFilter_ObjectSet(MkSet(eNetObject));
        NetIterator.AddFilter_LayerSet(AllLayers);
        NetIterator.AddFilter_Method(eProcessAll);
        Net := NetIterator.FirstPCBObject;
        while (Net <> nil) do
        begin
            BoardNets.Add(Net.Name);
            Net := NetIterator.NextPCBObject;
        end;
        Board.BoardIterator_Destroy(NetIterator);

        i := 0;
        while (NetClass.MemberName[i] <> '') do
        begin
            Members.Add(NetClass.MemberName[i]);
            i := i + 1;
        end;

        // Add nets to the class
        PCBServer.PreProcess;
        for i := 0 to NetNames.Count - 1 do
        begin
            NetIndex := BoardNets.IndexOf(NetNames[i]);
            if (NetIndex < 0) then
                NotFound.Add('"' + JSONEscapeString(NetNames[i]) + '"')
            else if (Members.IndexOf(BoardNets[NetIndex]) < 0) then
            begin
                NetClass.AddMemberByName(BoardNets[NetIndex]);
                Members.Add(BoardNets[NetIndex]);
                AddedCount := AddedCount + 1;
            end;
        end;
        PCBServer.PostProcess;
        
        // Clean up iterator
        Board.BoardIterator_Destroy(ClassIterator);
        
        // Build result JSON
        AddJSONBoolean(ResultProps, 'success', True);
        AddJSONProperty(ResultProps, 'class_name', ClassName);
        AddJSONBoolean(ResultProps, 'class_created', not ClassExists);
        AddJSONInteger(ResultProps, 'nets_added', AddedCount);
        if (NotFound.Count > 0) then
            ResultProps.Add(BuildJSONArray(NotFound, 'nets_not_found'))
        else
            ResultProps.Add('"nets_not_found": []');
        
        OutputLines := TStringList.Create;
        try
            OutputLines.Text := BuildJSONObject(ResultProps);
            Result := OutputLines.Text;
        finally
            OutputLines.Free;
        end;
    finally
        ResultProps.Free;
        BoardNets.Free;
        Members.Free;
        NotFound.Free;
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
                AddJSONNumber(LayerProps, 'copper_thickness_um', LayerObject.CopperThickness * 0.00254);
                
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
                    AddJSONNumber(LayerProps, 'dielectric_height_um', LayerObject.Dielectric.DielectricHeight * 0.00254);
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
        
        // Process mechanical layers. Keyed by the layer number: V6 ids only
        // reach Mechanical 16, and every layer above reads 72 (#32)
        for i := 1 to MAX_MECH_LAYERS do
        begin
            MechLayer := TheLayerStack.LayerObject_V7[ILayer.MechanicalLayer(i)];

            if (MechLayer <> nil) and MechLayer.MechanicalLayerEnabled then
            begin
                // Create layer properties
                LayerProps := TStringList.Create;
                try
                    // Add properties
                    AddJSONProperty(LayerProps, 'name', MechLayer.Name);
                    AddJSONProperty(LayerProps, 'layer_id', IntToStr(ILayer.MechanicalLayer(i)));
                    AddJSONProperty(LayerProps, 'layer_type', 'mechanical');
                    AddJSONProperty(LayerProps, 'mechanical_number', IntToStr(i));
                    AddJSONBoolean(LayerProps, 'is_displayed', MechLayer.IsDisplayed[Board]);
                    AddJSONBoolean(LayerProps, 'is_enabled', MechLayer.MechanicalLayerEnabled);
                    AddJSONBoolean(LayerProps, 'link_to_sheet', MechLayer.LinkToSheet);
                    AddJSONBoolean(LayerProps, 'is_paired', Board.MechanicalPairs.LayerUsed(ILayer.MechanicalLayer(i)));
                    AddJSONProperty(LayerProps, 'color', ColorToString(PCBServer.SystemOptions.LayerColors[ILayer.MechanicalLayer(i)]));
                    
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
            
            // The enabled mechanical layers listed above. (Counting with the
            // loop's own variable reported 33 whatever the board had.)
            AddJSONInteger(LayerProps, 'mechanical_layers_count', MechArray.Count);
            
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
function SetPCBLayerVisibility(LayerNamesList: TStringList; Visible: Boolean; Exclusive: Boolean): String;
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
    MechFound      : Boolean;
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
            
            // Check mechanical layers (they can have custom names). Keyed by
            // layer number, as their V6 ids repeat above Mechanical 16
            MechFound := False;
            for j := 1 to MAX_MECH_LAYERS do
            begin
                MechLayer := TheLayerStack.LayerObject_V7[ILayer.MechanicalLayer(j)];
                
                if (MechLayer <> nil) and MechLayer.MechanicalLayerEnabled and (MechLayer.Name = LayerName) then
                begin
                    FoundLayers.Add(IntToStr(ILayer.MechanicalLayer(j)));
                    FoundCount := FoundCount + 1;
                    MechFound := True;
                    break;
                end;
            end;
            
            // If we've checked all layer types and didn't find a match, add to not found list
            if not MechFound then
                NotFoundList.Add('"' + JSONEscapeString(LayerName) + '"');
        end;
        
        // Second phase: set visibility for all layers based on mode
        if Visible and Exclusive then
        begin
            // Show only the specified layers, hide all others
            
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
            for j := 1 to MAX_MECH_LAYERS do
            begin
                MechLayer := TheLayerStack.LayerObject_V7[ILayer.MechanicalLayer(j)];
                
                if (MechLayer <> nil) and MechLayer.MechanicalLayerEnabled then
                begin
                    if (FoundLayers.IndexOf(IntToStr(ILayer.MechanicalLayer(j))) >= 0) then
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
            // Show or hide only the specified layers, leave others unchanged
            
            // For copper layers
            LayerObj := TheLayerStack.FirstLayer;
            while (LayerObj <> nil) do
            begin
                // Check if this layer is in our found list
                if (FoundLayers.IndexOf(IntToStr(LayerObj.V6_LayerID)) >= 0) then
                    LayerObj.IsDisplayed[Board] := Visible;
                
                LayerObj := TheLayerStack.NextLayer(LayerObj);
            end;
            
            // For mechanical layers
            for j := 1 to MAX_MECH_LAYERS do
            begin
                MechLayer := TheLayerStack.LayerObject_V7[ILayer.MechanicalLayer(j)];
                
                if (MechLayer <> nil) and MechLayer.MechanicalLayerEnabled then
                begin
                    if (FoundLayers.IndexOf(IntToStr(ILayer.MechanicalLayer(j))) >= 0) then
                        MechLayer.IsDisplayed[Board] := Visible;
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
                    Board.LayerIsDisplayed[LayerID] := Visible;
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

// A value of create_pcb_rule's "key=value" list, or Default when absent
function RuleValue(Values: TStringList; Key: String; Default: Integer): Integer;
var
    i : Integer;
begin
    Result := Default;
    i := Values.IndexOfName(Key);
    if i >= 0 then Result := StrToInt(Values.ValueFromIndex[i]);
end;

function RuleHasValue(Values: TStringList; Key: String): Boolean;
begin
    Result := Values.IndexOfName(Key) >= 0;
end;

// How many rules of Kind are on Board besides Rule
function OtherRulesOfKind(Board: IPCB_Board; Rule: IPCB_Rule): Integer;
var
    Iterator : IPCB_BoardIterator;
    Other    : IPCB_Rule;
begin
    Result := 0;
    Iterator := Board.BoardIterator_Create;
    Iterator.AddFilter_ObjectSet(MkSet(eRuleObject));
    Iterator.AddFilter_LayerSet(AllLayers);
    Iterator.AddFilter_Method(eProcessAll);
    Other := Iterator.FirstPCBObject;
    while Other <> nil do
    begin
        if (Other.RuleKind = Rule.RuleKind) and (UpperCase(Other.Name) <> UpperCase(Rule.Name)) then
            Result := Result + 1;
        Other := Iterator.NextPCBObject;
    end;
    Board.BoardIterator_Destroy(Iterator);
end;

// The rule named Name on Board, or nil
function FindRuleByName(Board: IPCB_Board; Name: String): IPCB_Rule;
var
    Iterator : IPCB_BoardIterator;
    Rule     : IPCB_Rule;
begin
    Result := nil;
    Iterator := Board.BoardIterator_Create;
    Iterator.AddFilter_ObjectSet(MkSet(eRuleObject));
    Iterator.AddFilter_LayerSet(AllLayers);
    Iterator.AddFilter_Method(eProcessAll);
    Rule := Iterator.FirstPCBObject;
    while (Rule <> nil) and (Result = nil) do
    begin
        if UpperCase(Rule.Name) = UpperCase(Name) then Result := Rule;
        Rule := Iterator.NextPCBObject;
    end;
    Board.BoardIterator_Destroy(Iterator);
end;

// Add a design rule to the focused board. Values holds "key=value" lines in
// internal units (counts for entries, a word for connect_style), already
// checked by the server for this kind. Constraint properties are only
// reachable through a variable of the rule's own interface, hence one
// variable per kind. Every value is read back into the result: Descriptor
// shows what Altium actually stored.
function CreatePCBRule(Kind: String; Name: String; Scope1: String; Scope2: String; Comment: String;
                       Values: TStringList; Replace: Boolean): String;
var
    Board    : IPCB_Board;
    Existing : IPCB_Rule;
    Rule     : IPCB_Rule;
    Clear    : IPCB_ClearanceConstraint;
    Width    : IPCB_MaxMinWidthConstraint;
    Hole     : IPCB_MaxMinHoleSizeConstraint;
    Via      : IPCB_RoutingViaStyleRule;
    SMask    : IPCB_SolderMaskExpansionRule;
    PMask    : IPCB_PasteMaskExpansionRule;
    Connect  : IPCB_PolygonConnectStyleRule;
    L        : Integer;
    Style    : String;
    Props    : TStringList;
begin
    Board := GetBoardSafe(0);
    if Board = nil then
    begin
        Result := 'ERROR: No PCB document is focused';
        Exit;
    end;
    Existing := FindRuleByName(Board, Name);
    if (Existing <> nil) and (not Replace) then
    begin
        Result := 'ERROR: A rule named ' + Name + ' already exists (' + Existing.Descriptor +
                  '). Pass replace=true to replace it, or choose another name';
        Exit;
    end;

    Rule := nil;
    if Kind = 'clearance' then
    begin
        Clear := PCBServer.PCBRuleFactory(eRule_Clearance);
        Clear.Gap := RuleValue(Values, 'gap', 0);
        Rule := Clear;
    end
    else if Kind = 'width' then
    begin
        Width := PCBServer.PCBRuleFactory(eRule_MaxMinWidth);
        // Width is per layer; set it on every signal layer
        for L := eTopLayer to eBottomLayer do
        begin
            Width.MinWidth(L) := RuleValue(Values, 'min', 0);
            Width.MaxWidth(L) := RuleValue(Values, 'max', 0);
            Width.FavoredWidth(L) := RuleValue(Values, 'preferred', 0);
        end;
        Rule := Width;
    end
    else if Kind = 'hole_size' then
    begin
        Hole := PCBServer.PCBRuleFactory(eRule_MaxMinHoleSize);
        Hole.MinLimit := RuleValue(Values, 'min', 0);
        Hole.MaxLimit := RuleValue(Values, 'max', 0);
        Rule := Hole;
    end
    else if Kind = 'routing_via' then
    begin
        Via := PCBServer.PCBRuleFactory(eRule_RoutingViaStyle);
        Via.MinWidth := RuleValue(Values, 'min_diameter', 0);
        Via.MaxWidth := RuleValue(Values, 'max_diameter', 0);
        Via.PreferedWidth := RuleValue(Values, 'preferred_diameter', 0);
        Via.MinHoleWidth := RuleValue(Values, 'min_hole', 0);
        Via.MaxHoleWidth := RuleValue(Values, 'max_hole', 0);
        Via.PreferedHoleWidth := RuleValue(Values, 'preferred_hole', 0);
        Rule := Via;
    end
    else if Kind = 'solder_mask_expansion' then
    begin
        SMask := PCBServer.PCBRuleFactory(eRule_SolderMaskExpansion);
        SMask.Expansion := RuleValue(Values, 'expansion', 0);
        Rule := SMask;
    end
    else if Kind = 'paste_mask_expansion' then
    begin
        PMask := PCBServer.PCBRuleFactory(eRule_PasteMaskExpansion);
        PMask.Expansion := RuleValue(Values, 'expansion', 0);
        Rule := PMask;
    end
    else if Kind = 'polygon_connect' then
    begin
        Connect := PCBServer.PCBRuleFactory(eRule_PolygonConnectStyle);
        Style := Values.Values['connect_style'];
        if Style = 'direct' then
            Connect.ConnectStyle := eDirectConnectToPlane
        else if Style = 'none' then
            Connect.ConnectStyle := eNoConnect
        else
            Connect.ConnectStyle := eReliefConnectToPlane;
        if RuleHasValue(Values, 'conductor_width') then
            Connect.ReliefConductorWidth := RuleValue(Values, 'conductor_width', 0);
        if RuleHasValue(Values, 'air_gap') then
            Connect.ReliefAirGap := RuleValue(Values, 'air_gap', 0);
        if RuleHasValue(Values, 'entries') then
            Connect.ReliefEntries := RuleValue(Values, 'entries', 4);
        Rule := Connect;
    end;
    if Rule = nil then
    begin
        Result := 'ERROR: Unknown rule kind: ' + Kind;
        Exit;
    end;

    Rule.Name := Name;
    Rule.Scope1Expression := Scope1;
    Rule.Scope2Expression := Scope2;
    if Comment <> '' then Rule.Comment := Comment;

    // PostProcess must run whatever happens, or Altium's undo stays broken
    PCBServer.PreProcess;
    try
        if Existing <> nil then
        begin
            Board.RemovePCBObject(Existing);
            PCBServer.SendMessageToRobots(Board.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, Existing.I_ObjectAddress);
        end;
        Board.AddPCBObject(Rule);
        PCBServer.SendMessageToRobots(Board.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, Rule.I_ObjectAddress);
    finally
        PCBServer.PostProcess;
    end;

    Props := TStringList.Create;
    try
        AddJSONBoolean(Props, 'success', True);
        AddJSONProperty(Props, 'name', Rule.Name);
        AddJSONProperty(Props, 'descriptor', Rule.Descriptor);
        AddJSONProperty(Props, 'scope1', Rule.Scope1Expression);
        AddJSONProperty(Props, 'scope2', Rule.Scope2Expression);
        // A rule added by script always becomes priority 1 of its kind, and
        // Priority cannot be set from a script
        AddJSONInteger(Props, 'priority', Rule.Priority);
        AddJSONInteger(Props, 'other_rules_of_kind', OtherRulesOfKind(Board, Rule));
        AddJSONBoolean(Props, 'enabled', Rule.Enabled);
        AddJSONBoolean(Props, 'replaced', Existing <> nil);
        AddJSONProperty(Props, 'board', Board.FileName);
        Result := BuildJSONObject(Props);
    finally
        Props.Free;
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
                AddJSONProperty(RuleProps, 'name', Rule.Name);
                AddJSONBoolean(RuleProps, 'enabled', Rule.Enabled);
                AddJSONInteger(RuleProps, 'priority', Rule.Priority);
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

// One primitive of a violation: what it is, its net, and where
function ViolationPrimitiveJSON(Board: IPCB_Board; Prim: IPCB_Primitive): String;
var
    Props : TStringList;
    Rect  : TCoordRect;
    Detail, NetName : String;
begin
    Props := TStringList.Create;
    try
        Detail := '';
        NetName := '';
        try Detail := Prim.Detail; except end;
        try if Prim.Net <> nil then NetName := Prim.Net.Name; except end;
        AddJSONProperty(Props, 'object', Detail);
        AddJSONProperty(Props, 'net', NetName);
        try AddJSONProperty(Props, 'layer', Layer2String(Prim.Layer)); except end;
        try
            Rect := Prim.BoundingRectangle;
            AddJSONNumber(Props, 'x_mils', CoordToMils((Rect.Left + Rect.Right) div 2 - Board.XOrigin));
            AddJSONNumber(Props, 'y_mils', CoordToMils((Rect.Bottom + Rect.Top) div 2 - Board.YOrigin));
        except
        end;
        Result := BuildJSONObject(Props, 2);
    finally
        Props.Free;
    end;
end;

// Run the design rule check on the focused board and list the violations it
// leaves on the board. PCB:DesignRuleCheck opens the modal Design Rule
// Checker dialog and returns once it closes; run_drc presses its Run button
// from outside Altium. Coordinates are mils relative to the board origin.
function RunDRC(MaxViolations: Integer): String;
var
    Board     : IPCB_Board;
    Iterator  : IPCB_BoardIterator;
    Violation : IPCB_Violation;
    Rect      : TCoordRect;
    Items, Props, ResultProps : TStringList;
    Count     : Integer;
    RuleName  : String;
begin
    Board := GetBoardSafe(0);
    if Board = nil then
    begin
        Result := 'ERROR: No PCB document is focused';
        Exit;
    end;

    ResetParameters;
    RunProcess('PCB:DesignRuleCheck');

    Items := TStringList.Create;
    ResultProps := TStringList.Create;
    try
        Count := 0;
        Iterator := Board.BoardIterator_Create;
        Iterator.AddFilter_ObjectSet(MkSet(eViolationObject));
        Iterator.AddFilter_LayerSet(AllLayers);
        Iterator.AddFilter_Method(eProcessAll);
        Violation := Iterator.FirstPCBObject;
        while Violation <> nil do
        begin
            Count := Count + 1;
            if Count <= MaxViolations then
            begin
                Props := TStringList.Create;
                try
                    RuleName := '';
                    try if Violation.Rule <> nil then RuleName := Violation.Rule.Name; except end;
                    AddJSONProperty(Props, 'rule', RuleName);
                    // Description and the violation's own layer are empty;
                    // Descriptor says what is wrong and by how much
                    try AddJSONProperty(Props, 'description', Violation.Descriptor); except end;
                    try
                        Rect := Violation.BoundingRectangle;
                        AddJSONNumber(Props, 'x_mils', CoordToMils((Rect.Left + Rect.Right) div 2 - Board.XOrigin));
                        AddJSONNumber(Props, 'y_mils', CoordToMils((Rect.Bottom + Rect.Top) div 2 - Board.YOrigin));
                    except
                    end;
                    try
                        if Violation.Primitive1 <> nil then
                            Props.Add('"primitive1": ' + ViolationPrimitiveJSON(Board, Violation.Primitive1));
                    except
                    end;
                    try
                        if Violation.Primitive2 <> nil then
                            Props.Add('"primitive2": ' + ViolationPrimitiveJSON(Board, Violation.Primitive2));
                    except
                    end;
                    Items.Add(BuildJSONObject(Props, 1));
                finally
                    Props.Free;
                end;
            end;
            Violation := Iterator.NextPCBObject;
        end;
        Board.BoardIterator_Destroy(Iterator);

        AddJSONBoolean(ResultProps, 'success', True);
        AddJSONProperty(ResultProps, 'board', Board.FileName);
        AddJSONInteger(ResultProps, 'violation_count', Count);
        ResultProps.Add(BuildJSONArray(Items, 'violations'));
        Result := BuildJSONObject(ResultProps);
    finally
        Items.Free;
        ResultProps.Free;
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
        // AddJSONNumber: FloatToStr writes the locale's decimal separator,
        // and a decimal comma made the reply invalid JSON (#31)
        AddJSONNumber(ResultProps, 'new_x', NewX);
        AddJSONNumber(ResultProps, 'new_y', NewY);
        AddJSONNumber(ResultProps, 'rotation', Component.Rotation);
        
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
        // One undo step; this is also what marks the library as modified
        PCBServer.PreProcess;
        LibComp := PCBServer.CreatePCBLibComp;
        LibComp.Name := FootprintName;
        LibComp.Description := Description;

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
            // Every pad the tool creates is locked
            Pad.Moveable := False;

            LibComp.AddPCBObject(Pad);
            PCBServer.SendMessageToRobots(LibComp.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, Pad.I_ObjectAddress);

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
        PCBServer.SendMessageToRobots(LibComp.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, Track.I_ObjectAddress);

        Track := PCBServer.PCBObjectFactory(eTrackObject, eNoDimension, eCreate_Default);
        Track.Layer := ILayer.MechanicalLayer(15);
        Track.x1 := MMsToCoord(CrtX1); Track.y1 := MMsToCoord(CrtY2);
        Track.x2 := MMsToCoord(CrtX2); Track.y2 := MMsToCoord(CrtY2);
        Track.Width := TrackWidth;
        LibComp.AddPCBObject(Track);
        PCBServer.SendMessageToRobots(LibComp.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, Track.I_ObjectAddress);

        Track := PCBServer.PCBObjectFactory(eTrackObject, eNoDimension, eCreate_Default);
        Track.Layer := ILayer.MechanicalLayer(15);
        Track.x1 := MMsToCoord(CrtX1); Track.y1 := MMsToCoord(CrtY1);
        Track.x2 := MMsToCoord(CrtX1); Track.y2 := MMsToCoord(CrtY2);
        Track.Width := TrackWidth;
        LibComp.AddPCBObject(Track);
        PCBServer.SendMessageToRobots(LibComp.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, Track.I_ObjectAddress);

        Track := PCBServer.PCBObjectFactory(eTrackObject, eNoDimension, eCreate_Default);
        Track.Layer := ILayer.MechanicalLayer(15);
        Track.x1 := MMsToCoord(CrtX2); Track.y1 := MMsToCoord(CrtY1);
        Track.x2 := MMsToCoord(CrtX2); Track.y2 := MMsToCoord(CrtY2);
        Track.Width := TrackWidth;
        LibComp.AddPCBObject(Track);
        PCBServer.SendMessageToRobots(LibComp.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, Track.I_ObjectAddress);

        // Silkscreen on TopOverlay (inset 0.1mm from courtyard)
        Track := PCBServer.PCBObjectFactory(eTrackObject, eNoDimension, eCreate_Default);
        Track.Layer := SilkLayer;
        Track.x1 := MMsToCoord(CrtX1+0.1); Track.y1 := MMsToCoord(CrtY1+0.1);
        Track.x2 := MMsToCoord(CrtX2-0.1); Track.y2 := MMsToCoord(CrtY1+0.1);
        Track.Width := TrackWidth;
        LibComp.AddPCBObject(Track);
        PCBServer.SendMessageToRobots(LibComp.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, Track.I_ObjectAddress);

        Track := PCBServer.PCBObjectFactory(eTrackObject, eNoDimension, eCreate_Default);
        Track.Layer := SilkLayer;
        Track.x1 := MMsToCoord(CrtX1+0.1); Track.y1 := MMsToCoord(CrtY2-0.1);
        Track.x2 := MMsToCoord(CrtX2-0.1); Track.y2 := MMsToCoord(CrtY2-0.1);
        Track.Width := TrackWidth;
        LibComp.AddPCBObject(Track);
        PCBServer.SendMessageToRobots(LibComp.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, Track.I_ObjectAddress);

        // Left silk — split to mark pin 1 (gap at top-left corner for pin 1 indicator)
        Track := PCBServer.PCBObjectFactory(eTrackObject, eNoDimension, eCreate_Default);
        Track.Layer := SilkLayer;
        Track.x1 := MMsToCoord(CrtX1+0.1); Track.y1 := MMsToCoord(CrtY1+0.1);
        Track.x2 := MMsToCoord(CrtX1+0.1); Track.y2 := MMsToCoord(CrtY2-0.6);
        Track.Width := TrackWidth;
        LibComp.AddPCBObject(Track);
        PCBServer.SendMessageToRobots(LibComp.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, Track.I_ObjectAddress);

        // Right silk
        Track := PCBServer.PCBObjectFactory(eTrackObject, eNoDimension, eCreate_Default);
        Track.Layer := SilkLayer;
        Track.x1 := MMsToCoord(CrtX2-0.1); Track.y1 := MMsToCoord(CrtY1+0.1);
        Track.x2 := MMsToCoord(CrtX2-0.1); Track.y2 := MMsToCoord(CrtY2-0.1);
        Track.Width := TrackWidth;
        LibComp.AddPCBObject(Track);
        PCBServer.SendMessageToRobots(LibComp.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, Track.I_ObjectAddress);

        // Register with library board, navigate, and refresh
        PCBServer.SendMessageToRobots(PcbLib.Board.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, LibComp.I_ObjectAddress);
        PCBServer.PostProcess;
        PcbLib.CurrentComponent := LibComp;
        PcbLib.Board.ViewManager_FullUpdate;
        MarkDocumentModified(PcbLib.Board.FileName);

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
            if (DesignatorsList.Count > 0) then
                Result := 'ERROR: None of the given designators is on ' + ExtractFileName(Board.FileName) +
                          ': ' + DesignatorsList.CommaText
            else
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
    MissingArray : TStringList;
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
    MissingArray := TStringList.Create;

    try
        // Build the target component list: explicit designators, or selection
        if (DesignatorsList.Count > 0) then
        begin
            for i := 0 to DesignatorsList.Count - 1 do
            begin
                Designator := Trim(DesignatorsList[i]);
                if (Board.GetPcbComponentByRefDes(Designator) <> nil) then
                    TargetNames.Add(Designator)
                else
                    MissingArray.Add('"' + JSONEscapeString(Designator) + '"');
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
            if (DesignatorsList.Count > 0) then
                Result := 'ERROR: None of the given designators is on ' + ExtractFileName(Board.FileName) +
                          ': ' + DesignatorsList.CommaText
            else
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
        if (MissingArray.Count > 0) then
            ResultProps.Add(BuildJSONArray(MissingArray, 'missing_designators'))
        else
            ResultProps.Add('"missing_designators": []');

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
        MissingArray.Free;
    end;
end;

// Place multiple components at absolute positions in a single transaction.
// Each entry in PlacementsList is 'Designator|X|Y|Rotation|Layer' where X/Y
// are mils relative to the board origin, Rotation is degrees CCW (-1 = keep
// current) and Layer is 'top', 'bottom' or '' (keep current side).
// Set a component's designator (IsName) or comment text from a spec
// 'x;y;rotation;height;stroke width;visible[;autoposition]' (mils, degrees,
// 1/0, TTextAutoposition) as get_layout reports it: x and y are the text's
// anchor, as Altium stores it. The text is placed manually positioned, or
// Altium would move it while placing it; a non-zero autoposition is set
// back afterwards. The text is added to Moved, whose stroke geometry is
// rebuilt afterwards.
procedure PlaceComponentText(Comp: IPCB_Component; IsName: Boolean; Spec: String; XO, YO: TCoord; Moved: TStringList);
var
    Txt    : IPCB_Text;
    TX, TY : TCoord;
    Parts  : TStringList;
begin
    if Trim(Spec) = '' then Exit;
    Parts := TStringList.Create;
    try
        Parts.Text := StringReplace(Spec, ';', #13#10, REPLACEALL);
        if Parts.Count < 6 then Exit;
        if IsName then Txt := Comp.Name else Txt := Comp.Comment;
        PCBServer.SendMessageToRobots(Comp.I_ObjectAddress, c_Broadcast, PCBM_BeginModify, c_NoEventData);
        PCBServer.SendMessageToRobots(Txt.I_ObjectAddress, c_Broadcast, PCBM_BeginModify, c_NoEventData);
        if IsName then
        begin
            Comp.NameOn := Parts[5] = '1';
            Comp.NameAutoPosition := eAutoPos_Manual;
        end
        else
        begin
            Comp.CommentOn := Parts[5] = '1';
            Comp.CommentAutoPosition := eAutoPos_Manual;
        end;
        Txt.Size := MilsToCoord(SafeStrToFloat(Parts[3]));
        Txt.Width := MilsToCoord(SafeStrToFloat(Parts[4]));
        Txt.Rotation := SafeStrToFloat(Parts[2]);
        PCBServer.SendMessageToRobots(Txt.I_ObjectAddress, c_Broadcast, PCBM_EndModify, c_NoEventData);
        PCBServer.SendMessageToRobots(Comp.I_ObjectAddress, c_Broadcast, PCBM_EndModify, c_NoEventData);
        TX := MilsToCoord(SafeStrToFloat(Parts[0])) + XO;
        TY := MilsToCoord(SafeStrToFloat(Parts[1])) + YO;
        if (TX <> Txt.XLocation) or (TY <> Txt.YLocation) then
        begin
            PCBServer.SendMessageToRobots(Txt.I_ObjectAddress, c_Broadcast, PCBM_BeginModify, c_NoEventData);
            Txt.MoveByXY(TX - Txt.XLocation, TY - Txt.YLocation);
            PCBServer.SendMessageToRobots(Txt.I_ObjectAddress, c_Broadcast, PCBM_EndModify, c_NoEventData);
        end;
        if (Parts.Count > 6) and (Trim(Parts[6]) <> '') and (Trim(Parts[6]) <> '0') then
        begin
            PCBServer.SendMessageToRobots(Comp.I_ObjectAddress, c_Broadcast, PCBM_BeginModify, c_NoEventData);
            if IsName then
                Comp.NameAutoPosition := StrToInt(Trim(Parts[6]))
            else
                Comp.CommentAutoPosition := StrToInt(Trim(Parts[6]));
            PCBServer.SendMessageToRobots(Comp.I_ObjectAddress, c_Broadcast, PCBM_EndModify, c_NoEventData);
        end;
        Moved.AddObject(Comp.Name.Text, Txt);
    finally
        Parts.Free;
    end;
end;

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
    LockStr          : String;
    MovedTexts       : TStringList;
    Txt              : IPCB_Text;
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
    MovedTexts := TStringList.Create;
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
                // Locked: '1' locks, '0' unlocks, '' (or absent) leaves it
                LockStr := Trim(GetFieldFromPipeString(Entry, 5));

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

                    if LockStr = '1' then
                        Component.Moveable := False
                    else if LockStr = '0' then
                        Component.Moveable := True;

                    PCBServer.SendMessageToRobots(Component.I_ObjectAddress, c_Broadcast, PCBM_EndModify, c_NoEventData);

                    // Designator and comment text, when given (get_layout's form)
                    PlaceComponentText(Component, True, GetFieldFromPipeString(Entry, 6), xorigin, yorigin, MovedTexts);
                    PlaceComponentText(Component, False, GetFieldFromPipeString(Entry, 7), xorigin, yorigin, MovedTexts);

                    // Report the final state as Altium sees it
                    CompProps := TStringList.Create;
                    try
                        AddJSONProperty(CompProps, 'designator', Component.Name.Text);
                        AddJSONNumber(CompProps, 'x', CoordToMils(Component.x - xorigin));
                        AddJSONNumber(CompProps, 'y', CoordToMils(Component.y - yorigin));
                        AddJSONNumber(CompProps, 'rotation', Component.Rotation);
                        AddJSONProperty(CompProps, 'layer', Layer2String(Component.Layer));
                        AddJSONBoolean(CompProps, 'locked', not Component.Moveable);
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

        // Rebuild the stroke geometry of every text placed: batch DRC checks a
        // cached copy that scripted moves leave stale (see PlaceDesignatorsFromList)
        for i := 0 to MovedTexts.Count - 1 do
        begin
            Txt := MovedTexts.Objects[i];
            Txt.BeginModify;
            Txt.SetState_XSizeYSize;
            Txt.EndModify;
            Txt.GraphicallyInvalidate;
        end;

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
        MovedTexts.Free;
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

// The footprint with this name in a PCB library (case-insensitive), or nil
function FindLibFootprint(PcbLib: IPCB_Library; Name: String): IPCB_LibComponent;
var
    Iter : IPCB_LibraryIterator;
    Comp : IPCB_LibComponent;
begin
    Result := nil;
    Iter := PcbLib.LibraryIterator_Create;
    Iter.SetState_FilterAll;
    Comp := Iter.FirstPCBObject;
    while (Comp <> nil) do
    begin
        if (UpperCase(Comp.Name) = UpperCase(Name)) then
        begin
            Result := Comp;
            Break;
        end;
        Comp := Iter.NextPCBObject;
    end;
    PcbLib.LibraryIterator_Destroy(Iter);
end;

// Set a pad's paste and solder mask expansion before it is added to a
// footprint. Modes are TMaskExpansionMode ints as text (0 no mask, 1 rule,
// 2 manual), expansions in mils; '' leaves that setting alone. A manual
// expansion goes through the pad cache: the PasteMaskExpansion and
// SolderMaskExpansion setters stop the script. Mode 0 (no mask) cannot be set
// on a pad by script: neither mode setter accepts it, and PasteMaskEnabled
// and tenting are not kept. The result is then a problem to report,
// otherwise ''.
function SetPadMaskExpansion(Pad: IPCB_Pad; PasteMode, PasteExp, MaskMode, MaskExp: String): String;
var
    PadCache : IDispatch;
begin
    Result := '';
    if ((PasteMode = '2') and (PasteExp <> '')) or ((MaskMode = '2') and (MaskExp <> '')) then
    begin
        PadCache := Pad.GetState_Cache;
        if (PasteMode = '2') and (PasteExp <> '') then
        begin
            PadCache.PasteMaskExpansion := MilsToCoord(SafeStrToFloat(PasteExp));
            PadCache.PasteMaskExpansionValid := eCacheManual;
        end;
        if (MaskMode = '2') and (MaskExp <> '') then
        begin
            PadCache.SolderMaskExpansion := MilsToCoord(SafeStrToFloat(MaskExp));
            PadCache.SolderMaskExpansionValid := eCacheManual;
        end;
        Pad.SetState_Cache(PadCache);
    end;
    if (PasteMode = '1') then
        Pad.PasteMaskExpansionMode := 1;
    if (MaskMode = '1') then
        Pad.SolderMaskExpansionMode := 1;
    if (PasteMode = '0') and (MaskMode = '0') then
        Result := 'paste and solder mask mode 0 (no mask) cannot be set on a pad by script; left at rule'
    else if (PasteMode = '0') then
        Result := 'paste mask mode 0 (no mask) cannot be set on a pad by script; left at rule'
    else if (MaskMode = '0') then
        Result := 'solder mask mode 0 (no mask) cannot be set on a pad by script; left at rule';
end;

// ---------------------------------------------------------------------------
// Board-level read-only queries (get_board_summary, get_component_primitives).
// Coordinates are millimetres relative to the board origin.
// ---------------------------------------------------------------------------

// Increment the counter kept for Key in two parallel lists (Keys / Counts).
// TStringList.Values is avoided on purpose: it is unreliable in DelphiScript.
procedure TallyKey(Keys: TStringList; Counts: TStringList; Key: String);
var
    Idx: Integer;
begin
    Idx := Keys.IndexOf(Key);
    if Idx < 0 then
    begin
        Keys.Add(Key);
        Counts.Add('1');
    end
    else
        Counts[Idx] := IntToStr(StrToInt(Counts[Idx]) + 1);
end;

function PrimitiveTypeName(ObjectId: Integer): String;
begin
    if ObjectId = eArcObject then Result := 'arc'
    else if ObjectId = ePadObject then Result := 'pad'
    else if ObjectId = eViaObject then Result := 'via'
    else if ObjectId = eTrackObject then Result := 'track'
    else if ObjectId = eTextObject then Result := 'text'
    else if ObjectId = eFillObject then Result := 'fill'
    else if ObjectId = eRegionObject then Result := 'region'
    else Result := 'object_' + IntToStr(ObjectId);
end;

// Millimetres rounded to 0.1 um. Coordinates are stored in mil units, so
// a metric value comes back with float noise (0.2 mm reads 0.1999996).
function RoundedMMs(Coord: Integer): Double;
begin
    Result := Round(CoordToMMs(Coord) * 10000) / 10000;
end;

// Outline, origin, dimensions, vias by hole size, component sides, primitive
// counts per layer and the free texts on both overlays.
function GetBoardSummary(ROOT_DIR: String): String;
var
    Board        : IPCB_Board;
    Outline      : IPCB_BoardOutline;
    Iterator     : IPCB_BoardIterator;
    Obj          : IPCB_Primitive;
    Comp         : IPCB_Component;
    OX, OY       : Integer;
    I, TopCount, BottomCount, ViaCount : Integer;
    ResultProps  : TStringList;
    Props        : TStringList;
    DimArray     : TStringList;
    TextArray    : TStringList;
    HoleKeys, HoleCounts   : TStringList;
    LayerKeys, LayerCounts : TStringList;
    Items        : TStringList;
    OutputLines  : TStringList;
    LayerName    : String;
begin
    Board := GetBoardSafe(0);
    if Board = Nil then
    begin
        Result := 'ERROR: No PCB document focused';
        Exit;
    end;
    OX := Board.XOrigin;
    OY := Board.YOrigin;

    ResultProps := TStringList.Create;
    DimArray := TStringList.Create;
    TextArray := TStringList.Create;
    HoleKeys := TStringList.Create;
    HoleCounts := TStringList.Create;
    LayerKeys := TStringList.Create;
    LayerCounts := TStringList.Create;
    Items := TStringList.Create;
    try
        AddJSONProperty(ResultProps, 'pcb_path', Board.FileName);
        AddJSONNumber(ResultProps, 'origin_abs_x_mm', RoundedMMs(OX));
        AddJSONNumber(ResultProps, 'origin_abs_y_mm', RoundedMMs(OY));

        Outline := Board.BoardOutline;
        Props := TStringList.Create;
        try
            AddJSONNumber(Props, 'x1_mm', RoundedMMs(Outline.BoundingRectangle.Left - OX));
            AddJSONNumber(Props, 'y1_mm', RoundedMMs(Outline.BoundingRectangle.Bottom - OY));
            AddJSONNumber(Props, 'x2_mm', RoundedMMs(Outline.BoundingRectangle.Right - OX));
            AddJSONNumber(Props, 'y2_mm', RoundedMMs(Outline.BoundingRectangle.Top - OY));
            AddJSONNumber(Props, 'width_mm', RoundedMMs(Outline.BoundingRectangle.Right - Outline.BoundingRectangle.Left));
            AddJSONNumber(Props, 'height_mm', RoundedMMs(Outline.BoundingRectangle.Top - Outline.BoundingRectangle.Bottom));
            AddJSONInteger(Props, 'vertex_count', Outline.PointCount);
            ResultProps.Add('"board_outline": ' + Trim(BuildJSONObject(Props, 1)));
        finally
            Props.Free;
        end;

        // Dimensions (cotas)
        Iterator := Board.BoardIterator_Create;
        Iterator.AddFilter_ObjectSet(MkSet(eDimensionObject));
        Iterator.AddFilter_LayerSet(AllLayers);
        Iterator.AddFilter_Method(eProcessAll);
        Obj := Iterator.FirstPCBObject;
        while Obj <> Nil do
        begin
            Props := TStringList.Create;
            try
                AddJSONProperty(Props, 'layer', Layer2String(Obj.Layer));
                AddJSONNumber(Props, 'x1_mm', RoundedMMs(Obj.BoundingRectangle.Left - OX));
                AddJSONNumber(Props, 'y1_mm', RoundedMMs(Obj.BoundingRectangle.Bottom - OY));
                AddJSONNumber(Props, 'x2_mm', RoundedMMs(Obj.BoundingRectangle.Right - OX));
                AddJSONNumber(Props, 'y2_mm', RoundedMMs(Obj.BoundingRectangle.Top - OY));
                DimArray.Add(BuildJSONObject(Props, 1));
            finally
                Props.Free;
            end;
            Obj := Iterator.NextPCBObject;
        end;
        Board.BoardIterator_Destroy(Iterator);

        // Vias by hole size
        ViaCount := 0;
        Iterator := Board.BoardIterator_Create;
        Iterator.AddFilter_ObjectSet(MkSet(eViaObject));
        Iterator.AddFilter_LayerSet(AllLayers);
        Iterator.AddFilter_Method(eProcessAll);
        Obj := Iterator.FirstPCBObject;
        while Obj <> Nil do
        begin
            Inc(ViaCount);
            TallyKey(HoleKeys, HoleCounts, StringReplace(FloatToStr(RoundedMMs(Obj.HoleSize)), ',', '.', REPLACEALL));
            Obj := Iterator.NextPCBObject;
        end;
        Board.BoardIterator_Destroy(Iterator);

        // Components per side
        TopCount := 0;
        BottomCount := 0;
        Iterator := Board.BoardIterator_Create;
        Iterator.AddFilter_ObjectSet(MkSet(eComponentObject));
        Iterator.AddFilter_LayerSet(AllLayers);
        Iterator.AddFilter_Method(eProcessAll);
        Comp := Iterator.FirstPCBObject;
        while Comp <> Nil do
        begin
            if Comp.Layer = eBottomLayer then Inc(BottomCount) else Inc(TopCount);
            Comp := Iterator.NextPCBObject;
        end;
        Board.BoardIterator_Destroy(Iterator);

        // Primitive counts per layer, and free overlay texts
        Iterator := Board.BoardIterator_Create;
        Iterator.AddFilter_ObjectSet(MkSet(eTrackObject, eArcObject, eTextObject, eFillObject, eRegionObject, ePadObject));
        Iterator.AddFilter_LayerSet(AllLayers);
        Iterator.AddFilter_Method(eProcessAll);
        Obj := Iterator.FirstPCBObject;
        while Obj <> Nil do
        begin
            LayerName := Layer2String(Obj.Layer);
            if Obj.InComponent then
                TallyKey(LayerKeys, LayerCounts, LayerName + '|component')
            else
                TallyKey(LayerKeys, LayerCounts, LayerName + '|free');
            if (Obj.ObjectId = eTextObject) and (not Obj.InComponent) and
               ((Obj.Layer = eTopOverlay) or (Obj.Layer = eBottomOverlay)) then
                TextArray.Add('{"layer": "' + JSONEscapeString(LayerName) + '", "text": "' + JSONEscapeString(Obj.Text) + '"}');
            Obj := Iterator.NextPCBObject;
        end;
        Board.BoardIterator_Destroy(Iterator);

        ResultProps.Add(BuildJSONArray(DimArray, 'dimensions'));
        AddJSONInteger(ResultProps, 'via_count', ViaCount);
        Items.Clear;
        for I := 0 to HoleKeys.Count - 1 do
            Items.Add('{"hole_mm": ' + HoleKeys[I] + ', "count": ' + HoleCounts[I] + '}');
        ResultProps.Add(BuildJSONArray(Items, 'vias_by_hole'));
        AddJSONInteger(ResultProps, 'components_top', TopCount);
        AddJSONInteger(ResultProps, 'components_bottom', BottomCount);
        Items.Clear;
        for I := 0 to LayerKeys.Count - 1 do
            Items.Add('{"layer": "' + JSONEscapeString(GetFieldFromPipeString(LayerKeys[I], 0)) +
                '", "owner": "' + GetFieldFromPipeString(LayerKeys[I], 1) +
                '", "count": ' + LayerCounts[I] + '}');
        ResultProps.Add(BuildJSONArray(Items, 'layer_contents'));
        ResultProps.Add(BuildJSONArray(TextArray, 'overlay_texts'));

        OutputLines := TStringList.Create;
        try
            OutputLines.Text := BuildJSONObject(ResultProps);
            Result := WriteJSONToFile(OutputLines, ROOT_DIR + 'temp_board_summary.json');
        finally
            OutputLines.Free;
        end;
    finally
        ResultProps.Free;
        DimArray.Free;
        TextArray.Free;
        HoleKeys.Free;
        HoleCounts.Free;
        LayerKeys.Free;
        LayerCounts.Free;
        Items.Free;
    end;
end;

// Layer of every primitive of the given components (e.g. to check that a
// flipped fiducial has its copper and its solder-mask pad on the same side).
function GetComponentPrimitivesFromList(ROOT_DIR: String; DesignatorsList: TStringList): String;
var
    Board       : IPCB_Board;
    Iterator    : IPCB_BoardIterator;
    GroupIter   : IPCB_GroupIterator;
    Comp        : IPCB_Component;
    Prim        : IPCB_Primitive;
    OX, OY      : Integer;
    CompArray   : TStringList;
    PrimArray   : TStringList;
    CompProps   : TStringList;
    PrimProps   : TStringList;
    Missing     : TStringList;
    ResultProps : TStringList;
    OutputLines : TStringList;
    Found       : TStringList;
    I           : Integer;
begin
    Board := GetBoardSafe(0);
    if Board = Nil then
    begin
        Result := 'ERROR: No PCB document focused';
        Exit;
    end;
    OX := Board.XOrigin;
    OY := Board.YOrigin;

    CompArray := TStringList.Create;
    Found := TStringList.Create;
    Missing := TStringList.Create;
    ResultProps := TStringList.Create;
    try
        Iterator := Board.BoardIterator_Create;
        Iterator.AddFilter_ObjectSet(MkSet(eComponentObject));
        Iterator.AddFilter_LayerSet(AllLayers);
        Iterator.AddFilter_Method(eProcessAll);
        Comp := Iterator.FirstPCBObject;
        while Comp <> Nil do
        begin
            if DesignatorsList.IndexOf(Comp.Name.Text) >= 0 then
            begin
                Found.Add(Comp.Name.Text);
                PrimArray := TStringList.Create;
                CompProps := TStringList.Create;
                try
                    GroupIter := Comp.GroupIterator_Create;
                    GroupIter.AddFilter_ObjectSet(MkSet(ePadObject, eTrackObject, eArcObject, eFillObject, eRegionObject, eTextObject));
                    Prim := GroupIter.FirstPCBObject;
                    while Prim <> Nil do
                    begin
                        PrimProps := TStringList.Create;
                        try
                            AddJSONProperty(PrimProps, 'type', PrimitiveTypeName(Prim.ObjectId));
                            AddJSONProperty(PrimProps, 'layer', Layer2String(Prim.Layer));
                            if Prim.ObjectId = ePadObject then
                            begin
                                AddJSONProperty(PrimProps, 'name', Prim.Name);
                                AddJSONNumber(PrimProps, 'size_x_mm', RoundedMMs(Prim.TopXSize));
                                AddJSONNumber(PrimProps, 'size_y_mm', RoundedMMs(Prim.TopYSize));
                                AddJSONNumber(PrimProps, 'solder_mask_expansion_mm', RoundedMMs(Prim.SolderMaskExpansion));
                                AddJSONNumber(PrimProps, 'paste_mask_expansion_mm', RoundedMMs(Prim.PasteMaskExpansion));
                            end;
                            if Prim.ObjectId = eArcObject then
                            begin
                                AddJSONNumber(PrimProps, 'radius_mm', RoundedMMs(Prim.Radius));
                                AddJSONNumber(PrimProps, 'width_mm', RoundedMMs(Prim.LineWidth));
                            end;
                            if Prim.ObjectId = eTrackObject then
                                AddJSONNumber(PrimProps, 'width_mm', RoundedMMs(Prim.Width));
                            PrimArray.Add(BuildJSONObject(PrimProps, 2));
                        finally
                            PrimProps.Free;
                        end;
                        Prim := GroupIter.NextPCBObject;
                    end;
                    Comp.GroupIterator_Destroy(GroupIter);

                    AddJSONProperty(CompProps, 'designator', Comp.Name.Text);
                    AddJSONProperty(CompProps, 'footprint', Comp.Pattern);
                    AddJSONProperty(CompProps, 'layer', Layer2String(Comp.Layer));
                    AddJSONNumber(CompProps, 'x_mm', RoundedMMs(Comp.X - OX));
                    AddJSONNumber(CompProps, 'y_mm', RoundedMMs(Comp.Y - OY));
                    AddJSONNumber(CompProps, 'rotation', Comp.Rotation);
                    CompProps.Add(BuildJSONArray(PrimArray, 'primitives', 1));
                    CompArray.Add(BuildJSONObject(CompProps, 1));
                finally
                    PrimArray.Free;
                    CompProps.Free;
                end;
            end;
            Comp := Iterator.NextPCBObject;
        end;
        Board.BoardIterator_Destroy(Iterator);

        for I := 0 to DesignatorsList.Count - 1 do
            if Found.IndexOf(DesignatorsList[I]) < 0 then
                Missing.Add('"' + JSONEscapeString(DesignatorsList[I]) + '"');

        ResultProps.Add(BuildJSONArray(CompArray, 'components'));
        ResultProps.Add(BuildJSONArray(Missing, 'missing_designators'));
        OutputLines := TStringList.Create;
        try
            OutputLines.Text := BuildJSONObject(ResultProps);
            Result := WriteJSONToFile(OutputLines, ROOT_DIR + 'temp_component_primitives.json');
        finally
            OutputLines.Free;
        end;
    finally
        CompArray.Free;
        Found.Free;
        Missing.Free;
        ResultProps.Free;
    end;
end;

// The kind of a free primitive delete_pcb_objects and modify_pcb_objects act
// on, or '' for one they leave alone: pads, anything of a component, the
// copper a polygon poured (it is the polygon's own and goes with it),
// keepouts, and anything not on a copper layer (board outline, silkscreen,
// mechanical layers). A region is copper ('region') or a polygon pour
// cutout ('cutout'); board cutouts and named regions are left alone.
function EditableKind(Prim: IPCB_Primitive): String;
var
    Copper : Boolean;
begin
    Result := '';
    if Prim.InComponent or Prim.InPolygon then Exit;
    if Prim.ObjectId = eViaObject then
    begin
        Result := 'via';
        Exit;
    end;
    if Prim.IsKeepout then Exit;
    Copper := (Prim.Layer >= eTopLayer) and (Prim.Layer <= eBottomLayer);
    case Prim.ObjectId of
        eTrackObject:  if Copper then Result := 'track';
        eArcObject:    if Copper then Result := 'arc';
        ePolyObject:   if Copper then Result := 'polygon';
        eFillObject:   if Copper then Result := 'fill';
        eRegionObject:
            begin
                if (Prim.Kind = 0) and Copper then Result := 'region'
                else if (Prim.Kind = 1) and (Copper or (Prim.Layer = eMultiLayer)) then Result := 'cutout';
            end;
    end;
end;

// The primitives matching every filter: Kinds is '|track|via|...|', NetName
// and LayerName '' for any, Area 'x1|y1|x2|y2' (internal units from the board
// origin) or '' for anywhere; an object matches the area when it lies inside.
// A via matches a layer it spans. With Designators (upper-case names), the
// group's copper as get_layout lists it: inside Area (or the group's extent
// grown by Margin) or on one of the group's own nets. Locked objects are
// left out unless IncludeLocked, and listed in Locked.
function MatchPCBObjects(Board: IPCB_Board; Kinds: String; NetName: String; Layer: Integer;
                         Area: String; Designators: TStringList; Margin: Integer; IncludeLocked: Boolean;
                         Found: TInterfaceList; Locked: TInterfaceList): Integer;
var
    Iterator : IPCB_BoardIterator;
    Prim     : IPCB_Primitive;
    Comps    : TInterfaceList;
    Local    : TStringList;
    Kind, GroupArea : String;
    R        : TCoordRect;
    X1, Y1, X2, Y2 : Integer;
    Ok, HasArea, InArea : Boolean;
begin
    HasArea := Area <> '';
    if HasArea then
    begin
        X1 := Board.XOrigin + StrToInt(GetFieldFromPipeString(Area, 0));
        Y1 := Board.YOrigin + StrToInt(GetFieldFromPipeString(Area, 1));
        X2 := Board.XOrigin + StrToInt(GetFieldFromPipeString(Area, 2));
        Y2 := Board.YOrigin + StrToInt(GetFieldFromPipeString(Area, 3));
    end;
    Local := TStringList.Create;
    Local.Sorted := True;
    Comps := TInterfaceList.Create;
    if Designators.Count > 0 then
    begin
        LayoutFindComponents(Board, Designators, Comps);
        LayoutLocalNets(Board, Designators, Local);
        if (not HasArea) and (Comps.Count > 0) then
        begin
            GroupArea := LayoutGroupArea(Comps, Margin);
            X1 := StrToInt(GetFieldFromPipeString(GroupArea, 0));
            Y1 := StrToInt(GetFieldFromPipeString(GroupArea, 1));
            X2 := StrToInt(GetFieldFromPipeString(GroupArea, 2));
            Y2 := StrToInt(GetFieldFromPipeString(GroupArea, 3));
            HasArea := True;
        end;
    end;
    Iterator := Board.BoardIterator_Create;
    Iterator.AddFilter_ObjectSet(MkSet(eTrackObject, eArcObject, eViaObject, ePolyObject, eRegionObject, eFillObject));
    Iterator.AddFilter_LayerSet(AllLayers);
    Iterator.AddFilter_Method(eProcessAll);
    Prim := Iterator.FirstPCBObject;
    while Prim <> nil do
    begin
        Kind := EditableKind(Prim);
        Ok := (Kind <> '') and (Pos('|' + Kind + '|', Kinds) > 0);
        if Ok and (NetName <> '') then
            Ok := (Prim.Net <> nil) and (UpperCase(Prim.Net.Name) = UpperCase(NetName));
        if Ok and (Layer <> eNoLayer) then
        begin
            // Layer IDs are not in stack order: ask the via whether it spans it
            if Prim.ObjectId = eViaObject then
                Ok := Prim.IntersectLayer(Layer)
            else
                Ok := Prim.Layer = Layer;
        end;
        if Ok and HasArea then
        begin
            R := Prim.BoundingRectangle;
            InArea := (R.Left >= X1) and (R.Right <= X2) and (R.Bottom >= Y1) and (R.Top <= Y2);
            if Designators.Count > 0 then
                Ok := InArea or ((Prim.Net <> nil) and (Local.IndexOf(UpperCase(Prim.Net.Name)) >= 0))
            else
                Ok := InArea;
        end
        else if Ok and (Designators.Count > 0) then
            Ok := (Prim.Net <> nil) and (Local.IndexOf(UpperCase(Prim.Net.Name)) >= 0);
        if Ok then
        begin
            if (not IncludeLocked) and (not Prim.Moveable) then
                Locked.Add(Prim)
            else
                Found.Add(Prim);
        end;
        Prim := Iterator.NextPCBObject;
    end;
    Board.BoardIterator_Destroy(Iterator);
    Comps.Free;
    Local.Free;
    Result := Found.Count;
end;

// One matched object for the result: kind, net, layer, position in mils
function PCBObjectJSON(Board: IPCB_Board; Prim: IPCB_Primitive): String;
var
    Item : TStringList;
    R    : TCoordRect;
begin
    Item := TStringList.Create;
    try
        AddJSONProperty(Item, 'kind', EditableKind(Prim));
        if Prim.Net <> nil then AddJSONProperty(Item, 'net', Prim.Net.Name) else AddJSONProperty(Item, 'net', '');
        if Prim.ObjectId = eViaObject then
        begin
            AddJSONProperty(Item, 'layer', Layer2String(Prim.LowLayer) + ' - ' + Layer2String(Prim.HighLayer));
            AddJSONNumber(Item, 'x', CoordToMils(Prim.x - Board.XOrigin));
            AddJSONNumber(Item, 'y', CoordToMils(Prim.y - Board.YOrigin));
            AddJSONNumber(Item, 'diameter_mils', CoordToMils(Prim.Size));
            AddJSONNumber(Item, 'hole_mils', CoordToMils(Prim.HoleSize));
        end
        else
        begin
            AddJSONProperty(Item, 'layer', Layer2String(Prim.Layer));
            if Prim.ObjectId = eTrackObject then
            begin
                AddJSONNumber(Item, 'x1', CoordToMils(Prim.x1 - Board.XOrigin));
                AddJSONNumber(Item, 'y1', CoordToMils(Prim.y1 - Board.YOrigin));
                AddJSONNumber(Item, 'x2', CoordToMils(Prim.x2 - Board.XOrigin));
                AddJSONNumber(Item, 'y2', CoordToMils(Prim.y2 - Board.YOrigin));
                AddJSONNumber(Item, 'width_mils', CoordToMils(Prim.Width));
            end
            else
            begin
                R := Prim.BoundingRectangle;
                AddJSONNumber(Item, 'left', CoordToMils(R.Left - Board.XOrigin));
                AddJSONNumber(Item, 'bottom', CoordToMils(R.Bottom - Board.YOrigin));
                AddJSONNumber(Item, 'right', CoordToMils(R.Right - Board.XOrigin));
                AddJSONNumber(Item, 'top', CoordToMils(R.Top - Board.YOrigin));
                if Prim.ObjectId = eArcObject then AddJSONNumber(Item, 'width_mils', CoordToMils(Prim.LineWidth));
                if Prim.ObjectId = ePolyObject then AddJSONProperty(Item, 'name', Prim.Name);
            end;
        end;
        Result := BuildJSONObject(Item, 1);
    finally
        Item.Free;
    end;
end;

// delete_pcb_objects and modify_pcb_objects. Action is 'delete' or 'modify';
// with DryRun nothing changes and the matches are listed. Modify sets what is
// given: Width (tracks, arcs), ViaSize and ViaHole (vias), the net when SetNet
// ('' removes it), NewLayerName (tracks, arcs, fills, regions); -1 or ''
// leaves a value alone. All of it is one undo step.
function EditPCBObjects(Action: String; Kinds: String; NetName: String; LayerName: String; Area: String;
                        DesignatorText: String; Margin: Integer; IncludeLocked: Boolean;
                        DryRun: Boolean; Width: Integer; ViaSize: Integer; ViaHole: Integer;
                        SetNet: Boolean; NewNetName: String; NewLayerName: String): String;
var
    Board    : IPCB_Board;
    Found, Polys, Locked : TInterfaceList;
    Prim     : IPCB_Primitive;
    Layer, NewLayer, i, Changed, Skipped, BadSize, NewSize, NewHole : Integer;
    NewNet   : IPCB_Net;
    Kind     : String;
    Items, Props, LockedItems, Designators : TStringList;
    Did      : Boolean;
begin
    Board := GetBoardSafe(0);
    if Board = nil then
    begin
        Result := 'ERROR: No PCB document is focused';
        Exit;
    end;
    if (NetName <> '') and (FindBoardNet(Board, NetName) = nil) then
    begin
        Result := 'ERROR: Net not found on the board: ' + NetName;
        Exit;
    end;
    Layer := eNoLayer;
    if LayerName <> '' then
    begin
        Layer := ResolveBoardLayer(Board, LayerName);
        if Layer = eNoLayer then
        begin
            Result := 'ERROR: ' + LayerName + ' is not a copper layer of this board';
            Exit;
        end;
    end;
    NewNet := nil;
    if SetNet and (NewNetName <> '') then
    begin
        NewNet := FindBoardNet(Board, NewNetName);
        if NewNet = nil then
        begin
            Result := 'ERROR: Net not found on the board: ' + NewNetName;
            Exit;
        end;
    end;
    NewLayer := eNoLayer;
    if NewLayerName <> '' then
    begin
        NewLayer := ResolveBoardLayer(Board, NewLayerName);
        if NewLayer = eNoLayer then
        begin
            Result := 'ERROR: ' + NewLayerName + ' is not a copper layer of this board';
            Exit;
        end;
    end;

    Found := TInterfaceList.Create;
    Polys := TInterfaceList.Create;
    Locked := TInterfaceList.Create;
    Items := TStringList.Create;
    Props := TStringList.Create;
    LockedItems := TStringList.Create;
    Designators := TStringList.Create;
    try
        Designators.Text := UpperCase(StringReplace(DesignatorText, ';', #13#10, REPLACEALL));
        MatchPCBObjects(Board, Kinds, NetName, Layer, Area, Designators, Margin, IncludeLocked, Found, Locked);
        for i := 0 to Found.Count - 1 do
            if i < 50 then Items.Add(PCBObjectJSON(Board, Found.Items[i]));
        for i := 0 to Locked.Count - 1 do
            if i < 20 then LockedItems.Add(PCBObjectJSON(Board, Locked.Items[i]));

        Changed := 0;
        Skipped := 0;
        BadSize := 0;
        if (not DryRun) and (Found.Count > 0) then
        begin
            // PostProcess must run whatever happens, or Altium's undo stays broken
            PCBServer.PreProcess;
            try
                for i := 0 to Found.Count - 1 do
                begin
                    Prim := Found.Items[i];
                    if Action = 'delete' then
                    begin
                        Board.RemovePCBObject(Prim);
                        PCBServer.SendMessageToRobots(Board.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, Prim.I_ObjectAddress);
                        Changed := Changed + 1;
                    end
                    else
                    begin
                        Kind := EditableKind(Prim);
                        Did := False;
                        PCBServer.SendMessageToRobots(Prim.I_ObjectAddress, c_Broadcast, PCBM_BeginModify, c_NoEventData);
                        if (Width >= 0) and (Kind = 'track') then begin Prim.Width := Width; Did := True; end;
                        if (Width >= 0) and (Kind = 'arc') then begin Prim.LineWidth := Width; Did := True; end;
                        if (Kind = 'via') and ((ViaSize >= 0) or (ViaHole >= 0)) then
                        begin
                            // A size given alone is checked against the via's other size
                            NewSize := Prim.Size;
                            NewHole := Prim.HoleSize;
                            if ViaSize >= 0 then NewSize := ViaSize;
                            if ViaHole >= 0 then NewHole := ViaHole;
                            if NewHole < NewSize then
                            begin
                                Prim.Size := NewSize;
                                Prim.HoleSize := NewHole;
                                Did := True;
                            end
                            else
                                BadSize := BadSize + 1;
                        end;
                        if SetNet then begin Prim.Net := NewNet; Did := True; end;
                        if (NewLayer <> eNoLayer) and ((Kind = 'track') or (Kind = 'arc') or (Kind = 'fill') or (Kind = 'region')) then
                        begin
                            Prim.Layer := NewLayer;
                            Did := True;
                        end;
                        PCBServer.SendMessageToRobots(Prim.I_ObjectAddress, c_Broadcast, PCBM_EndModify, c_NoEventData);
                        if Did then Changed := Changed + 1 else Skipped := Skipped + 1;
                        if Did and (Kind = 'polygon') then Polys.Add(Prim);
                    end;
                end;
                // a polygon given another net pours again for it
                for i := 0 to Polys.Count - 1 do
                    Polys.Items[i].Rebuild;
            finally
                PCBServer.PostProcess;
            end;
            Board.ViewManager_FullUpdate;
        end;

        AddJSONBoolean(Props, 'success', True);
        AddJSONProperty(Props, 'action', Action);
        AddJSONBoolean(Props, 'dry_run', DryRun);
        AddJSONInteger(Props, 'matched', Found.Count);
        if Action = 'delete' then AddJSONInteger(Props, 'deleted', Changed)
        else
        begin
            AddJSONInteger(Props, 'changed', Changed);
            AddJSONInteger(Props, 'unchanged', Skipped);
            AddJSONInteger(Props, 'vias_left_as_hole_would_not_fit', BadSize);
        end;
        if Items.Count > 0 then Props.Add(BuildJSONArray(Items, 'objects')) else Props.Add('"objects": []');
        AddJSONInteger(Props, 'locked_skipped', Locked.Count);
        if LockedItems.Count > 0 then Props.Add(BuildJSONArray(LockedItems, 'locked'));
        AddJSONProperty(Props, 'board', Board.FileName);
        Result := BuildJSONObject(Props);
    finally
        Found.Free;
        Polys.Free;
        Locked.Free;
        Items.Free;
        Props.Free;
        LockedItems.Free;
        Designators.Free;
    end;
end;

// Altium's query engine, as the PCB Filter panel runs it: PCB:RunQuery with
// Apply selects the objects an expression matches, which are read from the
// selection. The engine reads bare numbers in its own unit, which is not the
// board's display unit (a board shown in mil queried in mm), so it is found
// once per call by asking for a known via or track size.
//
// Queries change the selection and apply a filter. Each call saves the
// user's selection first, in memory and as object addresses in StoreFile,
// and selects it again and clears the filter when done. A call the server
// stops (an expression Altium cannot evaluate) never gets there: query_restore,
// or the next query call, selects it again from StoreFile. A filter the user
// had applied cannot be read by a script, so it is cleared, not restored.

// Number as the query engine reads it: always a dot, whatever the locale
function QueryNumber(V: Double): String;
begin
    Result := StringReplace(FormatFloat('0.######', V), DecimalSeparator, '.', REPLACEALL);
end;

procedure QueryClearFilter(Dummy: Integer);
begin
    ResetParameters;
    AddStringParameter('Clear', 'True');
    RunProcess('PCB:RunQuery');
end;

// The objects Expr matches, in Found; the selection is left empty
procedure RunBoardQuery(Board: IPCB_Board; Expr: String; Found: TInterfaceList);
var
    i : Integer;
begin
    Board.SelectedObjects_Clear;
    try
        ResetParameters;
        AddStringParameter('Apply', 'True');
        AddStringParameter('Expr', Expr);
        AddStringParameter('Select', 'True');
        RunProcess('PCB:RunQuery');
        for i := 0 to Board.SelectecObjectCount - 1 do
            Found.Add(Board.SelectecObject[i]);
    finally
        Board.SelectedObjects_Clear;
        QueryClearFilter(0);
    end;
end;

// Selects again what a stopped query call saved in StoreFile for this board,
// clears the filter and deletes the file. How many objects were selected,
// or -1 when nothing was saved for this board.
function QueryRestoreSelection(Board: IPCB_Board; StoreFile: String): Integer;
var
    Lines    : TStringList;
    Iterator : IPCB_BoardIterator;
    Prim     : IPCB_Primitive;
begin
    Result := -1;
    if not FileExists(StoreFile) then Exit;
    Lines := TStringList.Create;
    try
        Lines.LoadFromFile(StoreFile);
        if Lines.Count = 0 then Exit;
        // Saved for another board: left for a query call on that board
        if UpperCase(Lines[0]) <> UpperCase(Board.FileName) then Exit;
        Lines.Delete(0);
        Lines.Sorted := True;
        Result := 0;
        Board.SelectedObjects_Clear;
        QueryClearFilter(0);
        if Lines.Count > 0 then
        begin
            Iterator := Board.BoardIterator_Create;
            Iterator.AddFilter_ObjectSet(MkSet(eArcObject, ePadObject, eViaObject, eTrackObject, eTextObject,
                eFillObject, eComponentObject, ePolyObject, eRegionObject, eDimensionObject, eComponentBodyObject));
            Iterator.AddFilter_LayerSet(AllLayers);
            Iterator.AddFilter_Method(eProcessAll);
            Prim := Iterator.FirstPCBObject;
            while Prim <> nil do
            begin
                if Lines.IndexOf(IntToStr(Prim.I_ObjectAddress)) >= 0 then
                begin
                    Prim.Selected := True;
                    Result := Result + 1;
                end;
                Prim := Iterator.NextPCBObject;
            end;
            Board.BoardIterator_Destroy(Iterator);
        end;
        DeleteFile(StoreFile);
    finally
        Lines.Free;
    end;
end;

// Saves the selection in Saved and in StoreFile, first selecting again what
// a stopped call left in StoreFile
procedure QuerySaveSelection(Board: IPCB_Board; StoreFile: String; Saved: TInterfaceList);
var
    Lines : TStringList;
    Prim  : IPCB_Primitive;
    i     : Integer;
begin
    QueryRestoreSelection(Board, StoreFile);
    Lines := TStringList.Create;
    try
        Lines.Add(Board.FileName);
        for i := 0 to Board.SelectecObjectCount - 1 do
        begin
            Prim := Board.SelectecObject[i];
            Saved.Add(Prim);
            Lines.Add(IntToStr(Prim.I_ObjectAddress));
        end;
        Lines.SaveToFile(StoreFile);
    finally
        Lines.Free;
    end;
end;

// Selects again what QuerySaveSelection saved, with the filter cleared
procedure QueryEndSelection(Board: IPCB_Board; StoreFile: String; Saved: TInterfaceList);
var
    i : Integer;
begin
    Board.SelectedObjects_Clear;
    for i := 0 to Saved.Count - 1 do
        Saved.Items[i].Selected := True;
    DeleteFile(StoreFile);
end;

// query_restore: the selection a stopped query call left behind
function QueryRestore(StoreFile: String): String;
var
    Board : IPCB_Board;
    Props : TStringList;
    Count : Integer;
begin
    Board := GetBoardSafe(0);
    if Board = nil then
    begin
        Result := 'ERROR: No PCB document is focused';
        Exit;
    end;
    Count := QueryRestoreSelection(Board, StoreFile);
    Props := TStringList.Create;
    try
        AddJSONBoolean(Props, 'success', True);
        AddJSONBoolean(Props, 'restored', Count >= 0);
        if Count >= 0 then AddJSONInteger(Props, 'selected', Count);
        Result := BuildJSONObject(Props);
    finally
        Props.Free;
    end;
end;

// 'mm' or 'mil': how the engine reads bare numbers on this board, from a via
// hole or a track width it must find again; '' when the board has neither
function QueryUnit(Board: IPCB_Board): String;
var
    Iterator : IPCB_BoardIterator;
    Prim     : IPCB_Primitive;
    Found    : TInterfaceList;
    Prop, Kind : String;
    Size     : Integer;
begin
    Result := '';
    Iterator := Board.BoardIterator_Create;
    Iterator.AddFilter_ObjectSet(MkSet(eViaObject, eTrackObject));
    Iterator.AddFilter_LayerSet(AllLayers);
    Iterator.AddFilter_Method(eProcessAll);
    Prim := Iterator.FirstPCBObject;
    while (Prim <> nil) and (Prim.ObjectId <> eViaObject) do Prim := Iterator.NextPCBObject;
    if Prim = nil then Prim := Iterator.FirstPCBObject;
    Board.BoardIterator_Destroy(Iterator);
    if Prim = nil then Exit;
    if Prim.ObjectId = eViaObject then
    begin
        Kind := 'IsVia'; Prop := 'HoleSize'; Size := Prim.HoleSize;
    end
    else
    begin
        Kind := 'IsTrack'; Prop := 'Width'; Size := Prim.Width;
    end;
    Found := TInterfaceList.Create;
    try
        RunBoardQuery(Board, Kind + ' And (' + Prop + ' > ' + QueryNumber(CoordToMMs(Size) * 0.999) +
                      ') And (' + Prop + ' < ' + QueryNumber(CoordToMMs(Size) * 1.001) + ')', Found);
        if Found.Count > 0 then Result := 'mm'
        else
        begin
            RunBoardQuery(Board, Kind + ' And (' + Prop + ' > ' + QueryNumber(CoordToMils(Size) * 0.999) +
                          ') And (' + Prop + ' < ' + QueryNumber(CoordToMils(Size) * 1.001) + ')', Found);
            if Found.Count > 0 then Result := 'mil';
        end;
    finally
        Found.Free;
    end;
end;

// The board's own name of a layer ('GND1' for Mid Layer 1 on a board that
// renamed it), or '' when it has none of its own. Read from the V7 layer
// stack: Prim.Layer holds V7 ids, and the V6 stack names Mechanical 17 and
// up after other layers ('Mid-Layer 16' for Mechanical 17).
function QueryBoardLayerName(Board: IPCB_Board; Layer: Integer): String;
var
    Obj : IPCB_LayerObject;
begin
    Result := '';
    try
        Obj := Board.LayerStack_V7.LayerObject_V7[Layer];
        if Obj <> nil then Result := Obj.Name;
    except
        Result := '';
    end;
    if SameText(Result, Layer2String(Layer)) then Result := '';
end;

// Generic replaced by Named where a layer name is expected: OnLayer('..')
// (ExistsOnLayer too), Layer = '..' and Layer <> '..', written as
// pcb_query.layer_arguments writes them. Other quoted text is left alone.
function QueryRenameLayer(Expr: String; Generic: String; Named: String): String;
begin
    Result := Expr;
    if (Named = '') or SameText(Named, Generic) then Exit;
    Result := StringReplace(Result, 'OnLayer(''' + Generic + ''')', 'OnLayer(''' + Named + ''')',
                            MkSet(rfReplaceAll, rfIgnoreCase));
    Result := StringReplace(Result, 'Layer = ''' + Generic + '''', 'Layer = ''' + Named + '''',
                            MkSet(rfReplaceAll, rfIgnoreCase));
    Result := StringReplace(Result, 'Layer <> ''' + Generic + '''', 'Layer <> ''' + Named + '''',
                            MkSet(rfReplaceAll, rfIgnoreCase));
end;

// The engine knows layers only by the board's names: OnLayer('Mid Layer 3')
// matches nothing on a board that calls it 'SIG/PWR'. Each generic layer
// name given as a layer is replaced by the board's name for it, mechanical
// layers past 16 included (their V6 ids stop at Mechanical 16, #32).
function QueryLayerNames(Board: IPCB_Board; Expr: String): String;
var
    L, i      : Integer;
    MechLayer : IPCB_MechanicalLayer;
begin
    Result := Expr;
    for L := eTopLayer to eMultiLayer do
        Result := QueryRenameLayer(Result, Layer2String(L), QueryBoardLayerName(Board, L));
    if Pos('MECHANICAL ', UpperCase(Result)) = 0 then Exit;
    for i := 17 to MAX_MECH_LAYERS do
    begin
        MechLayer := Board.LayerStack_V7.LayerObject_V7[ILayer.MechanicalLayer(i)];
        if MechLayer <> nil then
            Result := QueryRenameLayer(Result, 'Mechanical ' + IntToStr(i), MechLayer.Name);
    end;
end;

// Area is 'x1|y1|x2|y2' in internal units from the board origin, or '' for
// the whole board; True when the object's bounds touch it
function QueryInArea(Board: IPCB_Board; Prim: IPCB_Primitive; Area: String): Boolean;
var
    R : TCoordRect;
    X1, Y1, X2, Y2 : Integer;
begin
    Result := True;
    if Area = '' then Exit;
    X1 := Board.XOrigin + StrToInt(GetFieldFromPipeString(Area, 0));
    Y1 := Board.YOrigin + StrToInt(GetFieldFromPipeString(Area, 1));
    X2 := Board.XOrigin + StrToInt(GetFieldFromPipeString(Area, 2));
    Y2 := Board.YOrigin + StrToInt(GetFieldFromPipeString(Area, 3));
    R := Prim.BoundingRectangle;
    Result := (R.Left <= X2) and (R.Right >= X1) and (R.Bottom <= Y2) and (R.Top >= Y1);
end;

// Keeps the objects of Found inside Area
procedure QueryKeepInArea(Board: IPCB_Board; Found: TInterfaceList; Area: String);
var
    i : Integer;
begin
    if Area = '' then Exit;
    for i := Found.Count - 1 downto 0 do
        if not QueryInArea(Board, Found.Items[i], Area) then Found.Delete(i);
end;

function QueryKindName(Prim: IPCB_Primitive): String;
begin
    case Prim.ObjectId of
        eArcObject:       Result := 'arc';
        ePadObject:       Result := 'pad';
        eViaObject:       Result := 'via';
        eTrackObject:     Result := 'track';
        eTextObject:      Result := 'text';
        eFillObject:      Result := 'fill';
        eComponentObject: Result := 'component';
        ePolyObject:      Result := 'polygon';
        eRegionObject:    Result := 'region';
        eNetObject:       Result := 'net';
        eRuleObject:      Result := 'rule';
        eDimensionObject: Result := 'dimension';
    else
        Result := 'object ' + IntToStr(Prim.ObjectId);
    end;
end;

// One matched object: kind, its name (designator, pad, polygon, rule, net),
// net, layer and bounds in mils from the board origin
function QueryObjectJSON(Board: IPCB_Board; Prim: IPCB_Primitive): String;
var
    Item : TStringList;
    R    : TCoordRect;
begin
    Item := TStringList.Create;
    try
        AddJSONProperty(Item, 'kind', QueryKindName(Prim));
        case Prim.ObjectId of
            eComponentObject: AddJSONProperty(Item, 'name', Prim.Name.Text);
            ePadObject:
                begin
                    if Prim.Component <> nil then
                        AddJSONProperty(Item, 'name', Prim.Component.Name.Text + '-' + Prim.Name)
                    else
                        AddJSONProperty(Item, 'name', Prim.Name);
                end;
            ePolyObject, eRuleObject, eNetObject: AddJSONProperty(Item, 'name', Prim.Name);
        end;
        if (Prim.ObjectId <> eNetObject) and (Prim.ObjectId <> eRuleObject) and (Prim.ObjectId <> eComponentObject) then
        begin
            if Prim.Net <> nil then AddJSONProperty(Item, 'net', Prim.Net.Name) else AddJSONProperty(Item, 'net', '');
        end;
        if (Prim.ObjectId <> eNetObject) and (Prim.ObjectId <> eRuleObject) then
        begin
            if Prim.ObjectId = eViaObject then
                AddJSONProperty(Item, 'layer', Layer2String(Prim.LowLayer) + ' - ' + Layer2String(Prim.HighLayer))
            else
            begin
                AddJSONProperty(Item, 'layer', Layer2String(Prim.Layer));
                // the name the board gives it, which is what OnLayer() takes
                if QueryBoardLayerName(Board, Prim.Layer) <> '' then
                    AddJSONProperty(Item, 'layer_name', QueryBoardLayerName(Board, Prim.Layer));
            end;
            R := Prim.BoundingRectangle;
            AddJSONNumber(Item, 'left', CoordToMils(R.Left - Board.XOrigin));
            AddJSONNumber(Item, 'bottom', CoordToMils(R.Bottom - Board.YOrigin));
            AddJSONNumber(Item, 'right', CoordToMils(R.Right - Board.XOrigin));
            AddJSONNumber(Item, 'top', CoordToMils(R.Top - Board.YOrigin));
        end;
        Result := BuildJSONObject(Item, 1);
    finally
        Item.Free;
    end;
end;

// query_objects: ExprMM and ExprMil are the expression with its unit values
// written in mm and in mil; the one in the engine's unit is run. Area keeps
// the objects touching it. StoreFile keeps the user's selection while the
// queries run.
function QueryObjects(ExprMM: String; ExprMil: String; Limit: Integer; Area: String; StoreFile: String): String;
var
    Board : IPCB_Board;
    Found, Saved : TInterfaceList;
    Items, Kinds, Counts, Props : TStringList;
    Units, Expr, Kind : String;
    i, k, NoNet : Integer;
begin
    Board := GetBoardSafe(0);
    if Board = nil then
    begin
        Result := 'ERROR: No PCB document is focused';
        Exit;
    end;
    Found := TInterfaceList.Create;
    Saved := TInterfaceList.Create;
    Items := TStringList.Create;
    Kinds := TStringList.Create;
    Counts := TStringList.Create;
    Props := TStringList.Create;
    try
        QuerySaveSelection(Board, StoreFile, Saved);
        try
            Units := QueryUnit(Board);
            if Units = 'mil' then Expr := ExprMil else Expr := ExprMM;
            Expr := QueryLayerNames(Board, Expr);
            RunBoardQuery(Board, Expr, Found);
        finally
            QueryEndSelection(Board, StoreFile, Saved);
        end;
        QueryKeepInArea(Board, Found, Area);
        NoNet := 0;
        for i := 0 to Found.Count - 1 do
        begin
            // A copper pour that lost its net: InNet() cannot find it, and a
            // repour would pour it as dead copper
            if Found.Items[i].ObjectId = ePolyObject then
                if (Found.Items[i].Net = nil) and (Found.Items[i].Layer >= eTopLayer) and (Found.Items[i].Layer <= eBottomLayer) then NoNet := NoNet + 1;
            if i < Limit then Items.Add(QueryObjectJSON(Board, Found.Items[i]));
            Kind := QueryKindName(Found.Items[i]);
            k := Kinds.IndexOf(Kind);
            if k < 0 then
            begin
                Kinds.Add(Kind);
                Counts.Add('1');
            end
            else
                Counts[k] := IntToStr(StrToInt(Counts[k]) + 1);
        end;
        AddJSONBoolean(Props, 'success', True);
        AddJSONProperty(Props, 'expression_run', Expr);
        AddJSONProperty(Props, 'engine_unit', Units);
        AddJSONInteger(Props, 'matched', Found.Count);
        if NoNet > 0 then AddJSONInteger(Props, 'copper_polygons_without_net', NoNet);
        Item_Counts_JSON(Props, Kinds, Counts);
        if Items.Count > 0 then Props.Add(BuildJSONArray(Items, 'objects')) else Props.Add('"objects": []');
        AddJSONProperty(Props, 'board', Board.FileName);
        Result := BuildJSONObject(Props);
    finally
        Found.Free;
        Saved.Free;
        Items.Free;
        Kinds.Free;
        Counts.Free;
        Props.Free;
    end;
end;

// "by_kind": {"track": 12, ...}
procedure Item_Counts_JSON(Props: TStringList; Kinds: TStringList; Counts: TStringList);
var
    i : Integer;
    S : String;
begin
    S := '';
    for i := 0 to Kinds.Count - 1 do
    begin
        if S <> '' then S := S + ', ';
        S := S + '"' + JSONEscapeString(Kinds[i]) + '": ' + Counts[i];
    end;
    Props.Add('"by_kind": {' + S + '}');
end;

// The rule kind called Name, or -1
function RuleKindByName(Name: String): Integer;
begin
    Name := LowerCase(Name);
    Result := -1;
    if Name = 'clearance' then Result := eRule_Clearance
    else if Name = 'width' then Result := eRule_MaxMinWidth
    else if Name = 'routing_via' then Result := eRule_RoutingViaStyle
    else if Name = 'hole_size' then Result := eRule_MaxMinHoleSize
    else if Name = 'polygon_connect' then Result := eRule_PolygonConnectStyle
    else if Name = 'plane_connect' then Result := eRule_PowerPlaneConnectStyle
    else if Name = 'solder_mask_expansion' then Result := eRule_SolderMaskExpansion
    else if Name = 'paste_mask_expansion' then Result := eRule_PasteMaskExpansion
    else if Name = 'short_circuit' then Result := eRule_ShortCircuit
    else if Name = 'hole_to_hole' then Result := eRule_HoleToHoleClearance
    else if Name = 'silk_to_silk' then Result := eRule_SilkToSilkClearance
    else if Name = 'silk_to_mask' then Result := eRule_SilkToSolderMaskClearance
    else if Name = 'component_clearance' then Result := eRule_ComponentClearance;
end;

// Rule kinds that apply to one object (FindDominantRuleForObject), not to a pair
function RuleKindIsUnary(Kind: Integer): Boolean;
begin
    Result := (Kind = eRule_MaxMinWidth) or (Kind = eRule_MaxMinHoleSize) or (Kind = eRule_RoutingViaStyle) or
              (Kind = eRule_SolderMaskExpansion) or (Kind = eRule_PasteMaskExpansion) or
              (Kind = eRule_PowerPlaneConnectStyle);
end;

// Rule kinds the DRC tests, so that Rule.ActualCheck says whether objects pass
function RuleKindIsChecked(Kind: Integer): Boolean;
begin
    Result := (Kind = eRule_Clearance) or (Kind = eRule_MaxMinWidth) or (Kind = eRule_MaxMinHoleSize) or
              (Kind = eRule_RoutingViaStyle) or (Kind = eRule_ShortCircuit) or
              (Kind = eRule_HoleToHoleClearance) or (Kind = eRule_SilkToSilkClearance) or
              (Kind = eRule_SilkToSolderMaskClearance) or (Kind = eRule_ComponentClearance);
end;

// which_rule: the rule of RuleName that applies to the one object ExprA
// matches, or between it and the one ExprB matches with the distance between
// them, and whether they pass it. ExprB is empty for a one-object kind.
// AreaA and AreaB keep the objects touching them. StoreFile keeps the user's
// selection while the queries run.
function WhichRule(ExprAMM, ExprAMil, ExprBMM, ExprBMil, RuleName, AreaA, AreaB, StoreFile: String): String;
var
    Board     : IPCB_Board;
    A, B      : TInterfaceList;
    Saved     : TInterfaceList;
    Rule      : IPCB_Rule;
    Violation : IPCB_Violation;
    Kind      : Integer;
    Unary     : Boolean;
    Units     : String;
    Props     : TStringList;
begin
    Board := GetBoardSafe(0);
    if Board = nil then
    begin
        Result := 'ERROR: No PCB document is focused';
        Exit;
    end;
    Kind := RuleKindByName(RuleName);
    if Kind < 0 then
    begin
        Result := 'ERROR: Unknown rule kind ' + RuleName;
        Exit;
    end;
    Unary := RuleKindIsUnary(Kind);
    if Unary and (ExprBMM <> '') then
    begin
        Result := 'ERROR: ' + RuleName + ' rules apply to one object: leave object_b empty';
        Exit;
    end;
    if (not Unary) and (ExprBMM = '') then
    begin
        Result := 'ERROR: ' + RuleName + ' rules apply between two objects: give object_b';
        Exit;
    end;
    A := TInterfaceList.Create;
    B := TInterfaceList.Create;
    Saved := TInterfaceList.Create;
    Props := TStringList.Create;
    try
        QuerySaveSelection(Board, StoreFile, Saved);
        try
            Units := QueryUnit(Board);
            if Units = 'mil' then RunBoardQuery(Board, QueryLayerNames(Board, ExprAMil), A)
            else RunBoardQuery(Board, QueryLayerNames(Board, ExprAMM), A);
            if not Unary then
            begin
                if Units = 'mil' then RunBoardQuery(Board, QueryLayerNames(Board, ExprBMil), B)
                else RunBoardQuery(Board, QueryLayerNames(Board, ExprBMM), B);
            end;
        finally
            QueryEndSelection(Board, StoreFile, Saved);
        end;
        QueryKeepInArea(Board, A, AreaA);
        QueryKeepInArea(Board, B, AreaB);
        if Unary and (A.Count <> 1) then
        begin
            Result := 'ERROR: The expression must match exactly one object; object_a matched ' + IntToStr(A.Count);
            Exit;
        end;
        if (not Unary) and ((A.Count <> 1) or (B.Count <> 1)) then
        begin
            Result := 'ERROR: Each expression must match exactly one object; object_a matched ' +
                      IntToStr(A.Count) + ' and object_b ' + IntToStr(B.Count);
            Exit;
        end;
        AddJSONBoolean(Props, 'success', True);
        Props.Add('"object_a": ' + QueryObjectJSON(Board, A.Items[0]));
        if Unary then
            Rule := Board.FindDominantRuleForObject(A.Items[0], Kind)
        else
        begin
            Props.Add('"object_b": ' + QueryObjectJSON(Board, B.Items[0]));
            AddJSONNumber(Props, 'distance_mils', CoordToMils(Board.PrimPrimDistance(A.Items[0], B.Items[0])));
            Rule := Board.FindDominantRuleForObjectPair(A.Items[0], B.Items[0], Kind);
        end;
        if Rule = nil then
            AddJSONProperty(Props, 'rule', '')
        else
        begin
            AddJSONProperty(Props, 'rule', Rule.Name);
            AddJSONProperty(Props, 'descriptor', Rule.Descriptor);
            AddJSONInteger(Props, 'priority', Rule.Priority);
            AddJSONBoolean(Props, 'enabled', Rule.Enabled);
            AddJSONProperty(Props, 'scope1', Rule.Scope1Expression);
            if not Unary then AddJSONProperty(Props, 'scope2', Rule.Scope2Expression);
            if Kind = eRule_Clearance then
                AddJSONNumber(Props, 'gap_mils', CoordToMils(Rule.Gap));
            // ActualCheck is the DRC's own test of one object or pair, net
            // scope and clearance matrix included: nil when they comply. A
            // pair is tested both ways round, as check_silkscreen does: a
            // rule whose two scopes differ may only see it one way round.
            if RuleKindIsChecked(Kind) then
            begin
                if Unary then
                    Violation := Rule.ActualCheck(A.Items[0], nil)
                else
                begin
                    Violation := Rule.ActualCheck(A.Items[0], B.Items[0]);
                    if Violation = nil then
                        Violation := Rule.ActualCheck(B.Items[0], A.Items[0]);
                end;
                AddJSONBoolean(Props, 'passes', Violation = nil);
            end;
        end;
        AddJSONProperty(Props, 'board', Board.FileName);
        Result := BuildJSONObject(Props);
    finally
        A.Free;
        B.Free;
        Saved.Free;
        Props.Free;
    end;
end;

// Add the vias of place_vias to the focused board. Each record is
// 'x|y|diameter|hole|net|start layer|end layer', x and y in internal units
// relative to the board origin, sizes in internal units, an empty net for a
// via without one. Every net and layer is checked before anything is added,
// so a bad record adds nothing.
function CreateVias(Records: TStringList): String;
var
    Board       : IPCB_Board;
    Via         : IPCB_Via;
    Net         : IPCB_Net;
    i, Created  : Integer;
    NetName, StartName, EndName : String;
    StartLayer, EndLayer : Integer;
    ResultProps : TStringList;
begin
    Board := GetBoardSafe(0);
    if Board = nil then
    begin
        Result := 'ERROR: No PCB document is focused';
        Exit;
    end;

    for i := 0 to Records.Count - 1 do
    begin
        if Trim(Records[i]) = '' then Continue;
        NetName := GetFieldFromPipeString(Records[i], 4);
        StartName := GetFieldFromPipeString(Records[i], 5);
        EndName := GetFieldFromPipeString(Records[i], 6);
        if (NetName <> '') and (FindBoardNet(Board, NetName) = nil) then
        begin
            Result := 'ERROR: Net not found on the board: ' + NetName + ' (via ' + IntToStr(i + 1) + '); nothing was added';
            Exit;
        end;
        StartLayer := ResolveBoardLayer(Board, StartName);
        EndLayer := ResolveBoardLayer(Board, EndName);
        if (StartLayer = eNoLayer) or (EndLayer = eNoLayer) or
           (StartLayer > eBottomLayer) or (EndLayer > eBottomLayer) or (StartLayer = EndLayer) then
        begin
            Result := 'ERROR: A via spans two different signal layers of the board; ' + StartName + ' to ' +
                      EndName + ' is not that (via ' + IntToStr(i + 1) + '); nothing was added';
            Exit;
        end;
    end;

    // PostProcess must run whatever happens, or Altium's undo stays broken
    Created := 0;
    PCBServer.PreProcess;
    try
        for i := 0 to Records.Count - 1 do
        begin
            if Trim(Records[i]) = '' then Continue;
            NetName := GetFieldFromPipeString(Records[i], 4);
            StartLayer := ResolveBoardLayer(Board, GetFieldFromPipeString(Records[i], 5));
            EndLayer := ResolveBoardLayer(Board, GetFieldFromPipeString(Records[i], 6));
            Via := PCBServer.PCBObjectFactory(eViaObject, eNoDimension, eCreate_Default);
            Via.X := Board.XOrigin + StrToInt(GetFieldFromPipeString(Records[i], 0));
            Via.Y := Board.YOrigin + StrToInt(GetFieldFromPipeString(Records[i], 1));
            Via.Size := StrToInt(GetFieldFromPipeString(Records[i], 2));
            Via.HoleSize := StrToInt(GetFieldFromPipeString(Records[i], 3));
            // LowLayer and HighLayer are the lower and higher layer numbers
            // (Top Layer is 1), whichever end the caller named first
            if StartLayer < EndLayer then
            begin
                Via.LowLayer := StartLayer;
                Via.HighLayer := EndLayer;
            end
            else
            begin
                Via.LowLayer := EndLayer;
                Via.HighLayer := StartLayer;
            end;
            if NetName <> '' then
            begin
                Net := FindBoardNet(Board, NetName);
                Via.Net := Net;
            end;
            Board.AddPCBObject(Via);
            PCBServer.SendMessageToRobots(Board.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, Via.I_ObjectAddress);
            Created := Created + 1;
        end;
    finally
        PCBServer.PostProcess;
    end;
    Board.ViewManager_FullUpdate;

    ResultProps := TStringList.Create;
    try
        AddJSONBoolean(ResultProps, 'success', True);
        AddJSONInteger(ResultProps, 'created', Created);
        AddJSONProperty(ResultProps, 'board', Board.FileName);
        Result := BuildJSONObject(ResultProps);
    finally
        ResultProps.Free;
    end;
end;

// The focused board's routed copper for check_routing, one record per line
// in internal units: O|x origin|y origin, then T|layer|net|x1|y1|x2|y2|width
// for each track with a net outside components and polygon pours, A|layer|net
// for each such arc, and V|x|y|size and P|x|y|x size|y size for every via and
// pad (a corner under one is no corner). The server does the geometry: it is
// quicker in Python and can be tested there without Altium.
function GetRoutingGeometry(Dummy: Integer): String;
var
    Board    : IPCB_Board;
    Iterator : IPCB_BoardIterator;
    Prim     : IPCB_Primitive;
    Lines    : TStringList;
    NetName  : String;
begin
    Board := GetBoardSafe(0);
    if Board = nil then
    begin
        Result := 'ERROR: No PCB document is focused';
        Exit;
    end;

    Lines := TStringList.Create;
    try
        Lines.Add('O|' + IntToStr(Board.XOrigin) + '|' + IntToStr(Board.YOrigin));
        Iterator := Board.BoardIterator_Create;
        Iterator.AddFilter_ObjectSet(MkSet(eTrackObject, eArcObject, eViaObject, ePadObject));
        Iterator.AddFilter_LayerSet(AllLayers);
        Iterator.AddFilter_Method(eProcessAll);
        Prim := Iterator.FirstPCBObject;
        while Prim <> nil do
        begin
            NetName := '';
            if Prim.Net <> nil then NetName := Prim.Net.Name;
            if (Prim.ObjectId = eTrackObject) or (Prim.ObjectId = eArcObject) then
            begin
                if (NetName <> '') and (not Prim.InComponent) and (not Prim.InPolygon) then
                begin
                    if Prim.ObjectId = eTrackObject then
                        Lines.Add('T|' + Layer2String(Prim.Layer) + '|' + NetName + '|' +
                                  IntToStr(Prim.x1) + '|' + IntToStr(Prim.y1) + '|' +
                                  IntToStr(Prim.x2) + '|' + IntToStr(Prim.y2) + '|' + IntToStr(Prim.Width))
                    else
                        Lines.Add('A|' + Layer2String(Prim.Layer) + '|' + NetName);
                end;
            end
            else if Prim.ObjectId = eViaObject then
                Lines.Add('V|' + IntToStr(Prim.x) + '|' + IntToStr(Prim.y) + '|' + IntToStr(Prim.Size))
            else
                Lines.Add('P|' + IntToStr(Prim.x) + '|' + IntToStr(Prim.y) + '|' +
                          IntToStr(Prim.TopXSize) + '|' + IntToStr(Prim.TopYSize));
            Prim := Iterator.NextPCBObject;
        end;
        Board.BoardIterator_Destroy(Iterator);
        Result := Lines.Text;
    finally
        Lines.Free;
    end;
end;

// Add the tracks of route_track to the focused board. Segments holds
// 'x1|y1|x2|y2' records in internal units relative to the board origin, as
// the server computed them, so every segment is exactly 0, 45 or 90 degrees.
function CreateRouteTracks(NetName: String; LayerName: String; Width: Integer; Segments: TStringList): String;
var
    Board       : IPCB_Board;
    Iterator    : IPCB_BoardIterator;
    Net         : IPCB_Net;
    Prim        : IPCB_Primitive;
    Track       : IPCB_Track;
    Layer       : Integer;
    i, Created  : Integer;
    ResultProps : TStringList;
begin
    Board := GetBoardSafe(0);
    if Board = nil then
    begin
        Result := 'ERROR: No PCB document is focused';
        Exit;
    end;
    Layer := String2Layer(LayerName);
    if Layer = eNoLayer then
    begin
        Result := 'ERROR: Unknown layer: ' + LayerName;
        Exit;
    end;

    Net := nil;
    Iterator := Board.BoardIterator_Create;
    Iterator.AddFilter_ObjectSet(MkSet(eNetObject));
    Iterator.AddFilter_LayerSet(AllLayers);
    Iterator.AddFilter_Method(eProcessAll);
    Prim := Iterator.FirstPCBObject;
    while (Prim <> nil) and (Net = nil) do
    begin
        if UpperCase(Prim.Name) = UpperCase(NetName) then Net := Prim;
        Prim := Iterator.NextPCBObject;
    end;
    Board.BoardIterator_Destroy(Iterator);
    if Net = nil then
    begin
        Result := 'ERROR: Net not found on the board: ' + NetName;
        Exit;
    end;

    // PostProcess must run whatever happens, or Altium's undo stays broken
    Created := 0;
    PCBServer.PreProcess;
    try
        for i := 0 to Segments.Count - 1 do
        begin
            if Trim(Segments[i]) <> '' then
            begin
                Track := PCBServer.PCBObjectFactory(eTrackObject, eNoDimension, eCreate_Default);
                Track.x1 := Board.XOrigin + StrToInt(GetFieldFromPipeString(Segments[i], 0));
                Track.y1 := Board.YOrigin + StrToInt(GetFieldFromPipeString(Segments[i], 1));
                Track.x2 := Board.XOrigin + StrToInt(GetFieldFromPipeString(Segments[i], 2));
                Track.y2 := Board.YOrigin + StrToInt(GetFieldFromPipeString(Segments[i], 3));
                Track.Width := Width;
                Track.Layer := Layer;
                Track.Net := Net;
                Board.AddPCBObject(Track);
                PCBServer.SendMessageToRobots(Board.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, Track.I_ObjectAddress);
                Created := Created + 1;
            end;
        end;
    finally
        PCBServer.PostProcess;
    end;
    Board.ViewManager_FullUpdate;

    ResultProps := TStringList.Create;
    try
        AddJSONBoolean(ResultProps, 'success', True);
        AddJSONInteger(ResultProps, 'created', Created);
        AddJSONProperty(ResultProps, 'net', Net.Name);
        AddJSONProperty(ResultProps, 'layer', Layer2String(Layer));
        Result := BuildJSONObject(ResultProps);
    finally
        ResultProps.Free;
    end;
end;


// The 'x|y' records of an outline (internal units, relative to the board
// origin) as a contour in board coordinates
function OutlineContour(Board: IPCB_Board; Points: TStringList): IPCB_Contour;
var
    i : Integer;
begin
    Result := PCBServer.PCBContourFactory;
    for i := 0 to Points.Count - 1 do
        if Trim(Points[i]) <> '' then
            Result.AddPoint(Board.XOrigin + StrToInt(GetFieldFromPipeString(Points[i], 0)),
                            Board.YOrigin + StrToInt(GetFieldFromPipeString(Points[i], 1)));
end;

// What a polygon poured: its regions, one per separate piece of solid
// copper, or with Regions False the tracks and arcs of a hatched pour
function PolygonPourCount(Poly: IPCB_Polygon; Regions: Boolean): Integer;
var
    Iterator : IPCB_GroupIterator;
    Prim     : IPCB_Primitive;
begin
    Result := 0;
    Iterator := Poly.GroupIterator_Create;
    if Regions then
        Iterator.AddFilter_ObjectSet(MkSet(eRegionObject))
    else
        Iterator.AddFilter_ObjectSet(MkSet(eTrackObject, eArcObject));
    Prim := Iterator.FirstPCBObject;
    while Prim <> nil do
    begin
        Result := Result + 1;
        Prim := Iterator.NextPCBObject;
    end;
    Poly.GroupIterator_Destroy(Iterator);
end;

// Add a polygon pour with the outline in Points to the focused board and pour
// it. Hatch is solid, hatched_45, hatched_90 or none (outline only); TrackSize
// and Grid (internal units, 0 for Altium's default) shape a hatched pour.
function CreatePolygonPour(LayerName: String; NetName: String; PolyName: String; Hatch: String;
                           PourOverSameNet: Boolean; RemoveDead: Boolean; TrackSize: Integer;
                           Grid: Integer; Points: TStringList): String;
var
    Board  : IPCB_Board;
    Poly   : IPCB_Polygon;
    Net    : IPCB_Net;
    Seg    : TPolySegment;
    Layer, i, n : Integer;
    Props  : TStringList;
begin
    Board := GetBoardSafe(0);
    if Board = nil then
    begin
        Result := 'ERROR: No PCB document is focused';
        Exit;
    end;
    Layer := ResolveBoardLayer(Board, LayerName);
    if (Layer = eNoLayer) or (Layer > eBottomLayer) then
    begin
        Result := 'ERROR: ' + LayerName + ' is not a signal layer of this board; a pour goes on one';
        Exit;
    end;
    Net := nil;
    if NetName <> '' then
    begin
        Net := FindBoardNet(Board, NetName);
        if Net = nil then
        begin
            Result := 'ERROR: Net not found on the board: ' + NetName;
            Exit;
        end;
    end;

    // PostProcess must run whatever happens, or Altium's undo stays broken
    PCBServer.PreProcess;
    try
        Poly := PCBServer.PCBObjectFactory(ePolyObject, eNoDimension, eCreate_Default);
        Poly.Layer := Layer;
        if Hatch = 'hatched_45' then Poly.PolyHatchStyle := ePolyHatch45
        else if Hatch = 'hatched_90' then Poly.PolyHatchStyle := ePolyHatch90
        else if Hatch = 'none' then Poly.PolyHatchStyle := ePolyNoHatch
        else Poly.PolyHatchStyle := ePolySolid;
        if TrackSize > 0 then Poly.TrackSize := TrackSize;
        if Grid > 0 then Poly.Grid := Grid;
        // A TPolySegment must be created with ':= TPolySegment' before its
        // fields can be written
        n := 0;
        for i := 0 to Points.Count - 1 do
            if Trim(Points[i]) <> '' then n := n + 1;
        Poly.PointCount := n;
        Seg := TPolySegment;
        Seg.Kind := ePolySegmentLine;
        n := 0;
        for i := 0 to Points.Count - 1 do
            if Trim(Points[i]) <> '' then
            begin
                Seg.vx := Board.XOrigin + StrToInt(GetFieldFromPipeString(Points[i], 0));
                Seg.vy := Board.YOrigin + StrToInt(GetFieldFromPipeString(Points[i], 1));
                Poly.Segments[n] := Seg;
                n := n + 1;
            end;
        if Net <> nil then Poly.Net := Net;
        if PourOverSameNet then
            Poly.PourOver := ePolygonPourOver_SameNet
        else
            Poly.PourOver := ePolygonPourOver_None;
        Poly.RemoveDead := RemoveDead;
        if PolyName <> '' then Poly.Name := PolyName;
        Board.AddPCBObject(Poly);
        PCBServer.SendMessageToRobots(Board.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, Poly.I_ObjectAddress);
        Poly.Rebuild;
    finally
        PCBServer.PostProcess;
    end;
    Board.ViewManager_FullUpdate;

    Props := TStringList.Create;
    try
        AddJSONBoolean(Props, 'success', True);
        AddJSONProperty(Props, 'name', Poly.Name);
        AddJSONProperty(Props, 'layer', Layer2String(Poly.Layer));
        if Net <> nil then AddJSONProperty(Props, 'net', Net.Name) else AddJSONProperty(Props, 'net', '');
        AddJSONInteger(Props, 'corners', Poly.PointCount);
        AddJSONInteger(Props, 'poured_regions', PolygonPourCount(Poly, True));
        AddJSONInteger(Props, 'hatch_tracks', PolygonPourCount(Poly, False));
        AddJSONProperty(Props, 'board', Board.FileName);
        Result := BuildJSONObject(Props);
    finally
        Props.Free;
    end;
end;

// One cutout region with the outline in Points
function AddCutoutRegion(Board: IPCB_Board; Points: TStringList; Layer: Integer; Kind: Integer): IPCB_Region;
begin
    Result := PCBServer.PCBObjectFactory(eRegionObject, eNoDimension, eCreate_Default);
    Result.SetOutlineContour(OutlineContour(Board, Points));
    Result.Layer := Layer;
    Result.Kind := Kind;
    Board.AddPCBObject(Result);
    PCBServer.SendMessageToRobots(Board.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, Result.I_ObjectAddress);
end;

// Add a cutout with the outline in Points to the focused board, then repour
// the polygons it overlaps so their copper leaves it free. Kind 'polygon' is
// a polygon pour cutout on LayerName, or on every signal layer for 'all';
// kind 'board' cuts the board itself.
function CreateCutout(Kind: String; LayerName: String; Points: TStringList): String;
var
    Board    : IPCB_Board;
    Iterator : IPCB_BoardIterator;
    LayerIt  : IPCB_LayerObjectIterator;
    Poly     : IPCB_Polygon;
    Region   : IPCB_Region;
    Rect, PolyRect : TCoordRect;
    Layer, Created, Repoured : Integer;
    Layers, Props : TStringList;
begin
    Board := GetBoardSafe(0);
    if Board = nil then
    begin
        Result := 'ERROR: No PCB document is focused';
        Exit;
    end;
    Layers := TStringList.Create;
    Props := TStringList.Create;
    try
        if Kind = 'board' then
            Layers.Add(IntToStr(eMultiLayer))
        else if UpperCase(Trim(LayerName)) = 'ALL' then
        begin
            LayerIt := Board.ElectricalLayerIterator;
            while LayerIt.Next do
                if LayerIt.LayerObject.LayerID <= eBottomLayer then
                    Layers.Add(IntToStr(LayerIt.LayerObject.LayerID));
        end
        else
        begin
            Layer := ResolveBoardLayer(Board, LayerName);
            if (Layer = eNoLayer) or (Layer > eBottomLayer) then
            begin
                Result := 'ERROR: ' + LayerName + ' is not a signal layer of this board';
                Exit;
            end;
            Layers.Add(IntToStr(Layer));
        end;

        Created := 0;
        Repoured := 0;
        // PostProcess must run whatever happens, or Altium's undo stays broken
        PCBServer.PreProcess;
        try
            for Layer := 0 to Layers.Count - 1 do
            begin
                if Kind = 'board' then
                    Region := AddCutoutRegion(Board, Points, eMultiLayer, eRegionKind_BoardCutout)
                else
                    Region := AddCutoutRegion(Board, Points, StrToInt(Layers[Layer]), eRegionKind_Cutout);
                Rect := Region.BoundingRectangle;
                Created := Created + 1;
            end;

            // Repour every polygon the cutout overlaps on its layers
            Iterator := Board.BoardIterator_Create;
            Iterator.AddFilter_ObjectSet(MkSet(ePolyObject));
            Iterator.AddFilter_LayerSet(AllLayers);
            Iterator.AddFilter_Method(eProcessAll);
            Poly := Iterator.FirstPCBObject;
            while Poly <> nil do
            begin
                PolyRect := Poly.BoundingRectangle;
                if ((Kind = 'board') or (Layers.IndexOf(IntToStr(Poly.Layer)) >= 0)) and
                   (PolyRect.Left < Rect.Right) and (Rect.Left < PolyRect.Right) and
                   (PolyRect.Bottom < Rect.Top) and (Rect.Bottom < PolyRect.Top) then
                begin
                    Poly.Rebuild;
                    Repoured := Repoured + 1;
                end;
                Poly := Iterator.NextPCBObject;
            end;
            Board.BoardIterator_Destroy(Iterator);
        finally
            PCBServer.PostProcess;
        end;
        Board.ViewManager_FullUpdate;

        AddJSONBoolean(Props, 'success', True);
        AddJSONProperty(Props, 'kind', Kind);
        AddJSONInteger(Props, 'regions', Created);
        AddJSONInteger(Props, 'polygons_repoured', Repoured);
        AddJSONProperty(Props, 'board', Board.FileName);
        Result := BuildJSONObject(Props);
    finally
        Layers.Free;
        Props.Free;
    end;
end;

// Whether Poly overlaps Area ('x1|y1|x2|y2' in internal units from the board
// origin; '' for anywhere). Overlap, not inside: a whole-board pour must be
// repoured after an edit anywhere on it.
function PolygonInArea(Board: IPCB_Board; Poly: IPCB_Polygon; Area: String): Boolean;
var
    R : TCoordRect;
    X1, Y1, X2, Y2 : Integer;
begin
    Result := True;
    if Area = '' then Exit;
    X1 := Board.XOrigin + StrToInt(GetFieldFromPipeString(Area, 0));
    Y1 := Board.YOrigin + StrToInt(GetFieldFromPipeString(Area, 1));
    X2 := Board.XOrigin + StrToInt(GetFieldFromPipeString(Area, 2));
    Y2 := Board.YOrigin + StrToInt(GetFieldFromPipeString(Area, 3));
    R := Poly.BoundingRectangle;
    Result := (R.Left < X2) and (X1 < R.Right) and (R.Bottom < Y2) and (Y1 < R.Top);
end;

// Repour the polygons of the focused board, all of them or those matching
// the filters: Names is '|NAME1|NAME2|' (any case; names need not be unique),
// NetName and LayerName a net and a signal layer, each '' for any, Area as
// for PolygonInArea. A shelved
// polygon (Poured false) is skipped unless IncludeShelved: repouring it
// unshelves it. Rebuild is what repours; PCB:PolygonActions RepourAll does
// nothing when run from a script.
function RepourPolygons(Names: String; NetName: String; LayerName: String; Area: String;
                        IncludeShelved: Boolean): String;
var
    Board    : IPCB_Board;
    Iterator : IPCB_BoardIterator;
    Poly     : IPCB_Polygon;
    Net      : IPCB_Net;
    Layer, Repoured, i : Integer;
    Done, Shelved, Item, Props, Order : TStringList;
begin
    Board := GetBoardSafe(0);
    if Board = nil then
    begin
        Result := 'ERROR: No PCB document is focused';
        Exit;
    end;
    Net := nil;
    if NetName <> '' then
    begin
        Net := FindBoardNet(Board, NetName);
        if Net = nil then
        begin
            Result := 'ERROR: Net not found on the board: ' + NetName;
            Exit;
        end;
    end;
    Layer := eNoLayer;
    if LayerName <> '' then
    begin
        Layer := ResolveBoardLayer(Board, LayerName);
        if (Layer = eNoLayer) or (Layer > eBottomLayer) then
        begin
            Result := 'ERROR: ' + LayerName + ' is not a signal layer of this board';
            Exit;
        end;
    end;
    Names := UpperCase(Names);

    Done := TStringList.Create;
    Shelved := TStringList.Create;
    Item := TStringList.Create;
    Props := TStringList.Create;
    // The polygons to repour, sorted by pour index: where pours of different
    // nets overlap, the order decides which gets the copper, and Altium's own
    // Repour All goes by the Polygon Pour Order (the pour index)
    Order := TStringList.Create;
    Order.Sorted := True;
    try
        Repoured := 0;
        // PostProcess must run whatever happens, or Altium's undo stays broken
        PCBServer.PreProcess;
        try
            Iterator := Board.BoardIterator_Create;
            Iterator.AddFilter_ObjectSet(MkSet(ePolyObject));
            Iterator.AddFilter_LayerSet(AllLayers);
            Iterator.AddFilter_Method(eProcessAll);
            Poly := Iterator.FirstPCBObject;
            while Poly <> nil do
            begin
                if ((Names = '') or (Pos('|' + UpperCase(Poly.Name) + '|', Names) > 0)) and
                   ((Net = nil) or (Poly.Net = Net)) and
                   ((Layer = eNoLayer) or (Poly.Layer = Layer)) and
                   PolygonInArea(Board, Poly, Area) then
                begin
                    if (not Poly.Poured) and (not IncludeShelved) then
                        Shelved.Add('"' + JSONEscapeString(Poly.Name + ' (' + Layer2String(Poly.Layer) + ')') + '"')
                    else
                        Order.AddObject(IntToStr(1000000000 + Poly.PourIndex) + '|' + IntToStr(100000000 + Order.Count), Poly);
                end;
                Poly := Iterator.NextPCBObject;
            end;
            Board.BoardIterator_Destroy(Iterator);
            for i := 0 to Order.Count - 1 do
            begin
                Poly := Order.Objects[i];
                Poly.Rebuild;
                Repoured := Repoured + 1;
                Item.Clear;
                AddJSONProperty(Item, 'name', Poly.Name);
                AddJSONProperty(Item, 'layer', Layer2String(Poly.Layer));
                if Poly.Net <> nil then AddJSONProperty(Item, 'net', Poly.Net.Name) else AddJSONProperty(Item, 'net', '');
                AddJSONInteger(Item, 'pour_index', Poly.PourIndex);
                AddJSONInteger(Item, 'poured_regions', PolygonPourCount(Poly, True));
                AddJSONInteger(Item, 'hatch_tracks', PolygonPourCount(Poly, False));
                Done.Add(BuildJSONObject(Item, 1));
            end;
        finally
            PCBServer.PostProcess;
        end;
        Board.ViewManager_FullUpdate;

        AddJSONBoolean(Props, 'success', True);
        AddJSONInteger(Props, 'repoured', Repoured);
        if Done.Count > 0 then Props.Add(BuildJSONArray(Done, 'polygons')) else Props.Add('"polygons": []');
        if Shelved.Count > 0 then Props.Add(BuildJSONArray(Shelved, 'shelved_skipped')) else Props.Add('"shelved_skipped": []');
        AddJSONProperty(Props, 'board', Board.FileName);
        Result := BuildJSONObject(Props);
    finally
        Done.Free;
        Shelved.Free;
        Item.Free;
        Props.Free;
        Order.Free;
    end;
end;

// What each matching polygon poured, for get_pour_report: its pieces of solid
// copper (regions) and their sizes, so islands, slivers and pours with no net
// or no copper stand out. A region's Area is its outline only, holes not
// counted, so piece areas are upper bounds.
function PourReport(Names: String; NetName: String; LayerName: String; Area: String; SliverArea: Double): String;
var
    Board    : IPCB_Board;
    Iterator : IPCB_BoardIterator;
    GroupIt  : IPCB_GroupIterator;
    Poly     : IPCB_Polygon;
    Region   : IPCB_Region;
    Net      : IPCB_Net;
    Layer, Pieces, Slivers, Count : Integer;
    A, Smallest, Total : Double;
    Flags    : String;
    Items, Item, Props : TStringList;
begin
    Board := GetBoardSafe(0);
    if Board = nil then
    begin
        Result := 'ERROR: No PCB document is focused';
        Exit;
    end;
    Net := nil;
    if NetName <> '' then
    begin
        Net := FindBoardNet(Board, NetName);
        if Net = nil then
        begin
            Result := 'ERROR: Net not found on the board: ' + NetName;
            Exit;
        end;
    end;
    Layer := eNoLayer;
    if LayerName <> '' then
    begin
        Layer := ResolveBoardLayer(Board, LayerName);
        if (Layer = eNoLayer) or (Layer > eBottomLayer) then
        begin
            Result := 'ERROR: ' + LayerName + ' is not a signal layer of this board';
            Exit;
        end;
    end;
    Names := UpperCase(Names);
    Items := TStringList.Create;
    Item := TStringList.Create;
    Props := TStringList.Create;
    try
        Count := 0;
        Iterator := Board.BoardIterator_Create;
        Iterator.AddFilter_ObjectSet(MkSet(ePolyObject));
        Iterator.AddFilter_LayerSet(AllLayers);
        Iterator.AddFilter_Method(eProcessAll);
        Poly := Iterator.FirstPCBObject;
        while Poly <> nil do
        begin
            if ((Names = '') or (Pos('|' + UpperCase(Poly.Name) + '|', Names) > 0)) and
               ((Net = nil) or (Poly.Net = Net)) and
               ((Layer = eNoLayer) or (Poly.Layer = Layer)) and
               PolygonInArea(Board, Poly, Area) then
            begin
                Count := Count + 1;
                Pieces := 0;
                Slivers := 0;
                Smallest := -1;
                Total := 0;
                GroupIt := Poly.GroupIterator_Create;
                GroupIt.AddFilter_ObjectSet(MkSet(eRegionObject));
                Region := GroupIt.FirstPCBObject;
                while Region <> nil do
                begin
                    Pieces := Pieces + 1;
                    A := Region.Area / 100000000.0;     // internal units squared -> sq mil
                    Total := Total + A;
                    if (Smallest < 0) or (A < Smallest) then Smallest := A;
                    if A < SliverArea then Slivers := Slivers + 1;
                    Region := GroupIt.NextPCBObject;
                end;
                Poly.GroupIterator_Destroy(GroupIt);

                Flags := '';
                if not Poly.Poured then Flags := Flags + '"shelved", '
                else if (Pieces = 0) and (PolygonPourCount(Poly, False) = 0) then Flags := Flags + '"no_copper", ';
                // only on copper: a pour on a mask layer has no net by nature
                if (Poly.Net = nil) and (Poly.Layer <= eBottomLayer) then Flags := Flags + '"no_net", ';
                if Pieces > 1 then Flags := Flags + '"islands", ';
                if Slivers > 0 then Flags := Flags + '"slivers", ';
                if Flags <> '' then Flags := Copy(Flags, 1, Length(Flags) - 2);

                Item.Clear;
                AddJSONProperty(Item, 'name', Poly.Name);
                AddJSONProperty(Item, 'layer', Layer2String(Poly.Layer));
                if Poly.Net <> nil then AddJSONProperty(Item, 'net', Poly.Net.Name) else AddJSONProperty(Item, 'net', '');
                AddJSONBoolean(Item, 'shelved', not Poly.Poured);
                AddJSONInteger(Item, 'pieces', Pieces);
                AddJSONInteger(Item, 'hatch_tracks', PolygonPourCount(Poly, False));
                if Pieces > 0 then
                begin
                    AddJSONNumber(Item, 'smallest_piece_sq_mils', Smallest);
                    AddJSONNumber(Item, 'copper_sq_mils', Total);
                end;
                AddJSONInteger(Item, 'slivers', Slivers);
                Item.Add('"flags": [' + Flags + ']');
                Items.Add(BuildJSONObject(Item, 1));
            end;
            Poly := Iterator.NextPCBObject;
        end;
        Board.BoardIterator_Destroy(Iterator);

        AddJSONBoolean(Props, 'success', True);
        AddJSONInteger(Props, 'polygons_checked', Count);
        if Items.Count > 0 then Props.Add(BuildJSONArray(Items, 'polygons')) else Props.Add('"polygons": []');
        AddJSONProperty(Props, 'board', Board.FileName);
        Result := BuildJSONObject(Props);
    finally
        Items.Free;
        Item.Free;
        Props.Free;
    end;
end;

// ---------------------------------------------------------------- rule edits
// IPCB_Rule.Priority cannot be set from a script (the setter raises and
// changes nothing), but a rule removed and added again becomes priority 1
// of its kind while the others keep their order; MoveRuleTo builds any
// order from that. A rule changed between PCBM_BeginModify and
// PCBM_EndModify (needed for undo) also jumps to priority 1, so an edit
// moves it back where it was.

// create_pcb_rule's name for a rule's kind, or '' for kinds it cannot edit
function RuleKindWord(Rule: IPCB_Rule): String;
begin
    case Rule.RuleKind of
        eRule_Clearance:            Result := 'clearance';
        eRule_MaxMinWidth:          Result := 'width';
        eRule_MaxMinHoleSize:       Result := 'hole_size';
        eRule_RoutingViaStyle:      Result := 'routing_via';
        eRule_SolderMaskExpansion:  Result := 'solder_mask_expansion';
        eRule_PasteMaskExpansion:   Result := 'paste_mask_expansion';
        eRule_PolygonConnectStyle:  Result := 'polygon_connect';
    else
        Result := '';
    end;
end;

// The rules of Kind, highest priority (1) first
procedure RulesOfKindByPriority(Board: IPCB_Board; Kind: Integer; Rules: TInterfaceList);
var
    Iterator : IPCB_BoardIterator;
    Rule     : IPCB_Rule;
    i, j     : Integer;
begin
    Iterator := Board.BoardIterator_Create;
    Iterator.AddFilter_ObjectSet(MkSet(eRuleObject));
    Iterator.AddFilter_LayerSet(AllLayers);
    Iterator.AddFilter_Method(eProcessAll);
    Rule := Iterator.FirstPCBObject;
    while Rule <> nil do
    begin
        if Rule.RuleKind = Kind then
        begin
            j := Rules.Count;
            for i := Rules.Count - 1 downto 0 do
                if Rules.Items[i].Priority > Rule.Priority then j := i;
            Rules.Insert(j, Rule);
        end;
        Rule := Iterator.NextPCBObject;
    end;
    Board.BoardIterator_Destroy(Iterator);
end;

// "rules_of_kind": [{"name": ..., "priority": ...}, ...]
function RuleOrderJSON(Board: IPCB_Board; Kind: Integer): String;
var
    Rules : TInterfaceList;
    Items, Item : TStringList;
    i     : Integer;
begin
    Rules := TInterfaceList.Create;
    Items := TStringList.Create;
    Item := TStringList.Create;
    try
        RulesOfKindByPriority(Board, Kind, Rules);
        for i := 0 to Rules.Count - 1 do
        begin
            Item.Clear;
            AddJSONProperty(Item, 'name', Rules.Items[i].Name);
            AddJSONInteger(Item, 'priority', Rules.Items[i].Priority);
            Items.Add(BuildJSONObject(Item, 1));
        end;
        Result := BuildJSONArray(Items, 'rules_of_kind');
    finally
        Rules.Free;
        Items.Free;
        Item.Free;
    end;
end;

// The board's first signal layer
function FirstSignalLayer(Board: IPCB_Board): Integer;
var
    LayerIt : IPCB_LayerObjectIterator;
begin
    Result := eTopLayer;
    LayerIt := Board.ElectricalLayerIterator;
    while LayerIt.Next do
        if LayerIt.LayerObject.LayerID <= eBottomLayer then
        begin
            Result := LayerIt.LayerObject.LayerID;
            Exit;
        end;
end;

// Whether a width rule has the same minimum, maximum and preferred width on
// every signal layer of the board
function WidthRuleUniform(Board: IPCB_Board; Width: IPCB_MaxMinWidthConstraint): Boolean;
var
    LayerIt : IPCB_LayerObjectIterator;
    First, L : Integer;
begin
    Result := True;
    First := FirstSignalLayer(Board);
    LayerIt := Board.ElectricalLayerIterator;
    while LayerIt.Next do
    begin
        L := LayerIt.LayerObject.LayerID;
        if L <= eBottomLayer then
            if (Width.MinWidth(L) <> Width.MinWidth(First)) or (Width.MaxWidth(L) <> Width.MaxWidth(First)) or
               (Width.FavoredWidth(L) <> Width.FavoredWidth(First)) then
                Result := False;
    end;
end;

// The rule's values under create_pcb_rule's names, in mils, so an edit can
// be checked against them. A width rule gives those of the first signal
// layer, and per_layer_widths when the board's signal layers differ.
procedure RuleValuesJSON(Board: IPCB_Board; Rule: IPCB_Rule; Props: TStringList);
var
    Item  : TStringList;
    L     : Integer;
    Style : String;
begin
    Item := TStringList.Create;
    try
        case Rule.RuleKind of
            eRule_Clearance:
                AddJSONNumber(Item, 'gap_mils', CoordToMils(Rule.Gap));
            eRule_MaxMinWidth:
                begin
                    L := FirstSignalLayer(Board);
                    AddJSONNumber(Item, 'min_mils', CoordToMils(Rule.MinWidth(L)));
                    AddJSONNumber(Item, 'max_mils', CoordToMils(Rule.MaxWidth(L)));
                    AddJSONNumber(Item, 'preferred_mils', CoordToMils(Rule.FavoredWidth(L)));
                    AddJSONBoolean(Props, 'per_layer_widths', not WidthRuleUniform(Board, Rule));
                end;
            eRule_MaxMinHoleSize:
                begin
                    AddJSONNumber(Item, 'min_mils', CoordToMils(Rule.MinLimit));
                    AddJSONNumber(Item, 'max_mils', CoordToMils(Rule.MaxLimit));
                end;
            eRule_RoutingViaStyle:
                begin
                    AddJSONNumber(Item, 'min_diameter_mils', CoordToMils(Rule.MinWidth));
                    AddJSONNumber(Item, 'max_diameter_mils', CoordToMils(Rule.MaxWidth));
                    AddJSONNumber(Item, 'preferred_diameter_mils', CoordToMils(Rule.PreferedWidth));
                    AddJSONNumber(Item, 'min_hole_mils', CoordToMils(Rule.MinHoleWidth));
                    AddJSONNumber(Item, 'max_hole_mils', CoordToMils(Rule.MaxHoleWidth));
                    AddJSONNumber(Item, 'preferred_hole_mils', CoordToMils(Rule.PreferedHoleWidth));
                end;
            eRule_SolderMaskExpansion, eRule_PasteMaskExpansion:
                AddJSONNumber(Item, 'expansion_mils', CoordToMils(Rule.Expansion));
            eRule_PolygonConnectStyle:
                begin
                    if Rule.ConnectStyle = eDirectConnectToPlane then Style := 'direct'
                    else if Rule.ConnectStyle = eNoConnect then Style := 'none'
                    else Style := 'relief';
                    AddJSONProperty(Item, 'connect_style', Style);
                    AddJSONNumber(Item, 'conductor_width_mils', CoordToMils(Rule.ReliefConductorWidth));
                    AddJSONNumber(Item, 'air_gap_mils', CoordToMils(Rule.ReliefAirGap));
                    AddJSONInteger(Item, 'entries', Rule.ReliefEntries);
                end;
        end;
        Props.Add('"values": ' + BuildJSONObject(Item, 1));
    finally
        Item.Free;
    end;
end;

procedure RuleStateJSON(Board: IPCB_Board; Rule: IPCB_Rule; Props: TStringList);
begin
    AddJSONProperty(Props, 'name', Rule.Name);
    AddJSONProperty(Props, 'kind', RuleKindWord(Rule));
    RuleValuesJSON(Board, Rule, Props);
    AddJSONProperty(Props, 'descriptor', Rule.Descriptor);
    AddJSONProperty(Props, 'scope1', Rule.Scope1Expression);
    AddJSONProperty(Props, 'scope2', Rule.Scope2Expression);
    AddJSONProperty(Props, 'comment', Rule.Comment);
    AddJSONBoolean(Props, 'enabled', Rule.Enabled);
    AddJSONInteger(Props, 'priority', Rule.Priority);
    Props.Add(RuleOrderJSON(Board, Rule.RuleKind));
    AddJSONProperty(Props, 'board', Board.FileName);
end;

// Sets the values given in Values (create_pcb_rule's keys) on Rule; values
// not given keep what the rule has. A width changes only on the layers that
// had the old one (that of the first signal layer): the caller refuses
// rules whose signal layers differ, so layers the board does not use and
// that hold something else keep it.
procedure ApplyRuleValues(Board: IPCB_Board; Rule: IPCB_Rule; Values: TStringList);
var
    Clear    : IPCB_ClearanceConstraint;
    Width    : IPCB_MaxMinWidthConstraint;
    Hole     : IPCB_MaxMinHoleSizeConstraint;
    Via      : IPCB_RoutingViaStyleRule;
    SMask    : IPCB_SolderMaskExpansionRule;
    PMask    : IPCB_PasteMaskExpansionRule;
    Connect  : IPCB_PolygonConnectStyleRule;
    L, First : Integer;
    OldMin, OldMax, OldPreferred : Integer;
    Style    : String;
begin
    case Rule.RuleKind of
        eRule_Clearance:
            begin
                Clear := Rule;
                if RuleHasValue(Values, 'gap') then Clear.Gap := RuleValue(Values, 'gap', 0);
            end;
        eRule_MaxMinWidth:
            begin
                Width := Rule;
                First := FirstSignalLayer(Board);
                OldMin := Width.MinWidth(First);
                OldMax := Width.MaxWidth(First);
                OldPreferred := Width.FavoredWidth(First);
                for L := eTopLayer to eBottomLayer do
                begin
                    if RuleHasValue(Values, 'min') and (Width.MinWidth(L) = OldMin) then
                        Width.MinWidth(L) := RuleValue(Values, 'min', 0);
                    if RuleHasValue(Values, 'max') and (Width.MaxWidth(L) = OldMax) then
                        Width.MaxWidth(L) := RuleValue(Values, 'max', 0);
                    if RuleHasValue(Values, 'preferred') and (Width.FavoredWidth(L) = OldPreferred) then
                        Width.FavoredWidth(L) := RuleValue(Values, 'preferred', 0);
                end;
            end;
        eRule_MaxMinHoleSize:
            begin
                Hole := Rule;
                if RuleHasValue(Values, 'min') then Hole.MinLimit := RuleValue(Values, 'min', 0);
                if RuleHasValue(Values, 'max') then Hole.MaxLimit := RuleValue(Values, 'max', 0);
            end;
        eRule_RoutingViaStyle:
            begin
                Via := Rule;
                if RuleHasValue(Values, 'min_diameter') then Via.MinWidth := RuleValue(Values, 'min_diameter', 0);
                if RuleHasValue(Values, 'max_diameter') then Via.MaxWidth := RuleValue(Values, 'max_diameter', 0);
                if RuleHasValue(Values, 'preferred_diameter') then Via.PreferedWidth := RuleValue(Values, 'preferred_diameter', 0);
                if RuleHasValue(Values, 'min_hole') then Via.MinHoleWidth := RuleValue(Values, 'min_hole', 0);
                if RuleHasValue(Values, 'max_hole') then Via.MaxHoleWidth := RuleValue(Values, 'max_hole', 0);
                if RuleHasValue(Values, 'preferred_hole') then Via.PreferedHoleWidth := RuleValue(Values, 'preferred_hole', 0);
            end;
        eRule_SolderMaskExpansion:
            begin
                SMask := Rule;
                if RuleHasValue(Values, 'expansion') then SMask.Expansion := RuleValue(Values, 'expansion', 0);
            end;
        eRule_PasteMaskExpansion:
            begin
                PMask := Rule;
                if RuleHasValue(Values, 'expansion') then PMask.Expansion := RuleValue(Values, 'expansion', 0);
            end;
        eRule_PolygonConnectStyle:
            begin
                Connect := Rule;
                if RuleHasValue(Values, 'connect_style') then
                begin
                    Style := Values.Values['connect_style'];
                    if Style = 'direct' then Connect.ConnectStyle := eDirectConnectToPlane
                    else if Style = 'none' then Connect.ConnectStyle := eNoConnect
                    else Connect.ConnectStyle := eReliefConnectToPlane;
                end;
                if RuleHasValue(Values, 'conductor_width') then
                    Connect.ReliefConductorWidth := RuleValue(Values, 'conductor_width', 0);
                if RuleHasValue(Values, 'air_gap') then
                    Connect.ReliefAirGap := RuleValue(Values, 'air_gap', 0);
                if RuleHasValue(Values, 'entries') then
                    Connect.ReliefEntries := RuleValue(Values, 'entries', 4);
            end;
    end;
end;

// pcb_rule_info: a rule's kind, scopes, priority and the order of its kind
function PcbRuleInfo(Name: String): String;
var
    Board : IPCB_Board;
    Rule  : IPCB_Rule;
    Props : TStringList;
begin
    Board := GetBoardSafe(0);
    if Board = nil then begin Result := 'ERROR: No PCB document is focused'; Exit; end;
    Rule := FindRuleByName(Board, Name);
    if Rule = nil then begin Result := 'ERROR: No rule named ' + Name; Exit; end;
    Props := TStringList.Create;
    try
        AddJSONBoolean(Props, 'success', True);
        RuleStateJSON(Board, Rule, Props);
        Result := BuildJSONObject(Props);
    finally
        Props.Free;
    end;
end;

// Takes Rule off the board and adds it again: it becomes priority 1 of its
// kind, the others keep their order
procedure RuleToTop(Board: IPCB_Board; Rule: IPCB_Rule);
begin
    Board.RemovePCBObject(Rule);
    PCBServer.SendMessageToRobots(Board.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, Rule.I_ObjectAddress);
    Board.AddPCBObject(Rule);
    PCBServer.SendMessageToRobots(Board.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, Rule.I_ObjectAddress);
end;

// Moves Rule to priority Wanted of its kind (clamped to the rules there
// are); returns how many rules were added again. The rules that should
// stay above it are added again after it, lowest first, so they end up
// above it in their old order; the rules below are not touched. Call
// between PreProcess and PostProcess.
function MoveRuleTo(Board: IPCB_Board; Rule: IPCB_Rule; Wanted: Integer): Integer;
var
    Rules : TInterfaceList;
    i     : Integer;
begin
    Result := 0;
    Rules := TInterfaceList.Create;
    try
        RulesOfKindByPriority(Board, Rule.RuleKind, Rules);
        for i := Rules.Count - 1 downto 0 do
            if Rules.Items[i].I_ObjectAddress = Rule.I_ObjectAddress then Rules.Delete(i);
        if Wanted < 1 then Wanted := 1;
        if Wanted > Rules.Count + 1 then Wanted := Rules.Count + 1;
        if Wanted = Rule.Priority then Exit;
        RuleToTop(Board, Rule);
        for i := Wanted - 2 downto 0 do
            RuleToTop(Board, Rules.Items[i]);
        Result := Wanted;
    finally
        Rules.Free;
    end;
end;

// The rules a move of Rule to priority Wanted may take off and add again:
// Rule and those of its kind that stay above it. Read before the move, so
// the reply can say which of them came from the design (schematic
// directives, rooms) and whether any unique id changed. Ids gets their
// unique ids, Design the JSON-quoted names of those from the design; both
// are read here, before anything changes.
procedure RulesReadded(Board: IPCB_Board; Rule: IPCB_Rule; Wanted: Integer; Moves: TInterfaceList;
                       Ids: TStringList; Design: TStringList);
var
    Rules : TInterfaceList;
    i     : Integer;
begin
    Rules := TInterfaceList.Create;
    try
        RulesOfKindByPriority(Board, Rule.RuleKind, Rules);
        for i := Rules.Count - 1 downto 0 do
            if Rules.Items[i].I_ObjectAddress = Rule.I_ObjectAddress then Rules.Delete(i);
        Moves.Add(Rule);
        for i := 0 to Rules.Count - 1 do
            if i <= Wanted - 2 then Moves.Add(Rules.Items[i]);
        for i := 0 to Moves.Count - 1 do
        begin
            Ids.Add(Moves.Items[i].UniqueId);
            if Moves.Items[i].DefinedByLogicalDocument then
                Design.Add('"' + JSONEscapeString(Moves.Items[i].Name) + '"');
        end;
    finally
        Rules.Free;
    end;
end;

// "readded_rules", "readded_from_design" and "unique_id_changed" for the
// rules RulesReadded listed, after a move
procedure RuleMoveJSON(Props: TStringList; Moves: TInterfaceList; Ids: TStringList; Design: TStringList);
var
    Names, Changed : TStringList;
    i : Integer;
    Quoted : String;
begin
    Names := TStringList.Create;
    Changed := TStringList.Create;
    try
        for i := 0 to Moves.Count - 1 do
        begin
            Quoted := '"' + JSONEscapeString(Moves.Items[i].Name) + '"';
            Names.Add(Quoted);
            if Moves.Items[i].UniqueId <> Ids[i] then Changed.Add(Quoted);
        end;
        Props.Add(BuildJSONArray(Names, 'readded_rules'));
        Props.Add(BuildJSONArray(Design, 'readded_from_design'));
        Props.Add(BuildJSONArray(Changed, 'unique_id_changed'));
    finally
        Names.Free;
        Changed.Free;
    end;
end;

// edit_pcb_rule: changes what is given and keeps the rule where it is in
// its kind's priority order. Fields is '|scope1|scope2|comment|enabled|name|'
// for the fields to change.
function EditPCBRule(Name, NewName, Scope1, Scope2, Comment, Enabled, Fields: String; Values: TStringList): String;
var
    Board : IPCB_Board;
    Rule, Clash : IPCB_Rule;
    Props, Ids, Design : TStringList;
    Moves : TInterfaceList;
    Before, Moved : Integer;
begin
    Board := GetBoardSafe(0);
    if Board = nil then begin Result := 'ERROR: No PCB document is focused'; Exit; end;
    Rule := FindRuleByName(Board, Name);
    if Rule = nil then begin Result := 'ERROR: No rule named ' + Name; Exit; end;
    if Pos('|name|', Fields) > 0 then
    begin
        if Trim(NewName) = '' then begin Result := 'ERROR: The new name is empty'; Exit; end;
        Clash := FindRuleByName(Board, NewName);
        if Clash <> nil then
            if Clash.I_ObjectAddress <> Rule.I_ObjectAddress then
            begin
                Result := 'ERROR: A rule named ' + NewName + ' already exists';
                Exit;
            end;
    end;
    if (Rule.RuleKind = eRule_MaxMinWidth) and (Values.Count > 0) then
        if not WidthRuleUniform(Board, Rule) then
        begin
            Result := 'ERROR: ' + Name + ' has different widths per layer; edit them in Altium''s ' +
                      'PCB Rules and Constraints Editor';
            Exit;
        end;
    Before := Rule.Priority;
    Moves := TInterfaceList.Create;
    Ids := TStringList.Create;
    Design := TStringList.Create;
    Props := TStringList.Create;
    try
        RulesReadded(Board, Rule, Before, Moves, Ids, Design);
        PCBServer.PreProcess;
        try
            PCBServer.SendMessageToRobots(Rule.I_ObjectAddress, c_Broadcast, PCBM_BeginModify, c_NoEventData);
            // EndModify is sent even when a change raises, so the rule is
            // not left open for modification
            try
                if Pos('|scope1|', Fields) > 0 then Rule.Scope1Expression := Scope1;
                if Pos('|scope2|', Fields) > 0 then Rule.Scope2Expression := Scope2;
                if Pos('|comment|', Fields) > 0 then Rule.Comment := Comment;
                if Pos('|enabled|', Fields) > 0 then Rule.Enabled := Enabled = 'true';
                if Pos('|name|', Fields) > 0 then Rule.Name := NewName;
                ApplyRuleValues(Board, Rule, Values);
            finally
                PCBServer.SendMessageToRobots(Rule.I_ObjectAddress, c_Broadcast, PCBM_EndModify, c_NoEventData);
            end;
            Moved := MoveRuleTo(Board, Rule, Before);
        finally
            PCBServer.PostProcess;
        end;
        AddJSONBoolean(Props, 'success', True);
        AddJSONInteger(Props, 'priority_before', Before);
        AddJSONInteger(Props, 'rules_moved', Moved);
        if Moved > 0 then RuleMoveJSON(Props, Moves, Ids, Design);
        RuleStateJSON(Board, Rule, Props);
        Result := BuildJSONObject(Props);
    finally
        Moves.Free;
        Ids.Free;
        Design.Free;
        Props.Free;
    end;
end;

// set_rule_priority: Name to priority Wanted (1 is highest) of its kind
function SetRulePriority(Name: String; Wanted: Integer): String;
var
    Board : IPCB_Board;
    Rule  : IPCB_Rule;
    Props, Ids, Design : TStringList;
    Moves : TInterfaceList;
    Before, Moved : Integer;
begin
    Board := GetBoardSafe(0);
    if Board = nil then begin Result := 'ERROR: No PCB document is focused'; Exit; end;
    Rule := FindRuleByName(Board, Name);
    if Rule = nil then begin Result := 'ERROR: No rule named ' + Name; Exit; end;
    Before := Rule.Priority;
    Moves := TInterfaceList.Create;
    Ids := TStringList.Create;
    Design := TStringList.Create;
    Props := TStringList.Create;
    try
        RulesReadded(Board, Rule, Wanted, Moves, Ids, Design);
        PCBServer.PreProcess;
        try
            Moved := MoveRuleTo(Board, Rule, Wanted);
        finally
            PCBServer.PostProcess;
        end;
        AddJSONBoolean(Props, 'success', True);
        AddJSONInteger(Props, 'priority_before', Before);
        AddJSONInteger(Props, 'rules_moved', Moved);
        if Moved > 0 then RuleMoveJSON(Props, Moves, Ids, Design);
        RuleStateJSON(Board, Rule, Props);
        Result := BuildJSONObject(Props);
    finally
        Moves.Free;
        Ids.Free;
        Design.Free;
        Props.Free;
    end;
end;

function ExecuteRuleEdit(CommandName: String; RequestData: TStringList): String;
var
    Values : TStringList;
begin
    if CommandName = 'pcb_rule_info' then
        Result := PcbRuleInfo(RequestText(RequestData, 'rule_name', ''))
    else if CommandName = 'set_rule_priority' then
        Result := SetRulePriority(RequestText(RequestData, 'rule_name', ''),
                                  StrToInt(RequestText(RequestData, 'priority', '1')))
    else
    begin
        Values := TStringList.Create;
        try
            Values.Text := StringReplace(RequestText(RequestData, 'values', ''), ';', #13#10, REPLACEALL);
            Result := EditPCBRule(RequestText(RequestData, 'rule_name', ''), RequestText(RequestData, 'new_name', ''),
                                  RequestText(RequestData, 'scope1', ''), RequestText(RequestData, 'scope2', ''),
                                  RequestText(RequestData, 'comment', ''), RequestText(RequestData, 'enabled', ''),
                                  RequestText(RequestData, 'set', ''), Values);
        finally
            Values.Free;
        end;
    end;
end;
