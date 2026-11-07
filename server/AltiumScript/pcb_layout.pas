// Layout tools: read the placement and copper of an area or a group of
// components, place copper from a spec file, and list unrouted connections.
// Coordinates go out and come in as integers in internal units relative to
// the board origin, and angles as thousandths of a degree, so no number
// depends on the Windows decimal separator. pcb_layout.py converts.

// Round an angle to thousandths of a degree, as text
function LayoutAngle(A: Double): String;
begin
    Result := IntToStr(Round(A * 1000));
end;

// Whether Layer is a copper signal layer
function LayoutIsSignal(Layer: Integer): Boolean;
begin
    Result := (Layer >= eTopLayer) and (Layer <= eBottomLayer);
end;

// A layer by name, as ResolveBoardLayer finds it, or the Multi Layer (where a
// cutout for every signal layer lies), which it does not know
function LayoutLayer(Board: IPCB_Board; Name: String): Integer;
begin
    if UpperCase(Trim(Name)) = 'MULTI LAYER' then
        Result := eMultiLayer
    else
        Result := ResolveBoardLayer(Board, Name);
end;

// The components named in Designators (upper-case names), in a list
function LayoutFindComponents(Board: IPCB_Board; Designators: TStringList; Found: TInterfaceList): Integer;
var
    Iterator : IPCB_BoardIterator;
    Comp     : IPCB_Component;
begin
    Result := 0;
    Iterator := Board.BoardIterator_Create;
    Iterator.AddFilter_ObjectSet(MkSet(eComponentObject));
    Iterator.AddFilter_LayerSet(AllLayers);
    Iterator.AddFilter_Method(eProcessAll);
    Comp := Iterator.FirstPCBObject;
    while Comp <> nil do
    begin
        if Designators.IndexOf(UpperCase(Comp.Name.Text)) >= 0 then
        begin
            Found.Add(Comp);
            Result := Result + 1;
        end;
        Comp := Iterator.NextPCBObject;
    end;
    Board.BoardIterator_Destroy(Iterator);
end;

// One line per pour outline vertex: S|L|x|y, or S|A|x|y|cx|cy|r|a1|a2 for a
// vertex whose edge to the next vertex is an arc
function LayoutPolySegment(Board: IPCB_Board; Seg: TPolySegment): String;
begin
    if Seg.Kind = ePolySegmentArc then
        Result := 'S|A|' + IntToStr(Seg.vx - Board.XOrigin) + '|' + IntToStr(Seg.vy - Board.YOrigin) + '|' +
                  IntToStr(Seg.cx - Board.XOrigin) + '|' + IntToStr(Seg.cy - Board.YOrigin) + '|' +
                  IntToStr(Seg.Radius) + '|' + LayoutAngle(Seg.Angle1) + '|' + LayoutAngle(Seg.Angle2)
    else
        Result := 'S|L|' + IntToStr(Seg.vx - Board.XOrigin) + '|' + IntToStr(Seg.vy - Board.YOrigin);
end;

// The area of a group of components: their extent (pads, bodies, everything
// but name and comment) grown by Margin, as 'x1|y1|x2|y2' in absolute
// internal units, or '' when Comps is empty
function LayoutGroupArea(Comps: TInterfaceList; Margin: Integer): String;
var
    Rect : TCoordRect;
    X1, Y1, X2, Y2, i : Integer;
begin
    Result := '';
    if Comps.Count = 0 then Exit;
    Rect := Comps.Items[0].BoundingRectangleNoNameComment;
    X1 := Rect.Left; Y1 := Rect.Bottom; X2 := Rect.Right; Y2 := Rect.Top;
    for i := 1 to Comps.Count - 1 do
    begin
        Rect := Comps.Items[i].BoundingRectangleNoNameComment;
        if Rect.Left < X1 then X1 := Rect.Left;
        if Rect.Bottom < Y1 then Y1 := Rect.Bottom;
        if Rect.Right > X2 then X2 := Rect.Right;
        if Rect.Top > Y2 then Y2 := Rect.Top;
    end;
    Result := IntToStr(X1 - Margin) + '|' + IntToStr(Y1 - Margin) + '|' + IntToStr(X2 + Margin) + '|' + IntToStr(Y2 + Margin);
end;

// One get_layout record (lines) for a free primitive: a track, arc, via, fill,
// copper region or pour cutout. Returns 1 when it is not one of those (a
// keepout, or not on a copper layer) and was skipped, else 0.
function LayoutPrimRecord(Board: IPCB_Board; Prim: IPCB_Primitive; Lines: TStringList): Integer;
var
    NetName : String;
    i       : Integer;
begin
    Result := 0;
    NetName := '';
    if Prim.Net <> nil then NetName := Prim.Net.Name;
    if Prim.ObjectId = eViaObject then
        Lines.Add('V|' + NetName + '|' + IntToStr(Prim.X - Board.XOrigin) + '|' + IntToStr(Prim.Y - Board.YOrigin) + '|' +
                  IntToStr(Prim.Size) + '|' + IntToStr(Prim.HoleSize) + '|' + Layer2String(Prim.LowLayer) + '|' +
                  Layer2String(Prim.HighLayer))
    else if (Prim.ObjectId = eRegionObject) and (Prim.Kind = 1) and
            (LayoutIsSignal(Prim.Layer) or (Prim.Layer = eMultiLayer)) then
    begin
        // A polygon pour cutout
        Lines.Add('U|' + Layer2String(Prim.Layer));
        for i := 0 to Prim.ShapeSegmentCount - 1 do
            Lines.Add(LayoutPolySegment(Board, Prim.ShapeSegments[i]));
    end
    else if Prim.IsKeepout or (not LayoutIsSignal(Prim.Layer)) then
        Result := 1
    else if Prim.ObjectId = eTrackObject then
        Lines.Add('T|' + Layer2String(Prim.Layer) + '|' + NetName + '|' + IntToStr(Prim.Width) + '|' +
                  IntToStr(Prim.x1 - Board.XOrigin) + '|' + IntToStr(Prim.y1 - Board.YOrigin) + '|' +
                  IntToStr(Prim.x2 - Board.XOrigin) + '|' + IntToStr(Prim.y2 - Board.YOrigin))
    else if Prim.ObjectId = eArcObject then
        Lines.Add('A|' + Layer2String(Prim.Layer) + '|' + NetName + '|' + IntToStr(Prim.LineWidth) + '|' +
                  IntToStr(Prim.XCenter - Board.XOrigin) + '|' + IntToStr(Prim.YCenter - Board.YOrigin) + '|' +
                  IntToStr(Prim.Radius) + '|' + LayoutAngle(Prim.StartAngle) + '|' + LayoutAngle(Prim.EndAngle))
    else if Prim.ObjectId = eFillObject then
        Lines.Add('F|' + Layer2String(Prim.Layer) + '|' + NetName + '|' +
                  IntToStr(Prim.X1Location - Board.XOrigin) + '|' + IntToStr(Prim.Y1Location - Board.YOrigin) + '|' +
                  IntToStr(Prim.X2Location - Board.XOrigin) + '|' + IntToStr(Prim.Y2Location - Board.YOrigin) + '|' +
                  LayoutAngle(Prim.Rotation))
    else if (Prim.ObjectId = eRegionObject) and (Prim.Kind = 0) then
    begin
        Lines.Add('R|' + Layer2String(Prim.Layer) + '|' + NetName);
        for i := 0 to Prim.ShapeSegmentCount - 1 do
            Lines.Add(LayoutPolySegment(Board, Prim.ShapeSegments[i]));
    end
    else
        Result := 1;
end;

// The nets whose every pad belongs to one of the components in Designators
// (upper-case names): a group's own nets. Upper-case names, in Local.
procedure LayoutLocalNets(Board: IPCB_Board; Designators: TStringList; Local: TStringList);
var
    Iterator : IPCB_BoardIterator;
    Pad      : IPCB_Pad;
    Mine, Foreign : TStringList;
    NetName  : String;
    i        : Integer;
    IsMine   : Boolean;
begin
    Mine := TStringList.Create;
    Mine.Sorted := True;
    Foreign := TStringList.Create;
    Foreign.Sorted := True;
    try
        Iterator := Board.BoardIterator_Create;
        Iterator.AddFilter_ObjectSet(MkSet(ePadObject));
        Iterator.AddFilter_LayerSet(AllLayers);
        Iterator.AddFilter_Method(eProcessAll);
        Pad := Iterator.FirstPCBObject;
        while Pad <> nil do
        begin
            if Pad.Net <> nil then
            begin
                NetName := UpperCase(Pad.Net.Name);
                IsMine := False;
                if Pad.InComponent then
                    if Pad.Component <> nil then
                        IsMine := Designators.IndexOf(UpperCase(Pad.Component.Name.Text)) >= 0;
                if IsMine then
                begin
                    if Mine.IndexOf(NetName) < 0 then Mine.Add(NetName);
                end
                else if Foreign.IndexOf(NetName) < 0 then
                    Foreign.Add(NetName);
            end;
            Pad := Iterator.NextPCBObject;
        end;
        Board.BoardIterator_Destroy(Iterator);
        for i := 0 to Mine.Count - 1 do
            if Foreign.IndexOf(Mine[i]) < 0 then
                Local.Add(Mine[i]);
    finally
        Mine.Free;
        Foreign.Free;
    end;
end;

// get_layout: the components and the free copper of an area, one record per
// line (see pcb_layout.py for the fields). With designators and no area, the
// area is their extent (pads, bodies, everything but name and comment) grown
// by Margin. Copper is listed when it lies wholly inside the area, or, with
// Touching, when it reaches into it; with LocalNets also, wherever it runs,
// copper on the designators' own nets (nets with no pad on another part).
// Copper that belongs to a component or that a pour poured is never listed:
// the pour itself is.
function GetLayout(Designators: TStringList; HasArea: Boolean; AX1, AY1, AX2, AY2: Integer;
                   Margin: Integer; Touching: Boolean; LocalNets: Boolean): String;
var
    Board    : IPCB_Board;
    Comps    : TInterfaceList;
    Comp     : IPCB_Component;
    Iterator : IPCB_SpatialIterator;
    BIt      : IPCB_BoardIterator;
    Prim     : IPCB_Primitive;
    Rect     : TCoordRect;
    Lines    : TStringList;
    Seen     : TStringList;
    Local    : TStringList;
    NetName  : String;
    Addr     : String;
    X1, Y1, X2, Y2, i, Skipped : Integer;
    Inside   : Boolean;
begin
    Board := GetBoardSafe(0);
    if Board = nil then
    begin
        Result := 'ERROR: No PCB document is focused';
        Exit;
    end;
    Lines := TStringList.Create;
    Seen := TStringList.Create;
    Seen.Sorted := True;
    Local := TStringList.Create;
    Local.Sorted := True;
    Comps := TInterfaceList.Create;
    try
        if LocalNets and (Designators.Count > 0) then
            LayoutLocalNets(Board, Designators, Local);
        if Designators.Count > 0 then
        begin
            LayoutFindComponents(Board, Designators, Comps);
            for i := 0 to Comps.Count - 1 do
                Lines.Add('N|' + Comps.Items[i].Name.Text);
        end;
        if HasArea then
        begin
            X1 := Board.XOrigin + AX1; Y1 := Board.YOrigin + AY1;
            X2 := Board.XOrigin + AX2; Y2 := Board.YOrigin + AY2;
        end
        else
        begin
            if Comps.Count = 0 then
            begin
                Result := 'ERROR: None of the designators is on the board, and no area was given';
                Exit;
            end;
            Addr := LayoutGroupArea(Comps, Margin);
            X1 := StrToInt(GetFieldFromPipeString(Addr, 0));
            Y1 := StrToInt(GetFieldFromPipeString(Addr, 1));
            X2 := StrToInt(GetFieldFromPipeString(Addr, 2));
            Y2 := StrToInt(GetFieldFromPipeString(Addr, 3));
        end;
        Lines.Add('O|' + IntToStr(X1 - Board.XOrigin) + '|' + IntToStr(Y1 - Board.YOrigin) + '|' +
                  IntToStr(X2 - Board.XOrigin) + '|' + IntToStr(Y2 - Board.YOrigin));

        // Components: those named, or with no names those inside the area
        if Designators.Count = 0 then
        begin
            BIt := Board.BoardIterator_Create;
            BIt.AddFilter_ObjectSet(MkSet(eComponentObject));
            BIt.AddFilter_LayerSet(AllLayers);
            BIt.AddFilter_Method(eProcessAll);
            Comp := BIt.FirstPCBObject;
            while Comp <> nil do
            begin
                Rect := Comp.BoundingRectangleNoNameComment;
                if (Rect.Left >= X1) and (Rect.Right <= X2) and (Rect.Bottom >= Y1) and (Rect.Top <= Y2) then
                    Comps.Add(Comp);
                Comp := BIt.NextPCBObject;
            end;
            Board.BoardIterator_Destroy(BIt);
        end;
        for i := 0 to Comps.Count - 1 do
        begin
            Comp := Comps.Items[i];
            Lines.Add('K|' + Comp.Name.Text + '|' + IntToStr(Comp.X - Board.XOrigin) + '|' + IntToStr(Comp.Y - Board.YOrigin) + '|' +
                      LayoutAngle(Comp.Rotation) + '|' + Layer2String(Comp.Layer) + '|' + BoolToStr(not Comp.Moveable, True) + '|' +
                      Comp.Pattern + '|' +
                      IntToStr(Comp.Name.XLocation - Board.XOrigin) + '|' + IntToStr(Comp.Name.YLocation - Board.YOrigin) + '|' +
                      LayoutAngle(Comp.Name.Rotation) + '|' + IntToStr(Comp.Name.Size) + '|' + IntToStr(Comp.Name.Width) + '|' +
                      BoolToStr(Comp.NameOn, True) + '|' +
                      IntToStr(Comp.Comment.XLocation - Board.XOrigin) + '|' + IntToStr(Comp.Comment.YLocation - Board.YOrigin) + '|' +
                      LayoutAngle(Comp.Comment.Rotation) + '|' + IntToStr(Comp.Comment.Size) + '|' + IntToStr(Comp.Comment.Width) + '|' +
                      BoolToStr(Comp.CommentOn, True) + '|' + IntToStr(Comp.NameAutoPosition) + '|' +
                      IntToStr(Comp.CommentAutoPosition));
        end;

        // Pours: the spatial iterator returns their poured pieces, not the
        // pours, so they are looked up on their own; a board has few
        BIt := Board.BoardIterator_Create;
        BIt.AddFilter_ObjectSet(MkSet(ePolyObject));
        BIt.AddFilter_LayerSet(AllLayers);
        BIt.AddFilter_Method(eProcessAll);
        Prim := BIt.FirstPCBObject;
        while Prim <> nil do
        begin
            Rect := Prim.BoundingRectangle;
            if (Rect.Left < X2) and (Rect.Right > X1) and (Rect.Bottom < Y2) and (Rect.Top > Y1) then
            begin
                Inside := (Rect.Left >= X1) and (Rect.Right <= X2) and (Rect.Bottom >= Y1) and (Rect.Top <= Y2);
                if (Inside or Touching) and LayoutIsSignal(Prim.Layer) then
                begin
                    NetName := '';
                    if Prim.Net <> nil then NetName := Prim.Net.Name;
                    Lines.Add('G|' + Layer2String(Prim.Layer) + '|' + NetName + '|' + Prim.Name + '|' + IntToStr(Prim.PolyHatchStyle) + '|' +
                              IntToStr(Prim.PourOver) + '|' + BoolToStr(Prim.RemoveDead, True) + '|' +
                              BoolToStr(Prim.RemoveIslandsByArea, True) + '|' + IntToStr(Round(Prim.IslandAreaThreshold / 1E5)) + '|' +
                              BoolToStr(Prim.RemoveNarrowNecks, True) + '|' + IntToStr(Prim.NeckWidthThreshold) + '|' +
                              IntToStr(Prim.Grid) + '|' + IntToStr(Prim.TrackSize) + '|' + IntToStr(Prim.ArcApproximation) + '|' +
                              IntToStr(Prim.PourIndex) + '|' + BoolToStr(Prim.PrimitiveLock, True) + '|' +
                              BoolToStr(Prim.ObeyPolygonCutout, True) + '|' + BoolToStr(Prim.OptimalVoidRotation, True));
                    for i := 0 to Prim.PointCount - 1 do
                        Lines.Add(LayoutPolySegment(Board, Prim.Segments[i]));
                end;
            end;
            Prim := BIt.NextPCBObject;
        end;
        Board.BoardIterator_Destroy(BIt);

        Skipped := 0;
        Iterator := Board.SpatialIterator_Create;
        Iterator.AddFilter_ObjectSet(MkSet(eTrackObject, eArcObject, eViaObject, eFillObject, eRegionObject));
        Iterator.AddFilter_LayerSet(AllLayers);
        Iterator.AddFilter_Area(X1, Y1, X2, Y2);
        Prim := Iterator.FirstPCBObject;
        while Prim <> nil do
        begin
            if (not Prim.InComponent) and (not Prim.InPolygon) then
            begin
                Addr := IntToStr(Prim.I_ObjectAddress);
                if Seen.IndexOf(Addr) < 0 then
                begin
                    Seen.Add(Addr);
                    Rect := Prim.BoundingRectangle;
                    Inside := (Rect.Left >= X1) and (Rect.Right <= X2) and (Rect.Bottom >= Y1) and (Rect.Top <= Y2);
                    NetName := '';
                    if Prim.Net <> nil then NetName := UpperCase(Prim.Net.Name);
                    if Inside or Touching or ((NetName <> '') and (Local.IndexOf(NetName) >= 0)) then
                        Skipped := Skipped + LayoutPrimRecord(Board, Prim, Lines);
                end;
            end;
            Prim := Iterator.NextPCBObject;
        end;
        Board.SpatialIterator_Destroy(Iterator);

        // The group's own nets, where their copper leaves the area
        if Local.Count > 0 then
        begin
            BIt := Board.BoardIterator_Create;
            BIt.AddFilter_ObjectSet(MkSet(eTrackObject, eArcObject, eViaObject, eFillObject, eRegionObject));
            BIt.AddFilter_LayerSet(AllLayers);
            BIt.AddFilter_Method(eProcessAll);
            Prim := BIt.FirstPCBObject;
            while Prim <> nil do
            begin
                if (Prim.Net <> nil) and (not Prim.InComponent) and (not Prim.InPolygon) then
                    if Local.IndexOf(UpperCase(Prim.Net.Name)) >= 0 then
                    begin
                        Addr := IntToStr(Prim.I_ObjectAddress);
                        if Seen.IndexOf(Addr) < 0 then
                        begin
                            Seen.Add(Addr);
                            Skipped := Skipped + LayoutPrimRecord(Board, Prim, Lines);
                        end;
                    end;
                Prim := BIt.NextPCBObject;
            end;
            Board.BoardIterator_Destroy(BIt);
        end;
        Lines.Add('X|' + IntToStr(Skipped));
        Lines.Add('B|' + Board.FileName);
        Result := Lines.Text;
    finally
        Comps.Free;
        Local.Free;
        Seen.Free;
        Lines.Free;
    end;
end;

// The net of a spec record field, or nil for ''. Unknown names were refused
// before anything was added.
function LayoutNet(Board: IPCB_Board; Name: String): IPCB_Net;
begin
    Result := nil;
    if Name <> '' then
        Result := FindBoardNet(Board, Name);
end;

// The problem with one record of a place_copper spec, or '' when it is fine:
// its net must be on the board and its layers must be signal layers
function LayoutCheckRecord(Board: IPCB_Board; Line: String): String;
var
    Kind, NetName : String;
    Layer, Layer2 : Integer;
begin
    Result := '';
    Kind := GetFieldFromPipeString(Line, 0);
    if (Kind = 'S') or (Kind = '') then Exit;
    if Kind = 'U' then
    begin
        Layer := LayoutLayer(Board, GetFieldFromPipeString(Line, 1));
        if (not LayoutIsSignal(Layer)) and (Layer <> eMultiLayer) then
            Result := 'a cutout goes on a signal layer or the Multi Layer, not ' + GetFieldFromPipeString(Line, 1);
        Exit;
    end;
    if Kind = 'V' then
    begin
        NetName := GetFieldFromPipeString(Line, 1);
        Layer := ResolveBoardLayer(Board, GetFieldFromPipeString(Line, 6));
        Layer2 := ResolveBoardLayer(Board, GetFieldFromPipeString(Line, 7));
        if (not LayoutIsSignal(Layer)) or (not LayoutIsSignal(Layer2)) or (Layer = Layer2) then
            Result := 'a via must span two different signal layers: ' + GetFieldFromPipeString(Line, 6) + ' to ' +
                      GetFieldFromPipeString(Line, 7);
    end
    else
    begin
        NetName := GetFieldFromPipeString(Line, 2);
        Layer := ResolveBoardLayer(Board, GetFieldFromPipeString(Line, 1));
        if not LayoutIsSignal(Layer) then
            Result := GetFieldFromPipeString(Line, 1) + ' is not a signal layer of this board';
    end;
    if (Result = '') and (NetName <> '') and (FindBoardNet(Board, NetName) = nil) then
        Result := 'net not found on the board: ' + NetName;
end;

// Register a new primitive with the board
procedure LayoutAdd(Board: IPCB_Board; Prim: IPCB_Primitive);
begin
    Board.AddPCBObject(Prim);
    PCBServer.SendMessageToRobots(Board.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, Prim.I_ObjectAddress);
end;

// A pour outline vertex from an S record
function LayoutSegment(Board: IPCB_Board; Line: String): TPolySegment;
begin
    Result := TPolySegment;
    Result.vx := Board.XOrigin + StrToInt(GetFieldFromPipeString(Line, 2));
    Result.vy := Board.YOrigin + StrToInt(GetFieldFromPipeString(Line, 3));
    if GetFieldFromPipeString(Line, 1) = 'A' then
    begin
        Result.Kind := ePolySegmentArc;
        Result.cx := Board.XOrigin + StrToInt(GetFieldFromPipeString(Line, 4));
        Result.cy := Board.YOrigin + StrToInt(GetFieldFromPipeString(Line, 5));
        Result.Radius := StrToInt(GetFieldFromPipeString(Line, 6));
        Result.Angle1 := StrToInt(GetFieldFromPipeString(Line, 7)) / 1000;
        Result.Angle2 := StrToInt(GetFieldFromPipeString(Line, 8)) / 1000;
    end
    else
        Result.Kind := ePolySegmentLine;
end;

// Add a pour from a G record and the S records after it (from line Start)
function LayoutAddPour(Board: IPCB_Board; Spec: TStringList; Start: Integer): IPCB_Polygon;
var
    Poly : IPCB_Polygon;
    Line : String;
    n, i : Integer;
begin
    Line := Spec[Start];
    Poly := PCBServer.PCBObjectFactory(ePolyObject, eNoDimension, eCreate_Default);
    Poly.Layer := ResolveBoardLayer(Board, GetFieldFromPipeString(Line, 1));
    Poly.PolyHatchStyle := StrToInt(GetFieldFromPipeString(Line, 4));
    n := 0;
    i := Start + 1;
    while (i < Spec.Count) and (GetFieldFromPipeString(Spec[i], 0) = 'S') do
    begin
        n := n + 1;
        i := i + 1;
    end;
    Poly.PointCount := n;
    for i := 0 to n - 1 do
        Poly.Segments[i] := LayoutSegment(Board, Spec[Start + 1 + i]);
    if GetFieldFromPipeString(Line, 2) <> '' then
        Poly.Net := LayoutNet(Board, GetFieldFromPipeString(Line, 2));
    Poly.PourOver := StrToInt(GetFieldFromPipeString(Line, 5));
    Poly.RemoveDead := GetFieldFromPipeString(Line, 6) = '1';
    Poly.RemoveIslandsByArea := GetFieldFromPipeString(Line, 7) = '1';
    Poly.IslandAreaThreshold := StrToInt(GetFieldFromPipeString(Line, 8)) * 1E5;
    Poly.RemoveNarrowNecks := GetFieldFromPipeString(Line, 9) = '1';
    Poly.NeckWidthThreshold := StrToInt(GetFieldFromPipeString(Line, 10));
    if StrToInt(GetFieldFromPipeString(Line, 11)) > 0 then Poly.Grid := StrToInt(GetFieldFromPipeString(Line, 11));
    if StrToInt(GetFieldFromPipeString(Line, 12)) > 0 then Poly.TrackSize := StrToInt(GetFieldFromPipeString(Line, 12));
    if StrToInt(GetFieldFromPipeString(Line, 13)) > 0 then Poly.ArcApproximation := StrToInt(GetFieldFromPipeString(Line, 13));
    if GetFieldFromPipeString(Line, 3) <> '' then Poly.Name := GetFieldFromPipeString(Line, 3);
    LayoutAdd(Board, Poly);
    if StrToInt(GetFieldFromPipeString(Line, 14)) > 0 then Poly.PourIndex := StrToInt(GetFieldFromPipeString(Line, 14));
    Poly.PrimitiveLock := GetFieldFromPipeString(Line, 15) = '1';
    Poly.ObeyPolygonCutout := GetFieldFromPipeString(Line, 16) = '1';
    Poly.OptimalVoidRotation := GetFieldFromPipeString(Line, 17) = '1';
    Result := Poly;
end;

// Add a copper region (an R record, Kind 0) or a pour cutout (a U record,
// Kind 1) and the S records after it (from line Start); the server writes arc
// edges as straight pieces
function LayoutAddRegion(Board: IPCB_Board; Spec: TStringList; Start: Integer; Kind: Integer): IPCB_Region;
var
    Region : IPCB_Region;
    Points : TStringList;
    Line   : String;
    i      : Integer;
begin
    Line := Spec[Start];
    Points := TStringList.Create;
    try
        i := Start + 1;
        while (i < Spec.Count) and (GetFieldFromPipeString(Spec[i], 0) = 'S') do
        begin
            Points.Add(GetFieldFromPipeString(Spec[i], 2) + '|' + GetFieldFromPipeString(Spec[i], 3));
            i := i + 1;
        end;
        Region := PCBServer.PCBObjectFactory(eRegionObject, eNoDimension, eCreate_Default);
        Region.SetOutlineContour(OutlineContour(Board, Points));
        Region.Layer := LayoutLayer(Board, GetFieldFromPipeString(Line, 1));
        Region.Kind := Kind;
        if (Kind = 0) and (GetFieldFromPipeString(Line, 2) <> '') then
            Region.Net := LayoutNet(Board, GetFieldFromPipeString(Line, 2));
        LayoutAdd(Board, Region);
        Result := Region;
    finally
        Points.Free;
    end;
end;

// place_copper: add the tracks, arcs, vias, fills and pours of a spec file
// (records as get_layout writes them, see pcb_layout.py) to the focused board
// in one undo step. Every record is checked first, so a bad one adds nothing.
// Pours are added last and poured in the order of the spec (the server sorts
// them by pour index), around the copper added before them.
function PlaceCopper(SpecPath: String): String;
var
    Board    : IPCB_Board;
    Spec     : TStringList;
    Problems : TStringList;
    Pours    : TInterfaceList;
    Props    : TStringList;
    PourList : TStringList;
    Prim     : IPCB_Primitive;
    Poly     : IPCB_Polygon;
    Line, Kind, Problem : String;
    i, nT, nA, nV, nF, nR, nU : Integer;
    L1, L2   : Integer;
begin
    Board := GetBoardSafe(0);
    if Board = nil then
    begin
        Result := 'ERROR: No PCB document is focused';
        Exit;
    end;
    if not FileExists(SpecPath) then
    begin
        Result := 'ERROR: Spec file not found: ' + SpecPath;
        Exit;
    end;
    Spec := TStringList.Create;
    Problems := TStringList.Create;
    Pours := TInterfaceList.Create;
    Props := TStringList.Create;
    PourList := TStringList.Create;
    try
        Spec.LoadFromFile(SpecPath);
        for i := 0 to Spec.Count - 1 do
        begin
            Problem := LayoutCheckRecord(Board, Spec[i]);
            if (Problem <> '') and (Problems.Count < 20) then
                Problems.Add('"' + JSONEscapeString('line ' + IntToStr(i + 1) + ': ' + Problem) + '"');
        end;
        if Problems.Count > 0 then
        begin
            AddJSONBoolean(Props, 'success', False);
            AddJSONProperty(Props, 'error', 'Nothing was added: some records name a net or layer the board does not have');
            Props.Add(BuildJSONArray(Problems, 'problems'));
            Result := BuildJSONObject(Props);
            Exit;
        end;

        nT := 0; nA := 0; nV := 0; nF := 0; nR := 0; nU := 0;
        // PostProcess must run whatever happens, or Altium's undo stays broken
        PCBServer.PreProcess;
        try
            for i := 0 to Spec.Count - 1 do
            begin
                Line := Spec[i];
                Kind := GetFieldFromPipeString(Line, 0);
                if Kind = 'T' then
                begin
                    Prim := PCBServer.PCBObjectFactory(eTrackObject, eNoDimension, eCreate_Default);
                    Prim.Layer := ResolveBoardLayer(Board, GetFieldFromPipeString(Line, 1));
                    Prim.Width := StrToInt(GetFieldFromPipeString(Line, 3));
                    Prim.x1 := Board.XOrigin + StrToInt(GetFieldFromPipeString(Line, 4));
                    Prim.y1 := Board.YOrigin + StrToInt(GetFieldFromPipeString(Line, 5));
                    Prim.x2 := Board.XOrigin + StrToInt(GetFieldFromPipeString(Line, 6));
                    Prim.y2 := Board.YOrigin + StrToInt(GetFieldFromPipeString(Line, 7));
                    if GetFieldFromPipeString(Line, 2) <> '' then Prim.Net := LayoutNet(Board, GetFieldFromPipeString(Line, 2));
                    LayoutAdd(Board, Prim);
                    nT := nT + 1;
                end
                else if Kind = 'A' then
                begin
                    Prim := PCBServer.PCBObjectFactory(eArcObject, eNoDimension, eCreate_Default);
                    Prim.Layer := ResolveBoardLayer(Board, GetFieldFromPipeString(Line, 1));
                    Prim.LineWidth := StrToInt(GetFieldFromPipeString(Line, 3));
                    Prim.XCenter := Board.XOrigin + StrToInt(GetFieldFromPipeString(Line, 4));
                    Prim.YCenter := Board.YOrigin + StrToInt(GetFieldFromPipeString(Line, 5));
                    Prim.Radius := StrToInt(GetFieldFromPipeString(Line, 6));
                    Prim.StartAngle := StrToInt(GetFieldFromPipeString(Line, 7)) / 1000;
                    Prim.EndAngle := StrToInt(GetFieldFromPipeString(Line, 8)) / 1000;
                    if GetFieldFromPipeString(Line, 2) <> '' then Prim.Net := LayoutNet(Board, GetFieldFromPipeString(Line, 2));
                    LayoutAdd(Board, Prim);
                    nA := nA + 1;
                end
                else if Kind = 'V' then
                begin
                    Prim := PCBServer.PCBObjectFactory(eViaObject, eNoDimension, eCreate_Default);
                    Prim.X := Board.XOrigin + StrToInt(GetFieldFromPipeString(Line, 2));
                    Prim.Y := Board.YOrigin + StrToInt(GetFieldFromPipeString(Line, 3));
                    Prim.Size := StrToInt(GetFieldFromPipeString(Line, 4));
                    Prim.HoleSize := StrToInt(GetFieldFromPipeString(Line, 5));
                    L1 := ResolveBoardLayer(Board, GetFieldFromPipeString(Line, 6));
                    L2 := ResolveBoardLayer(Board, GetFieldFromPipeString(Line, 7));
                    if L1 < L2 then
                    begin
                        Prim.LowLayer := L1;
                        Prim.HighLayer := L2;
                    end
                    else
                    begin
                        Prim.LowLayer := L2;
                        Prim.HighLayer := L1;
                    end;
                    if GetFieldFromPipeString(Line, 1) <> '' then Prim.Net := LayoutNet(Board, GetFieldFromPipeString(Line, 1));
                    LayoutAdd(Board, Prim);
                    nV := nV + 1;
                end
                else if Kind = 'F' then
                begin
                    Prim := PCBServer.PCBObjectFactory(eFillObject, eNoDimension, eCreate_Default);
                    Prim.Layer := ResolveBoardLayer(Board, GetFieldFromPipeString(Line, 1));
                    Prim.X1Location := Board.XOrigin + StrToInt(GetFieldFromPipeString(Line, 3));
                    Prim.Y1Location := Board.YOrigin + StrToInt(GetFieldFromPipeString(Line, 4));
                    Prim.X2Location := Board.XOrigin + StrToInt(GetFieldFromPipeString(Line, 5));
                    Prim.Y2Location := Board.YOrigin + StrToInt(GetFieldFromPipeString(Line, 6));
                    Prim.Rotation := StrToInt(GetFieldFromPipeString(Line, 7)) / 1000;
                    if GetFieldFromPipeString(Line, 2) <> '' then Prim.Net := LayoutNet(Board, GetFieldFromPipeString(Line, 2));
                    LayoutAdd(Board, Prim);
                    nF := nF + 1;
                end
                else if Kind = 'R' then
                begin
                    LayoutAddRegion(Board, Spec, i, 0);
                    nR := nR + 1;
                end
                else if Kind = 'U' then
                begin
                    LayoutAddRegion(Board, Spec, i, 1);
                    nU := nU + 1;
                end;
            end;
            for i := 0 to Spec.Count - 1 do
                if GetFieldFromPipeString(Spec[i], 0) = 'G' then
                    Pours.Add(LayoutAddPour(Board, Spec, i));
            for i := 0 to Pours.Count - 1 do
            begin
                Poly := Pours.Items[i];
                Poly.Rebuild;
            end;
        finally
            PCBServer.PostProcess;
        end;
        // Bring the ratsnest up to date on the nets the copper is on, or the
        // lines it now connects stay drawn
        for i := 0 to Spec.Count - 1 do
        begin
            Kind := GetFieldFromPipeString(Spec[i], 0);
            if Kind = 'V' then
                Problem := GetFieldFromPipeString(Spec[i], 1)
            else if (Kind <> 'S') and (Kind <> 'U') and (Kind <> '') then
                Problem := GetFieldFromPipeString(Spec[i], 2)
            else
                Problem := '';
            if (Problem <> '') and (Problems.IndexOf(Problem) < 0) then
                Problems.Add(Problem);
        end;
        for i := 0 to Problems.Count - 1 do
            Board.AnalyzeNet(FindBoardNet(Board, Problems[i]));
        Problems.Clear;
        Board.ViewManager_FullUpdate;

        for i := 0 to Pours.Count - 1 do
        begin
            Poly := Pours.Items[i];
            PourList.Add('{"name": "' + JSONEscapeString(Poly.Name) + '", "layer": "' + JSONEscapeString(Layer2String(Poly.Layer)) +
                         '", "poured_regions": ' + IntToStr(PolygonPourCount(Poly, True)) + ', "hatch_tracks": ' +
                         IntToStr(PolygonPourCount(Poly, False)) + '}');
        end;
        AddJSONBoolean(Props, 'success', True);
        AddJSONInteger(Props, 'tracks', nT);
        AddJSONInteger(Props, 'arcs', nA);
        AddJSONInteger(Props, 'vias', nV);
        AddJSONInteger(Props, 'fills', nF);
        AddJSONInteger(Props, 'regions', nR);
        AddJSONInteger(Props, 'cutouts', nU);
        Props.Add(BuildJSONArray(PourList, 'pours'));
        AddJSONProperty(Props, 'board', Board.FileName);
        Result := BuildJSONObject(Props);
    finally
        PourList.Free;
        Props.Free;
        Pours.Free;
        Problems.Free;
        Spec.Free;
    end;
end;

// get_unrouted_connections: the board's unrouted connections (ratsnest
// lines), one C|net|x1|y1|x2|y2|layer1|layer2 line each, on the nets in
// NetNames (upper case) or on every net when it is empty; and P lines for the
// pads of the components named in Designators, so the server can tell which
// ends are their pads.
function GetUnroutedConnections(NetNames: TStringList; Designators: TStringList): String;
var
    Board    : IPCB_Board;
    Iterator : IPCB_BoardIterator;
    GIt      : IPCB_GroupIterator;
    Comps    : TInterfaceList;
    Comp     : IPCB_Component;
    Prim     : IPCB_Primitive;
    Lines    : TStringList;
    NetName  : String;
    i        : Integer;
begin
    Board := GetBoardSafe(0);
    if Board = nil then
    begin
        Result := 'ERROR: No PCB document is focused';
        Exit;
    end;
    Lines := TStringList.Create;
    Comps := TInterfaceList.Create;
    try
        if Designators.Count > 0 then
            LayoutFindComponents(Board, Designators, Comps);
        for i := 0 to Comps.Count - 1 do
        begin
            Comp := Comps.Items[i];
            GIt := Comp.GroupIterator_Create;
            GIt.AddFilter_ObjectSet(MkSet(ePadObject));
            Prim := GIt.FirstPCBObject;
            while Prim <> nil do
            begin
                NetName := '';
                if Prim.Net <> nil then NetName := Prim.Net.Name;
                Lines.Add('P|' + Comp.Name.Text + '|' + Prim.Name + '|' + IntToStr(Prim.X - Board.XOrigin) + '|' +
                          IntToStr(Prim.Y - Board.YOrigin) + '|' + NetName);
                if (NetName <> '') and (Designators.Count > 0) and (NetNames.IndexOf(UpperCase(NetName)) < 0) then
                    NetNames.Add(UpperCase(NetName));
                Prim := GIt.NextPCBObject;
            end;
            Comp.GroupIterator_Destroy(GIt);
        end;
        // Altium does not update the ratsnest after copper a script added, so
        // a stale line can stay where copper now connects: analyze each net
        // first (all 223 nets of a 6-layer board took 0.25 s).
        // Net.ConnectivelyInValidate does not clear such a line. Analyzing can
        // join straight track segments that continue each other; the copper
        // stays the same.
        Iterator := Board.BoardIterator_Create;
        Iterator.AddFilter_ObjectSet(MkSet(eNetObject));
        Iterator.AddFilter_LayerSet(AllLayers);
        Iterator.AddFilter_Method(eProcessAll);
        Prim := Iterator.FirstPCBObject;
        while Prim <> nil do
        begin
            if (NetNames.Count = 0) or (NetNames.IndexOf(UpperCase(Prim.Name)) >= 0) then
                Board.AnalyzeNet(Prim);
            Prim := Iterator.NextPCBObject;
        end;
        Board.BoardIterator_Destroy(Iterator);

        Iterator := Board.BoardIterator_Create;
        Iterator.AddFilter_ObjectSet(MkSet(eConnectionObject));
        Iterator.AddFilter_LayerSet(AllLayers);
        Iterator.AddFilter_Method(eProcessAll);
        Prim := Iterator.FirstPCBObject;
        while Prim <> nil do
        begin
            NetName := '';
            if Prim.Net <> nil then NetName := Prim.Net.Name;
            if (NetNames.Count = 0) or (NetNames.IndexOf(UpperCase(NetName)) >= 0) then
                Lines.Add('C|' + NetName + '|' + IntToStr(Prim.x1 - Board.XOrigin) + '|' + IntToStr(Prim.y1 - Board.YOrigin) + '|' +
                          IntToStr(Prim.x2 - Board.XOrigin) + '|' + IntToStr(Prim.y2 - Board.YOrigin) + '|' +
                          Layer2String(Prim.Layer1) + '|' + Layer2String(Prim.Layer2));
            Prim := Iterator.NextPCBObject;
        end;
        Board.BoardIterator_Destroy(Iterator);
        Lines.Add('B|' + Board.FileName);
        Result := Lines.Text;
    finally
        Comps.Free;
        Lines.Free;
    end;
end;
