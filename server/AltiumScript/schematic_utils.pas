// Helper function to convert string to pin electrical type
function StrToPinElectricalType(ElecType: String): TPinElectrical;
begin
    if ElecType = 'eElectricHiZ' then
        Result := eElectricHiZ
    else if ElecType = 'eElectricInput' then
        Result := eElectricInput
    else if ElecType = 'eElectricIO' then
        Result := eElectricIO
    else if ElecType = 'eElectricOpenCollector' then
        Result := eElectricOpenCollector
    else if ElecType = 'eElectricOpenEmitter' then
        Result := eElectricOpenEmitter
    else if ElecType = 'eElectricOutput' then
        Result := eElectricOutput
    else if ElecType = 'eElectricPassive' then
        Result := eElectricPassive
    else if ElecType = 'eElectricPower' then
        Result := eElectricPower
    else
        Result := eElectricPassive; // Default
end;

// Helper function to convert string to pin orientation
function StrToPinOrientation(Orient: String): TRotationBy90;
begin
    if Orient = 'eRotate0' then
        Result := eRotate0
    else if Orient = 'eRotate90' then
        Result := eRotate90
    else if Orient = 'eRotate180' then
        Result := eRotate180
    else if Orient = 'eRotate270' then
        Result := eRotate270
    else
        Result := eRotate0; // Default
end;

// Function to get current schematic library component data
function GetLibrarySymbolReference(ROOT_DIR: String): String;
var
    CurrentLib       : ISch_Lib;
    SchComponent     : ISch_Component;
    PinIterator      : ISch_Iterator;
    Pin              : ISch_Pin;
    ComponentProps   : TStringList;
    PinsArray        : TStringList;
    PinProps         : TStringList;
    OutputLines      : TStringList;
    PinName, PinNum  : String;
    PinType          : String;
    PinOrient        : String;
    PinX, PinY       : Integer;
begin
    Result := '';
    
    // Check if we have a schematic library document
    CurrentLib := SchServer.GetCurrentSchDocument;
    if (CurrentLib.ObjectID <> eSchLib) Then
    begin
        Result := 'ERROR: Please open a schematic library document';
        Exit;
    end;
    
    // Get the currently focused component from the library
    SchComponent := CurrentLib.CurrentSchComponent;
    if SchComponent = Nil Then
    begin
        Result := 'ERROR: No component is currently selected in the library';
        Exit;
    end;
    
    // Create component properties
    ComponentProps := TStringList.Create;
    
    try
        // Add basic component properties
        AddJSONProperty(ComponentProps, 'library_name', ExtractFileName(CurrentLib.DocumentName));
        AddJSONProperty(ComponentProps, 'component_name', SchComponent.LibReference);
        AddJSONProperty(ComponentProps, 'description', SchComponent.ComponentDescription);
        AddJSONProperty(ComponentProps, 'designator', SchComponent.Designator.Text);
        AddJSONInteger(ComponentProps, 'part_count', SchComponent.PartCount);

        // Create an array for pins
        PinsArray := TStringList.Create;
        
        try
            // Create pin iterator
            PinIterator := SchComponent.SchIterator_Create;
            PinIterator.AddFilter_ObjectSet(MkSet(ePin));
            
            Pin := PinIterator.FirstSchObject;
            
            // Process all pins
            while (Pin <> nil) do
            begin
                // Create pin properties
                PinProps := TStringList.Create;
                
                try
                    // Get pin properties
                    PinNum := Pin.Designator;
                    PinName := Pin.Name;
                    
                    // Convert electrical type to string
                    case Pin.Electrical of
                        eElectricHiZ: PinType := 'eElectricHiZ';
                        eElectricInput: PinType := 'eElectricInput';
                        eElectricIO: PinType := 'eElectricIO';
                        eElectricOpenCollector: PinType := 'eElectricOpenCollector';
                        eElectricOpenEmitter: PinType := 'eElectricOpenEmitter';
                        eElectricOutput: PinType := 'eElectricOutput';
                        eElectricPassive: PinType := 'eElectricPassive';
                        eElectricPower: PinType := 'eElectricPower';
                        else PinType := 'eElectricPassive';
                    end;
                    
                    // Convert orientation to string
                    case Pin.Orientation of
                        eRotate0: PinOrient := 'eRotate0';
                        eRotate90: PinOrient := 'eRotate90';
                        eRotate180: PinOrient := 'eRotate180';
                        eRotate270: PinOrient := 'eRotate270';
                        else PinOrient := 'eRotate0';
                    end;
                    
                    // Get coordinates
                    PinX := CoordToMils(Pin.Location.X);
                    PinY := CoordToMils(Pin.Location.Y);
                    
                    // Add pin properties
                    AddJSONProperty(PinProps, 'pin_number', PinNum);
                    AddJSONProperty(PinProps, 'pin_name', PinName);
                    AddJSONProperty(PinProps, 'pin_type', PinType);
                    AddJSONProperty(PinProps, 'pin_orientation', PinOrient);
                    AddJSONNumber(PinProps, 'x', PinX);
                    AddJSONNumber(PinProps, 'y', PinY);
                    AddJSONInteger(PinProps, 'owner_part_id', Pin.OwnerPartId);

                    // Add this pin to the pins array
                    PinsArray.Add(BuildJSONObject(PinProps, 1));
                    
                    // Move to next pin
                    Pin := PinIterator.NextSchObject;
                finally
                    PinProps.Free;
                end;
            end;
            
            SchComponent.SchIterator_Destroy(PinIterator);
            
            // Add pins array to component - pass empty string as the array name
            // because we're adding it directly to the ComponentProps
            ComponentProps.Add('"pins": ' + BuildJSONArray(PinsArray));
            
            // Build final JSON
            OutputLines := TStringList.Create;
            
            try
                OutputLines.Text := BuildJSONObject(ComponentProps);
                Result := WriteJSONToFile(OutputLines, ROOT_DIR+'temp_symbol_reference.json');
            finally
                OutputLines.Free;
            end;
        finally
            PinsArray.Free;
        end;
    finally
        ComponentProps.Free;
    end;
end;

function CreateSchematicSymbol(SymbolName: String; PinsList: TStringList; GraphicsList: TStringList; PartCount: Integer = 1): String;
var
    CurrentLib       : ISch_Lib;
    SchComponent     : ISch_Component;
    SchPin           : ISch_Pin;
    R                : ISch_Rectangle;
    SchLine          : ISch_Line;
    SchPolyline      : ISch_Polyline;
    SchPolygon       : ISch_Polygon;
    SchArc           : ISch_Arc;
    SchEllipse       : ISch_Ellipse;
    SchLabel         : ISch_Label;
    I, J, PinCount   : Integer;
    GraphicsCount    : Integer;
    Entry            : String;
    FieldValue       : String;
    GType            : String;
    GPart            : Integer;
    GWidth           : Integer;
    FIdx, VCount, V  : Integer;
    PinName, PinNum  : String;
    PinType          : String;
    PinOrient        : String;
    PinX, PinY       : Integer;
    PinOwnerPartId   : Integer;
    PinElec          : TPinElectrical;
    PinOrientation   : TRotationBy90;
    MinX, MaxX, MinY, MaxY : Integer;
    HasPins          : Boolean;
    ResultProps      : TStringList;
    Description      : String;
    OutputLines      : TStringList;
begin
    // Check if we have a schematic library document
    CurrentLib := SchServer.GetCurrentSchDocument;
    if (CurrentLib.ObjectID <> eSchLib) Then
    begin
        Result := 'ERROR: Please open a schematic library document';
        Exit;
    end;

    Description := 'New Component';  // Default description

    // Parse the pins list for description and auto-detect PartCount from max owner_part_id
    for I := 0 to PinsList.Count - 1 do
    begin
        if (Pos('Description=', PinsList[I]) = 1) then
        begin
            Description := Copy(PinsList[I], 13, Length(PinsList[I]) - 12);
        end
        else
        begin
            FieldValue := Trim(GetFieldFromPipeString(PinsList[I], 6));
            if (FieldValue <> '') then
            begin
                PinOwnerPartId := StrToInt(FieldValue);
                if (PinOwnerPartId > PartCount) then
                    PartCount := PinOwnerPartId;
            end;
        end;
    end;

    // Also auto-detect PartCount from graphics owner part ids
    if (GraphicsList <> nil) then
        for I := 0 to GraphicsList.Count - 1 do
        begin
            FieldValue := Trim(GetFieldFromPipeString(GraphicsList[I], 1));
            if (FieldValue <> '') then
            begin
                GPart := StrToInt(FieldValue);
                if (GPart > PartCount) then
                    PartCount := GPart;
            end;
        end;

    // One undo step; this is also what marks the library as modified (the
    // user saves it)
    SchServer.ProcessControl.PreProcess(CurrentLib, '');

    // Create a library component (a page of the library is created)
    SchComponent := SchServer.SchObjectFactory(eSchComponent, eCreate_Default);
    if (SchComponent = Nil) Then
    begin
        SchServer.ProcessControl.PostProcess(CurrentLib, '');
        Result := 'ERROR: Failed to create component';
        Exit;
    end;

    // Set up parameters for the library component
    SchComponent.CurrentPartID := 1;
    SchComponent.DisplayMode := 0;
    SchComponent.PartCount := PartCount;

    // Define the LibReference and component description
    SchComponent.LibReference := SymbolName;
    SchComponent.ComponentDescription := Description;
    SchComponent.Designator.Text := 'U?';

    // Create a body for each part: an auto-sized rectangle when no explicit
    // graphics are given (legacy behavior), otherwise the caller's graphics
    // define the body and only the designator position is derived from pins
    PinCount := 0;
    if (GraphicsList <> nil) then
        GraphicsCount := GraphicsList.Count
    else
        GraphicsCount := 0;

    for J := 1 to PartCount do
    begin
        // Compute bounding box for this part's pins (including shared pins with OwnerPartId=0)
        MinX := 9999; MaxX := -9999; MinY := 9999; MaxY := -9999;
        HasPins := False;

        for I := 0 to PinsList.Count - 1 do
        begin
            if (Pos('Description=', PinsList[I]) = 1) then Continue;

            FieldValue := Trim(GetFieldFromPipeString(PinsList[I], 4));
            if (FieldValue = '') then Continue;
            PinX := StrToInt(FieldValue);
            PinY := StrToInt(Trim(GetFieldFromPipeString(PinsList[I], 5)));

            // Determine owner part id (default 1 for backward compatibility)
            FieldValue := Trim(GetFieldFromPipeString(PinsList[I], 6));
            if (FieldValue <> '') then
                PinOwnerPartId := StrToInt(FieldValue)
            else
                PinOwnerPartId := 1;

            // Include pin in this part's bounding box if it belongs to this part or is shared (0)
            if (PinOwnerPartId = J) or (PinOwnerPartId = 0) then
            begin
                MinX := Min(MinX, PinX);
                MaxX := Max(MaxX, PinX);
                MinY := Min(MinY, PinY);
                MaxY := Max(MaxY, PinY);
                HasPins := True;
            end;
        end;

        // Default rectangle if no pins for this part
        if not HasPins then
        begin
            MinX := 300; MinY := 0; MaxX := 1000; MaxY := 1000;
        end;

        // Create an auto-sized body rectangle only when no graphics are
        // given AND the part actually has content - a symbol with neither
        // pins nor graphics stays empty
        if (GraphicsCount = 0) and HasPins then
        begin
            R := SchServer.SchObjectFactory(eRectangle, eCreate_Default);
            if (R <> Nil) Then
            begin
                R.LineWidth := eSmall;
                R.Location := Point(MilsToCoord(MinX), MilsToCoord(MinY - 100));
                R.Corner := Point(MilsToCoord(MaxX), MilsToCoord(MaxY + 100));
                R.AreaColor := $00B0FFFF; // Yellow (BGR format)
                R.Color := $00FF0000;     // Blue (BGR format)
                R.IsSolid := True;
                R.OwnerPartId := J;
                R.OwnerPartDisplayMode := 0;
                SchComponent.AddSchObject(R);
            end;
        end;

        // Position designator using Part 1's bounding box
        if (J = 1) then
            SchComponent.Designator.Location := Point(MilsToCoord(MinX), MilsToCoord(MaxY + 100));
    end;

    // Create explicit graphic primitives. Entry formats (coords in mils,
    // width 0..3 = zero/small/medium/large, solid 0/1):
    //   line|part|width|x1|y1|x2|y2
    //   polyline|part|width|x1|y1|x2|y2|...
    //   polygon|part|width|solid|x1|y1|x2|y2|...
    //   rectangle|part|width|solid|x1|y1|x2|y2
    //   arc|part|width|cx|cy|radius|start_angle|end_angle
    //   ellipse|part|width|solid|cx|cy|radius|secondary_radius
    //   label|part|x|y|text
    for I := 0 to GraphicsCount - 1 do
    begin
        Entry := Trim(GraphicsList[I]);
        if (Entry = '') then Continue;

        GType := LowerCase(Trim(GetFieldFromPipeString(Entry, 0)));

        FieldValue := Trim(GetFieldFromPipeString(Entry, 1));
        if (FieldValue <> '') then
            GPart := StrToInt(FieldValue)
        else
            GPart := 1;

        FieldValue := Trim(GetFieldFromPipeString(Entry, 2));
        if (FieldValue <> '') then
            GWidth := StrToInt(FieldValue)
        else
            GWidth := 1;

        if (GType = 'line') then
        begin
            SchLine := SchServer.SchObjectFactory(eLine, eCreate_Default);
            if (SchLine <> Nil) then
            begin
                SchLine.LineWidth := GWidth;
                SchLine.Location := Point(MilsToCoord(SafeStrToFloat(GetFieldFromPipeString(Entry, 3))),
                                          MilsToCoord(SafeStrToFloat(GetFieldFromPipeString(Entry, 4))));
                SchLine.Corner := Point(MilsToCoord(SafeStrToFloat(GetFieldFromPipeString(Entry, 5))),
                                        MilsToCoord(SafeStrToFloat(GetFieldFromPipeString(Entry, 6))));
                SchLine.OwnerPartId := GPart;
                SchLine.OwnerPartDisplayMode := 0;
                SchComponent.AddSchObject(SchLine);
            end;
        end
        else if (GType = 'polyline') then
        begin
            SchPolyline := SchServer.SchObjectFactory(ePolyline, eCreate_Default);
            if (SchPolyline <> Nil) then
            begin
                SchPolyline.LineWidth := GWidth;
                // Count coordinate fields from index 3 up
                VCount := 0;
                while (Trim(GetFieldFromPipeString(Entry, 3 + VCount)) <> '') do
                    VCount := VCount + 1;
                VCount := VCount div 2;
                SchPolyline.VerticesCount := VCount;
                for V := 1 to VCount do
                    SchPolyline.Vertex[V] := Point(MilsToCoord(SafeStrToFloat(GetFieldFromPipeString(Entry, 3 + (V-1)*2))),
                                                   MilsToCoord(SafeStrToFloat(GetFieldFromPipeString(Entry, 4 + (V-1)*2))));
                SchPolyline.OwnerPartId := GPart;
                SchPolyline.OwnerPartDisplayMode := 0;
                SchComponent.AddSchObject(SchPolyline);
            end;
        end
        else if (GType = 'polygon') then
        begin
            SchPolygon := SchServer.SchObjectFactory(ePolygon, eCreate_Default);
            if (SchPolygon <> Nil) then
            begin
                SchPolygon.LineWidth := GWidth;
                SchPolygon.IsSolid := (Trim(GetFieldFromPipeString(Entry, 3)) = '1');
                SchPolygon.AreaColor := $00B0FFFF; // Standard body yellow (BGR)
                SchPolygon.Color := $00FF0000;     // Standard body blue (BGR)
                VCount := 0;
                while (Trim(GetFieldFromPipeString(Entry, 4 + VCount)) <> '') do
                    VCount := VCount + 1;
                VCount := VCount div 2;
                SchPolygon.VerticesCount := VCount;
                for V := 1 to VCount do
                    SchPolygon.Vertex[V] := Point(MilsToCoord(SafeStrToFloat(GetFieldFromPipeString(Entry, 4 + (V-1)*2))),
                                                  MilsToCoord(SafeStrToFloat(GetFieldFromPipeString(Entry, 5 + (V-1)*2))));
                SchPolygon.OwnerPartId := GPart;
                SchPolygon.OwnerPartDisplayMode := 0;
                SchComponent.AddSchObject(SchPolygon);
            end;
        end
        else if (GType = 'rectangle') then
        begin
            R := SchServer.SchObjectFactory(eRectangle, eCreate_Default);
            if (R <> Nil) then
            begin
                R.LineWidth := GWidth;
                R.IsSolid := (Trim(GetFieldFromPipeString(Entry, 3)) = '1');
                R.AreaColor := $00B0FFFF;
                R.Color := $00FF0000;
                R.Location := Point(MilsToCoord(SafeStrToFloat(GetFieldFromPipeString(Entry, 4))),
                                    MilsToCoord(SafeStrToFloat(GetFieldFromPipeString(Entry, 5))));
                R.Corner := Point(MilsToCoord(SafeStrToFloat(GetFieldFromPipeString(Entry, 6))),
                                  MilsToCoord(SafeStrToFloat(GetFieldFromPipeString(Entry, 7))));
                R.OwnerPartId := GPart;
                R.OwnerPartDisplayMode := 0;
                SchComponent.AddSchObject(R);
            end;
        end
        else if (GType = 'arc') then
        begin
            SchArc := SchServer.SchObjectFactory(eArc, eCreate_Default);
            if (SchArc <> Nil) then
            begin
                SchArc.LineWidth := GWidth;
                SchArc.Location := Point(MilsToCoord(SafeStrToFloat(GetFieldFromPipeString(Entry, 3))),
                                         MilsToCoord(SafeStrToFloat(GetFieldFromPipeString(Entry, 4))));
                SchArc.Radius := MilsToCoord(SafeStrToFloat(GetFieldFromPipeString(Entry, 5)));
                SchArc.StartAngle := SafeStrToFloat(GetFieldFromPipeString(Entry, 6));
                SchArc.EndAngle := SafeStrToFloat(GetFieldFromPipeString(Entry, 7));
                SchArc.OwnerPartId := GPart;
                SchArc.OwnerPartDisplayMode := 0;
                SchComponent.AddSchObject(SchArc);
            end;
        end
        else if (GType = 'elliptical_arc') then
        begin
            SchArc := SchServer.SchObjectFactory(eEllipticalArc, eCreate_Default);
            if (SchArc <> Nil) then
            begin
                SchArc.LineWidth := GWidth;
                SchArc.Location := Point(MilsToCoord(SafeStrToFloat(GetFieldFromPipeString(Entry, 3))),
                                         MilsToCoord(SafeStrToFloat(GetFieldFromPipeString(Entry, 4))));
                SchArc.Radius := MilsToCoord(SafeStrToFloat(GetFieldFromPipeString(Entry, 5)));
                SchArc.SecondaryRadius := MilsToCoord(SafeStrToFloat(GetFieldFromPipeString(Entry, 6)));
                SchArc.StartAngle := SafeStrToFloat(GetFieldFromPipeString(Entry, 7));
                SchArc.EndAngle := SafeStrToFloat(GetFieldFromPipeString(Entry, 8));
                SchArc.OwnerPartId := GPart;
                SchArc.OwnerPartDisplayMode := 0;
                SchComponent.AddSchObject(SchArc);
            end;
        end
        else if (GType = 'ellipse') then
        begin
            SchEllipse := SchServer.SchObjectFactory(eEllipse, eCreate_Default);
            if (SchEllipse <> Nil) then
            begin
                SchEllipse.LineWidth := GWidth;
                SchEllipse.IsSolid := (Trim(GetFieldFromPipeString(Entry, 3)) = '1');
                SchEllipse.Location := Point(MilsToCoord(SafeStrToFloat(GetFieldFromPipeString(Entry, 4))),
                                             MilsToCoord(SafeStrToFloat(GetFieldFromPipeString(Entry, 5))));
                SchEllipse.Radius := MilsToCoord(SafeStrToFloat(GetFieldFromPipeString(Entry, 6)));
                SchEllipse.SecondaryRadius := MilsToCoord(SafeStrToFloat(GetFieldFromPipeString(Entry, 7)));
                SchEllipse.OwnerPartId := GPart;
                SchEllipse.OwnerPartDisplayMode := 0;
                SchComponent.AddSchObject(SchEllipse);
            end;
        end
        else if (GType = 'label') then
        begin
            SchLabel := SchServer.SchObjectFactory(eLabel, eCreate_Default);
            if (SchLabel <> Nil) then
            begin
                // label|part|x|y|text (no width field)
                SchLabel.Location := Point(MilsToCoord(SafeStrToFloat(GetFieldFromPipeString(Entry, 2))),
                                           MilsToCoord(SafeStrToFloat(GetFieldFromPipeString(Entry, 3))));
                SchLabel.Text := GetFieldFromPipeString(Entry, 4);
                SchLabel.OwnerPartId := GPart;
                SchLabel.OwnerPartDisplayMode := 0;
                SchComponent.AddSchObject(SchLabel);
            end;
        end;
    end;

    // Add pins to the component. Format:
    //   number|name|electrical|orientation|x|y[|owner_part_id[|length[|show_name[|show_designator]]]]
    for I := 0 to PinsList.Count - 1 do
    begin
        if (Pos('Description=', PinsList[I]) = 1) then Continue;

        Entry := PinsList[I];
        FieldValue := Trim(GetFieldFromPipeString(Entry, 5));
        if (FieldValue = '') then Continue;

        PinNum := Trim(GetFieldFromPipeString(Entry, 0));
        // Pin name is NOT trimmed: some libraries carry trailing spaces in
        // pin names and round-trip fidelity preserves them exactly
        PinName := GetFieldFromPipeString(Entry, 1);
        PinType := Trim(GetFieldFromPipeString(Entry, 2));
        PinOrient := Trim(GetFieldFromPipeString(Entry, 3));
        PinX := StrToInt(Trim(GetFieldFromPipeString(Entry, 4)));
        PinY := StrToInt(FieldValue);

        // Determine owner part id (default 1 for backward compatibility)
        FieldValue := Trim(GetFieldFromPipeString(Entry, 6));
        if (FieldValue <> '') then
            PinOwnerPartId := StrToInt(FieldValue)
        else
            PinOwnerPartId := 1;

        // Create a pin
        SchPin := SchServer.SchObjectFactory(ePin, eCreate_Default);
        if (SchPin = Nil) Then
            Continue;

        // Set pin properties
        PinElec := StrToPinElectricalType(PinType);
        PinOrientation := StrToPinOrientation(PinOrient);

        SchPin.Designator := PinNum;
        SchPin.Name := PinName;
        SchPin.Electrical := PinElec;
        SchPin.Orientation := PinOrientation;
        SchPin.Location := Point(MilsToCoord(PinX), MilsToCoord(PinY));

        // Optional pin length in mils
        FieldValue := Trim(GetFieldFromPipeString(Entry, 7));
        if (FieldValue <> '') then
            SchPin.PinLength := MilsToCoord(SafeStrToFloat(FieldValue));

        // Optional name/designator visibility (1 = shown, 0 = hidden)
        FieldValue := Trim(GetFieldFromPipeString(Entry, 8));
        if (FieldValue <> '') then
            SchPin.ShowName := (FieldValue = '1');
        FieldValue := Trim(GetFieldFromPipeString(Entry, 9));
        if (FieldValue <> '') then
            SchPin.ShowDesignator := (FieldValue = '1');

        // Set ownership to the specified part (0 = shared across all parts)
        SchPin.OwnerPartId := PinOwnerPartId;
        SchPin.OwnerPartDisplayMode := 0;

        SchComponent.AddSchObject(SchPin);
        PinCount := PinCount + 1;
    end;

    // Add the component to the library and register it with the document
    CurrentLib.AddSchComponent(SchComponent);
    SchServer.RobotManager.SendMessage(CurrentLib.I_ObjectAddress, c_BroadCast, SCHM_PrimitiveRegistration, SchComponent.I_ObjectAddress);
    SchServer.ProcessControl.PostProcess(CurrentLib, '');
    CurrentLib.CurrentSchComponent := SchComponent;
    MarkDocumentModified(CurrentLib.DocumentName);

    // Refresh library
    CurrentLib.GraphicallyInvalidate;

    // Create result JSON
    ResultProps := TStringList.Create;
    try
        AddJSONBoolean(ResultProps, 'success', True);
        AddJSONProperty(ResultProps, 'component_name', SymbolName);
        AddJSONInteger(ResultProps, 'pins_count', PinCount);
        AddJSONInteger(ResultProps, 'part_count', PartCount);

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
    end;
end;

// Function to search for a symbol in a schematic library and navigate to it
function SearchLibrarySymbol(ROOT_DIR: String; LibraryPath: String; SymbolName: String): String;
var
    CurrentLib       : ISch_Lib;
    LibIterator      : ISch_Iterator;
    LibComp          : ISch_Component;
    MatchedComp      : ISch_Component;
    ResultProps      : TStringList;
    MatchesArray     : TStringList;
    AllSymbolsArray  : TStringList;
    MatchProps       : TStringList;
    OutputLines      : TStringList;
    SearchUpper      : String;
    LibRefUpper      : String;
    MatchCount       : Integer;
    ServerDoc        : IServerDocument;
    OpenDlg          : TOpenDialog;
    NeedToOpen       : Boolean;
begin
    Result := '';
    MatchedComp := Nil;
    MatchCount := 0;
    SearchUpper := UpperCase(SymbolName);
    NeedToOpen := False;

    // If a library path is provided, open it
    if (LibraryPath <> '') then
    begin
        NeedToOpen := True;
    end
    else
    begin
        // No path provided - check if a SchLib is already open
        if (SchServer <> Nil) then
        begin
            CurrentLib := SchServer.GetCurrentSchDocument;
            if (CurrentLib <> Nil) and (CurrentLib.ObjectID = eSchLib) then
                NeedToOpen := False  // Already have a SchLib open
            else
                NeedToOpen := True;  // No SchLib open, need to browse
        end
        else
            NeedToOpen := True;
    end;

    // If we need to open a library and no path was given, prompt the user
    if NeedToOpen and (LibraryPath = '') then
    begin
        OpenDlg := TOpenDialog.Create(nil);
        try
            OpenDlg.Title := 'Select Schematic Library (.SchLib)';
            OpenDlg.Filter := 'Schematic Library (*.SchLib)|*.SchLib|All Files (*.*)|*.*';
            OpenDlg.FilterIndex := 1;
            if OpenDlg.Execute then
                LibraryPath := OpenDlg.FileName
            else
            begin
                Result := 'ERROR: No library selected. User cancelled the file browser.';
                Exit;
            end;
        finally
            OpenDlg.Free;
        end;
    end;

    // Open the library if we have a path
    if (LibraryPath <> '') then
    begin
        // Check if the file exists
        if not FileExists(LibraryPath) then
        begin
            Result := 'ERROR: Library file not found: ' + LibraryPath;
            Exit;
        end;

        // Open the library, or only focus it when it is already open
        ServerDoc := OpenLibraryDocument('SchLib', LibraryPath);
        if ServerDoc = Nil then
        begin
            Result := 'ERROR: Failed to open library: ' + LibraryPath;
            Exit;
        end;
    end;

    // Get the current schematic library document
    CurrentLib := SchServer.GetCurrentSchDocument;
    if CurrentLib = Nil then
    begin
        Result := 'ERROR: No schematic library document is currently open';
        Exit;
    end;

    if (CurrentLib.ObjectID <> eSchLib) then
    begin
        Result := 'ERROR: Current document is not a schematic library. Please open a .SchLib file';
        Exit;
    end;

    // Create arrays for results
    MatchesArray := TStringList.Create;
    AllSymbolsArray := TStringList.Create;
    ResultProps := TStringList.Create;

    try
        // Create library iterator to enumerate all symbols
        // NOTE: Must use SchLibIterator_Create (not SchIterator_Create) for SchLib documents
        LibIterator := CurrentLib.SchLibIterator_Create;
        LibIterator.AddFilter_ObjectSet(MkSet(eSchComponent));

        LibComp := LibIterator.FirstSchObject;
        while (LibComp <> Nil) do
        begin
            LibRefUpper := UpperCase(LibComp.LibReference);

            // Add to all symbols list
            AllSymbolsArray.Add('"' + LibComp.LibReference + '"');

            // Check for partial match
            if (Pos(SearchUpper, LibRefUpper) > 0) then
            begin
                MatchCount := MatchCount + 1;

                // Record this match
                MatchProps := TStringList.Create;
                try
                    AddJSONProperty(MatchProps, 'name', LibComp.LibReference);
                    AddJSONProperty(MatchProps, 'description', LibComp.ComponentDescription);

                    // Check for exact match
                    if (LibRefUpper = SearchUpper) then
                        AddJSONBoolean(MatchProps, 'exact_match', True)
                    else
                        AddJSONBoolean(MatchProps, 'exact_match', False);

                    MatchesArray.Add(BuildJSONObject(MatchProps, 1));
                finally
                    MatchProps.Free;
                end;

                // Prefer exact match, otherwise use first partial match
                if (LibRefUpper = SearchUpper) then
                    MatchedComp := LibComp
                else if (MatchedComp = Nil) then
                    MatchedComp := LibComp;
            end;

            LibComp := LibIterator.NextSchObject;
        end;

        CurrentLib.SchIterator_Destroy(LibIterator);

        // Navigate to the matched component if found
        if (MatchedComp <> Nil) then
        begin
            CurrentLib.CurrentSchComponent := MatchedComp;
            CurrentLib.GraphicallyInvalidate;

            AddJSONBoolean(ResultProps, 'found', True);
            AddJSONProperty(ResultProps, 'navigated_to', MatchedComp.LibReference);
            AddJSONProperty(ResultProps, 'description', MatchedComp.ComponentDescription);
        end
        else
        begin
            AddJSONBoolean(ResultProps, 'found', False);
            AddJSONProperty(ResultProps, 'message', 'No symbol matching "' + SymbolName + '" was found');
        end;

        AddJSONInteger(ResultProps, 'match_count', MatchCount);
        AddJSONProperty(ResultProps, 'library_name', ExtractFileName(CurrentLib.DocumentName));
        AddJSONInteger(ResultProps, 'total_symbols', AllSymbolsArray.Count);
        ResultProps.Add('"matches": ' + BuildJSONArray(MatchesArray));

        // Build final JSON
        OutputLines := TStringList.Create;
        try
            OutputLines.Text := BuildJSONObject(ResultProps);
            Result := WriteJSONToFile(OutputLines, ROOT_DIR + 'temp_search_symbol.json');
        finally
            OutputLines.Free;
        end;
    finally
        MatchesArray.Free;
        AllSymbolsArray.Free;
        ResultProps.Free;
    end;
end;

// Create one symbol for CreateSymbolsBatch. Returns True when created.
function BatchCreateOne(SymName, SymDesc: String; PartCount: Integer; PinsList, GraphicsList: TStringList): Boolean;
var
    R: String;
begin
    Result := False;
    if (SymName = '') then Exit;
    if (SymDesc <> '') then
        PinsList.Add('Description=' + SymDesc);
    R := CreateSchematicSymbol(SymName, PinsList, GraphicsList, PartCount);
    Result := (Pos('"success": true', R) > 0);
end;

// Finish the current symbol of a batch: create it (Mode 'SYMBOL') or find it
// (Mode 'EDITSYMBOL' - never created when missing), then apply its edit
// records. Returns 1 created, 2 edited, 0 nothing to do, -1 failed (the name
// is added to Failed, the reason to Problems).
function BatchFlushSymbol(Mode, SymName, SymDesc: String; PartCount: Integer;
    PinsList, GraphicsList, EditsList, Failed, Problems: TStringList): Integer;
var
    Lib  : ISch_Lib;
    Comp : ISch_Component;
begin
    Result := 0;
    if (SymName = '') then Exit;

    if (Mode = 'SYMBOL') then
    begin
        if not BatchCreateOne(SymName, SymDesc, PartCount, PinsList, GraphicsList) then
        begin
            Failed.Add('"' + JSONEscapeString(SymName) + '"');
            Result := -1;
            Exit;
        end;
        Result := 1;
    end
    else
        Result := 2;

    Comp := nil;
    Lib := SchServer.GetCurrentSchDocument;
    if (Lib <> nil) then
        if (Lib.ObjectID = eSchLib) then
            Comp := FindLibSymbol(Lib, SymName);
    if (Comp = nil) then
    begin
        if (Mode = 'EDITSYMBOL') then
        begin
            Failed.Add('"' + JSONEscapeString(SymName) + '"');
            Problems.Add('"' + JSONEscapeString(SymName + ': not in the focused schematic library (EDITSYMBOL never creates a symbol)') + '"');
            Result := -1;
        end
        else if (EditsList.Count > 0) then
            Problems.Add('"' + JSONEscapeString(SymName + ': created, but not found again to set its properties') + '"');
        Exit;
    end;
    ApplySymbolEdits(Lib, Comp, EditsList, Problems);
end;

// Create or edit many symbols in a single script run from a spec file. The
// file is plain text, one record per line, pipe-delimited (no JSON, so field
// text is preserved exactly):
//   LIBRARY|<path to .SchLib>            (optional first line - focus/open)
//   SYMBOL|<name>|<description>|<part_count>   start a new symbol
//   EDITSYMBOL|<name>                    start editing an existing symbol
//   PIN|<same fields as create_schematic_symbol pins>      (SYMBOL only)
//   GRAPHIC|<same entry format as create_schematic_symbol graphics>  (SYMBOL only)
//   COMMENT|<text>[|<visible 1/0>]       set the Comment (hidden unless visible is 1)
//   DESCRIPTION|<text>                   set the Description
//   SYMPARAM|<name>|<value>|<visible 1/0>[|x|y[|justification[|font name:size]]]
//   PINPARAM|<pin number>|<name>|<value>
//   PINDESC|<pin number>|<text>
//   PINSYMBOL|<pin number>|<inside|inside_edge|outside_edge|outside>|<IEEE symbol>
// Records belong to the most recent SYMBOL or EDITSYMBOL line. Far fewer
// Altium script launches than one create call per symbol.
function CreateSymbolsBatch(SpecFilePath: String): String;
var
    Lines         : TStringList;
    PinsList      : TStringList;
    GraphicsList  : TStringList;
    EditsList     : TStringList;
    FailedArray   : TStringList;
    ProblemsArray : TStringList;
    ResultProps   : TStringList;
    ServerDoc     : IServerDocument;
    Line, Kind    : String;
    LibPath       : String;
    Mode          : String;
    CurrentName   : String;
    CurrentDesc   : String;
    FieldValue    : String;
    PartCount     : Integer;
    CreatedCount  : Integer;
    EditedCount   : Integer;
    Outcome       : Integer;
    i             : Integer;
begin
    if not FileExists(SpecFilePath) then
    begin
        Result := 'ERROR: Spec file not found: ' + SpecFilePath;
        Exit;
    end;

    Lines := TStringList.Create;
    PinsList := TStringList.Create;
    GraphicsList := TStringList.Create;
    EditsList := TStringList.Create;
    FailedArray := TStringList.Create;
    ProblemsArray := TStringList.Create;
    ResultProps := TStringList.Create;
    Mode := '';
    CurrentName := '';
    CurrentDesc := '';
    PartCount := 1;
    CreatedCount := 0;
    EditedCount := 0;

    try
        Lines.LoadFromFile(SpecFilePath);

        // One extra pass: a final line that flushes the last symbol
        for i := 0 to Lines.Count do
        begin
            if (i < Lines.Count) then
            begin
                Line := Lines[i];
                Kind := UpperCase(Trim(GetFieldFromPipeString(Line, 0)));
            end
            else
            begin
                Line := '';
                Kind := 'END';
            end;

            if (Kind = 'LIBRARY') then
            begin
                // Focus if already open; never re-open (reload discards
                // unsaved symbols). Stop unless that library is now the
                // focused one - never work on whatever else is focused.
                LibPath := Trim(GetFieldFromPipeString(Line, 1));
                if FileExists(LibPath) then
                    ServerDoc := OpenLibraryDocument('SchLib', LibPath);
                if not SchLibIsFocused(LibPath) then
                begin
                    Result := 'ERROR: Could not open and focus ' + LibPath + ' - nothing was created or edited';
                    Exit;
                end;
            end
            else if (Kind = 'SYMBOL') or (Kind = 'EDITSYMBOL') or (Kind = 'END') then
            begin
                // Finish the previous symbol
                Outcome := BatchFlushSymbol(Mode, CurrentName, CurrentDesc, PartCount,
                                            PinsList, GraphicsList, EditsList, FailedArray, ProblemsArray);
                if (Outcome = 1) then
                    CreatedCount := CreatedCount + 1
                else if (Outcome = 2) then
                    EditedCount := EditedCount + 1;
                PinsList.Clear;
                GraphicsList.Clear;
                EditsList.Clear;
                Mode := Kind;
                CurrentName := Trim(GetFieldFromPipeString(Line, 1));
                CurrentDesc := '';
                PartCount := 1;
                if (Kind = 'SYMBOL') then
                begin
                    CurrentDesc := GetFieldFromPipeString(Line, 2);
                    FieldValue := Trim(GetFieldFromPipeString(Line, 3));
                    if (FieldValue <> '') then
                        PartCount := StrToInt(FieldValue);
                end;
            end
            else if (Kind = 'PIN') then
            begin
                if (Mode = 'SYMBOL') then
                    PinsList.Add(Copy(Line, 5, Length(Line)))
                else
                    ProblemsArray.Add('"' + JSONEscapeString('line ' + IntToStr(i + 1) + ': PIN only belongs to a SYMBOL') + '"');
            end
            else if (Kind = 'GRAPHIC') then
            begin
                if (Mode = 'SYMBOL') then
                    GraphicsList.Add(Copy(Line, 9, Length(Line)))
                else
                    ProblemsArray.Add('"' + JSONEscapeString('line ' + IntToStr(i + 1) + ': GRAPHIC only belongs to a SYMBOL') + '"');
            end
            else if (Kind = 'COMMENT') or (Kind = 'DESCRIPTION') or (Kind = 'SYMPARAM') or
                    (Kind = 'PINPARAM') or (Kind = 'PINDESC') or (Kind = 'PINSYMBOL') then
            begin
                if (CurrentName <> '') then
                    EditsList.Add(Line)
                else
                    ProblemsArray.Add('"' + JSONEscapeString('line ' + IntToStr(i + 1) + ': ' + Kind + ' before any SYMBOL or EDITSYMBOL') + '"');
            end;
        end;

        // Created symbols mark the library already; edits alone do not
        if (EditedCount > 0) then
            MarkDocumentModified(LibPathOfFocusedSchLib(0));
        AddJSONInteger(ResultProps, 'created', CreatedCount);
        AddJSONInteger(ResultProps, 'edited', EditedCount);
        if (FailedArray.Count > 0) then
            ResultProps.Add(BuildJSONArray(FailedArray, 'failed'))
        else
            ResultProps.Add('"failed": []');
        if (ProblemsArray.Count > 0) then
            ResultProps.Add(BuildJSONArray(ProblemsArray, 'problems'))
        else
            ResultProps.Add('"problems": []');

        Result := BuildJSONObject(ResultProps);
    finally
        Lines.Free;
        PinsList.Free;
        GraphicsList.Free;
        EditsList.Free;
        FailedArray.Free;
        ProblemsArray.Free;
        ResultProps.Free;
    end;
end;

// Dump the vertices of a polyline-like object as a JSON array property
procedure AddVerticesProperty(Props: TStringList; Poly: ISch_Polyline);
var
    VertsArray : TStringList;
    VProps     : TStringList;
    V          : Integer;
begin
    VertsArray := TStringList.Create;
    try
        for V := 1 to Poly.VerticesCount do
        begin
            VProps := TStringList.Create;
            try
                AddJSONNumber(VProps, 'x', CoordToMils(Poly.Vertex[V].X));
                AddJSONNumber(VProps, 'y', CoordToMils(Poly.Vertex[V].Y));
                VertsArray.Add(BuildJSONObject(VProps, 3));
            finally
                VProps.Free;
            end;
        end;
        Props.Add(BuildJSONArray(VertsArray, 'vertices', 2));
    finally
        VertsArray.Free;
    end;
end;

// Get the graphic primitives of symbols in a schematic library.
// SymbolName = '' -> inventory mode: every symbol with per-type primitive
// counts. SymbolName given -> full geometry dump of that symbol (mils).
function GetSymbolPrimitives(ROOT_DIR: String; LibraryPath: String; SymbolName: String): String;
var
    CurrentLib   : ISch_Lib;
    LibIterator  : ISch_Iterator;
    LibComp      : ISch_Component;
    PrimIterator : ISch_Iterator;
    Prim         : ISch_GraphicalObject;
    ServerDoc    : IServerDocument;
    ResultProps  : TStringList;
    SymbolsArray : TStringList;
    SymProps     : TStringList;
    PrimsArray   : TStringList;
    PrimProps    : TStringList;
    OutputLines  : TStringList;
    Counts       : TStringList;
    TypeName     : String;
    i            : Integer;
    Found        : Boolean;
    PinObj       : ISch_Pin;
begin
    Result := '';

    // Open the requested library, or use the currently focused SchLib.
    // IMPORTANT: if the document is already open, only focus it -
    // re-opening an open document reloads it from disk and silently
    // discards any unsaved changes (e.g. symbols created this session).
    if (LibraryPath <> '') then
    begin
        if not FileExists(LibraryPath) then
        begin
            Result := 'ERROR: Library file not found: ' + LibraryPath;
            Exit;
        end;
        ServerDoc := OpenLibraryDocument('SchLib', LibraryPath);
        if ServerDoc = Nil then
        begin
            Result := 'ERROR: Failed to open library: ' + LibraryPath;
            Exit;
        end;
    end;

    CurrentLib := SchServer.GetCurrentSchDocument;
    if (CurrentLib = Nil) or (CurrentLib.ObjectID <> eSchLib) then
    begin
        Result := 'ERROR: No schematic library is open (provide library_path or open a .SchLib)';
        Exit;
    end;

    ResultProps := TStringList.Create;
    SymbolsArray := TStringList.Create;
    Found := False;

    try
        AddJSONProperty(ResultProps, 'library_name', ExtractFileName(CurrentLib.DocumentName));

        LibIterator := CurrentLib.SchLibIterator_Create;
        LibIterator.AddFilter_ObjectSet(MkSet(eSchComponent));

        LibComp := LibIterator.FirstSchObject;
        while (LibComp <> Nil) do
        begin
            if (SymbolName = '') then
            begin
                // Inventory mode: count primitives by type
                Counts := TStringList.Create;
                SymProps := TStringList.Create;
                try
                    PrimIterator := LibComp.SchIterator_Create;
                    Prim := PrimIterator.FirstSchObject;
                    while (Prim <> Nil) do
                    begin
                        case Prim.ObjectId of
                            ePin:            TypeName := 'pins';
                            eRectangle:      TypeName := 'rectangles';
                            eLine:           TypeName := 'lines';
                            ePolyline:       TypeName := 'polylines';
                            ePolygon:        TypeName := 'polygons';
                            eArc:            TypeName := 'arcs';
                            eEllipticalArc:  TypeName := 'elliptical_arcs';
                            eEllipse:        TypeName := 'ellipses';
                            eBezier:         TypeName := 'beziers';
                            ePie:            TypeName := 'pies';
                            eRoundRectangle: TypeName := 'round_rectangles';
                            eLabel:          TypeName := 'labels';
                            eParameter:      TypeName := '';
                            eDesignator:     TypeName := '';
                        else
                            TypeName := 'other';
                        end;

                        if (TypeName <> '') then
                        begin
                            i := Counts.IndexOfName(TypeName);
                            if (i < 0) then
                                Counts.Add(TypeName + '=1')
                            else
                                Counts[i] := TypeName + '=' + IntToStr(StrToInt(Counts.ValueFromIndex[i]) + 1);
                        end;

                        Prim := PrimIterator.NextSchObject;
                    end;
                    LibComp.SchIterator_Destroy(PrimIterator);

                    AddJSONProperty(SymProps, 'name', LibComp.LibReference);
                    AddJSONProperty(SymProps, 'description', LibComp.ComponentDescription);
                    AddJSONInteger(SymProps, 'part_count', LibComp.PartCount);
                    for i := 0 to Counts.Count - 1 do
                        AddJSONInteger(SymProps, Counts.Names[i], StrToInt(Counts.ValueFromIndex[i]));

                    SymbolsArray.Add(BuildJSONObject(SymProps, 1));
                finally
                    Counts.Free;
                    SymProps.Free;
                end;
            end
            else if (SymbolName = '*') or (UpperCase(LibComp.LibReference) = UpperCase(SymbolName)) then
            begin
                // Dump mode: full geometry of every primitive.
                // SymbolName '*' dumps every symbol into the symbols array.
                Found := True;
                SymProps := TStringList.Create;
                PrimsArray := TStringList.Create;
                try
                    AddJSONProperty(SymProps, 'symbol_name', LibComp.LibReference);
                    AddJSONProperty(SymProps, 'description', LibComp.ComponentDescription);
                    AddJSONInteger(SymProps, 'part_count', LibComp.PartCount);
                    AddSymbolPropertiesJSON(SymProps, LibComp);

                    PrimIterator := LibComp.SchIterator_Create;
                    Prim := PrimIterator.FirstSchObject;
                    while (Prim <> Nil) do
                    begin
                        PrimProps := TStringList.Create;
                        try
                          // One unreadable primitive must not crash the dump:
                          // a property access that throws would otherwise leave
                          // the script paused in the debugger, wedging all
                          // later script runs
                          try
                            case Prim.ObjectId of
                                ePin:
                                begin
                                    PinObj := Prim;
                                    AddJSONProperty(PrimProps, 'type', 'pin');
                                    AddJSONProperty(PrimProps, 'pin_number', PinObj.Designator);
                                    AddJSONProperty(PrimProps, 'pin_name', PinObj.Name);
                                    AddJSONInteger(PrimProps, 'electrical', PinObj.Electrical);
                                    AddJSONInteger(PrimProps, 'orientation', PinObj.Orientation);
                                    AddJSONNumber(PrimProps, 'x', CoordToMils(PinObj.Location.X));
                                    AddJSONNumber(PrimProps, 'y', CoordToMils(PinObj.Location.Y));
                                    AddJSONNumber(PrimProps, 'length', CoordToMils(PinObj.PinLength));
                                    AddJSONBoolean(PrimProps, 'show_name', PinObj.ShowName);
                                    AddJSONBoolean(PrimProps, 'show_designator', PinObj.ShowDesignator);
                                    AddJSONProperty(PrimProps, 'description', PinObj.Description);
                                    AddJSONProperty(PrimProps, 'symbol_inside', IeeeSymbolName(PinObj.Symbol_Inner));
                                    AddJSONProperty(PrimProps, 'symbol_inside_edge', IeeeSymbolName(PinObj.Symbol_InnerEdge));
                                    AddJSONProperty(PrimProps, 'symbol_outside_edge', IeeeSymbolName(PinObj.Symbol_OuterEdge));
                                    AddJSONProperty(PrimProps, 'symbol_outside', IeeeSymbolName(PinObj.Symbol_Outer));
                                    AddPinParametersJSON(PrimProps, PinObj);
                                end;
                                eRectangle:
                                begin
                                    AddJSONProperty(PrimProps, 'type', 'rectangle');
                                    AddJSONNumber(PrimProps, 'x1', CoordToMils(Prim.Location.X));
                                    AddJSONNumber(PrimProps, 'y1', CoordToMils(Prim.Location.Y));
                                    AddJSONNumber(PrimProps, 'x2', CoordToMils(Prim.Corner.X));
                                    AddJSONNumber(PrimProps, 'y2', CoordToMils(Prim.Corner.Y));
                                    AddJSONInteger(PrimProps, 'line_width', Prim.LineWidth);
                                    AddJSONBoolean(PrimProps, 'is_solid', Prim.IsSolid);
                                end;
                                eLine:
                                begin
                                    AddJSONProperty(PrimProps, 'type', 'line');
                                    AddJSONNumber(PrimProps, 'x1', CoordToMils(Prim.Location.X));
                                    AddJSONNumber(PrimProps, 'y1', CoordToMils(Prim.Location.Y));
                                    AddJSONNumber(PrimProps, 'x2', CoordToMils(Prim.Corner.X));
                                    AddJSONNumber(PrimProps, 'y2', CoordToMils(Prim.Corner.Y));
                                    AddJSONInteger(PrimProps, 'line_width', Prim.LineWidth);
                                end;
                                ePolyline:
                                begin
                                    AddJSONProperty(PrimProps, 'type', 'polyline');
                                    AddJSONInteger(PrimProps, 'line_width', Prim.LineWidth);
                                    AddVerticesProperty(PrimProps, Prim);
                                end;
                                ePolygon:
                                begin
                                    AddJSONProperty(PrimProps, 'type', 'polygon');
                                    AddJSONInteger(PrimProps, 'line_width', Prim.LineWidth);
                                    AddJSONBoolean(PrimProps, 'is_solid', Prim.IsSolid);
                                    AddVerticesProperty(PrimProps, Prim);
                                end;
                                eArc:
                                begin
                                    AddJSONProperty(PrimProps, 'type', 'arc');
                                    AddJSONNumber(PrimProps, 'cx', CoordToMils(Prim.Location.X));
                                    AddJSONNumber(PrimProps, 'cy', CoordToMils(Prim.Location.Y));
                                    AddJSONNumber(PrimProps, 'radius', CoordToMils(Prim.Radius));
                                    AddJSONNumber(PrimProps, 'start_angle', Prim.StartAngle);
                                    AddJSONNumber(PrimProps, 'end_angle', Prim.EndAngle);
                                    AddJSONInteger(PrimProps, 'line_width', Prim.LineWidth);
                                end;
                                eEllipticalArc:
                                begin
                                    AddJSONProperty(PrimProps, 'type', 'elliptical_arc');
                                    AddJSONNumber(PrimProps, 'cx', CoordToMils(Prim.Location.X));
                                    AddJSONNumber(PrimProps, 'cy', CoordToMils(Prim.Location.Y));
                                    AddJSONNumber(PrimProps, 'radius', CoordToMils(Prim.Radius));
                                    AddJSONNumber(PrimProps, 'secondary_radius', CoordToMils(Prim.SecondaryRadius));
                                    AddJSONNumber(PrimProps, 'start_angle', Prim.StartAngle);
                                    AddJSONNumber(PrimProps, 'end_angle', Prim.EndAngle);
                                    AddJSONInteger(PrimProps, 'line_width', Prim.LineWidth);
                                end;
                                eEllipse:
                                begin
                                    AddJSONProperty(PrimProps, 'type', 'ellipse');
                                    AddJSONNumber(PrimProps, 'cx', CoordToMils(Prim.Location.X));
                                    AddJSONNumber(PrimProps, 'cy', CoordToMils(Prim.Location.Y));
                                    AddJSONNumber(PrimProps, 'radius', CoordToMils(Prim.Radius));
                                    AddJSONNumber(PrimProps, 'secondary_radius', CoordToMils(Prim.SecondaryRadius));
                                    AddJSONBoolean(PrimProps, 'is_solid', Prim.IsSolid);
                                    AddJSONInteger(PrimProps, 'line_width', Prim.LineWidth);
                                end;
                                eBezier:
                                begin
                                    AddJSONProperty(PrimProps, 'type', 'bezier');
                                    AddJSONInteger(PrimProps, 'line_width', Prim.LineWidth);
                                    AddVerticesProperty(PrimProps, Prim);
                                end;
                                ePie:
                                begin
                                    AddJSONProperty(PrimProps, 'type', 'pie');
                                    AddJSONNumber(PrimProps, 'cx', CoordToMils(Prim.Location.X));
                                    AddJSONNumber(PrimProps, 'cy', CoordToMils(Prim.Location.Y));
                                    AddJSONNumber(PrimProps, 'radius', CoordToMils(Prim.Radius));
                                    AddJSONNumber(PrimProps, 'start_angle', Prim.StartAngle);
                                    AddJSONNumber(PrimProps, 'end_angle', Prim.EndAngle);
                                    AddJSONBoolean(PrimProps, 'is_solid', Prim.IsSolid);
                                    AddJSONInteger(PrimProps, 'line_width', Prim.LineWidth);
                                end;
                                eRoundRectangle:
                                begin
                                    AddJSONProperty(PrimProps, 'type', 'round_rectangle');
                                    AddJSONNumber(PrimProps, 'x1', CoordToMils(Prim.Location.X));
                                    AddJSONNumber(PrimProps, 'y1', CoordToMils(Prim.Location.Y));
                                    AddJSONNumber(PrimProps, 'x2', CoordToMils(Prim.Corner.X));
                                    AddJSONNumber(PrimProps, 'y2', CoordToMils(Prim.Corner.Y));
                                    AddJSONNumber(PrimProps, 'corner_x_radius', CoordToMils(Prim.CornerXRadius));
                                    AddJSONNumber(PrimProps, 'corner_y_radius', CoordToMils(Prim.CornerYRadius));
                                    AddJSONInteger(PrimProps, 'line_width', Prim.LineWidth);
                                    AddJSONBoolean(PrimProps, 'is_solid', Prim.IsSolid);
                                end;
                                eLabel:
                                begin
                                    AddJSONProperty(PrimProps, 'type', 'label');
                                    AddJSONProperty(PrimProps, 'text', Prim.Text);
                                    AddJSONNumber(PrimProps, 'x', CoordToMils(Prim.Location.X));
                                    AddJSONNumber(PrimProps, 'y', CoordToMils(Prim.Location.Y));
                                end;
                            eParameter:
                                AddJSONProperty(PrimProps, 'type', '');
                            eDesignator:
                                AddJSONProperty(PrimProps, 'type', '');
                            // Footprint/model links are metadata, not drawn
                            // graphics - excluded like parameters
                            eImplementation:
                                AddJSONProperty(PrimProps, 'type', '');
                            eImplementationMap:
                                AddJSONProperty(PrimProps, 'type', '');
                            else
                            begin
                                // Surface unknown graphic types instead of
                                // hiding them - the object_id identifies them
                                AddJSONProperty(PrimProps, 'type', 'unknown');
                                AddJSONInteger(PrimProps, 'object_id', Prim.ObjectId);
                            end;
                            end;

                            if (PrimProps.Count > 0) then
                            begin
                                // Skip parameters/designator
                                if (Pos('"type": ""', PrimProps[0]) = 0) then
                                begin
                                    // Unknown object kinds may not expose
                                    // OwnerPartId (they are not standard
                                    // graphical objects) - do not touch it
                                    if (Pos('"type": "unknown"', PrimProps[0]) = 0) then
                                        AddJSONInteger(PrimProps, 'owner_part_id', Prim.OwnerPartId);
                                    PrimsArray.Add(BuildJSONObject(PrimProps, 1));
                                end;
                            end;
                          except
                            PrimProps.Clear;
                            AddJSONProperty(PrimProps, 'type', 'unreadable');
                            PrimsArray.Add(BuildJSONObject(PrimProps, 1));
                          end;
                        finally
                            PrimProps.Free;
                        end;

                        Prim := PrimIterator.NextSchObject;
                    end;
                    LibComp.SchIterator_Destroy(PrimIterator);

                    SymProps.Add(BuildJSONArray(PrimsArray, 'primitives', 1));

                    if (SymbolName = '*') then
                        SymbolsArray.Add(BuildJSONObject(SymProps, 1))
                    else
                        for i := 0 to SymProps.Count - 1 do
                            ResultProps.Add(SymProps[i]);
                finally
                    SymProps.Free;
                    PrimsArray.Free;
                end;
            end;

            LibComp := LibIterator.NextSchObject;
        end;

        CurrentLib.SchIterator_Destroy(LibIterator);

        if (SymbolName = '') or (SymbolName = '*') then
        begin
            AddJSONInteger(ResultProps, 'symbol_count', SymbolsArray.Count);
            ResultProps.Add(BuildJSONArray(SymbolsArray, 'symbols', 1));
        end
        else if not Found then
        begin
            Result := 'ERROR: Symbol not found in library: ' + SymbolName;
            Exit;
        end;

        OutputLines := TStringList.Create;
        try
            OutputLines.Text := BuildJSONObject(ResultProps);
            Result := WriteJSONToFile(OutputLines, ROOT_DIR + '\temp_symbol_primitives.json');
        finally
            OutputLines.Free;
        end;
    finally
        ResultProps.Free;
        SymbolsArray.Free;
    end;
end;

// Function to get all schematic component data
function GetSchematicData(ROOT_DIR: String): String;
var
    Project     : IProject;
    Doc         : IDocument;
    CurrentSch  : ISch_Document;
    Iterator    : ISch_Iterator;
    PIterator   : ISch_Iterator;
    Component   : ISch_Component;
    Parameter, NextParameter : ISch_Parameter;
    Rect        : TCoordRect;
    ComponentsArray : TStringList;
    CompProps   : TStringList;
    ParamsProps : TStringList;
    OutputLines : TStringList;
    Designator, Sheet, ParameterName, ParameterValue : String;
    x, y, width, height, rotation : String;
    left, right, top, bottom : String;
    i : Integer;
    SchematicCount, ComponentCount : Integer;
begin
    Result := '';

    // Retrieve the current project
    Project := GetWorkspace.DM_FocusedProject;
    If (Project = Nil) Then
    begin
        ShowMessage('Error: No project is currently open');
        Exit;
    end;

    // Create array for components
    ComponentsArray := TStringList.Create;
    
    try
        // Count the number of schematic documents
        SchematicCount := 0;
        For i := 0 to Project.DM_LogicalDocumentCount - 1 Do
        Begin
            Doc := Project.DM_LogicalDocuments(i);
            If Doc.DM_DocumentKind = 'SCH' Then
                SchematicCount := SchematicCount + 1;
        End;

        // Process each schematic document
        ComponentCount := 0;
        For i := 0 to Project.DM_LogicalDocumentCount - 1 Do
        Begin
            Doc := Project.DM_LogicalDocuments(i);
            If Doc.DM_DocumentKind = 'SCH' Then
            Begin
                // Open the schematic document
                Client.OpenDocument('SCH', Doc.DM_FullPath);
                CurrentSch := SchServer.GetSchDocumentByPath(Doc.DM_FullPath);

                If (CurrentSch <> Nil) Then
                Begin
                    // Get schematic components
                    Iterator := CurrentSch.SchIterator_Create;
                    Iterator.AddFilter_ObjectSet(MkSet(eSchComponent));

                    Component := Iterator.FirstSchObject;
                    While (Component <> Nil) Do
                    Begin
                        // Create component properties
                        CompProps := TStringList.Create;
                        
                        try
                            // Get basic component properties
                            Designator := Component.Designator.Text;
                            Sheet := Doc.DM_FullPath;

                            // Get position, dimensions and rotation
                            x := FloatToStr(CoordToMils(Component.Location.X));
                            y := FloatToStr(CoordToMils(Component.Location.Y));

                            Rect := Component.BoundingRectangle;
                            left := FloatToStr(CoordToMils(Rect.Left));
                            right := FloatToStr(CoordToMils(Rect.Right));
                            top := FloatToStr(CoordToMils(Rect.Top));
                            bottom := FloatToStr(CoordToMils(Rect.Bottom));

                            width := FloatToStr(CoordToMils(Rect.Right - Rect.Left));
                            height := FloatToStr(CoordToMils(Rect.Bottom - Rect.Top));

                            If Component.Orientation = eRotate0 Then
                                rotation := '0'
                            Else If Component.Orientation = eRotate90 Then
                                rotation := '90'
                            Else If Component.Orientation = eRotate180 Then
                                rotation := '180'
                            Else If Component.Orientation = eRotate270 Then
                                rotation := '270'
                            Else
                                rotation := '0';

                            // Add component properties
                            AddJSONProperty(CompProps, 'designator', Designator);
                            AddJSONProperty(CompProps, 'sheet', Sheet);
                            AddJSONNumber(CompProps, 'schematic_x', StrToFloat(x));
                            AddJSONNumber(CompProps, 'schematic_y', StrToFloat(y));
                            AddJSONNumber(CompProps, 'schematic_width', StrToFloat(width));
                            AddJSONNumber(CompProps, 'schematic_height', StrToFloat(height));
                            AddJSONNumber(CompProps, 'schematic_rotation', StrToFloat(rotation));
                            
                            // Get parameters
                            ParamsProps := TStringList.Create;
                            try
                                // Create parameter iterator
                                PIterator := Component.SchIterator_Create;
                                PIterator.AddFilter_ObjectSet(MkSet(eParameter));

                                Parameter := PIterator.FirstSchObject;
                                
                                // Process all parameters
                                while (Parameter <> nil) do
                                begin
                                    // Get this parameter's info
                                    ParameterName := Parameter.Name;
                                    ParameterValue := Parameter.Text;

                                    // Add parameter to the list
                                    AddJSONProperty(ParamsProps, ParameterName, ParameterValue);
                                    
                                    // Move to next parameter
                                    Parameter := PIterator.NextSchObject;
                                end;

                                Component.SchIterator_Destroy(PIterator);
                                
                                // Add parameters to component
                                CompProps.Add('"parameters": ' + BuildJSONObject(ParamsProps, 2));
                                
                                // Add to components array
                                ComponentsArray.Add(BuildJSONObject(CompProps, 1));
                                ComponentCount := ComponentCount + 1;
                            finally
                                ParamsProps.Free;
                            end;
                        finally
                            CompProps.Free;
                        end;

                        // Move to next component
                        Component := Iterator.NextSchObject;
                    End;

                    CurrentSch.SchIterator_Destroy(Iterator);
                End;
            End;
        End;
        
        // Build the final JSON array
        OutputLines := TStringList.Create;
        try
            OutputLines.Text := BuildJSONArray(ComponentsArray);
            Result := WriteJSONToFile(OutputLines, ROOT_DIR+'temp_schematic_data.json');
        finally
            OutputLines.Free;
        end;
    finally
        ComponentsArray.Free;
    end;
end;


{..............................................................................}
{ Circuit builder                                                              }
{                                                                              }
{ Builds a wired schematic from a spec file. dev/SCHEMATIC_CONVENTIONS.md holds }
{ the drafting rules the spec is expected to already satisfy - this code places }
{ what it is told and reports what it did; it does not lay out.                 }
{                                                                              }
{ Spec records (pipe-delimited, one per line, coordinates in mils):             }
{   PART|desig|symlib|symbol|designitemid|x|y|orient|mirror                     }
{   COMMENT|t   DESCRIPTION|t   FOOTPRINT|n   PARAM|name|value                  }
{   WIRE|x1|y1|x2|y2[|...]      JUNCTION|x|y                                    }
{   NETLABEL|x|y|orient|text    POWER|x|y|orient|style|text|shownetname         }
{   NOTE|x|y|text                                                               }
{                                                                              }
{ Uses LOCAL variables throughout, deliberately. The dev sandbox shares scratch }
{ variables between blocks and that silently corrupted results three separate   }
{ times - a loop counter overwriting a stored coordinate yields plausible wrong }
{ output rather than an error.                                                  }
{..............................................................................}

// Absolute electrical connection point of a pin. Pin.Location is the end
// attached to the BODY; the hot end is PinLength further along the pin's
// orientation. Verified 119:0 against a hand-wired production sheet.
procedure GetPinHotEnd(Pin: ISch_Pin; var HotX: Integer; var HotY: Integer);
var
    PinLen : Integer;
begin
    HotX := CoordToMils(Pin.Location.X);
    HotY := CoordToMils(Pin.Location.Y);
    PinLen := CoordToMils(Pin.PinLength);
    if      (Pin.Orientation = 0) then HotX := HotX + PinLen
    else if (Pin.Orientation = 1) then HotY := HotY + PinLen
    else if (Pin.Orientation = 2) then HotX := HotX - PinLen
    else if (Pin.Orientation = 3) then HotY := HotY - PinLen;
end;

// Body extent EXCLUDING parameter text. Component.BoundingRectangle includes
// the text, so anchoring text to it feeds back on itself and walks the block
// off the sheet.
procedure GetComponentBody(Comp: ISch_Component; var BodyL: Integer; var BodyR: Integer; var BodyT: Integer);
var
    Iter : ISch_Iterator;
    Prim : ISch_GraphicalObject;
    Rect : IDispatch;
begin
    BodyL := 999999;
    BodyR := -999999;
    BodyT := -999999;
    Iter := Comp.SchIterator_Create;
    Iter.AddFilter_ObjectSet(MkSet(ePin, eLine, eRectangle, eArc, ePolyline, eEllipse));
    Prim := Iter.FirstSchObject;
    while (Prim <> nil) do
    begin
        Rect := Prim.BoundingRectangle;
        if (CoordToMils(Rect.Left)  < BodyL) then BodyL := CoordToMils(Rect.Left);
        if (CoordToMils(Rect.Right) > BodyR) then BodyR := CoordToMils(Rect.Right);
        if (CoordToMils(Rect.Top)   > BodyT) then BodyT := CoordToMils(Rect.Top);
        Prim := Iter.NextSchObject;
    end;
    Comp.SchIterator_Destroy(Iter);
end;

// Apply harvested parameter placement to one component. Harvested offsets are
// relative SPACING from a reference sheet; the block is translated to clear the
// body and sit below the topmost pin, so it stays together on one side of any
// rail instead of straddling it. Justification is what aligns the column.
procedure StyleComponentText(Comp: ISch_Component; Placement: TStringList);
var
    LibRef, Rec, PName : String;
    i, MaxDy, AnchorX, BlockTop, Just, TextY : Integer;
    BodyL, BodyR, BodyT : Integer;
    Found, ExactPose : Boolean;
    Iter : ISch_Iterator;
    Param : ISch_Parameter;
begin
    LibRef := Comp.LibReference;

    // Prefer a record harvested from a part in the SAME pose. Its offsets are
    // then literal - a reference sheet may put a horizontal resistor's four
    // fields at the four corners, which is not a stack and must not be
    // re-flowed.
    ExactPose := False;
    Found := False;
    MaxDy := -999999;
    for i := 0 to Placement.Count - 1 do
        if (GetFieldFromPipeString(Placement[i], 1) = LibRef) then
        begin
            Found := True;
            if (StrToInt(GetFieldFromPipeString(Placement[i], 2)) = Comp.Orientation) then
                ExactPose := True;
            if (StrToInt(GetFieldFromPipeString(Placement[i], 5)) > MaxDy) then
                MaxDy := StrToInt(GetFieldFromPipeString(Placement[i], 5));
        end;
    if not Found then Exit;

    GetComponentBody(Comp, BodyL, BodyR, BodyT);
    BlockTop := BodyT - 100;

    Iter := Comp.SchIterator_Create;
    Iter.AddFilter_ObjectSet(MkSet(eParameter));
    Param := Iter.FirstSchObject;
    while (Param <> nil) do
    begin
        Param.IsHidden := True;
        Param := Iter.NextSchObject;
    end;
    Comp.SchIterator_Destroy(Iter);

    for i := 0 to Placement.Count - 1 do
    begin
        Rec := Placement[i];
        if (GetFieldFromPipeString(Rec, 1) = LibRef) and
           ((not ExactPose) or (StrToInt(GetFieldFromPipeString(Rec, 2)) = Comp.Orientation)) then
        begin
            PName := GetFieldFromPipeString(Rec, 3);
            Just  := StrToInt(GetFieldFromPipeString(Rec, 6));
            // Placement is harvested per LibReference, but the reference part
            // may have been ROTATED while this one is not. Beside-the-body only
            // works for a vertical part; on a horizontal one it lands exactly
            // where the wire leaves the end pin. So stack the block ABOVE a
            // horizontal part and beside a vertical one.
            if ExactPose then
            begin
                // Same pose as the reference: offsets are literal.
                AnchorX := CoordToMils(Comp.Location.X) + StrToInt(GetFieldFromPipeString(Rec, 4));
                TextY   := CoordToMils(Comp.Location.Y) + StrToInt(GetFieldFromPipeString(Rec, 5));

                // ...unless the anchor lands ON the body. Offsets such as a
                // resistor's -100 / +600 sit clear of its body; dx=0 on a
                // rotated capacitor is the middle of its plates. Push those
                // clear, keeping dy.
                if (AnchorX >= BodyL) and (AnchorX <= BodyR) then
                    if (Just = 2) or (Just = 5) or (Just = 8) then
                        AnchorX := BodyL - 50
                    else
                        AnchorX := BodyR + 50;
            end
            else
            begin
                // No reference in this pose - fall back to a column beside the
                // body, keeping the harvested spacing and justification.
                if (Just = 2) or (Just = 5) or (Just = 8) then
                    AnchorX := BodyL - 50
                else
                    AnchorX := BodyR + 50;
                TextY := BlockTop - (MaxDy - StrToInt(GetFieldFromPipeString(Rec, 5)));
            end;

            if (PName = 'DESIGNATOR') then
            begin
                Comp.Designator.Autoposition := False;
                Comp.Designator.Orientation := StrToInt(GetFieldFromPipeString(Rec, 7));
                Comp.Designator.Justification := Just;
                Comp.Designator.MoveToXY(MilsToCoord(AnchorX), MilsToCoord(TextY));
            end
            else
            begin
                Iter := Comp.SchIterator_Create;
                Iter.AddFilter_ObjectSet(MkSet(eParameter));
                Param := Iter.FirstSchObject;
                while (Param <> nil) do
                begin
                    if (UpperCase(Param.Name) = UpperCase(PName)) then
                    begin
                        Param.IsHidden := False;
                        Param.Autoposition := False;
                        Param.Orientation := StrToInt(GetFieldFromPipeString(Rec, 7));
                        Param.Justification := Just;
                        Param.MoveToXY(MilsToCoord(AnchorX), MilsToCoord(TextY));
                    end;
                    Param := Iter.NextSchObject;
                end;
                Comp.SchIterator_Destroy(Iter);
            end;
        end;
    end;
end;

function BuildCircuitFromSpec(SpecPath: String; PlacementPath: String): String;
var
    Spec, Placement, PinMap : TStringList;
    TargetDoc, LibDoc : ISch_Document;
    Comp, Found, Replica : ISch_Component;
    Iter, ChildIter : ISch_Iterator;
    Prim, Param, Obj : ISch_GraphicalObject;
    Impl : ISch_Implementation;
    Rec, Kind, CurLib, PName, PVal, Adopted : String;
    Proj : IProject;
    i, fld, vtx, PartCount, GfxCount, PinCount : Integer;
    HotX, HotY : Integer;
    Exists : Boolean;
begin
    Result := '{"success": false, "error": "build did not run"}';

    if not FileExists(SpecPath) then
    begin
        Result := '{"success": false, "error": "spec file not found"}';
        Exit;
    end;

    Spec := TStringList.Create;
    Spec.LoadFromFile(SpecPath);
    Placement := TStringList.Create;
    if FileExists(PlacementPath) then Placement.LoadFromFile(PlacementPath);

    // SAFETY: opening a symbol library makes THAT document current. Stray
    // components were once registered into shared libraries on a read-only
    // share exactly that way. Create the target up front, hold it by
    // reference, and verify it is a schematic (32) not a library (33) once.
    GetWorkSpace.DM_CreateNewDocument('SCH');
    TargetDoc := SchServer.GetCurrentSchDocument;
    if (TargetDoc = nil) then
    begin
        Spec.Free;
        Placement.Free;
        Result := '{"success": false, "error": "could not create a target schematic"}';
        Exit;
    end;
    if (TargetDoc.ObjectID <> 32) then
    begin
        Spec.Free;
        Placement.Free;
        Result := '{"success": false, "error": "target is not a schematic - refusing to build"}';
        Exit;
    end;

    // DM_CreateNewDocument attaches the new sheet to the FOCUSED PROJECT, so
    // generated sheets were being silently adopted into the user's design -
    // they showed up as [13]/[14] in Base.PrjPcb instead of under Free
    // Documents. A generated sheet must not modify an existing project
    // unless the caller asks for that, so detach it again.
    Adopted := '';
    Proj := GetWorkspace.DM_FocusedProject;
    if (Proj <> nil) then
        if (Pos('Free Documents', Proj.DM_ProjectFileName) = 0) then
        begin
            Adopted := Proj.DM_ProjectFileName;
            Proj.DM_RemoveSourceDocument(TargetDoc.DocumentName);
        end;

    SchServer.ProcessControl.PreProcess(TargetDoc, '');
    Comp := nil;
    CurLib := '';
    PartCount := 0;
    GfxCount := 0;

    for i := 0 to Spec.Count - 1 do
    begin
        Rec := Spec[i];
        Kind := GetFieldFromPipeString(Rec, 0);

        if (Kind = 'PART') then
        begin
            if (GetFieldFromPipeString(Rec, 2) <> CurLib) then
            begin
                CurLib := GetFieldFromPipeString(Rec, 2);
                OpenLibraryDocument('SchLib', CurLib);
                Sleep(700);
            end;
            LibDoc := SchServer.GetCurrentSchDocument;

            Found := nil;
            Iter := LibDoc.SchLibIterator_Create;
            Iter.AddFilter_ObjectSet(MkSet(eSchComponent));
            Prim := Iter.FirstSchObject;
            while (Prim <> nil) do
            begin
                if (Prim.LibReference = GetFieldFromPipeString(Rec, 3)) then Found := Prim;
                Prim := Iter.NextSchObject;
            end;
            LibDoc.SchIterator_Destroy(Iter);

            if (Found = nil) then
                Comp := nil
            else
            begin
                Replica := Found.Replicate;
                Replica.Designator.Text := GetFieldFromPipeString(Rec, 1);
                Replica.DesignItemID    := GetFieldFromPipeString(Rec, 4);
                Replica.Orientation     := StrToInt(GetFieldFromPipeString(Rec, 7));

                TargetDoc.RegisterSchObjectInContainer(Replica);
                SchServer.RobotManager.SendMessage(TargetDoc.I_ObjectAddress, c_BroadCast,
                    SCHM_PrimitiveRegistration, Replica.I_ObjectAddress);

                // Mirror is the editor's X key. IsMirrored is a display flag
                // only - it sets True and leaves the pin coordinates alone.
                if (GetFieldFromPipeString(Rec, 8) = '1') then
                    Replica.Mirror(Replica.Location);

                // MoveByXY, not Location, so child text travels with the part
                Replica.MoveByXY(
                    MilsToCoord(StrToInt(GetFieldFromPipeString(Rec, 5)) - CoordToMils(Replica.Location.X)),
                    MilsToCoord(StrToInt(GetFieldFromPipeString(Rec, 6)) - CoordToMils(Replica.Location.Y)));

                Comp := Replica;
                PartCount := PartCount + 1;
            end;
        end

        else if (Kind = 'COMMENT') then
        begin
            if (Comp <> nil) then Comp.Comment.Text := GetFieldFromPipeString(Rec, 1);
        end

        else if (Kind = 'DESCRIPTION') then
        begin
            if (Comp <> nil) then Comp.ComponentDescription := GetFieldFromPipeString(Rec, 1);
        end

        else if (Kind = 'FOOTPRINT') then
        begin
            if (Comp <> nil) then
            begin
                Impl := Comp.AddSchImplementation;
                Impl.ModelName := GetFieldFromPipeString(Rec, 1);
                Impl.ModelType := 'PCBLIB';
                Impl.IsCurrent := True;
                Impl.UseComponentLibrary := True;
            end;
        end

        else if (Kind = 'PARAM') then
        begin
            if (Comp <> nil) then
            begin
                PName := GetFieldFromPipeString(Rec, 1);
                PVal  := GetFieldFromPipeString(Rec, 2);
                Exists := False;
                ChildIter := Comp.SchIterator_Create;
                ChildIter.AddFilter_ObjectSet(MkSet(eParameter));
                Param := ChildIter.FirstSchObject;
                while (Param <> nil) do
                begin
                    if (UpperCase(Param.Name) = UpperCase(PName)) then
                    begin
                        Param.Text := PVal;
                        Exists := True;
                    end;
                    Param := ChildIter.NextSchObject;
                end;
                Comp.SchIterator_Destroy(ChildIter);

                if not Exists then
                begin
                    Param := SchServer.SchObjectFactory(eParameter, eCreate_Default);
                    Param.Name := PName;
                    Param.Text := PVal;
                    Param.ParamType := eParameterType_String;
                    Param.ReadOnlyState := eReadOnly_None;
                    Param.IsHidden := True;
                    Comp.AddSchObject(Param);
                    SchServer.RobotManager.SendMessage(Comp.I_ObjectAddress, c_BroadCast,
                        SCHM_PrimitiveRegistration, Param.I_ObjectAddress);
                end;
            end;
        end

        else if (Kind = 'WIRE') then
        begin
            Obj := SchServer.SchObjectFactory(eWire, eCreate_GlobalCopy);
            Obj.Location := Point(MilsToCoord(StrToInt(GetFieldFromPipeString(Rec, 1))),
                                  MilsToCoord(StrToInt(GetFieldFromPipeString(Rec, 2))));
            fld := 1;
            vtx := 0;
            while (GetFieldFromPipeString(Rec, fld) <> '') do
            begin
                vtx := vtx + 1;
                Obj.InsertVertex := vtx;
                Obj.SetState_Vertex(vtx,
                    Point(MilsToCoord(StrToInt(GetFieldFromPipeString(Rec, fld))),
                          MilsToCoord(StrToInt(GetFieldFromPipeString(Rec, fld + 1)))));
                fld := fld + 2;
            end;
            TargetDoc.RegisterSchObjectInContainer(Obj);
            SchServer.RobotManager.SendMessage(TargetDoc.I_ObjectAddress, c_BroadCast,
                SCHM_PrimitiveRegistration, Obj.I_ObjectAddress);
            GfxCount := GfxCount + 1;
        end

        else if (Kind = 'JUNCTION') then
        begin
            Obj := SchServer.SchObjectFactory(eJunction, eCreate_GlobalCopy);
            Obj.Location := Point(MilsToCoord(StrToInt(GetFieldFromPipeString(Rec, 1))),
                                  MilsToCoord(StrToInt(GetFieldFromPipeString(Rec, 2))));
            TargetDoc.RegisterSchObjectInContainer(Obj);
            SchServer.RobotManager.SendMessage(TargetDoc.I_ObjectAddress, c_BroadCast,
                SCHM_PrimitiveRegistration, Obj.I_ObjectAddress);
            GfxCount := GfxCount + 1;
        end

        else if (Kind = 'NETLABEL') then
        begin
            Obj := SchServer.SchObjectFactory(eNetLabel, eCreate_GlobalCopy);
            Obj.Location := Point(MilsToCoord(StrToInt(GetFieldFromPipeString(Rec, 1))),
                                  MilsToCoord(StrToInt(GetFieldFromPipeString(Rec, 2))));
            Obj.Orientation := StrToInt(GetFieldFromPipeString(Rec, 3));
            Obj.Text := GetFieldFromPipeString(Rec, 4);
            TargetDoc.RegisterSchObjectInContainer(Obj);
            SchServer.RobotManager.SendMessage(TargetDoc.I_ObjectAddress, c_BroadCast,
                SCHM_PrimitiveRegistration, Obj.I_ObjectAddress);
            GfxCount := GfxCount + 1;
        end

        else if (Kind = 'POWER') then
        begin
            Obj := SchServer.SchObjectFactory(ePowerObject, eCreate_GlobalCopy);
            Obj.Location := Point(MilsToCoord(StrToInt(GetFieldFromPipeString(Rec, 1))),
                                  MilsToCoord(StrToInt(GetFieldFromPipeString(Rec, 2))));
            Obj.Orientation := StrToInt(GetFieldFromPipeString(Rec, 3));
            Obj.Style := StrToInt(GetFieldFromPipeString(Rec, 4));
            // NOTE: ShowNetName := False does NOT hide the label - Altium draws
            // it regardless. Keep wires out of the ~300 mil band below a port.
            Obj.ShowNetName := (GetFieldFromPipeString(Rec, 6) = '1');
            Obj.Text := GetFieldFromPipeString(Rec, 5);
            TargetDoc.RegisterSchObjectInContainer(Obj);
            SchServer.RobotManager.SendMessage(TargetDoc.I_ObjectAddress, c_BroadCast,
                SCHM_PrimitiveRegistration, Obj.I_ObjectAddress);
            GfxCount := GfxCount + 1;
        end

        else if (Kind = 'SPORT') then
        begin
            // Sheet port: SPORT|x|y|name|iotype|style|width
            // IOType: 0=unspecified 1=output 2=input 3=bidirectional.
            // Location is the port's connection-side end; wire to it.
            Obj := SchServer.SchObjectFactory(ePort, eCreate_GlobalCopy);
            Obj.Location := Point(MilsToCoord(StrToInt(GetFieldFromPipeString(Rec, 1))),
                                  MilsToCoord(StrToInt(GetFieldFromPipeString(Rec, 2))));
            Obj.Name := GetFieldFromPipeString(Rec, 3);
            Obj.IOType := StrToInt(GetFieldFromPipeString(Rec, 4));
            Obj.Style := StrToInt(GetFieldFromPipeString(Rec, 5));
            Obj.Width := MilsToCoord(StrToInt(GetFieldFromPipeString(Rec, 6)));
            TargetDoc.RegisterSchObjectInContainer(Obj);
            SchServer.RobotManager.SendMessage(TargetDoc.I_ObjectAddress, c_BroadCast,
                SCHM_PrimitiveRegistration, Obj.I_ObjectAddress);
            GfxCount := GfxCount + 1;
        end

        else if (Kind = 'NOTE') then
        begin
            Obj := SchServer.SchObjectFactory(eLabel, eCreate_GlobalCopy);
            Obj.Location := Point(MilsToCoord(StrToInt(GetFieldFromPipeString(Rec, 1))),
                                  MilsToCoord(StrToInt(GetFieldFromPipeString(Rec, 2))));
            Obj.Text := GetFieldFromPipeString(Rec, 3);
            TargetDoc.RegisterSchObjectInContainer(Obj);
            SchServer.RobotManager.SendMessage(TargetDoc.I_ObjectAddress, c_BroadCast,
                SCHM_PrimitiveRegistration, Obj.I_ObjectAddress);
            GfxCount := GfxCount + 1;
        end;
    end;

    // Parameter text last, once every part is placed.
    if (Placement.Count > 0) then
    begin
        Iter := TargetDoc.SchIterator_Create;
        Iter.AddFilter_ObjectSet(MkSet(eSchComponent));
        Prim := Iter.FirstSchObject;
        while (Prim <> nil) do
        begin
            StyleComponentText(Prim, Placement);
            Prim := Iter.NextSchObject;
        end;
        TargetDoc.SchIterator_Destroy(Iter);
    end;

    SchServer.ProcessControl.PostProcess(TargetDoc, '');
    TargetDoc.GraphicallyInvalidate;

    // Pin map: absolute connection point of every placed pin, so a caller can
    // route from measured coordinates instead of predicting rotated offsets.
    PinMap := TStringList.Create;
    PinCount := 0;
    Iter := TargetDoc.SchIterator_Create;
    Iter.AddFilter_ObjectSet(MkSet(eSchComponent));
    Prim := Iter.FirstSchObject;
    while (Prim <> nil) do
    begin
        ChildIter := Prim.SchIterator_Create;
        ChildIter.AddFilter_ObjectSet(MkSet(ePin));
        Param := ChildIter.FirstSchObject;
        while (Param <> nil) do
        begin
            GetPinHotEnd(Param, HotX, HotY);
            PinMap.Add('PIN|' + Prim.Designator.Text + '|' + Param.Name + '|' +
                       IntToStr(HotX) + '|' + IntToStr(HotY));
            PinCount := PinCount + 1;
            Param := ChildIter.NextSchObject;
        end;
        Prim.SchIterator_Destroy(ChildIter);
        Prim := Iter.NextSchObject;
    end;
    TargetDoc.SchIterator_Destroy(Iter);
    PinMap.SaveToFile('C:\Users\Public\altium_mcp\pin_map.txt');
    PinMap.Free;

    Result := '{"success": true, "detached_from": "' + Adopted + '", "sheet": "' + TargetDoc.DocumentName +
              '", "parts": ' + IntToStr(PartCount) +
              ', "graphics": ' + IntToStr(GfxCount) +
              ', "pins": ' + IntToStr(PinCount) + '}';
    Spec.Free;
    Placement.Free;
end;

// The symbol (library component) with this name in a schematic library, or
// nil. SchLibIterator_Create, not SchIterator_Create, works on SchLib documents.
function FindLibSymbol(Lib: ISch_Lib; Name: String): ISch_Component;
var
    Iter : ISch_Iterator;
    Comp : ISch_Component;
begin
    Result := nil;
    Iter := Lib.SchLibIterator_Create;
    Iter.AddFilter_ObjectSet(MkSet(eSchComponent));
    Comp := Iter.FirstSchObject;
    while (Comp <> nil) do
    begin
        if (UpperCase(Comp.LibReference) = UpperCase(Name)) then
        begin
            Result := Comp;
            Break;
        end;
        Comp := Iter.NextSchObject;
    end;
    Lib.SchIterator_Destroy(Iter);
end;

// True when a parameter that Owner's iterator returned belongs to Owner
// itself. A symbol's iterator also returns its pins' parameters. While a
// symbol is shown in the library editor, the library document holds its
// visible parameters (their Container is the SchLib) and hands them back
// when another symbol is shown; those still belong to the symbol.
function IsOwnParameter(Param: ISch_Parameter; Owner: ISch_GraphicalObject): Boolean;
begin
    Result := False;
    if (Param.Container = nil) then Exit;
    if (Param.Container.I_ObjectAddress = Owner.I_ObjectAddress) then
        Result := True
    else if (Owner.ObjectId = eSchComponent) then
        Result := (Param.Container.ObjectId = eSchLib);
end;

// A parameter of a symbol or a pin by name (case-insensitive), or nil
function FindChildParameter(Owner: ISch_GraphicalObject; Name: String): ISch_Parameter;
var
    Iter  : ISch_Iterator;
    Param : ISch_Parameter;
begin
    Result := nil;
    Iter := Owner.SchIterator_Create;
    Iter.AddFilter_ObjectSet(MkSet(eParameter));
    Param := Iter.FirstSchObject;
    while (Param <> nil) do
    begin
        if (UpperCase(Param.Name) = UpperCase(Name)) and IsOwnParameter(Param, Owner) then
        begin
            Result := Param;
            Break;
        end;
        Param := Iter.NextSchObject;
    end;
    Owner.SchIterator_Destroy(Iter);
end;

// Set a symbol's Comment and whether it is visible. The library editor
// keeps its own copy of the Comment of the symbol it shows and writes that
// copy back to the symbol when it shows another one, so for that symbol the
// copy is set too.
procedure SetSymbolComment(Lib: ISch_Lib; Comp: ISch_Component; Text: String; Visible: Boolean);
var
    Iter : ISch_Iterator;
    LibCopy : ISch_Parameter;
    Prim : ISch_Parameter;
begin
    SchServer.RobotManager.SendMessage(Comp.I_ObjectAddress, c_BroadCast, SCHM_BeginModify, c_NoEventData);
    Comp.Comment.Text := Text;
    Comp.Comment.IsHidden := not Visible;
    SchServer.RobotManager.SendMessage(Comp.I_ObjectAddress, c_BroadCast, SCHM_EndModify, c_NoEventData);

    if (Lib.CurrentSchComponent = nil) then Exit;
    if (Lib.CurrentSchComponent.I_ObjectAddress <> Comp.I_ObjectAddress) then Exit;
    LibCopy := nil;
    Iter := Lib.SchIterator_Create;
    Iter.AddFilter_ObjectSet(MkSet(eParameter));
    Prim := Iter.FirstSchObject;
    while (Prim <> nil) do
    begin
        if (UpperCase(Prim.Name) = 'COMMENT') and IsOwnParameter(Prim, Lib) then
            LibCopy := Prim;
        Prim := Iter.NextSchObject;
    end;
    Lib.SchIterator_Destroy(Iter);
    if (LibCopy = nil) then Exit;
    SchServer.RobotManager.SendMessage(LibCopy.I_ObjectAddress, c_BroadCast, SCHM_BeginModify, c_NoEventData);
    LibCopy.Text := Text;
    LibCopy.IsHidden := not Visible;
    SchServer.RobotManager.SendMessage(LibCopy.I_ObjectAddress, c_BroadCast, SCHM_EndModify, c_NoEventData);
end;

// The bottom-left corner of a symbol's first part, as 'x|y' (coords): the
// extent of its drawn graphics, or of its pins when it has none. Used to
// place parameters below the body. Taken from the geometry, not the
// bounding rectangles, which include line widths and so land off grid.
function SymbolBodyCorner(Comp: ISch_Component): String;
var
    Iter    : ISch_Iterator;
    Prim    : ISch_GraphicalObject;
    ObjId   : Integer;
    Pass, V : Integer;
    Found   : Boolean;
    Use     : Boolean;
    MinX    : Integer;
    MinY    : Integer;
    PX, PY  : Integer;
begin
    Found := False;
    MinX := 0;
    MinY := 0;
    for Pass := 1 to 2 do
    begin
        if not Found then
        begin
            Iter := Comp.SchIterator_Create;
            Prim := Iter.FirstSchObject;
            while (Prim <> nil) do
            begin
                // Pass 1: the drawn body; pass 2: the pins (Location is
                // the end at the body)
                ObjId := Prim.ObjectId;
                Use := False;
                if (Prim.OwnerPartId <= 1) then
                begin
                    if (Pass = 2) then
                    begin
                        if (ObjId = ePin) then
                        begin
                            Use := True;
                            PX := Prim.Location.X;
                            PY := Prim.Location.Y;
                        end;
                    end
                    else if (ObjId = eRectangle) or (ObjId = eRoundRectangle) or (ObjId = eLine) then
                    begin
                        Use := True;
                        PX := Prim.Location.X;
                        PY := Prim.Location.Y;
                        if (Prim.Corner.X < PX) then PX := Prim.Corner.X;
                        if (Prim.Corner.Y < PY) then PY := Prim.Corner.Y;
                    end
                    else if (ObjId = ePolyline) or (ObjId = ePolygon) or (ObjId = eBezier) then
                    begin
                        for V := 1 to Prim.VerticesCount do
                        begin
                            if (not Use) or (Prim.Vertex[V].X < PX) then PX := Prim.Vertex[V].X;
                            if (not Use) or (Prim.Vertex[V].Y < PY) then PY := Prim.Vertex[V].Y;
                            Use := True;
                        end;
                    end
                    else if (ObjId = eArc) or (ObjId = ePie) then
                    begin
                        Use := True;
                        PX := Prim.Location.X - Prim.Radius;
                        PY := Prim.Location.Y - Prim.Radius;
                    end
                    else if (ObjId = eEllipticalArc) or (ObjId = eEllipse) then
                    begin
                        Use := True;
                        PX := Prim.Location.X - Prim.Radius;
                        PY := Prim.Location.Y - Prim.SecondaryRadius;
                    end;
                end;
                if Use then
                begin
                    if (not Found) or (PX < MinX) then MinX := PX;
                    if (not Found) or (PY < MinY) then MinY := PY;
                    Found := True;
                end;
                Prim := Iter.NextSchObject;
            end;
            Comp.SchIterator_Destroy(Iter);
        end;
    end;
    Result := IntToStr(MinX) + '|' + IntToStr(MinY);
end;

// Apply edit records to a symbol, as one undo step:
//   COMMENT|<text>[|<visible 1/0>]               set the Comment (hidden unless visible is 1)
//   DESCRIPTION|<text>                           set the Description
//   SYMPARAM|<name>|<value>|<visible 1/0>[|x|y[|justification[|font name:size]]]
//                                                add or replace a symbol parameter;
//                                                one the tool positions gets
//                                                Autoposition off, so it stays put
//   PINPARAM|<pin number>|<name>|<value>         add or replace a hidden pin
//                                                parameter on every pin with that number
//   PINDESC|<pin number>|<text>                  set the Description of every pin with
//                                                that number
//   PINSYMBOL|<pin number>|<edge>|<symbol>       set an IEEE symbol of every pin with that
//                                                number; edge inside, inside_edge,
//                                                outside_edge or outside
// By default, visible symbol parameters without x|y go below the body's
// bottom-left corner, 100 mil apart, in record order. Problems (e.g. no pin with that
// number) are added to Problems as JSON strings. Returns the records applied.
function ApplySymbolEdits(Lib: ISch_Lib; Comp: ISch_Component; Edits: TStringList; Problems: TStringList): Integer;
var
    i, Placed : Integer;
    Line      : String;
    Kind      : String;
    Name      : String;
    Value     : String;
    PinNum    : String;
    Visible   : Boolean;
    Matched   : Boolean;
    Param     : ISch_Parameter;
    Iter      : ISch_Iterator;
    Pin       : ISch_Pin;
    Corner    : String;
    X, Y      : Integer;
    Just      : Integer;
    Sym       : Integer;
    Edge      : String;
    FontID    : Integer;
    FontSpec  : String;
begin
    Result := 0;
    if (Edits.Count = 0) then Exit;
    Corner := SymbolBodyCorner(Comp);
    Placed := 0;

    SchServer.ProcessControl.PreProcess(Lib, '');
    for i := 0 to Edits.Count - 1 do
    begin
        Line := Edits[i];
        Kind := UpperCase(Trim(GetFieldFromPipeString(Line, 0)));
        if (Kind = 'COMMENT') then
        begin
            // Hidden unless the optional visible field is 1
            SetSymbolComment(Lib, Comp, GetFieldFromPipeString(Line, 1),
                             Trim(GetFieldFromPipeString(Line, 2)) = '1');
            Result := Result + 1;
        end
        else if (Kind = 'DESCRIPTION') then
        begin
            SchServer.RobotManager.SendMessage(Comp.I_ObjectAddress, c_BroadCast, SCHM_BeginModify, c_NoEventData);
            Comp.ComponentDescription := GetFieldFromPipeString(Line, 1);
            SchServer.RobotManager.SendMessage(Comp.I_ObjectAddress, c_BroadCast, SCHM_EndModify, c_NoEventData);
            Result := Result + 1;
        end
        else if (Kind = 'SYMPARAM') then
        begin
            Name := Trim(GetFieldFromPipeString(Line, 1));
            Value := GetFieldFromPipeString(Line, 2);
            Visible := (Trim(GetFieldFromPipeString(Line, 3)) = '1');
            // Optional justification; omitted keeps the parameter's own
            Just := -1;
            if (Trim(GetFieldFromPipeString(Line, 6)) <> '') then
            begin
                Just := JustificationValue(GetFieldFromPipeString(Line, 6));
                if (Just < 0) then
                    Problems.Add('"' + JSONEscapeString(Comp.LibReference + ': unknown justification ' +
                        Trim(GetFieldFromPipeString(Line, 6)) + ' for SYMPARAM ' + Name) + '"');
            end;
            // Optional font as <name>:<size>; omitted keeps the parameter's own
            FontID := -1;
            FontSpec := Trim(GetFieldFromPipeString(Line, 7));
            if (FontSpec <> '') then
            begin
                FontID := FontIDFromSpec(FontSpec);
                if (FontID < 0) then
                    Problems.Add('"' + JSONEscapeString(Comp.LibReference + ': font ' + FontSpec +
                        ' for SYMPARAM ' + Name + ' is not <font name>:<size>') + '"');
            end;
            if (Trim(GetFieldFromPipeString(Line, 4)) <> '') then
            begin
                X := MilsToCoord(SafeStrToFloat(Trim(GetFieldFromPipeString(Line, 4))));
                Y := MilsToCoord(SafeStrToFloat(Trim(GetFieldFromPipeString(Line, 5))));
            end
            else
            begin
                if Visible then
                    Placed := Placed + 1;
                X := StrToInt(GetFieldFromPipeString(Corner, 0));
                Y := StrToInt(GetFieldFromPipeString(Corner, 1)) - MilsToCoord(100) * Placed;
            end;
            Param := FindChildParameter(Comp, Name);
            if (Param = nil) then
            begin
                Param := SchServer.SchObjectFactory(eParameter, eCreate_Default);
                Param.Name := Name;
                Param.Text := Value;
                Param.IsHidden := not Visible;
                Param.ShowName := False;
                // Autoposition would let Altium re-stack the parameter away
                // from the location given here
                Param.Autoposition := False;
                Param.Location := Point(X, Y);
                if (Just >= 0) then
                    Param.Justification := Just;
                if (FontID >= 0) then
                    Param.FontID := FontID;
                Comp.AddSchObject(Param);
                SchServer.RobotManager.SendMessage(Comp.I_ObjectAddress, c_BroadCast, SCHM_PrimitiveRegistration, Param.I_ObjectAddress);
            end
            else
            begin
                SchServer.RobotManager.SendMessage(Param.I_ObjectAddress, c_BroadCast, SCHM_BeginModify, c_NoEventData);
                Param.Text := Value;
                Param.IsHidden := not Visible;
                if Visible or (Trim(GetFieldFromPipeString(Line, 4)) <> '') then
                begin
                    Param.Autoposition := False;
                    Param.Location := Point(X, Y);
                end;
                if (Just >= 0) then
                    Param.Justification := Just;
                if (FontID >= 0) then
                    Param.FontID := FontID;
                SchServer.RobotManager.SendMessage(Param.I_ObjectAddress, c_BroadCast, SCHM_EndModify, c_NoEventData);
            end;
            Result := Result + 1;
        end
        else if (Kind = 'PINPARAM') or (Kind = 'PINDESC') or (Kind = 'PINSYMBOL') then
        begin
            PinNum := Trim(GetFieldFromPipeString(Line, 1));
            Name := Trim(GetFieldFromPipeString(Line, 2));
            Value := GetFieldFromPipeString(Line, 3);
            Matched := False;
            Sym := 0;
            Edge := LowerCase(Name);
            if (Kind = 'PINSYMBOL') then
            begin
                Sym := IeeeSymbolValue(Value);
                if (Edge <> 'inside') and (Edge <> 'inside_edge') and (Edge <> 'outside_edge') and (Edge <> 'outside') then
                begin
                    Problems.Add('"' + JSONEscapeString(Comp.LibReference + ': unknown pin edge ' + Name +
                        ' for PINSYMBOL (inside, inside_edge, outside_edge or outside)') + '"');
                    Sym := -1;
                end
                else if (Sym < 0) then
                    Problems.Add('"' + JSONEscapeString(Comp.LibReference + ': unknown IEEE symbol ' + Trim(Value) +
                        ' for PINSYMBOL') + '"');
            end;
            Iter := Comp.SchIterator_Create;
            Iter.AddFilter_ObjectSet(MkSet(ePin));
            Pin := Iter.FirstSchObject;
            while (Pin <> nil) and (Sym >= 0) do
            begin
                if (Pin.Designator = PinNum) and (Kind <> 'PINPARAM') then
                begin
                    Matched := True;
                    SchServer.RobotManager.SendMessage(Pin.I_ObjectAddress, c_BroadCast, SCHM_BeginModify, c_NoEventData);
                    if (Kind = 'PINDESC') then
                        Pin.Description := GetFieldFromPipeString(Line, 2)
                    else if (Edge = 'inside') then
                        Pin.Symbol_Inner := Sym
                    else if (Edge = 'inside_edge') then
                        Pin.Symbol_InnerEdge := Sym
                    else if (Edge = 'outside_edge') then
                        Pin.Symbol_OuterEdge := Sym
                    else
                        Pin.Symbol_Outer := Sym;
                    SchServer.RobotManager.SendMessage(Pin.I_ObjectAddress, c_BroadCast, SCHM_EndModify, c_NoEventData);
                end
                else if (Pin.Designator = PinNum) then
                begin
                    Matched := True;
                    Param := FindChildParameter(Pin, Name);
                    if (Param = nil) then
                    begin
                        Param := SchServer.SchObjectFactory(eParameter, eCreate_Default);
                        Param.Name := Name;
                        Param.Text := Value;
                        Param.IsHidden := True;
                        Param.Location := Pin.Location;
                        Pin.AddSchObject(Param);
                        SchServer.RobotManager.SendMessage(Comp.I_ObjectAddress, c_BroadCast, SCHM_PrimitiveRegistration, Param.I_ObjectAddress);
                    end
                    else
                    begin
                        SchServer.RobotManager.SendMessage(Param.I_ObjectAddress, c_BroadCast, SCHM_BeginModify, c_NoEventData);
                        Param.Text := Value;
                        Param.IsHidden := True;
                        SchServer.RobotManager.SendMessage(Param.I_ObjectAddress, c_BroadCast, SCHM_EndModify, c_NoEventData);
                    end;
                end;
                Pin := Iter.NextSchObject;
            end;
            Comp.SchIterator_Destroy(Iter);
            if Matched then
                Result := Result + 1
            else if (Kind = 'PINPARAM') then
                Problems.Add('"' + JSONEscapeString(Comp.LibReference + ': no pin ' + PinNum + ' for PINPARAM ' + Name) + '"')
            else if (Sym >= 0) then
                Problems.Add('"' + JSONEscapeString(Comp.LibReference + ': no pin ' + PinNum + ' for ' + Kind) + '"');
        end;
    end;
    SchServer.ProcessControl.PostProcess(Lib, '');
    Lib.GraphicallyInvalidate;
end;

// Add a pin's parameters (name, value) to its JSON properties, when it has any
procedure AddPinParametersJSON(Props: TStringList; Pin: ISch_Pin);
var
    Iter   : ISch_Iterator;
    Param  : ISch_Parameter;
    Items  : TStringList;
    PProps : TStringList;
begin
    Items := TStringList.Create;
    try
        Iter := Pin.SchIterator_Create;
        Iter.AddFilter_ObjectSet(MkSet(eParameter));
        Param := Iter.FirstSchObject;
        while (Param <> nil) do
        begin
            if IsOwnParameter(Param, Pin) then
            begin
                PProps := TStringList.Create;
                try
                    AddJSONProperty(PProps, 'name', Param.Name);
                    AddJSONProperty(PProps, 'value', Param.Text);
                    Items.Add(BuildJSONObject(PProps, 3));
                finally
                    PProps.Free;
                end;
            end;
            Param := Iter.NextSchObject;
        end;
        Pin.SchIterator_Destroy(Iter);
        if (Items.Count > 0) then
            Props.Add(BuildJSONArray(Items, 'parameters', 2));
    finally
        Items.Free;
    end;
end;

// Add a symbol's Comment (text, visible) and its parameters (name, value,
// visible, location in mils) to its JSON properties
procedure AddSymbolPropertiesJSON(Props: TStringList; Comp: ISch_Component);
var
    Iter   : ISch_Iterator;
    Param  : ISch_Parameter;
    Items  : TStringList;
    PProps : TStringList;
begin
    PProps := TStringList.Create;
    try
        AddJSONProperty(PProps, 'text', Comp.Comment.Text);
        AddJSONBoolean(PProps, 'visible', not Comp.Comment.IsHidden);
        Props.Add('"comment": ' + BuildJSONObject(PProps, 2));
    finally
        PProps.Free;
    end;

    Items := TStringList.Create;
    try
        Iter := Comp.SchIterator_Create;
        Iter.AddFilter_ObjectSet(MkSet(eParameter));
        Param := Iter.FirstSchObject;
        while (Param <> nil) do
        begin
            // The Comment is reported above; pin parameters are reported
            // with their pins
            if (UpperCase(Param.Name) <> 'COMMENT') and IsOwnParameter(Param, Comp) then
            begin
                PProps := TStringList.Create;
                try
                    AddJSONProperty(PProps, 'name', Param.Name);
                    AddJSONProperty(PProps, 'value', Param.Text);
                    AddJSONBoolean(PProps, 'visible', not Param.IsHidden);
                    AddJSONNumber(PProps, 'x', CoordToMils(Param.Location.X));
                    AddJSONNumber(PProps, 'y', CoordToMils(Param.Location.Y));
                    AddJSONProperty(PProps, 'justification', JustificationName(Param.Justification));
                    AddJSONBoolean(PProps, 'autoposition', Param.Autoposition);
                    PProps.Add('"font": ' + FontJSON(Param.FontID));
                    Items.Add(BuildJSONObject(PProps, 2));
                finally
                    PProps.Free;
                end;
            end;
            Param := Iter.NextSchObject;
        end;
        Comp.SchIterator_Destroy(Iter);
        Props.Add(BuildJSONArray(Items, 'parameters', 1));
    finally
        Items.Free;
    end;
end;

// True when the focused document is the schematic library at Path (compared
// with NormalizedPath, so spelling differences do not matter)
function SchLibIsFocused(Path: String): Boolean;
var
    Lib : ISch_Lib;
begin
    Result := False;
    Lib := SchServer.GetCurrentSchDocument;
    if (Lib <> nil) then
        if (Lib.ObjectID = eSchLib) then
            Result := (NormalizedPath(Lib.DocumentName) = NormalizedPath(Path));
end;

// The file path of the focused schematic library, or ''
function LibPathOfFocusedSchLib(Dummy: Integer): String;
var
    Lib : ISch_Lib;
begin
    Result := '';
    Lib := SchServer.GetCurrentSchDocument;
    if (Lib <> nil) then
        if (Lib.ObjectID = eSchLib) then
            Result := Lib.DocumentName;
end;

// TIeeeSymbol names without the leading 'e', in enum order: a name's
// position is its value (eNoSymbol = 0 ... eBidirectionalSignalFlow = 34)
function IeeeSymbolNames(Dummy: Integer): String;
begin
    Result := 'NOSYMBOL|DOT|RIGHTLEFTSIGNALFLOW|CLOCK|ACTIVELOWINPUT|ANALOGSIGNALIN|' +
              'NOTLOGICCONNECTION|SHIFTRIGHT|POSTPONEDOUTPUT|OPENCOLLECTOR|HIZ|HIGHCURRENT|' +
              'PULSE|SCHMITT|DELAY|GROUPLINE|GROUPBIN|ACTIVELOWOUTPUT|PISYMBOL|GREATEREQUAL|' +
              'LESSEQUAL|SIGMA|OPENCOLLECTORPULLUP|OPENEMITTER|OPENEMITTERPULLUP|' +
              'DIGITALSIGNALIN|AND|INVERTOR|OR|XOR|SHIFTLEFT|INPUTOUTPUT|OPENCIRCUITOUTPUT|' +
              'LEFTRIGHTSIGNALFLOW|BIDIRECTIONALSIGNALFLOW';
end;

// The TIeeeSymbol value of a name (case-insensitive, no leading 'e'; 'none'
// is eNoSymbol), or -1 for an unknown name
function IeeeSymbolValue(Name: String): Integer;
var
    i   : Integer;
    Key : String;
begin
    Result := -1;
    Key := UpperCase(Trim(Name));
    if (Key = 'NONE') then
        Key := 'NOSYMBOL';
    if (Key = '') then Exit;
    for i := 0 to 34 do
        if (GetFieldFromPipeString(IeeeSymbolNames(0), i) = Key) then
        begin
            Result := i;
            Exit;
        end;
end;

// The lower-case name of a TIeeeSymbol value; 'none' for eNoSymbol
function IeeeSymbolName(Value: Integer): String;
begin
    if (Value = 0) then
        Result := 'none'
    else
        Result := LowerCase(GetFieldFromPipeString(IeeeSymbolNames(0), Value));
end;

// Text justification names in TTextJustification order: a name's position
// is its value (eJustify_BottomLeft = 0 ... eJustify_TopRight = 8)
function JustificationNames(Dummy: Integer): String;
begin
    Result := 'bottom_left|bottom_center|bottom_right|center_left|center|' +
              'center_right|top_left|top_center|top_right';
end;

// The TTextJustification value of a name (case-insensitive), or -1
function JustificationValue(Name: String): Integer;
var
    i : Integer;
begin
    Result := -1;
    for i := 0 to 8 do
        if (GetFieldFromPipeString(JustificationNames(0), i) = LowerCase(Trim(Name))) then
        begin
            Result := i;
            Exit;
        end;
end;

// The name of a TTextJustification value
function JustificationName(Value: Integer): String;
begin
    Result := GetFieldFromPipeString(JustificationNames(0), Value);
end;

// The font ID for '<font name>:<size>' (e.g. 'Times New Roman:9'), adding
// the font to the font table when it is missing; -1 when the text is not of
// that form. Fonts are always given by name and size: font IDs differ
// between documents.
function FontIDFromSpec(Spec: String): Integer;
var
    i, Cut   : Integer;
    FontName : String;
    SizeText : String;
begin
    Result := -1;
    Cut := 0;
    for i := 1 to Length(Spec) do
        if (Copy(Spec, i, 1) = ':') then
            Cut := i;
    if (Cut < 2) then Exit;
    FontName := Trim(Copy(Spec, 1, Cut - 1));
    SizeText := Trim(Copy(Spec, Cut + 1, Length(Spec)));
    if (FontName = '') or (SizeText = '') or (Length(SizeText) > 3) then Exit;
    for i := 1 to Length(SizeText) do
        if (Pos(Copy(SizeText, i, 1), '0123456789') = 0) then Exit;
    if (StrToInt(SizeText) < 1) then Exit;
    Result := SchServer.FontManager.GetFontID(StrToInt(SizeText), 0, False, False, False, False, FontName);
end;

// A font as JSON: {"name": ..., "size": ...}
function FontJSON(FontID: Integer): String;
var
    Props : TStringList;
begin
    Props := TStringList.Create;
    try
        AddJSONProperty(Props, 'name', SchServer.FontManager.FontName[FontID]);
        AddJSONInteger(Props, 'size', SchServer.FontManager.Size[FontID]);
        Result := BuildJSONObject(Props, 3);
    finally
        Props.Free;
    end;
end;
