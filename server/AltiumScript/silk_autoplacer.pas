// Silkscreen Auto Placer, headless, for the MCP bridge.
//
// Port of https://github.com/coffeenmusic/Silkscreen_Auto_Placer
// (AutoPlaceSilkscreen.pas). The placement algorithm is that script's own:
// obstacles cached per component, a candidate grid over the enabled
// autopositions with shrinking text, a retry pass with a rotation flip and a
// wider grid, and the optional 2nd-pass "wiggle" search. What changed for
// the bridge:
// - The GUI is gone. Options arrive as parameters (see APS_Run) and the
//   result is JSON; nothing modal, so the bridge never blocks on a dialog.
// - Every name carries an APS_ prefix so it cannot clash with the other
//   units of the Altium_API script project.
// - Scope can be an explicit designator list as well as the selection.
//   Unhide-all and auto-hide only touch components in scope.
// - "Try altered rotation" on the bottom layer now flips 0 <-> 270; the
//   original's 90 - Rotation left mirrored text at 90/180 (upside down).
// - Each placed designator's stroke geometry is rebuilt at the end. Batch DRC
//   checks a cached copy that scripted moves and resizes leave stale, so
//   without this it reports collisions at old positions.

const
  APS_SLKPAD = 40000; // Allowed silk-to-silk designator overlap = 4 mil
  APS_PADPAD = 10000; // Margin beyond pad = 1 mil
  // TList stores untyped pointers: on 64-bit Altium they read back as
  // unsigned, so negative values overflow. All coordinates stored in the
  // obstacle TLists are offset by this bias to keep them positive.
  APS_COORD_BIAS = 100000000;

var
  APS_Board: IPCB_Board;
  APS_AllowUnder: TStringList;
  APS_Scope: TStringList;
  APS_SelectedOnly: Boolean;
  APS_CmpOutlineLayerID: Integer;
  APS_AvoidVias: Boolean;
  APS_DictionaryCache: TStringList;
  APS_TextProps: TStringList;
  APS_Positions: TStringList; // Enabled autoposition names, in priority order
  APS_PositionDelta: TCoord;
  APS_FixedWidth: TCoord;
  APS_FixedSize: TCoord;
  APS_IsFixedWidth: Boolean;
  APS_IsFixedSize: Boolean;
  APS_TryAlteredRotation: Integer;
  APS_RotationStrategy: Integer;
  APS_WiggleEnabled: Boolean;
  APS_UnhideAll: Boolean;
  APS_AutoHideList: TStringList;
  APS_AutoHidden: TStringList;

  // Obstacle cache, rebuilt once per component. Rectangles are stored with
  // padding margins and APS_COORD_BIAS baked in.
  APS_ObsAL, APS_ObsAB, APS_ObsAR, APS_ObsAT: TList; // Pads/tracks/arcs/vias/bodies
  APS_ObsBL, APS_ObsBB, APS_ObsBR, APS_ObsBT: TList; // Silkscreen text
  // Base candidate rectangle/anchor per enabled autoposition, as strings so
  // StrToInt returns clean signed integers
  APS_BaseL, APS_BaseB, APS_BaseR, APS_BaseT: TStringList;
  APS_BaseX, APS_BaseY, APS_BaseIdx: TStringList;
  APS_BodyRectCnt: Integer;
  APS_BoardRect: TCoordRect;
  APS_BoardIsRectangular: Boolean;
  // Board Outline Clearance for text, and the outline vertices (biased) for
  // testing it on shaped boards
  APS_EdgeGap: TCoord;
  APS_OutlineX, APS_OutlineY: TList;

function APS_GetObjRect(Obj: IPCB_ObjectClass): TCoordRect;
var
  ObjID: Integer;
begin
  ObjID := Obj.ObjectId;
  if ObjID = eBoardObject then
    result := Obj.BoardOutline.BoundingRectangle
  else if ObjID = eComponentObject then
    result := Obj.BoundingRectangleNoNameCommentForSignals
  else
    result := Obj.BoundingRectangle;
end;

// Guess silkscreen size from the component size
function APS_GetSilkSize(Slk: IPCB_Text; Min_Size: Integer): Integer;
var
  Rect: TCoordRect;
  area: Integer;
  size: Integer;
begin
  Rect := APS_GetObjRect(Slk.Component);
  area := CoordToMils(Rect.Right - Rect.Left) * CoordToMils(Rect.Top - Rect.Bottom);
  size := Int((82 * area) / (16700 + area));
  if size < Min_Size then
    size := Min_Size;
  result := size;
end;

function APS_GetLayerSet(SlkLayer: Integer; ObjID: Integer): PAnsiChar;
var
  TopBot: Integer;
begin
  TopBot := eTopLayer;
  if (Layer2String(SlkLayer) = 'Bottom Overlay') then
    TopBot := eBottomLayer;

  result := MkSet(SlkLayer);
  if (ObjID = eComponentObject) or (ObjID = ePadObject) or (ObjID = eViaObject) then
    result := MkSet(TopBot, eMultiLayer)
  else if (ObjID = eComponentBodyObject) then
    result := MkSet(APS_CmpOutlineLayerID);
end;

function APS_AllowUnderCmp(Cmp: IPCB_Component): Boolean;
var
  i: Integer;
begin
  result := False;
  if (APS_AllowUnder <> nil) then
    for i := 0 to APS_AllowUnder.Count - 1 do
      if LowerCase(Cmp.Name.Text) = LowerCase(APS_AllowUnder[i]) then
      begin
        result := True;
        Exit;
      end;
end;

// Components to place: an explicit designator list, else the selection when
// asked, else everything
function APS_InScope(Cmp: IPCB_Component): Boolean;
begin
  if APS_Scope.Count > 0 then
    result := APS_Scope.IndexOf(Cmp.Name.Text) >= 0
  else if APS_SelectedOnly then
    result := Cmp.Selected
  else
    result := True;
end;

procedure APS_CacheAddRect(GroupB: Boolean; L: TCoord; B: TCoord; R: TCoord; T: TCoord);
var
  j: Integer;
  Lb, Bb, Rb, Tb: TCoord;
begin
  Lb := L + APS_COORD_BIAS;
  Bb := B + APS_COORD_BIAS;
  Rb := R + APS_COORD_BIAS;
  Tb := T + APS_COORD_BIAS;

  if GroupB then
  begin
    APS_ObsBL.Add(Lb);
    APS_ObsBB.Add(Bb);
    APS_ObsBR.Add(Rb);
    APS_ObsBT.Add(Tb);
  end
  else
  begin
    // Skip rectangles fully covered by a component body already cached
    for j := 0 to APS_BodyRectCnt - 1 do
    begin
      if not ((Lb < APS_ObsAL.Items[j]) or (Rb > APS_ObsAR.Items[j]) or
        (Bb < APS_ObsAB.Items[j]) or (Tb > APS_ObsAT.Items[j])) then
        Exit;
    end;
    APS_ObsAL.Add(Lb);
    APS_ObsAB.Add(Bb);
    APS_ObsAR.Add(Rb);
    APS_ObsAT.Add(Tb);
  end;
end;

// Collect every obstacle rectangle near the component with one set of
// spatial queries; candidates are then tested in memory
procedure APS_BuildObstacleCache(Slk: IPCB_Text; RegionL: TCoord; RegionB: TCoord;
  RegionR: TCoord; RegionT: TCoord);
var
  Iterator: IPCB_SpatialIterator;
  Obj: IPCB_ObjectClass;
  Cmp: IPCB_Component;
  Rect: TCoordRect;
  Delta: TCoord;
begin
  APS_ObsAL.Clear; APS_ObsAB.Clear; APS_ObsAR.Clear; APS_ObsAT.Clear;
  APS_ObsBL.Clear; APS_ObsBB.Clear; APS_ObsBR.Clear; APS_ObsBT.Clear;
  APS_BodyRectCnt := 0;

  // Component bodies -> parent footprint rectangles, first, so everything they
  // fully cover can be pruned
  if APS_CmpOutlineLayerID <> 0 then
  begin
    Iterator := APS_Board.SpatialIterator_Create;
    Iterator.AddFilter_ObjectSet(MkSet(eComponentBodyObject));
    Iterator.AddFilter_LayerSet(MkSet(APS_CmpOutlineLayerID));
    Iterator.AddFilter_Area(RegionL, RegionB, RegionR, RegionT);
    Obj := Iterator.FirstPCBObject;
    while Obj <> nil do
    begin
      if not Obj.IsHidden then
      begin
        Cmp := Obj.Component;
        if (Cmp <> nil) then
          if (not APS_AllowUnderCmp(Cmp)) and (Cmp.Name.Layer = Slk.Layer) then
          begin
            Rect := APS_GetObjRect(Cmp);
            APS_CacheAddRect(False, Rect.Left, Rect.Bottom, Rect.Right, Rect.Top);
          end;
      end;
      Obj := Iterator.NextPCBObject;
    end;
    APS_Board.SpatialIterator_Destroy(Iterator);
  end;
  APS_BodyRectCnt := APS_ObsAL.Count;

  // Silkscreen text, tracks & arcs on the same overlay
  Iterator := APS_Board.SpatialIterator_Create;
  Iterator.AddFilter_ObjectSet(MkSet(eTextObject, eTrackObject, eArcObject));
  Iterator.AddFilter_LayerSet(MkSet(Slk.Layer));
  Iterator.AddFilter_Area(RegionL, RegionB, RegionR, RegionT);
  Obj := Iterator.FirstPCBObject;
  while Obj <> nil do
  begin
    if not Obj.IsHidden then
    begin
      if Obj.ObjectId = eTextObject then
      begin
        if not (Obj.IsDesignator and (Obj.Text = Slk.Text)) then
        begin
          Rect := APS_GetObjRect(Obj);
          Delta := 0;
          if Obj.IsDesignator then
            Delta := APS_SLKPAD;
          APS_CacheAddRect(True, Rect.Left + Delta, Rect.Bottom + Delta,
            Rect.Right - Delta, Rect.Top - Delta);
        end;
      end
      else
      begin
        Rect := Obj.BoundingRectangle;
        APS_CacheAddRect(False, Rect.Left, Rect.Bottom, Rect.Right, Rect.Top);
      end;
    end;
    Obj := Iterator.NextPCBObject;
  end;
  APS_Board.SpatialIterator_Destroy(Iterator);

  // Pads (and optionally vias) on the same side or multilayer
  Iterator := APS_Board.SpatialIterator_Create;
  if APS_AvoidVias then
    Iterator.AddFilter_ObjectSet(MkSet(ePadObject, eViaObject))
  else
    Iterator.AddFilter_ObjectSet(MkSet(ePadObject));
  Iterator.AddFilter_LayerSet(APS_GetLayerSet(Slk.Layer, ePadObject));
  Iterator.AddFilter_Area(RegionL, RegionB, RegionR, RegionT);
  Obj := Iterator.FirstPCBObject;
  while Obj <> nil do
  begin
    if not Obj.IsHidden then
    begin
      Delta := 0;
      if Obj.ObjectId = ePadObject then
        Delta := APS_PADPAD;
      Rect := Obj.BoundingRectangle;
      APS_CacheAddRect(False, Rect.Left - Delta, Rect.Bottom - Delta,
        Rect.Right + Delta, Rect.Top + Delta);
    end;
    Obj := Iterator.NextPCBObject;
  end;
  APS_Board.SpatialIterator_Destroy(Iterator);
end;

function APS_CandidateIsClear(L: TCoord; B: TCoord; R: TCoord; T: TCoord): Boolean;
var
  i: Integer;
  Lb, Bb, Rb, Tb: TCoord;
  Ls, Bs, Rs, Ts: TCoord;
begin
  result := False;

  // Keep the Board Outline Clearance rule's distance from the edge
  if (L - APS_EdgeGap < APS_BoardRect.Left) or (R + APS_EdgeGap > APS_BoardRect.Right) or
    (B - APS_EdgeGap < APS_BoardRect.Bottom) or (T + APS_EdgeGap > APS_BoardRect.Top) then
    Exit;

  Lb := L + APS_COORD_BIAS;
  Bb := B + APS_COORD_BIAS;
  Rb := R + APS_COORD_BIAS;
  Tb := T + APS_COORD_BIAS;

  for i := 0 to APS_ObsAL.Count - 1 do
  begin
    if not ((Bb > APS_ObsAT.Items[i]) or (Tb < APS_ObsAB.Items[i]) or
      (Lb > APS_ObsAR.Items[i]) or (Rb < APS_ObsAL.Items[i])) then
      Exit;
  end;

  Ls := Lb + APS_SLKPAD;
  Bs := Bb + APS_SLKPAD;
  Rs := Rb - APS_SLKPAD;
  Ts := Tb - APS_SLKPAD;
  for i := 0 to APS_ObsBL.Count - 1 do
  begin
    if not ((Bs > APS_ObsBT.Items[i]) or (Ts < APS_ObsBB.Items[i]) or
      (Ls > APS_ObsBR.Items[i]) or (Rs < APS_ObsBL.Items[i])) then
      Exit;
  end;

  if not APS_BoardIsRectangular then
  begin
    Ls := L - APS_EdgeGap;
    Bs := B - APS_EdgeGap;
    Rs := R + APS_EdgeGap;
    Ts := T + APS_EdgeGap;
    if not APS_Board.BoardOutline.PointInPolygon(Ls, Bs) then Exit;
    if not APS_Board.BoardOutline.PointInPolygon(Ls, Ts) then Exit;
    if not APS_Board.BoardOutline.PointInPolygon(Rs, Bs) then Exit;
    if not APS_Board.BoardOutline.PointInPolygon(Rs, Ts) then Exit;
    // A notch can reach in between the corners
    Ls := Ls + APS_COORD_BIAS;
    Bs := Bs + APS_COORD_BIAS;
    Rs := Rs + APS_COORD_BIAS;
    Ts := Ts + APS_COORD_BIAS;
    for i := 0 to APS_OutlineX.Count - 1 do
    begin
      if (APS_OutlineX.Items[i] > Ls) and (APS_OutlineX.Items[i] < Rs) and
        (APS_OutlineY.Items[i] > Bs) and (APS_OutlineY.Items[i] < Ts) then
        Exit;
    end;
  end;

  result := True;
end;

// Park the designators in scope off the board, remembering their properties
procedure APS_MoveSilkOffBoard(Dummy: Integer);
var
  Iterator: IPCB_BoardIterator;
  Slk: IPCB_Text;
begin
  Iterator := APS_Board.BoardIterator_Create;
  Iterator.AddFilter_ObjectSet(MkSet(eTextObject));
  Iterator.AddFilter_IPCB_LayerSet(MkSet(eTopOverlay, eBottomOverlay));
  Iterator.AddFilter_Method(eProcessAll);

  Slk := Iterator.FirstPCBObject;
  while Slk <> nil do
  begin
    if Slk.IsDesignator and (not Slk.IsHidden) then
    begin
      if APS_InScope(Slk.Component) then
      begin
        APS_TextProps.Add(Slk.Text + '.Rotation=' + IntToStr(Slk.Rotation));
        APS_TextProps.Add(Slk.Text + '.Width=' + IntToStr(Slk.Width));
        APS_TextProps.Add(Slk.Text + '.Size=' + IntToStr(Slk.Size));
        APS_TextProps.Add(Slk.Text + '.XLocation=' + IntToStr(Slk.XLocation));
        APS_TextProps.Add(Slk.Text + '.YLocation=' + IntToStr(Slk.YLocation));

        Slk.BeginModify;
        Slk.MoveToXY(APS_Board.XOrigin - 1000000, APS_Board.YOrigin - 1000000);
        Slk.EndModify;
      end;
    end;
    Slk := Iterator.NextPCBObject;
  end;
  APS_Board.BoardIterator_Destroy(Iterator);
end;

// Leading letters of a designator: TP12 -> TP
function APS_DesignatorPrefix(Designator: TPCBString): TPCBString;
var
  i: Integer;
  c: String;
begin
  result := '';
  for i := 1 to Length(Designator) do
  begin
    c := Copy(Designator, i, 1);
    if (c >= '0') and (c <= '9') then
      Break;
    result := result + c;
  end;
end;

// Hide the designators of in-scope components whose prefix is in the
// auto-hide list (e.g. TP, MT, FID)
procedure APS_AutoHideDesignators(Dummy: Integer);
var
  Iterator: IPCB_BoardIterator;
  Cmp: IPCB_Component;
  Prefix: TPCBString;
  i: Integer;
  Match: Boolean;
begin
  if (APS_AutoHideList.Count = 0) then
    Exit;

  Iterator := APS_Board.BoardIterator_Create;
  Iterator.AddFilter_ObjectSet(MkSet(eComponentObject));
  Iterator.AddFilter_LayerSet(MkSet(eTopLayer, eBottomLayer));
  Iterator.AddFilter_Method(eProcessAll);

  Cmp := Iterator.FirstPCBObject;
  while Cmp <> nil do
  begin
    if Cmp.NameOn and APS_InScope(Cmp) then
    begin
      Prefix := LowerCase(APS_DesignatorPrefix(Cmp.Name.Text));
      Match := False;
      if Prefix <> '' then
        for i := 0 to APS_AutoHideList.Count - 1 do
          if LowerCase(APS_AutoHideList[i]) = Prefix then
          begin
            Match := True;
            Break;
          end;

      if Match then
      begin
        Cmp.Name.BeginModify;
        Cmp.ChangeNameAutoposition := eAutoPos_CenterCenter;
        Cmp.Name.EndModify;

        Cmp.BeginModify;
        Cmp.NameOn := False;
        Cmp.EndModify;
        APS_AutoHidden.Add(Cmp.Name.Text);
      end;
    end;
    Cmp := Iterator.NextPCBObject;
  end;
  APS_Board.BoardIterator_Destroy(Iterator);
end;

// Show every in-scope designator so previously hidden ones get placed too
procedure APS_UnhideAllDesignators(Dummy: Integer);
var
  Iterator: IPCB_BoardIterator;
  Cmp: IPCB_Component;
begin
  Iterator := APS_Board.BoardIterator_Create;
  Iterator.AddFilter_ObjectSet(MkSet(eComponentObject));
  Iterator.AddFilter_LayerSet(MkSet(eTopLayer, eBottomLayer));
  Iterator.AddFilter_Method(eProcessAll);

  Cmp := Iterator.FirstPCBObject;
  while Cmp <> nil do
  begin
    if (not Cmp.NameOn) and APS_InScope(Cmp) then
    begin
      Cmp.BeginModify;
      Cmp.NameOn := True;
      Cmp.EndModify;
    end;
    Cmp := Iterator.NextPCBObject;
  end;
  APS_Board.BoardIterator_Destroy(Iterator);
end;

procedure APS_HideDesignators(SlkList: TObjectList);
var
  Slk: IPCB_Text;
  i: Integer;
begin
  for i := 0 to SlkList.Count - 1 do
  begin
    Slk := SlkList[i];
    Slk.BeginModify;
    Slk.Component.ChangeNameAutoposition := eAutoPos_CenterCenter;
    Slk.EndModify;

    Slk.Component.BeginModify;
    Slk.Component.NameOn := False;
    Slk.Component.EndModify;
  end;
end;

procedure APS_MoveSilkOverComp(SlkList: TObjectList);
var
  Slk: IPCB_Text;
  i: Integer;
begin
  for i := 0 to SlkList.Count - 1 do
  begin
    Slk := SlkList[i];
    Slk.BeginModify;
    Slk.Component.ChangeNameAutoposition := eAutoPos_CenterCenter;
    Slk.EndModify;
  end;
end;

procedure APS_RestoreComp(SlkList: TObjectList);
var
  Slk: IPCB_Text;
  i: Integer;
  Idx: Integer;
  X, Y: Integer;
begin
  for i := 0 to SlkList.Count - 1 do
  begin
    Slk := SlkList[i];
    Idx := APS_TextProps.IndexOfName(Slk.Text + '.Rotation');
    if Idx < 0 then
      Continue;

    Slk.BeginModify;
    Slk.Rotation := APS_TextProps.ValueFromIndex[Idx];
    Idx := APS_TextProps.IndexOfName(Slk.Text + '.Width');
    Slk.Width := APS_TextProps.ValueFromIndex[Idx];
    Idx := APS_TextProps.IndexOfName(Slk.Text + '.Size');
    Slk.Size := APS_TextProps.ValueFromIndex[Idx];
    Slk.EndModify;

    Slk.BeginModify;
    Idx := APS_TextProps.IndexOfName(Slk.Text + '.XLocation');
    X := APS_TextProps.ValueFromIndex[Idx];
    Idx := APS_TextProps.IndexOfName(Slk.Text + '.YLocation');
    Y := APS_TextProps.ValueFromIndex[Idx];
    Slk.MoveToXY(X, Y);
    Slk.EndModify;
  end;
end;

function APS_StrToAutoPos(Name: String): Integer;
begin
  case Name of
    'CenterRight': result := eAutoPos_CenterRight;
    'TopCenter': result := eAutoPos_TopCenter;
    'CenterLeft': result := eAutoPos_CenterLeft;
    'BottomCenter': result := eAutoPos_BottomCenter;
    'TopLeft': result := eAutoPos_TopLeft;
    'TopRight': result := eAutoPos_TopRight;
    'BottomLeft': result := eAutoPos_BottomLeft;
    'BottomRight': result := eAutoPos_BottomRight;
  else
    result := -1;
  end;
end;

procedure APS_AutoPosDeltaAdjust(autoPos: Integer; X_offset: Integer; Y_offset: Integer;
  Silk: IPCB_Text; Layer: TPCBString);
var
  dx, dy, d: Integer;
  flipx: Integer;
  R: Integer;
begin
  d := APS_PositionDelta;
  dx := 0;
  dy := 0;
  R := Silk.Rotation;
  flipx := 1; // x direction flips on the bottom layer
  if Layer = 'Bottom Layer' then
    flipx := -1;

  case autoPos of
    eAutoPos_CenterRight: dx := -d * flipx;
    eAutoPos_TopCenter: dy := -d;
    eAutoPos_CenterLeft: dx := d * flipx;
    eAutoPos_BottomCenter: dy := d;
    eAutoPos_TopLeft: dy := -d;
    eAutoPos_TopRight: dy := -d;
    eAutoPos_BottomLeft: dy := d;
    eAutoPos_BottomRight: dy := d;
  end;

  if (R = 90) or (R = 270) then
  begin
    if (autoPos = eAutoPos_TopLeft) or (autoPos = eAutoPos_BottomLeft) then
      dx := d * flipx
    else if (autoPos = eAutoPos_TopRight) or (autoPos = eAutoPos_BottomRight) then
      dx := -d * flipx;
  end;
  Silk.MoveByXY(dx + MilsToCoord(X_offset), dy + MilsToCoord(Y_offset));
end;

function APS_MirrorBottomRotation(Text: IPCB_Text; Rotation: TAngle): TAngle;
begin
  result := Rotation;
  if Text.Layer = eBottomOverlay then
    result := 360 - Rotation;
end;

procedure APS_RotationMatchSilk2Comp(Silk: IPCB_Text);
var
  R: Integer;
begin
  R := Silk.Component.Rotation;
  if (R = 0) or (R = 180) or (R = 360) then
    Silk.Rotation := APS_MirrorBottomRotation(Silk, 0)
  else if (R = 90) or (R = 270) then
    Silk.Rotation := APS_MirrorBottomRotation(Silk, 90);
end;

// 1 = horizontal part, 0 = vertical, from the pad grid (strategy "Along Pins")
function APS_CalculateHor(Component: IPCB_Component): Integer;
var
  CompIterator: IPCB_GroupIterator;
  Pad: IPCB_Pad2;
  OldRotation: Float;
  DictionaryX: TStringList;
  DictionaryY: TStringList;
  Number: String;
  Indeks: Integer;
  Num: Integer;
  MaxX: Integer;
  MaxY: Integer;
  PadX: Integer;
  PadY: Integer;
  PadMaxX: Integer;
  PadMinX: Integer;
  PadMaxY: Integer;
  PadMinY: Integer;
begin
  Indeks := APS_DictionaryCache.IndexOfName(Component.Pattern);
  if Indeks <> -1 then
  begin
    result := APS_DictionaryCache.ValueFromIndex[Indeks];
    Exit;
  end;

  OldRotation := Component.Rotation;
  Component.BeginModify;
  Component.Rotation := 0;
  Component.EndModify;

  CompIterator := Component.GroupIterator_Create;
  CompIterator.AddFilter_ObjectSet(MkSet(ePadObject));
  DictionaryX := TStringList.Create;
  DictionaryY := TStringList.Create;
  DictionaryX.NameValueSeparator := '=';
  DictionaryY.NameValueSeparator := '=';
  MaxX := 1;
  MaxY := 1;

  Pad := CompIterator.FirstPCBObject;
  while (Pad <> nil) do
  begin
    PadX := IntToStr(Trunc(CoordToMMs(Pad.X) * 100));
    PadY := IntToStr(Trunc(CoordToMMs(Pad.Y) * 100));

    if DictionaryX.Count = 0 then
    begin
      PadMinX := Pad.X;
      PadMaxX := Pad.X;
      PadMinY := Pad.Y;
      PadMaxY := Pad.Y;
    end;
    if PadMinX > Pad.X then PadMinX := Pad.X;
    if PadMaxX < Pad.X then PadMaxX := Pad.X;
    if PadMinY > Pad.Y then PadMinY := Pad.Y;
    if PadMaxY < Pad.Y then PadMaxY := Pad.Y;

    Indeks := DictionaryX.IndexOfName(PadX);
    if Indeks = -1 then
      DictionaryX.Add(PadX + '=1')
    else
    begin
      Number := DictionaryX.ValueFromIndex[Indeks];
      Num := StrToInt(Number) + 1;
      if Num > MaxX then MaxX := Num;
      DictionaryX.Put(Indeks, PadX + '=' + IntToStr(Num));
    end;

    Indeks := DictionaryY.IndexOfName(PadY);
    if Indeks = -1 then
      DictionaryY.Add(PadY + '=1')
    else
    begin
      Number := DictionaryY.ValueFromIndex[Indeks];
      Num := StrToInt(Number) + 1;
      if Num > MaxY then MaxY := Num;
      DictionaryY.Put(Indeks, PadY + '=' + IntToStr(Num));
    end;

    Pad := CompIterator.NextPCBObject;
  end;
  Component.GroupIterator_Destroy(CompIterator);
  DictionaryX.Free;
  DictionaryY.Free;

  Component.BeginModify;
  Component.Rotation := OldRotation;
  Component.EndModify;

  if MaxY > MaxX then
    result := 1
  else if MaxY < MaxX then
    result := 0
  else if (PadMaxX - PadMinX) > (PadMaxY - PadMinY) then
    result := 1
  else
    result := 0;
  APS_DictionaryCache.Add(Component.Pattern + '=' + IntToStr(result));
end;

// Like APS_CalculateHor, but reads the orientation from where pin 1 sits
// relative to the other pads (strategy "KLC Style")
function APS_CalculateHor2(Component: IPCB_Component): Integer;
var
  CompIterator: IPCB_GroupIterator;
  Pad: IPCB_Pad2;
  OldRotation: Float;
  DictionaryX: TStringList;
  DictionaryY: TStringList;
  Number: String;
  Indeks: Integer;
  Num: Integer;
  MaxX: Integer;
  MaxY: Integer;
  PadX: Integer;
  PadY: Integer;
  PadMaxX: Integer;
  PadMinX: Integer;
  PadMaxY: Integer;
  PadMinY: Integer;
  Pad1X: Integer;
  Pad1Y: Integer;
  EPS: Integer;
  Q1, Q2, Q3, Q4: Integer;
  Count: Integer;
begin
  Indeks := APS_DictionaryCache.IndexOfName(Component.Pattern);
  if Indeks <> -1 then
  begin
    result := APS_DictionaryCache.ValueFromIndex[Indeks];
    Exit;
  end;

  OldRotation := Component.Rotation;
  Component.BeginModify;
  Component.Rotation := 0;
  Component.EndModify;

  CompIterator := Component.GroupIterator_Create;
  CompIterator.AddFilter_ObjectSet(MkSet(ePadObject));
  DictionaryX := TStringList.Create;
  DictionaryY := TStringList.Create;
  DictionaryX.NameValueSeparator := '=';
  DictionaryY.NameValueSeparator := '=';
  MaxX := 1;
  MaxY := 1;
  result := 0;

  Count := 0;
  Pad := CompIterator.FirstPCBObject;
  while (Pad <> nil) do
  begin
    if (Pad.Name = '1') then
    begin
      Pad1X := Pad.X;
      Pad1Y := Pad.Y;
    end;
    Count := Count + 1;
    Pad := CompIterator.NextPCBObject;
  end;

  Pad := CompIterator.FirstPCBObject;
  Q1 := 0;
  Q2 := 0;
  Q3 := 0;
  Q4 := 0;
  EPS := MMsToCoord(0.01);
  while (Pad <> nil) do
  begin
    if (Pad.Name <> '1') then
    begin
      if (Pad.X - Pad1X < EPS) and (Pad.Y - Pad1Y > -EPS) then Q1 := Q1 + 1;
      if (Pad.X - Pad1X > -EPS) and (Pad.Y - Pad1Y > -EPS) then Q2 := Q2 + 1;
      if (Pad.X - Pad1X > -EPS) and (Pad.Y - Pad1Y < EPS) then Q3 := Q3 + 1;
      if (Pad.X - Pad1X < EPS) and (Pad.Y - Pad1Y < EPS) then Q4 := Q4 + 1;
    end;

    PadX := IntToStr(Trunc(CoordToMMs(Pad.X) * 100));
    PadY := IntToStr(Trunc(CoordToMMs(Pad.Y) * 100));

    if DictionaryX.Count = 0 then
    begin
      PadMinX := Pad.X;
      PadMaxX := Pad.X;
      PadMinY := Pad.Y;
      PadMaxY := Pad.Y;
    end;
    if PadMinX > Pad.X then PadMinX := Pad.X;
    if PadMaxX < Pad.X then PadMaxX := Pad.X;
    if PadMinY > Pad.Y then PadMinY := Pad.Y;
    if PadMaxY < Pad.Y then PadMaxY := Pad.Y;

    Indeks := DictionaryX.IndexOfName(PadX);
    if Indeks = -1 then
      DictionaryX.Add(PadX + '=1')
    else
    begin
      Number := DictionaryX.ValueFromIndex[Indeks];
      Num := StrToInt(Number) + 1;
      if Num > MaxX then MaxX := Num;
      DictionaryX.Put(Indeks, PadX + '=' + IntToStr(Num));
    end;

    Indeks := DictionaryY.IndexOfName(PadY);
    if Indeks = -1 then
      DictionaryY.Add(PadY + '=1')
    else
    begin
      Number := DictionaryY.ValueFromIndex[Indeks];
      Num := StrToInt(Number) + 1;
      if Num > MaxY then MaxY := Num;
      DictionaryY.Put(Indeks, PadY + '=' + IntToStr(Num));
    end;

    Pad := CompIterator.NextPCBObject;
  end;
  Component.GroupIterator_Destroy(CompIterator);
  DictionaryX.Free;
  DictionaryY.Free;

  Component.BeginModify;
  Component.Rotation := OldRotation;
  Component.EndModify;

  if (Q1 = 0) and (Q2 > 0) and (Q3 > 0) and (Q4 >= 0) then result := 1;
  if (Q2 = 0) and (Q1 >= 0) and (Q3 > 0) and (Q4 > 0) then result := 0;
  if (Q3 = 0) and (Q1 > 0) and (Q2 >= 0) and (Q4 > 0) then result := 1;
  if (Q4 = 0) and (Q1 > 0) and (Q2 > 0) and (Q3 >= 0) then result := 0;

  if (Q1 < Q4) and (Q2 < Q3) and (Q1 > 0) and (Q2 > 0) then result := 1;
  if (Q2 < Q1) and (Q3 < Q4) and (Q2 > 0) and (Q3 > 0) then result := 0;
  if (Q3 < Q2) and (Q4 < Q1) and (Q3 > 0) and (Q4 > 0) then result := 1;
  if (Q4 < Q3) and (Q1 < Q2) and (Q4 > 0) and (Q1 > 0) then result := 0;

  if (Count = 2) then
  begin
    if (PadMaxX - PadMinX) > (PadMaxY - PadMinY) then
      result := 1
    else
      result := 0;
  end;
  APS_DictionaryCache.Add(Component.Pattern + '=' + IntToStr(result));
end;

// Text rotation for "Along pins" / "KLC style": along the pad rows
procedure APS_RotationAlongPins(Silk: IPCB_Text; SilkscreenHor: Integer);
var
  CR: Integer;
begin
  CR := Silk.Component.Rotation;
  if (SilkscreenHor = 1) then
  begin
    if (CR = 0) or (CR = 180) or (CR = 360) then
      Silk.Rotation := APS_MirrorBottomRotation(Silk, 0)
    else if (CR = 90) or (CR = 270) then
      Silk.Rotation := APS_MirrorBottomRotation(Silk, 90);
  end
  else
  begin
    if (CR = 0) or (CR = 180) or (CR = 360) then
      Silk.Rotation := APS_MirrorBottomRotation(Silk, 90)
    else if (CR = 90) or (CR = 270) then
      Silk.Rotation := APS_MirrorBottomRotation(Silk, 0);
  end;
end;

procedure APS_RotationSilk(Silk: IPCB_Text; SilkscreenHor: Integer; NameAutoPosition: Integer);
var
  CR: Integer;
begin
  CR := Silk.Component.Rotation;
  if APS_RotationStrategy = 0 then
  begin
    // Component rotation
    if (CR = 0) or (CR = 180) or (CR = 360) then
      Silk.Rotation := APS_MirrorBottomRotation(Silk, 0)
    else if (CR = 90) or (CR = 270) then
      Silk.Rotation := APS_MirrorBottomRotation(Silk, 90);
  end
  else if APS_RotationStrategy = 1 then
    Silk.Rotation := APS_MirrorBottomRotation(Silk, 0)       // Horizontal
  else if APS_RotationStrategy = 2 then
  begin
    // Along side: vertical text beside the part, horizontal above/below
    if (NameAutoPosition = eAutoPos_CenterRight) or (NameAutoPosition = eAutoPos_CenterLeft) then
      Silk.Rotation := APS_MirrorBottomRotation(Silk, 90)
    else
      Silk.Rotation := APS_MirrorBottomRotation(Silk, 0);
  end
  else if APS_RotationStrategy = 3 then
  begin
    // Along axis
    if (Silk.Component.BoundingRectangle.Right - Silk.Component.BoundingRectangle.Left) >
      (Silk.Component.BoundingRectangle.Top - Silk.Component.BoundingRectangle.Bottom) then
      Silk.Rotation := APS_MirrorBottomRotation(Silk, 0)
    else
      Silk.Rotation := APS_MirrorBottomRotation(Silk, 90);
  end
  else
    APS_RotationAlongPins(Silk, SilkscreenHor);              // 4 along pins, 5 KLC style
end;

// The other readable orientation: 0 <-> 90 on top, 0 <-> 270 on the
// mirrored bottom. The original script's "90 - Rotation" turned bottom text
// to 90/180, which reads upside down.
procedure APS_AlterRotation(Silk: IPCB_Text);
var
  U: Integer;
begin
  if Silk.Layer = eBottomOverlay then
  begin
    U := (360 - Round(Silk.Rotation)) mod 360;   // unmirrored: 0 or 90
    Silk.Rotation := (360 - (90 - U)) mod 360;
  end
  else
    Silk.Rotation := 90 - Silk.Rotation;
end;

// Size and stroke for a fresh attempt
procedure APS_ResetSize(Silkscreen: IPCB_Text; MinSize: Integer);
begin
  if APS_IsFixedSize then
    Silkscreen.Size := APS_FixedSize
  else
    Silkscreen.Size := MilsToCoord(APS_GetSilkSize(Silkscreen, MinSize));
  if APS_IsFixedWidth then
    Silkscreen.Width := APS_FixedWidth
  else
    Silkscreen.Width := 2 * (Silkscreen.Size / 10);
end;

// Base rectangle/anchor for every enabled autoposition at the current size
procedure APS_BuildBases(Silkscreen: IPCB_Text; SilkscreenHor: Integer; AlteredRotation: Integer;
  CompLayerName: TPCBString);
var
  i, NextAutoP: Integer;
  Rect: TCoordRect;
begin
  APS_BaseL.Clear; APS_BaseB.Clear; APS_BaseR.Clear; APS_BaseT.Clear;
  APS_BaseX.Clear; APS_BaseY.Clear; APS_BaseIdx.Clear;
  for i := 0 to APS_Positions.Count - 1 do
  begin
    NextAutoP := APS_StrToAutoPos(APS_Positions[i]);
    if NextAutoP < 0 then
      Continue;

    APS_RotationSilk(Silkscreen, SilkscreenHor, NextAutoP);
    if AlteredRotation = 1 then
      APS_AlterRotation(Silkscreen);

    Silkscreen.Component.ChangeNameAutoposition := NextAutoP;
    APS_AutoPosDeltaAdjust(NextAutoP, 0, 0, Silkscreen, CompLayerName);

    Rect := APS_GetObjRect(Silkscreen);
    APS_BaseIdx.Add(IntToStr(i));
    APS_BaseL.Add(IntToStr(Rect.Left));
    APS_BaseB.Add(IntToStr(Rect.Bottom));
    APS_BaseR.Add(IntToStr(Rect.Right));
    APS_BaseT.Add(IntToStr(Rect.Top));
    APS_BaseX.Add(IntToStr(Silkscreen.XLocation));
    APS_BaseY.Add(IntToStr(Silkscreen.YLocation));
  end;
end;

// Commit base b shifted by (dx, dy), with undo bracketing
procedure APS_Commit(Silkscreen: IPCB_Text; SilkscreenHor: Integer; AlteredRotation: Integer;
  b: Integer; dx: TCoord; dy: TCoord);
var
  NextAutoP: Integer;
begin
  NextAutoP := APS_StrToAutoPos(APS_Positions[StrToInt(APS_BaseIdx.Get(b))]);
  Silkscreen.BeginModify;
  APS_RotationSilk(Silkscreen, SilkscreenHor, NextAutoP);
  if AlteredRotation = 1 then
    APS_AlterRotation(Silkscreen);
  Silkscreen.Component.ChangeNameAutoposition := NextAutoP;
  Silkscreen.MoveToXY(StrToInt(APS_BaseX.Get(b)) + dx, StrToInt(APS_BaseY.Get(b)) + dy);
  Silkscreen.EndModify;
end;

// Not placed: reset size and park the designator off the board
procedure APS_Park(Silkscreen: IPCB_Text);
begin
  Silkscreen.BeginModify;
  APS_ResetSize(Silkscreen, 30);
  APS_RotationMatchSilk2Comp(Silkscreen);
  Silkscreen.EndModify;

  Silkscreen.BeginModify;
  Silkscreen.Component.ChangeNameAutoposition := eAutoPos_Manual;
  Silkscreen.MoveToXY(APS_Board.XOrigin - 1000000, APS_Board.YOrigin + 1000000);
  Silkscreen.EndModify;
end;

function APS_SilkscreenHor(Silkscreen: IPCB_Text): Integer;
begin
  if APS_RotationStrategy = 4 then
    result := APS_CalculateHor(Silkscreen.Component)
  else if APS_RotationStrategy = 5 then
    result := APS_CalculateHor2(Silkscreen.Component)
  else
    result := -1;
end;

// Cache obstacles within reach of every candidate
procedure APS_CacheAround(Silkscreen: IPCB_Text; ReachMils: Integer);
var
  Rect, CmpRect: TCoordRect;
  MaxDim, RegionExpansion: TCoord;
begin
  Rect := APS_GetObjRect(Silkscreen);
  MaxDim := Rect.Right - Rect.Left;
  if (Rect.Top - Rect.Bottom) > MaxDim then
    MaxDim := Rect.Top - Rect.Bottom;
  RegionExpansion := MaxDim + APS_PositionDelta + MilsToCoord(ReachMils + 20);
  CmpRect := APS_GetObjRect(Silkscreen.Component);
  APS_BuildObstacleCache(Silkscreen, CmpRect.Left - RegionExpansion,
    CmpRect.Bottom - RegionExpansion, CmpRect.Right + RegionExpansion,
    CmpRect.Top + RegionExpansion);
end;

// Try to place one designator: autoposition x offset grid x shrinking size x
// optional altered rotation, tested in memory; only the winner is committed
function APS_PlaceSilkscreen(Silkscreen: IPCB_Text; MaxOffset: Integer): Boolean;
const
  OFFSET_DELTA = 5;       // [mils] grid step
  MIN_SILK_SIZE = 30;     // [mils]
  ABS_MIN_SILK_SIZE = 25; // [mils]
  SILK_SIZE_DELTA = 5;    // [mils] shrink step
var
  xinc, yinc, xoff, yoff: Integer;
  dx, dy: TCoord;
  b: Integer;
  SilkscreenHor: Integer;
  AlteredRotation: Integer;
  CompLayerName: TPCBString;
begin
  result := False;
  if Silkscreen.IsHidden then
  begin
    result := True;
    Exit;
  end;

  SilkscreenHor := APS_SilkscreenHor(Silkscreen);
  CompLayerName := Layer2String(Silkscreen.Component.Layer);

  APS_ResetSize(Silkscreen, MIN_SILK_SIZE);
  APS_CacheAround(Silkscreen, MaxOffset * OFFSET_DELTA);

  for AlteredRotation := 0 to APS_TryAlteredRotation do
  begin
    APS_ResetSize(Silkscreen, MIN_SILK_SIZE);

    while (CoordToMils(Silkscreen.Size) >= ABS_MIN_SILK_SIZE) or APS_IsFixedSize do
    begin
      APS_BuildBases(Silkscreen, SilkscreenHor, AlteredRotation, CompLayerName);

      xoff := 0;
      for xinc := 0 to MaxOffset do
      begin
        yoff := 0;
        for yinc := 0 to MaxOffset do
        begin
          dx := MilsToCoord(xoff * OFFSET_DELTA);
          dy := MilsToCoord(yoff * OFFSET_DELTA);
          for b := 0 to APS_BaseIdx.Count - 1 do
          begin
            if APS_CandidateIsClear(StrToInt(APS_BaseL.Get(b)) + dx, StrToInt(APS_BaseB.Get(b)) + dy,
              StrToInt(APS_BaseR.Get(b)) + dx, StrToInt(APS_BaseT.Get(b)) + dy) then
            begin
              APS_Commit(Silkscreen, SilkscreenHor, AlteredRotation, b, dx, dy);
              result := True;
              Exit;
            end;
          end;
          yoff := yoff * -1;
          if yoff >= 0 then
            yoff := yoff + 1;
        end;
        xoff := xoff * -1;
        if xoff >= 0 then
          xoff := xoff + 1;
      end;

      if APS_IsFixedSize then
        Break;
      if (CoordToMils(Silkscreen.Size) - SILK_SIZE_DELTA) < ABS_MIN_SILK_SIZE then
        Break;

      Silkscreen.Size := Silkscreen.Size - MilsToCoord(SILK_SIZE_DELTA);
      if APS_IsFixedWidth then
        Silkscreen.Width := APS_FixedWidth
      else
        Silkscreen.Width := Int(2 * (Silkscreen.Size / 10) - 10000);
    end;
  end;

  APS_Park(Silkscreen);
end;

// Second chance: flip rotation on squarish, non-orthogonal parts, then a wider
// offset grid. Placed designators are not added to StillFailed.
function APS_RetryFailed(SlkList: TObjectList; StillFailed: TObjectList; FirstPassOffset: Integer): Integer;
const
  MAX_RATIO = 1.2;
  EXTENDED_OFFSET_CNT = 8; // +/- 40 mil
var
  Slk: IPCB_Text;
  Rect: TCoordRect;
  i, L, w: Integer;
  PlaceCnt: Integer;
  Rotation: Integer;
  R: Integer;
  FlipUseful: Boolean;
  Placed: Boolean;
begin
  PlaceCnt := 0;
  for i := 0 to SlkList.Count - 1 do
  begin
    Slk := SlkList[i];
    Placed := False;

    R := Slk.Component.Rotation;
    FlipUseful := ((APS_RotationStrategy = 0) or (APS_RotationStrategy = 4) or
      (APS_RotationStrategy = 5)) and (R <> 0) and (R <> 90) and (R <> 180) and
      (R <> 270) and (R <> 360);

    if FlipUseful then
    begin
      Rect := APS_GetObjRect(Slk.Component);
      L := Rect.Right - Rect.Left;
      w := Rect.Top - Rect.Bottom;
      if w < L then
      begin
        w := Rect.Right - Rect.Left;
        L := Rect.Top - Rect.Bottom;
      end;
      if (L > 0) then
        if ((w / L) <= MAX_RATIO) then
        begin
          Rotation := Slk.Rotation;
          if (Rotation = 0) or (Rotation = 180) or (Rotation = 360) then
            Slk.Rotation := APS_MirrorBottomRotation(Slk, 90)
          else if (Rotation = 90) or (Rotation = 270) then
            Slk.Rotation := APS_MirrorBottomRotation(Slk, 0)
          else
            Slk.Rotation := Slk.Component.Rotation;

          if APS_PlaceSilkscreen(Slk, FirstPassOffset) then
            Placed := True
          else
            Slk.Rotation := Rotation;
        end;
    end;

    if not Placed then
      Placed := APS_PlaceSilkscreen(Slk, EXTENDED_OFFSET_CNT);

    if Placed then
      Inc(PlaceCnt)
    else
      StillFailed.Add(Slk);
  end;
  result := PlaceCnt;
end;

// 2nd pass: expanding ring grid around every enabled autoposition (both
// rotations); takes the clear spot closest to its ideal position
function APS_WigglePlace(Silkscreen: IPCB_Text): Boolean;
const
  WIGGLE_RADIUS_MILS = 100;
  WIGGLE_STEP_MILS = 10;
  MIN_SILK_SIZE = 30;
  ABS_MIN_SILK_SIZE = 25;
  SILK_SIZE_DELTA = 5;
var
  r, xoff, yoff: Integer;
  WigSteps, Dist2: Integer;
  BestScore, BestB, BestXoff, BestYoff: Integer;
  dx, dy: TCoord;
  b: Integer;
  SilkscreenHor: Integer;
  AlteredRotation: Integer;
  CompLayerName: TPCBString;
begin
  result := False;
  if Silkscreen.IsHidden then
  begin
    result := True;
    Exit;
  end;

  SilkscreenHor := APS_SilkscreenHor(Silkscreen);
  CompLayerName := Layer2String(Silkscreen.Component.Layer);
  WigSteps := WIGGLE_RADIUS_MILS div WIGGLE_STEP_MILS;

  APS_ResetSize(Silkscreen, MIN_SILK_SIZE);
  APS_CacheAround(Silkscreen, WIGGLE_RADIUS_MILS);

  for AlteredRotation := 0 to 1 do
  begin
    APS_ResetSize(Silkscreen, MIN_SILK_SIZE);

    while (CoordToMils(Silkscreen.Size) >= ABS_MIN_SILK_SIZE) or APS_IsFixedSize do
    begin
      APS_BuildBases(Silkscreen, SilkscreenHor, AlteredRotation, CompLayerName);

      BestScore := -1;
      BestB := -1;
      BestXoff := 0;
      BestYoff := 0;
      for r := 0 to WigSteps do
      begin
        if (BestScore >= 0) and ((r * r) >= BestScore) then
          Break;
        for xoff := -r to r do
        begin
          for yoff := -r to r do
          begin
            if (Abs(xoff) <> r) and (Abs(yoff) <> r) then
              Continue;
            Dist2 := (xoff * xoff) + (yoff * yoff);
            if (BestScore >= 0) and (Dist2 >= BestScore) then
              Continue;

            dx := MilsToCoord(xoff * WIGGLE_STEP_MILS);
            dy := MilsToCoord(yoff * WIGGLE_STEP_MILS);
            for b := 0 to APS_BaseIdx.Count - 1 do
            begin
              if APS_CandidateIsClear(StrToInt(APS_BaseL.Get(b)) + dx, StrToInt(APS_BaseB.Get(b)) + dy,
                StrToInt(APS_BaseR.Get(b)) + dx, StrToInt(APS_BaseT.Get(b)) + dy) then
              begin
                BestScore := Dist2;
                BestB := b;
                BestXoff := xoff;
                BestYoff := yoff;
                Break;
              end;
            end;
          end;
        end;
      end;

      if BestB >= 0 then
      begin
        APS_Commit(Silkscreen, SilkscreenHor, AlteredRotation, BestB,
          MilsToCoord(BestXoff * WIGGLE_STEP_MILS), MilsToCoord(BestYoff * WIGGLE_STEP_MILS));
        result := True;
        Exit;
      end;

      if APS_IsFixedSize then
        Break;
      if (CoordToMils(Silkscreen.Size) - SILK_SIZE_DELTA) < ABS_MIN_SILK_SIZE then
        Break;

      Silkscreen.Size := Silkscreen.Size - MilsToCoord(SILK_SIZE_DELTA);
      if APS_IsFixedWidth then
        Silkscreen.Width := APS_FixedWidth
      else
        Silkscreen.Width := Int(2 * (Silkscreen.Size / 10) - 10000);
    end;
  end;

  APS_Park(Silkscreen);
end;

// Mechanical layer for component bodies: the named one, else "Mechanical 13"
// or any layer called "...Component Outline..." (the script's default)
function APS_FindOutlineLayer(Name: String): Integer;
var
  MechIterator: IPCB_LayerObjectIterator;
  LayerObj: IPCB_LayerObject;
begin
  result := 0;
  MechIterator := APS_Board.MechanicalLayerIterator;
  while MechIterator.Next do
  begin
    LayerObj := MechIterator.LayerObject;
    if Name <> '' then
    begin
      if LowerCase(LayerObj.Name) = LowerCase(Name) then
        result := LayerObj.V6_LayerID;
    end
    else if (LayerObj.Name = 'Mechanical 13') or (ContainsText(LayerObj.Name, 'Component Outline')) then
      result := LayerObj.V6_LayerID;
  end;
end;

function APS_Quoted(List: TStringList): String;
var
  i: Integer;
begin
  result := '';
  for i := 0 to List.Count - 1 do
  begin
    if i > 0 then
      result := result + ', ';
    result := result + '"' + JSONEscapeString(List[i]) + '"';
  end;
  result := '[' + result + ']';
end;

// Run the auto placer. Options:
//   Scope             designators to place (empty = SelectedOnly / all)
//   Positions         enabled autoposition names in priority order
//   FailMode          'center' (over the part), 'hide' or 'restore'
//   RotationStrategy  0 component, 1 horizontal, 2 along side, 3 along axis,
//                     4 along pins, 5 KLC style
//   Sizes in mils; FixedSizeMils/FixedWidthMils <= 0 = size from the part
function APS_Run(Scope: TStringList; SelectedOnly: Boolean; Positions: TStringList;
  FailMode: String; AvoidVias: Boolean; RotationStrategy: Integer; TryAlteredRotation: Boolean;
  WiggleEnabled: Boolean; UnhideAll: Boolean; AutoHideList: TStringList; AllowUnder: TStringList;
  FixedSizeMils: Double; FixedWidthMils: Double; PositionDeltaMils: Double;
  OutlineLayerName: String): String;
const
  OFFSET_CNT = 3;
var
  Silkscreen: IPCB_Text;
  Cmp: IPCB_Component;
  Iterator: IPCB_BoardIterator;
  Count, PlaceCnt, Pass1Cnt, RetryCnt, Pass2Cnt, i: Integer;
  NotPlaced, StillFailed, Remaining: TObjectList;
  SortedComps: TStringList;
  FailedNames: TStringList;
  CmpRect: TCoordRect;
  SizeKey: Integer;
  Outline: IPCB_BoardOutline;
  Rule: IPCB_Rule;
  vx, vy: TCoord;
  PCBSystemOptions: IPCB_SystemOptions;
  DRCSetting: Boolean;
  StartTime: TDateTime;
  Props: TStringList;
begin
  APS_Board := GetBoardSafe(0);
  if APS_Board = nil then
  begin
    result := 'ERROR: No PCB document is currently active';
    Exit;
  end;
  StartTime := Now();

  APS_Scope := Scope;
  APS_SelectedOnly := SelectedOnly;
  APS_Positions := Positions;
  APS_AvoidVias := AvoidVias;
  APS_RotationStrategy := RotationStrategy;
  if TryAlteredRotation then APS_TryAlteredRotation := 1 else APS_TryAlteredRotation := 0;
  APS_WiggleEnabled := WiggleEnabled;
  APS_UnhideAll := UnhideAll;
  APS_AutoHideList := AutoHideList;
  APS_AllowUnder := AllowUnder;
  APS_IsFixedSize := FixedSizeMils > 0;
  APS_FixedSize := MilsToCoord(FixedSizeMils);
  APS_IsFixedWidth := FixedWidthMils > 0;
  APS_FixedWidth := MilsToCoord(FixedWidthMils);
  APS_PositionDelta := MilsToCoord(PositionDeltaMils);
  APS_CmpOutlineLayerID := APS_FindOutlineLayer(OutlineLayerName);

  PCBServer.PreProcess;

  // Online DRC off while designators move (speed)
  PCBSystemOptions := PCBServer.SystemOptions;
  if PCBSystemOptions <> nil then
  begin
    DRCSetting := PCBSystemOptions.DoOnlineDRC;
    PCBSystemOptions.DoOnlineDRC := False;
  end;

  APS_TextProps := TStringList.Create;
  APS_TextProps.NameValueSeparator := '=';
  APS_DictionaryCache := TStringList.Create;
  APS_DictionaryCache.NameValueSeparator := '=';
  APS_AutoHidden := TStringList.Create;
  APS_ObsAL := TList.Create; APS_ObsAB := TList.Create; APS_ObsAR := TList.Create; APS_ObsAT := TList.Create;
  APS_ObsBL := TList.Create; APS_ObsBB := TList.Create; APS_ObsBR := TList.Create; APS_ObsBT := TList.Create;
  APS_BaseL := TStringList.Create; APS_BaseB := TStringList.Create; APS_BaseR := TStringList.Create;
  APS_BaseT := TStringList.Create; APS_BaseX := TStringList.Create; APS_BaseY := TStringList.Create;
  APS_BaseIdx := TStringList.Create;
  FailedNames := TStringList.Create;
  Props := TStringList.Create;

  // Board outline: polygon tests only on non-rectangular boards
  APS_BoardRect := APS_GetObjRect(APS_Board);
  APS_BoardIsRectangular := True;
  Outline := APS_Board.BoardOutline;
  if Outline.PointCount <> 4 then
    APS_BoardIsRectangular := False
  else
    for i := 0 to Outline.PointCount - 1 do
    begin
      if Outline.Segments[i].Kind <> ePolySegmentLine then
        APS_BoardIsRectangular := False
      else
      begin
        vx := Outline.Segments[i].vx;
        vy := Outline.Segments[i].vy;
        if ((vx <> APS_BoardRect.Left) and (vx <> APS_BoardRect.Right)) or
          ((vy <> APS_BoardRect.Bottom) and (vy <> APS_BoardRect.Top)) then
          APS_BoardIsRectangular := False;
      end;
    end;
  APS_OutlineX := TList.Create;
  APS_OutlineY := TList.Create;
  for i := 0 to Outline.PointCount - 1 do
  begin
    APS_OutlineX.Add(Outline.Segments[i].vx + APS_COORD_BIAS);
    APS_OutlineY.Add(Outline.Segments[i].vy + APS_COORD_BIAS);
  end;

  // The original script ignores the Board Outline Clearance rule; honour the
  // largest text-to-outline clearance
  APS_EdgeGap := 0;
  Iterator := APS_Board.BoardIterator_Create;
  Iterator.AddFilter_ObjectSet(MkSet(eRuleObject));
  Iterator.AddFilter_LayerSet(AllLayers);
  Iterator.AddFilter_Method(eProcessAll);
  Rule := Iterator.FirstPCBObject;
  while Rule <> nil do
  begin
    if Rule.Enabled and (Rule.RuleKind = eRule_BoardOutlineClearance) then
      if Rule.GetClearance(eObjectClearanceID_Text, eObjectClearanceID_OutlineEdge) > APS_EdgeGap then
        APS_EdgeGap := Rule.GetClearance(eObjectClearanceID_Text, eObjectClearanceID_OutlineEdge);
    Rule := Iterator.NextPCBObject;
  end;
  APS_Board.BoardIterator_Destroy(Iterator);

  if APS_UnhideAll then
    APS_UnhideAllDesignators(0);
  APS_AutoHideDesignators(0);
  APS_MoveSilkOffBoard(0);

  // Smallest components first: dense clusters get first pick of free space
  Iterator := APS_Board.BoardIterator_Create;
  Iterator.AddFilter_ObjectSet(MkSet(eComponentObject));
  Iterator.AddFilter_LayerSet(MkSet(eTopLayer, eBottomLayer));
  Iterator.AddFilter_Method(eProcessAll);
  SortedComps := TStringList.Create;
  Cmp := Iterator.FirstPCBObject;
  while (Cmp <> nil) do
  begin
    if APS_InScope(Cmp) then
    begin
      CmpRect := APS_GetObjRect(Cmp);
      SizeKey := Round(CoordToMils(CmpRect.Right - CmpRect.Left) + CoordToMils(CmpRect.Top - CmpRect.Bottom));
      SortedComps.AddObject(IntToStr(100000000 + SizeKey), Cmp);
    end;
    Cmp := Iterator.NextPCBObject;
  end;
  APS_Board.BoardIterator_Destroy(Iterator);
  SortedComps.Sort;

  NotPlaced := TObjectList.Create;
  StillFailed := TObjectList.Create;

  Count := 0;
  PlaceCnt := 0;
  for i := 0 to SortedComps.Count - 1 do
  begin
    Cmp := SortedComps.Objects[i];
    Silkscreen := Cmp.Name;
    if APS_PlaceSilkscreen(Silkscreen, OFFSET_CNT) then
      Inc(PlaceCnt)
    else
      NotPlaced.Add(Silkscreen);
    Inc(Count);
  end;
  Pass1Cnt := PlaceCnt;

  RetryCnt := APS_RetryFailed(NotPlaced, StillFailed, OFFSET_CNT);
  PlaceCnt := PlaceCnt + RetryCnt;

  Pass2Cnt := 0;
  if APS_WiggleEnabled and (StillFailed.Count > 0) then
  begin
    Remaining := TObjectList.Create;
    for i := 0 to StillFailed.Count - 1 do
    begin
      Silkscreen := StillFailed[i];
      if APS_WigglePlace(Silkscreen) then
      begin
        Inc(PlaceCnt);
        Inc(Pass2Cnt);
      end
      else
        Remaining.Add(Silkscreen);
    end;
    StillFailed := Remaining;
  end;

  for i := 0 to StillFailed.Count - 1 do
  begin
    Silkscreen := StillFailed[i];
    FailedNames.Add(Silkscreen.Text);
  end;

  if FailMode = 'hide' then
    APS_HideDesignators(StillFailed)
  else if FailMode = 'restore' then
    APS_RestoreComp(StillFailed)
  else
    APS_MoveSilkOverComp(StillFailed);

  // Rebuild every touched designator's stroke geometry: batch DRC checks a
  // cached copy that scripted moves and resizes leave stale
  for i := 0 to SortedComps.Count - 1 do
  begin
    Cmp := SortedComps.Objects[i];
    Silkscreen := Cmp.Name;
    Silkscreen.BeginModify;
    Silkscreen.SetState_XSizeYSize;
    Silkscreen.EndModify;
    Silkscreen.GraphicallyInvalidate;
  end;

  if PCBSystemOptions <> nil then
    PCBSystemOptions.DoOnlineDRC := DRCSetting;
  PCBServer.PostProcess;
  APS_Board.ViewManager_FullUpdate;

  AddJSONInteger(Props, 'component_count', Count);
  AddJSONInteger(Props, 'placed_count', PlaceCnt);
  AddJSONInteger(Props, 'placed_first_pass', Pass1Cnt);
  AddJSONInteger(Props, 'placed_retry', RetryCnt);
  AddJSONInteger(Props, 'placed_second_pass', Pass2Cnt);
  AddJSONInteger(Props, 'failed_count', FailedNames.Count);
  Props.Add('"failed": ' + APS_Quoted(FailedNames));
  AddJSONProperty(Props, 'failed_action', FailMode);
  Props.Add('"auto_hidden": ' + APS_Quoted(APS_AutoHidden));
  AddJSONInteger(Props, 'outline_layer_found', APS_CmpOutlineLayerID);
  AddJSONNumber(Props, 'seconds', Round((Now() - StartTime) * 86400 * 10) / 10);
  AddJSONProperty(Props, 'board', APS_Board.FileName);
  result := BuildJSONObject(Props);

  APS_DictionaryCache.Free;
  APS_TextProps.Free;
  APS_AutoHidden.Free;
  SortedComps.Free;
  FailedNames.Free;
  Props.Free;
  APS_ObsAL.Free; APS_ObsAB.Free; APS_ObsAR.Free; APS_ObsAT.Free;
  APS_ObsBL.Free; APS_ObsBB.Free; APS_ObsBR.Free; APS_ObsBT.Free;
  APS_BaseL.Free; APS_BaseB.Free; APS_BaseR.Free; APS_BaseT.Free;
  APS_BaseX.Free; APS_BaseY.Free; APS_BaseIdx.Free;
  APS_OutlineX.Free; APS_OutlineY.Free;
end;
