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

    // Create a library component (a page of the library is created)
    SchComponent := SchServer.SchObjectFactory(eSchComponent, eCreate_Default);
    if (SchComponent = Nil) Then
    begin
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

        // Create an auto-sized body rectangle only when no graphics are given
        if (GraphicsCount = 0) then
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
        PinName := Trim(GetFieldFromPipeString(Entry, 1));
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

    // Add the component to the library
    CurrentLib.AddSchComponent(SchComponent);

    // Send a system notification that a new component has been added to the library
    SchServer.RobotManager.SendMessage(nil, c_BroadCast, SCHM_PrimitiveRegistration, SchComponent.I_ObjectAddress);
    CurrentLib.CurrentSchComponent := SchComponent;

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

        // Open the library document. If it is already open, only focus it -
        // re-opening reloads from disk and silently discards unsaved changes.
        if Client.IsDocumentOpen(LibraryPath) then
            ServerDoc := Client.GetDocumentByPath(LibraryPath)
        else
            ServerDoc := Client.OpenDocument('SchLib', LibraryPath);
        if ServerDoc = Nil then
        begin
            Result := 'ERROR: Failed to open library: ' + LibraryPath;
            Exit;
        end;
        Client.ShowDocument(ServerDoc);
        Sleep(500); // Give Altium time to focus the document
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
        if Client.IsDocumentOpen(LibraryPath) then
            ServerDoc := Client.GetDocumentByPath(LibraryPath)
        else
            ServerDoc := Client.OpenDocument('SchLib', LibraryPath);
        if ServerDoc = Nil then
        begin
            Result := 'ERROR: Failed to open library: ' + LibraryPath;
            Exit;
        end;
        Client.ShowDocument(ServerDoc);
        Sleep(500);
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

                    PrimIterator := LibComp.SchIterator_Create;
                    Prim := PrimIterator.FirstSchObject;
                    while (Prim <> Nil) do
                    begin
                        PrimProps := TStringList.Create;
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
                            else
                                AddJSONProperty(PrimProps, 'type', '');
                            end;

                            if (PrimProps.Count > 0) then
                            begin
                                // Skip parameters/designator and unknown types
                                if (Pos('"type": ""', PrimProps[0]) = 0) then
                                begin
                                    AddJSONInteger(PrimProps, 'owner_part_id', Prim.OwnerPartId);
                                    PrimsArray.Add(BuildJSONObject(PrimProps, 1));
                                end;
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
