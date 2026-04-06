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

function CreateSchematicSymbol(SymbolName: String; PinsList: TStringList; PartCount: Integer = 1): String;
var
    CurrentLib       : ISch_Lib;
    SchComponent     : ISch_Component;
    SchPin           : ISch_Pin;
    R                : ISch_Rectangle;
    I, J, PinCount   : Integer;
    PinData          : TStringList;
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
            // Check for owner_part_id in pin data to auto-detect PartCount
            PinData := TStringList.Create;
            try
                PinData.Delimiter := '|';
                PinData.DelimitedText := PinsList[I];
                if (PinData.Count >= 7) then
                begin
                    PinOwnerPartId := StrToInt(PinData[6]);
                    if (PinOwnerPartId > PartCount) then
                        PartCount := PinOwnerPartId;
                end;
            finally
                PinData.Free;
            end;
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

    // Create a body rectangle for each part
    PinCount := 0;
    for J := 1 to PartCount do
    begin
        // Compute bounding box for this part's pins (including shared pins with OwnerPartId=0)
        MinX := 9999; MaxX := -9999; MinY := 9999; MaxY := -9999;
        HasPins := False;

        for I := 0 to PinsList.Count - 1 do
        begin
            if (Pos('Description=', PinsList[I]) = 1) then Continue;

            PinData := TStringList.Create;
            try
                PinData.Delimiter := '|';
                PinData.DelimitedText := PinsList[I];

                if (PinData.Count >= 6) then
                begin
                    PinX := StrToInt(PinData[4]);
                    PinY := StrToInt(PinData[5]);

                    // Determine owner part id (default 1 for backward compatibility)
                    if (PinData.Count >= 7) then
                        PinOwnerPartId := StrToInt(PinData[6])
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
            finally
                PinData.Free;
            end;
        end;

        // Default rectangle if no pins for this part
        if not HasPins then
        begin
            MinX := 300; MinY := 0; MaxX := 1000; MaxY := 1000;
        end;

        // Create a rectangle for this part's body
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

        // Position designator using Part 1's bounding box
        if (J = 1) then
            SchComponent.Designator.Location := Point(MilsToCoord(MinX), MilsToCoord(MaxY + 100));
    end;

    // Add pins to the component
    for I := 0 to PinsList.Count - 1 do
    begin
        if (Pos('Description=', PinsList[I]) = 1) then Continue;

        PinData := TStringList.Create;
        try
            PinData.Delimiter := '|';
            PinData.DelimitedText := PinsList[I];

            if (PinData.Count >= 6) then
            begin
                PinNum := PinData[0];
                PinName := PinData[1];
                PinType := PinData[2];
                PinOrient := PinData[3];
                PinX := StrToInt(PinData[4]);
                PinY := StrToInt(PinData[5]);

                // Determine owner part id (default 1 for backward compatibility)
                if (PinData.Count >= 7) then
                    PinOwnerPartId := StrToInt(PinData[6])
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

                // Set ownership to the specified part (0 = shared across all parts)
                SchPin.OwnerPartId := PinOwnerPartId;
                SchPin.OwnerPartDisplayMode := 0;

                SchComponent.AddSchObject(SchPin);
                PinCount := PinCount + 1;
            end;
        finally
            PinData.Free;
        end;
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

        // Open the library document
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
