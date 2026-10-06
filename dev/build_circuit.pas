// Build a schematic circuit from a circuit spec file
// (C:\Users\Public\altium_mcp\circuit_spec.txt). A generator script writes
// it, taking each part's data from the database as dev/make_part_spec.py does.
//
// Places database-linked components, wires them, and adds junctions, net
// labels, power ports and notes. Afterwards it writes pin_map.txt - every
// placed pin's ABSOLUTE location - so wire routes can be computed from real
// coordinates instead of predicted ones (rotated symbols make prediction
// unreliable).
//
// Spec records:
//   PART|desig|symlib|symbol|designitemid|x|y|orient
//   COMMENT|t   DESCRIPTION|t   FOOTPRINT|n   PARAM|name|value
//   WIRE|x1|y1|x2|y2[|...]   JUNCTION|x|y
//   NETLABEL|x|y|orient|text   POWER|x|y|orient|style|text   NOTE|x|y|text
//
// SAFETY: opening a symbol library makes THAT document current, and stray
// components were once registered into shared libraries on the read-only
// share that way. So the target sheet is created up front, held in TargetDoc,
// and verified to be a schematic (ObjectID 32, not 33 = library) exactly once.
// Everything registers into TargetDoc by reference, never into "current".

SandboxLog('loading circuit spec');
List1 := TStringList.Create;
List1.LoadFromFile('C:\Users\Public\altium_mcp\circuit_spec.txt');
SandboxLog('spec lines: ' + IntToStr(List1.Count));

SandboxLog('creating target schematic');
GetWorkSpace.DM_CreateNewDocument('SCH');
TargetDoc := SchServer.GetCurrentSchDocument;
SandboxLog('target: ' + TargetDoc.DocumentName + ' objectID=' + IntToStr(TargetDoc.ObjectID));

if (TargetDoc.ObjectID <> 32) then
begin
    ResultText := '{"error": "target is not a schematic - refusing to build"}';
    SandboxLog('ABORT: target is not a schematic');
end
else
begin
    SchServer.ProcessControl.PreProcess(TargetDoc, '');

    Obj3 := nil;    // component currently being configured
    S4 := '';       // symbol library currently open (avoids reopening)
    I4 := 0;        // parts placed
    I5 := 0;        // graphics placed (wires/junctions/labels/ports/notes)

    for I3 := 0 to List1.Count - 1 do
    begin
        S1 := List1[I3];
        S2 := SbxField(S1, 0);

        // ---------------------------------------------------------- PART
        if (S2 = 'PART') then
        begin
            S3 := SbxField(S1, 2);                 // symbol library path
            if (S3 <> S4) then
            begin
                SandboxLog('opening library ' + S3);
                Obj2 := Client.OpenDocument('SchLib', S3);
                Client.ShowDocument(Obj2);
                Sleep(1200);
                S4 := S3;
            end;
            Obj1 := SchServer.GetCurrentSchDocument;

            // locate the symbol by LibReference
            Obj3 := nil;
            Obj2 := Obj1.SchLibIterator_Create;
            Obj2.AddFilter_ObjectSet(MkSet(eSchComponent));
            Obj6 := Obj2.FirstSchObject;
            while (Obj6 <> nil) do
            begin
                if (Obj6.LibReference = SbxField(S1, 3)) then Obj3 := Obj6;
                Obj6 := Obj2.NextSchObject;
            end;
            Obj1.SchIterator_Destroy(Obj2);

            if (Obj3 = nil) then
                SandboxLog('  MISSING symbol ' + SbxField(S1, 3) + ' for ' + SbxField(S1, 1))
            else
            begin
                Obj3 := Obj3.Replicate;
                Obj3.Designator.Text := SbxField(S1, 1);
                Obj3.DesignItemID    := SbxField(S1, 4);
                Obj3.Orientation     := StrToInt(SbxField(S1, 7));

                TargetDoc.RegisterSchObjectInContainer(Obj3);
                SchServer.RobotManager.SendMessage(TargetDoc.I_ObjectAddress, c_BroadCast,
                    SCHM_PrimitiveRegistration, Obj3.I_ObjectAddress);

                // Field 9 = mirror (the schematic editor's X key). Applied
                // after registration; the flag does not survive on a detached
                // replica. Pin x-positions are logged either side so it is
                // evident whether the pins actually moved.
                if (SbxField(S1, 8) = '1') then
                begin
                    Obj5 := Obj3.SchIterator_Create;
                    Obj5.AddFilter_ObjectSet(MkSet(ePin));
                    Obj7 := Obj5.FirstSchObject;
                    S5 := '';
                    while (Obj7 <> nil) do
                    begin
                        S5 := S5 + Obj7.Name + '@' + IntToStr(CoordToMils(Obj7.Location.X)) + ' ';
                        Obj7 := Obj5.NextSchObject;
                    end;
                    Obj3.SchIterator_Destroy(Obj5);
                    SandboxLog('  mirror before: ' + S5);

                    // IsMirrored is a display flag only - verified: it sets
                    // True but the pin coordinates do not move. The real
                    // transform is Mirror(Axis), where Axis is a point the
                    // component is reflected about (the editor's X key).
                    SandboxLog('  calling Mirror(axis) about component Location');
                    Obj3.Mirror(Obj3.Location);
                    SandboxLog('  Mirror(axis) returned');

                    Obj5 := Obj3.SchIterator_Create;
                    Obj5.AddFilter_ObjectSet(MkSet(ePin));
                    Obj7 := Obj5.FirstSchObject;
                    S5 := '';
                    while (Obj7 <> nil) do
                    begin
                        S5 := S5 + Obj7.Name + '@' + IntToStr(CoordToMils(Obj7.Location.X)) + ' ';
                        Obj7 := Obj5.NextSchObject;
                    end;
                    Obj3.SchIterator_Destroy(Obj5);
                    SandboxLog('  mirror after : ' + S5);
                end;

                // MoveByXY (not Location) so the designator/comment text moves too
                Obj3.MoveByXY(
                    MilsToCoord(StrToInt(SbxField(S1, 5)) - CoordToMils(Obj3.Location.X)),
                    MilsToCoord(StrToInt(SbxField(S1, 6)) - CoordToMils(Obj3.Location.Y)));

                I4 := I4 + 1;
                SandboxLog('  placed ' + SbxField(S1, 1) + ' (' + SbxField(S1, 3) + ') at ' +
                           SbxField(S1, 5) + ',' + SbxField(S1, 6) + ' o' + SbxField(S1, 7));
            end;
        end

        // ------------------------------------------ per-component records
        else if (S2 = 'COMMENT') then
        begin
            if (Obj3 <> nil) then Obj3.Comment.Text := SbxField(S1, 1);
        end
        else if (S2 = 'DESCRIPTION') then
        begin
            if (Obj3 <> nil) then Obj3.ComponentDescription := SbxField(S1, 1);
        end
        else if (S2 = 'FOOTPRINT') then
        begin
            if (Obj3 <> nil) then
            begin
                Obj5 := Obj3.AddSchImplementation;
                Obj5.ModelName := SbxField(S1, 1);
                Obj5.ModelType := 'PCBLIB';
                Obj5.IsCurrent := True;
                Obj5.UseComponentLibrary := True;
            end;
        end
        else if (S2 = 'PARAM') then
        begin
            if (Obj3 <> nil) then
            begin
                S5 := SbxField(S1, 1);            // parameter name
                S3 := SbxField(S1, 2);            // parameter value

                // overwrite the symbol's placeholder if it already has one
                B1 := 0;
                Obj2 := Obj3.SchIterator_Create;
                Obj2.AddFilter_ObjectSet(MkSet(eParameter));
                Obj6 := Obj2.FirstSchObject;
                while (Obj6 <> nil) do
                begin
                    if (UpperCase(Obj6.Name) = UpperCase(S5)) then
                    begin
                        Obj6.Text := S3;
                        B1 := 1;
                    end;
                    Obj6 := Obj2.NextSchObject;
                end;
                Obj3.SchIterator_Destroy(Obj2);

                if (B1 = 0) then
                begin
                    Obj6 := SchServer.SchObjectFactory(eParameter, eCreate_Default);
                    Obj6.Name := S5;
                    Obj6.Text := S3;
                    Obj6.ParamType := eParameterType_String;
                    Obj6.ReadOnlyState := eReadOnly_None;
                    Obj6.IsHidden := True;
                    Obj3.AddSchObject(Obj6);
                    SchServer.RobotManager.SendMessage(Obj3.I_ObjectAddress, c_BroadCast,
                        SCHM_PrimitiveRegistration, Obj6.I_ObjectAddress);
                end;
            end;
        end

        // ---------------------------------------------------------- WIRE
        else if (S2 = 'WIRE') then
        begin
            Obj5 := SchServer.SchObjectFactory(eWire, eCreate_GlobalCopy);
            Obj5.Location := Point(MilsToCoord(StrToInt(SbxField(S1, 1))),
                                   MilsToCoord(StrToInt(SbxField(S1, 2))));
            I1 := 1;    // field cursor
            I2 := 0;    // vertex number (1-based)
            while (SbxField(S1, I1) <> '') do
            begin
                I2 := I2 + 1;
                Obj5.InsertVertex := I2;
                Obj5.SetState_Vertex(I2,
                    Point(MilsToCoord(StrToInt(SbxField(S1, I1))),
                          MilsToCoord(StrToInt(SbxField(S1, I1 + 1)))));
                I1 := I1 + 2;
            end;
            TargetDoc.RegisterSchObjectInContainer(Obj5);
            SchServer.RobotManager.SendMessage(TargetDoc.I_ObjectAddress, c_BroadCast,
                SCHM_PrimitiveRegistration, Obj5.I_ObjectAddress);
            I5 := I5 + 1;
        end

        // ------------------------------------------------------ JUNCTION
        else if (S2 = 'JUNCTION') then
        begin
            Obj5 := SchServer.SchObjectFactory(eJunction, eCreate_GlobalCopy);
            Obj5.Location := Point(MilsToCoord(StrToInt(SbxField(S1, 1))),
                                   MilsToCoord(StrToInt(SbxField(S1, 2))));
            TargetDoc.RegisterSchObjectInContainer(Obj5);
            SchServer.RobotManager.SendMessage(TargetDoc.I_ObjectAddress, c_BroadCast,
                SCHM_PrimitiveRegistration, Obj5.I_ObjectAddress);
            I5 := I5 + 1;
        end

        // ------------------------------------------------------ NETLABEL
        else if (S2 = 'NETLABEL') then
        begin
            Obj5 := SchServer.SchObjectFactory(eNetLabel, eCreate_GlobalCopy);
            Obj5.Location := Point(MilsToCoord(StrToInt(SbxField(S1, 1))),
                                   MilsToCoord(StrToInt(SbxField(S1, 2))));
            Obj5.Orientation := StrToInt(SbxField(S1, 3));
            Obj5.Text := SbxField(S1, 4);
            TargetDoc.RegisterSchObjectInContainer(Obj5);
            SchServer.RobotManager.SendMessage(TargetDoc.I_ObjectAddress, c_BroadCast,
                SCHM_PrimitiveRegistration, Obj5.I_ObjectAddress);
            I5 := I5 + 1;
        end

        // --------------------------------------------------------- POWER
        else if (S2 = 'POWER') then
        begin
            Obj5 := SchServer.SchObjectFactory(ePowerObject, eCreate_GlobalCopy);
            Obj5.Location := Point(MilsToCoord(StrToInt(SbxField(S1, 1))),
                                   MilsToCoord(StrToInt(SbxField(S1, 2))));
            Obj5.Orientation := StrToInt(SbxField(S1, 3));
            Obj5.Style := StrToInt(SbxField(S1, 4));
            // Field 6 = show the net name. Ground ports hide it: the label
            // renders below the symbol and crowds any wire running under it.
            Obj5.ShowNetName := (SbxField(S1, 6) = '1');
            Obj5.Text := SbxField(S1, 5);
            TargetDoc.RegisterSchObjectInContainer(Obj5);
            SchServer.RobotManager.SendMessage(TargetDoc.I_ObjectAddress, c_BroadCast,
                SCHM_PrimitiveRegistration, Obj5.I_ObjectAddress);
            I5 := I5 + 1;
        end

        // ---------------------------------------------------------- NOTE
        else if (S2 = 'NOTE') then
        begin
            Obj5 := SchServer.SchObjectFactory(eLabel, eCreate_GlobalCopy);
            Obj5.Location := Point(MilsToCoord(StrToInt(SbxField(S1, 1))),
                                   MilsToCoord(StrToInt(SbxField(S1, 2))));
            Obj5.Text := SbxField(S1, 3);
            TargetDoc.RegisterSchObjectInContainer(Obj5);
            SchServer.RobotManager.SendMessage(TargetDoc.I_ObjectAddress, c_BroadCast,
                SCHM_PrimitiveRegistration, Obj5.I_ObjectAddress);
            I5 := I5 + 1;
        end;
    end;

    // Parameters added programmatically default to the component origin, so
    // they stack on top of the symbol. Let Altium lay them out properly.
    SandboxLog('resetting parameter positions');
    TargetDoc.ResetAllSchParametersPosition;
    SandboxLog('parameter positions reset');

    // Apply harvested parameter placement, keyed by LibReference.
    // Offsets, Justification and Orientation come from real components on a
    // hand-drawn sheet (dev/harvest_param_placement.pas), so generated parts
    // match house style. Justification is what makes a column line up -
    // setting position alone still leaves the text ragged.
    // Stash the counters: the styling loop below reuses I4/I5 and would
    // otherwise clobber the placed-part and graphics totals.
    S3 := IntToStr(I4) + '|' + IntToStr(I5);

    // Apply harvested parameter placement, keyed by LibReference.
    // Offsets, Justification and Orientation come from real components on a
    // hand-drawn sheet (dev/harvest_param_placement.pas). Justification is
    // what makes a column line up; position alone leaves it ragged.
    //
    // The harvested offsets are RELATIVE SPACING - they are re-anchored here
    // rather than applied raw, for two reasons found by inspection:
    //   * raw offsets put a right-justified column over the capacitor body
    //   * they also left the designator on the far side of the rail from the
    //     rest of its own parameters, since its dy reaches above the top pin
    // So the block keeps its harvested spacing and justification but is
    // translated to clear the body and sit below the topmost pin.
    SandboxLog('applying harvested parameter placement');
    List2 := TStringList.Create;
    if FileExists('C:\Users\Public\altium_mcp\param_placement.txt') then List2.LoadFromFile('C:\Users\Public\altium_mcp\param_placement.txt');
    SandboxLog('placement records = ' + IntToStr(List2.Count));

    Obj2 := TargetDoc.SchIterator_Create;
    Obj2.AddFilter_ObjectSet(MkSet(eSchComponent));
    Obj6 := Obj2.FirstSchObject;
    while (Obj6 <> nil) do
    begin
        S4 := Obj6.LibReference;

        // Scan the records ONCE, before I1/I2/I3 are reused for the body
        // extent. Doing this after would clobber body-left with the loop
        // counter and fling the text off the sheet.
        B1 := 0;
        B1MAX := -999999;
        for I1 := 0 to List2.Count - 1 do
            if (SbxField(List2[I1], 1) = S4) then
            begin
                B1 := 1;
                if (StrToInt(SbxField(List2[I1], 5)) > B1MAX) then
                    B1MAX := StrToInt(SbxField(List2[I1], 5));
            end;

        if (B1 = 1) then
        begin
            // Body extent EXCLUDING parameter text. Component.BoundingRectangle
            // includes the text, so anchoring to it feeds back on itself and
            // walks the block off the sheet; pins and graphics are stable.
            I1 := 999999;
            I2 := -999999;
            I3 := -999999;
            // Filter to drawable primitives. An unfiltered child iterator
            // returns objects whose BoundingRectangle is not valid and kills
            // the script.
            Obj4 := Obj6.SchIterator_Create;
            Obj4.AddFilter_ObjectSet(MkSet(ePin, eLine, eRectangle, eArc, ePolyline, eEllipse));
            Obj7 := Obj4.FirstSchObject;
            while (Obj7 <> nil) do
            begin
                Obj5 := Obj7.BoundingRectangle;
                if (CoordToMils(Obj5.Left) < I1) then I1 := CoordToMils(Obj5.Left);
                if (CoordToMils(Obj5.Right) > I2) then I2 := CoordToMils(Obj5.Right);
                if (CoordToMils(Obj5.Top) > I3) then I3 := CoordToMils(Obj5.Top);
                Obj7 := Obj4.NextSchObject;
            end;
            Obj6.SchIterator_Destroy(Obj4);
            SandboxLog('  body ' + S4 + ' L=' + IntToStr(I1) + ' R=' + IntToStr(I2) + ' T=' + IntToStr(I3));

            // hide everything, then reveal exactly what the reference showed
            Obj4 := Obj6.SchIterator_Create;
            Obj4.AddFilter_ObjectSet(MkSet(eParameter));
            Obj7 := Obj4.FirstSchObject;
            while (Obj7 <> nil) do
            begin
                Obj7.IsHidden := True;
                Obj7 := Obj4.NextSchObject;
            end;
            Obj6.SchIterator_Destroy(Obj4);

            I5 := I3 - 100;                    // block top, below the pins
            for B1 := 0 to List2.Count - 1 do
            begin
                S1 := List2[B1];
                if (SbxField(S1, 1) = S4) then
                begin
                    S5 := SbxField(S1, 3);
                    if (StrToInt(SbxField(S1, 6)) = 2) or (StrToInt(SbxField(S1, 6)) = 5) or
                       (StrToInt(SbxField(S1, 6)) = 8) then
                        I4 := I1 - 50
                    else
                        I4 := I2 + 50;
                    I2X := I5 - (B1MAX - StrToInt(SbxField(S1, 5)));
                    if (S5 = 'DESIGNATOR') then
                    begin
                        Obj6.Designator.Autoposition := False;
                        Obj6.Designator.Orientation := StrToInt(SbxField(S1, 7));
                        Obj6.Designator.Justification := StrToInt(SbxField(S1, 6));
                        Obj6.Designator.MoveToXY(MilsToCoord(I4), MilsToCoord(I2X));
                    end
                    else
                    begin
                        Obj4 := Obj6.SchIterator_Create;
                        Obj4.AddFilter_ObjectSet(MkSet(eParameter));
                        Obj7 := Obj4.FirstSchObject;
                        while (Obj7 <> nil) do
                        begin
                            if (UpperCase(Obj7.Name) = UpperCase(S5)) then
                            begin
                                Obj7.IsHidden := False;
                                Obj7.Autoposition := False;
                                Obj7.Orientation := StrToInt(SbxField(S1, 7));
                                Obj7.Justification := StrToInt(SbxField(S1, 6));
                                Obj7.MoveToXY(MilsToCoord(I4), MilsToCoord(I2X));
                            end;
                            Obj7 := Obj4.NextSchObject;
                        end;
                        Obj6.SchIterator_Destroy(Obj4);
                    end;
                end;
            end;
            SandboxLog('  styled ' + Obj6.Designator.Text + ' (' + S4 + ')');
        end;
        Obj6 := Obj2.NextSchObject;
    end;
    TargetDoc.SchIterator_Destroy(Obj2);
    List2.Free;

    SchServer.ProcessControl.PostProcess(TargetDoc, '');
    TargetDoc.GraphicallyInvalidate;
    SandboxLog('parts placed = ' + IntToStr(I4) + '  graphics placed = ' + IntToStr(I5));

    // ---- write the pin map: absolute location of every placed pin --------
    SandboxLog('writing pin map');
    List2 := TStringList.Create;
    I1 := 0;
    Obj2 := TargetDoc.SchIterator_Create;
    Obj2.AddFilter_ObjectSet(MkSet(eSchComponent));
    Obj6 := Obj2.FirstSchObject;
    while (Obj6 <> nil) do
    begin
        Obj4 := Obj6.SchIterator_Create;
        Obj4.AddFilter_ObjectSet(MkSet(ePin));
        Obj7 := Obj4.FirstSchObject;
        while (Obj7 <> nil) do
        begin
            // ISch_Pin.Location is the end attached to the component BODY, not
            // the electrical connection point. The hot end is PinLength away
            // along the pin's orientation (0/1/2/3 = right/up/left/down).
            // Wiring to Location instead runs the wire straight through the
            // pin and out the far side - which shorts neighbouring pins that
            // share a connection column, and leaves the pin itself unconnected.
            I2 := CoordToMils(Obj7.Location.X);
            B1 := CoordToMils(Obj7.Location.Y);
            I3 := CoordToMils(Obj7.PinLength);
            if (Obj7.Orientation = 0) then I2 := I2 + I3
            else if (Obj7.Orientation = 1) then B1 := B1 + I3
            else if (Obj7.Orientation = 2) then I2 := I2 - I3
            else if (Obj7.Orientation = 3) then B1 := B1 - I3;
            List2.Add('PIN|' + Obj6.Designator.Text + '|' + Obj7.Name + '|' +
                      IntToStr(I2) + '|' + IntToStr(B1));
            I1 := I1 + 1;
            Obj7 := Obj4.NextSchObject;
        end;
        Obj6.SchIterator_Destroy(Obj4);
        Obj6 := Obj2.NextSchObject;
    end;
    TargetDoc.SchIterator_Destroy(Obj2);
    List2.SaveToFile('C:\Users\Public\altium_mcp\pin_map.txt');
    SandboxLog('pins mapped = ' + IntToStr(I1));
    List2.Free;

    ResultText := '{"sheet": "' + TargetDoc.DocumentName + '", "parts": ' + SbxField(S3, 0) +
                  ', "graphics": ' + SbxField(S3, 1) + ', "pins": ' + IntToStr(I1) + '}';
end;
List1.Free;
