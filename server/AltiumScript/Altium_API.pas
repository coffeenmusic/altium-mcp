// altium_bridge.pas
// This script acts as a bridge between the MCP server and Altium
// It reads commands from a request JSON file, executes them, and writes results to a response JSON file

const
	constScriptProjectName = 'Altium_API'; // Define the script project name
    REPLACEALL = 1;
var
    RequestData : TStringList;
    ResponseData : TStringList;
    Params : TStringList;
	REQUEST_FILE : String;
    RESPONSE_FILE : String;
    ROOT_DIR: String;
    RequestId : String;  // echoed in the response so the server can reject stale replies

{..............................................................................}
{ Initialize file paths using a fixed exchange directory.                      }
{ Both the Python MCP server and this script independently resolve to          }
{ C:\Users\Public\altium_mcp\ — avoiding fragile script-project-path          }
{ resolution that breaks when Altium caches stale script projects.             }
{..............................................................................}
procedure InitializeFilePaths();
begin
    ROOT_DIR := 'C:\Users\Public\altium_mcp\';

    // Create the directory if it doesn't exist
    if not DirectoryExists(ROOT_DIR) then
        ForceDirectories(ROOT_DIR);

    // Set the file paths
    REQUEST_FILE := ROOT_DIR + 'request.json';
    RESPONSE_FILE := ROOT_DIR + 'response.json';
end;

// Extract the component pins logic
function ExecuteGetComponentPins(RequestData: TStringList): String;
var
    ParamValue: String;
    i: Integer;
    DesignatorsList: TStringList;
begin
    DesignatorsList := TStringList.Create;
    try
        // Look through all the RequestData lines to find designators
        for i := 0 to RequestData.Count - 1 do
        begin
            if (Pos('"designators"', RequestData[i]) > 0) then
            begin
                // Found the designators parameter
                i := ReadJSONStringArray(RequestData, i, DesignatorsList);
                
                break;
            end;
        end;
        
        if DesignatorsList.Count > 0 then
        begin
            Result := GetComponentPinsFromList(ROOT_DIR, DesignatorsList);
        end
        else
        begin
            LogScriptError('Error: No designators found for get_component_pins');
            Result := '';
        end;
    finally
        DesignatorsList.Free;
    end;
end;

// Extract the create net class logic
function ExecuteCreateNetClass(RequestData: TStringList): String;
var
    ParamValue: String;
    i, ValueStart: Integer;
    ComponentName: String;
    SourceList: TStringList;
begin
    ComponentName := '';
    SourceList := TStringList.Create;
    
    try
        // Parse parameters from the request
        for i := 0 to RequestData.Count - 1 do
        begin
            // Look for class_name
            if (Pos('"class_name"', RequestData[i]) > 0) then
                ComponentName := JSONLineValue(RequestData[i])
            // Look for net_names array
            else if (Pos('"net_names"', RequestData[i]) > 0) then
            begin
                i := ReadJSONStringArray(RequestData, i, SourceList);
            end;
        end;
        
        if (ComponentName <> '') and (SourceList.Count > 0) then
        begin
            Result := CreateNetClass(ComponentName, SourceList);
        end
        else
        begin
            if ComponentName = '' then
                Result := '{"success": false, "error": "No class name provided"}'
            else
                Result := '{"success": false, "error": "No net names provided"}';
        end;
    finally
        SourceList.Free;
    end;
end;

// Extract the take view screenshot logic
function ExecuteTakeViewScreenshot(RequestData: TStringList): String;
var
    ParamValue: String;
    i, ValueStart: Integer;
    ViewType: String;
    DesignatorsList: TStringList;
begin
    // Extract the view type parameter
    ViewType := 'pcb';  // Default to PCB
    DesignatorsList := TStringList.Create;

    try
        // Parse parameters from the request
        for i := 0 to RequestData.Count - 1 do
        begin
            // Look for view_type parameter
            if (Pos('"view_type"', RequestData[i]) > 0) then
            begin
                ValueStart := Pos(':', RequestData[i]) + 1;
                ParamValue := Copy(RequestData[i], ValueStart, Length(RequestData[i]) - ValueStart + 1);
                ParamValue := TrimJSON(ParamValue);
                ViewType := ParamValue;
            end
            // Look for optional designators array to zoom to before capture
            else if (Pos('"designators"', RequestData[i]) > 0) then
            begin
                i := ReadJSONStringArray(RequestData, i, DesignatorsList);
            end;
        end;

        Result := TakeViewScreenshot(ViewType, DesignatorsList);
    finally
        DesignatorsList.Free;
    end;
end;

// Extract the create schematic symbol logic
function ExecuteCreateSchematicSymbol(RequestData: TStringList): String;
var
    ParamValue: String;
    i, ValueStart: Integer;
    ComponentName: String;
    PartCount: Integer;
    PinsList: TStringList;
    GraphicsList: TStringList;
begin
    // Look for component name
    ComponentName := '';
    PartCount := 1;  // Default to single-part symbol
    PinsList := TStringList.Create;
    GraphicsList := TStringList.Create;

    try
        // Parse parameters from the request
        for i := 0 to RequestData.Count - 1 do
        begin
            // Look for component name
            if (Pos('"symbol_name"', RequestData[i]) > 0) then
            begin
                ComponentName := JSONLineValue(RequestData[i]);
            end
            // Look for part_count
            else if (Pos('"part_count"', RequestData[i]) > 0) then
            begin
                ValueStart := Pos(':', RequestData[i]) + 1;
                ParamValue := Copy(RequestData[i], ValueStart, Length(RequestData[i]) - ValueStart + 1);
                ParamValue := TrimJSON(ParamValue);
                PartCount := StrToInt(ParamValue);
            end
            // Look for pins array
            else if (Pos('"pins"', RequestData[i]) > 0) then
            begin
                i := ReadJSONStringArray(RequestData, i, PinsList);
            end
            // Look for graphics array
            else if (Pos('"graphics"', RequestData[i]) > 0) then
            begin
                i := ReadJSONStringArray(RequestData, i, GraphicsList);
            end
            // Look for description
            else if (Pos('"description"', RequestData[i]) > 0) then
            begin
                ValueStart := Pos(':', RequestData[i]) + 1;
                ParamValue := Copy(RequestData[i], ValueStart, Length(RequestData[i]) - ValueStart + 1);
                ParamValue := JSONLineValue(RequestData[i]);
                PinsList.Add('Description=' + ParamValue);
            end;
        end;

        if ComponentName <> '' then
        begin
            Result := CreateSchematicSymbol(ComponentName, PinsList, GraphicsList, PartCount);
        end
        else
        begin
            LogScriptError('Error: No component name provided');
            Result := '';
        end;
    finally
        PinsList.Free;
        GraphicsList.Free;
    end;
end;

// Extract the set PCB layer visibility logic
function ExecuteSetPCBLayerVisibility(RequestData: TStringList): String;
var
    ParamValue: String;
    i, ValueStart: Integer;
    SourceList: TStringList;
    Visible: Boolean;
begin
    // Create a stringlist for layer names and extract the visible parameter
    SourceList := TStringList.Create;
    Visible := False;
    
    try
        // Parse parameters from the request
        for i := 0 to RequestData.Count - 1 do
        begin
            // Look for layer_names array
            if (Pos('"layer_names"', RequestData[i]) > 0) then
            begin
                i := ReadJSONStringArray(RequestData, i, SourceList);
            end
            // Look for visible parameter
            else if (Pos('"visible"', RequestData[i]) > 0) then
            begin
                ValueStart := Pos(':', RequestData[i]) + 1;
                ParamValue := Copy(RequestData[i], ValueStart, Length(RequestData[i]) - ValueStart + 1);
                ParamValue := TrimJSON(ParamValue);
                Visible := (ParamValue = 'true');
            end;
        end;
        
        if SourceList.Count > 0 then
        begin
            Result := SetPCBLayerVisibility(SourceList, Visible);
        end
        else
        begin
            Result := '{"success": false, "error": "No layer names provided"}';
        end;
    finally
        SourceList.Free;
    end;
end;

// Extract the set component position logic
function ExecuteSetComponentPosition(RequestData: TStringList): String;
var
    ParamValue: String;
    i, ValueStart: Integer;
    Designator: String;
    NewX, NewY: Float;
    Rotation: Float;
begin
    Designator := '';
    NewX := 0;
    NewY := 0;
    Rotation := -1;  // Default -1 means keep current rotation
    
    try
        // Parse parameters from the request
        for i := 0 to RequestData.Count - 1 do
        begin
            // Look for designator
            if (Pos('"designator"', RequestData[i]) > 0) then
            begin
                ValueStart := Pos(':', RequestData[i]) + 1;
                ParamValue := Copy(RequestData[i], ValueStart, Length(RequestData[i]) - ValueStart + 1);
                ParamValue := TrimJSON(ParamValue);
                ParamValue := StringReplace(ParamValue, '"', '', REPLACEALL);
                Designator := Trim(ParamValue);
            end
            // Look for x
            else if (Pos('"x"', RequestData[i]) > 0) then
            begin
                ValueStart := Pos(':', RequestData[i]) + 1;
                ParamValue := Copy(RequestData[i], ValueStart, Length(RequestData[i]) - ValueStart + 1);
                ParamValue := TrimJSON(ParamValue);
                NewX := SafeStrToFloat(ParamValue);
            end
            // Look for y
            else if (Pos('"y"', RequestData[i]) > 0) then
            begin
                ValueStart := Pos(':', RequestData[i]) + 1;
                ParamValue := Copy(RequestData[i], ValueStart, Length(RequestData[i]) - ValueStart + 1);
                ParamValue := TrimJSON(ParamValue);
                NewY := SafeStrToFloat(ParamValue);
            end
            // Look for rotation
            else if (Pos('"rotation"', RequestData[i]) > 0) then
            begin
                ValueStart := Pos(':', RequestData[i]) + 1;
                ParamValue := Copy(RequestData[i], ValueStart, Length(RequestData[i]) - ValueStart + 1);
                ParamValue := TrimJSON(ParamValue);
                Rotation := SafeStrToFloat(ParamValue);
            end;
        end;
        
        if Designator <> '' then
        begin
            Result := SetComponentPosition(Designator, NewX, NewY, Rotation);
        end
        else
        begin
            LogScriptError('Error: No designator found for set_component_position');
            Result := '';
        end;
    finally
    end;
end;

// Extract the move components logic
function ExecuteMoveComponents(RequestData: TStringList): String;
var
    ParamValue: String;
    i, ValueStart: Integer;
    DesignatorsList: TStringList;
    XOffset, YOffset: Integer;
    Rotation: Float;
begin
    // For this command, we need to extract the designators array and the offset values
    DesignatorsList := TStringList.Create;
    XOffset := 0;
    YOffset := 0;
    Rotation := 0;  // Default rotation is 0 (no change)
    
    try
        // Parse parameters from the request
        for i := 0 to RequestData.Count - 1 do
        begin
            // Look for designators array
            if (Pos('"designators"', RequestData[i]) > 0) then
            begin
                i := ReadJSONStringArray(RequestData, i, DesignatorsList);
            end
            // Look for x_offset
            else if (Pos('"x_offset"', RequestData[i]) > 0) then
            begin
                ValueStart := Pos(':', RequestData[i]) + 1;
                ParamValue := Copy(RequestData[i], ValueStart, Length(RequestData[i]) - ValueStart + 1);
                ParamValue := TrimJSON(ParamValue);
                XOffset := MilsToCoord(SafeStrToFloat(ParamValue));
            end
            // Look for y_offset
            else if (Pos('"y_offset"', RequestData[i]) > 0) then
            begin
                ValueStart := Pos(':', RequestData[i]) + 1;
                ParamValue := Copy(RequestData[i], ValueStart, Length(RequestData[i]) - ValueStart + 1);
                ParamValue := TrimJSON(ParamValue);
                YOffset := MilsToCoord(SafeStrToFloat(ParamValue));
            end
            // Look for rotation
            else if (Pos('"rotation"', RequestData[i]) > 0) then
            begin
                ValueStart := Pos(':', RequestData[i]) + 1;
                ParamValue := Copy(RequestData[i], ValueStart, Length(RequestData[i]) - ValueStart + 1);
                ParamValue := TrimJSON(ParamValue);
                Rotation := SafeStrToFloat(ParamValue);
            end;
        end;
        
        if DesignatorsList.Count > 0 then
        begin
            Result := MoveComponentsByDesignators(DesignatorsList, XOffset, YOffset, Rotation);
        end
        else
        begin
            LogScriptError('Error: No designators found for move_components');
            Result := '';
        end;
    finally
        DesignatorsList.Free;
    end;
end;

// Extract the get footprint primitives logic
function ExecuteGetFootprintPrimitives(RequestData: TStringList): String;
var
    ParamValue: String;
    i, ValueStart: Integer;
    LibraryPath: String;
    FootprintName: String;
begin
    LibraryPath := '';
    FootprintName := '';

    for i := 0 to RequestData.Count - 1 do
    begin
        if (Pos('"library_path"', RequestData[i]) > 0) then
        begin
            ValueStart := Pos(':', RequestData[i]) + 1;
            ParamValue := Copy(RequestData[i], ValueStart, Length(RequestData[i]) - ValueStart + 1);
            LibraryPath := JSONLineValue(RequestData[i]);
        end
        else if (Pos('"footprint_name"', RequestData[i]) > 0) then
        begin
            ValueStart := Pos(':', RequestData[i]) + 1;
            ParamValue := Copy(RequestData[i], ValueStart, Length(RequestData[i]) - ValueStart + 1);
            FootprintName := JSONLineValue(RequestData[i]);
        end;
    end;

    Result := GetFootprintPrimitives(ROOT_DIR, LibraryPath, FootprintName);
end;

// Extract the create footprints batch logic
function ExecuteCreateFootprintsBatch(RequestData: TStringList): String;
var
    ParamValue: String;
    i, ValueStart: Integer;
    SpecFile: String;
begin
    SpecFile := '';

    for i := 0 to RequestData.Count - 1 do
    begin
        if (Pos('"spec_file"', RequestData[i]) > 0) then
        begin
            ValueStart := Pos(':', RequestData[i]) + 1;
            ParamValue := Copy(RequestData[i], ValueStart, Length(RequestData[i]) - ValueStart + 1);
            SpecFile := JSONLineValue(RequestData[i]);
        end;
    end;

    if (SpecFile <> '') then
        Result := CreateFootprintsBatch(SpecFile)
    else
        Result := 'ERROR: No spec_file provided for create_footprints_batch';
end;

// Extract the create symbols batch logic
function ExecuteCreateSymbolsBatch(RequestData: TStringList): String;
var
    ParamValue: String;
    i, ValueStart: Integer;
    SpecFile: String;
begin
    SpecFile := '';

    // Parse parameters from the request
    for i := 0 to RequestData.Count - 1 do
    begin
        if (Pos('"spec_file"', RequestData[i]) > 0) then
        begin
            ValueStart := Pos(':', RequestData[i]) + 1;
            ParamValue := Copy(RequestData[i], ValueStart, Length(RequestData[i]) - ValueStart + 1);
            SpecFile := JSONLineValue(RequestData[i]);
        end;
    end;

    if (SpecFile <> '') then
        Result := CreateSymbolsBatch(SpecFile)
    else
        Result := 'ERROR: No spec_file provided for create_symbols_batch';
end;

// Extract the get symbol primitives logic
function ExecuteGetSymbolPrimitives(RequestData: TStringList): String;
var
    ParamValue: String;
    i, ValueStart: Integer;
    LibraryPath: String;
    SymbolName: String;
begin
    LibraryPath := '';
    SymbolName := '';

    // Parse parameters from the request
    for i := 0 to RequestData.Count - 1 do
    begin
        // Look for library_path
        if (Pos('"library_path"', RequestData[i]) > 0) then
        begin
            ValueStart := Pos(':', RequestData[i]) + 1;
            ParamValue := Copy(RequestData[i], ValueStart, Length(RequestData[i]) - ValueStart + 1);
            LibraryPath := JSONLineValue(RequestData[i]);
        end
        // Look for symbol_name
        else if (Pos('"symbol_name"', RequestData[i]) > 0) then
        begin
            ValueStart := Pos(':', RequestData[i]) + 1;
            ParamValue := Copy(RequestData[i], ValueStart, Length(RequestData[i]) - ValueStart + 1);
            SymbolName := JSONLineValue(RequestData[i]);
        end;
    end;

    Result := GetSymbolPrimitives(ROOT_DIR, LibraryPath, SymbolName);
end;

// Extract the get net connections logic
function ExecuteGetNetConnections(RequestData: TStringList): String;
var
    ParamValue: String;
    i: Integer;
    DesignatorsList: TStringList;
begin
    DesignatorsList := TStringList.Create;

    try
        // Parse parameters from the request
        for i := 0 to RequestData.Count - 1 do
        begin
            // Look for designators array (optional - empty means use selection)
            if (Pos('"designators"', RequestData[i]) > 0) then
            begin
                i := ReadJSONStringArray(RequestData, i, DesignatorsList);
            end;
        end;

        Result := GetNetConnections(ROOT_DIR, DesignatorsList);
    finally
        DesignatorsList.Free;
    end;
end;

// Extract the check placement logic
function ExecuteCheckPlacement(RequestData: TStringList): String;
var
    ParamValue: String;
    i, ValueStart: Integer;
    DesignatorsList: TStringList;
    ClearanceMils: Double;
begin
    DesignatorsList := TStringList.Create;
    ClearanceMils := 6;

    try
        // Parse parameters from the request
        for i := 0 to RequestData.Count - 1 do
        begin
            // Look for designators array (optional - empty means use selection)
            if (Pos('"designators"', RequestData[i]) > 0) then
            begin
                i := ReadJSONStringArray(RequestData, i, DesignatorsList);
            end
            // Look for clearance_mils
            else if (Pos('"clearance_mils"', RequestData[i]) > 0) then
            begin
                ValueStart := Pos(':', RequestData[i]) + 1;
                ParamValue := Copy(RequestData[i], ValueStart, Length(RequestData[i]) - ValueStart + 1);
                ParamValue := TrimJSON(ParamValue);
                ClearanceMils := SafeStrToFloat(ParamValue);
            end;
        end;

        Result := CheckPlacement(DesignatorsList, ClearanceMils);
    finally
        DesignatorsList.Free;
    end;
end;

// Extract the place components logic
function ExecutePlaceComponents(RequestData: TStringList): String;
var
    ParamValue: String;
    i: Integer;
    PlacementsList: TStringList;
begin
    // Placements arrive as an array of pipe-delimited strings
    // ('Designator|X|Y|Rotation|Layer') so the line-based parser stays robust
    PlacementsList := TStringList.Create;

    try
        // Parse parameters from the request
        for i := 0 to RequestData.Count - 1 do
        begin
            // Look for placements array
            if (Pos('"placements"', RequestData[i]) > 0) then
            begin
                i := ReadJSONStringArray(RequestData, i, PlacementsList);
            end;
        end;

        if PlacementsList.Count > 0 then
        begin
            Result := PlaceComponentsFromList(PlacementsList);
        end
        else
        begin
            Result := 'ERROR: No placements provided for place_components';
        end;
    finally
        PlacementsList.Free;
    end;
end;

// Extract the layout duplicator apply logic
function ExecuteLayoutDuplicatorApply(RequestData: TStringList): String;
var
    ParamValue: String;
    i: Integer;
    SourceList, DestList: TStringList;
begin
    // For this command, we need to extract the source and destination lists
    SourceList := TStringList.Create;
    DestList := TStringList.Create;
    
    try
        // Parse parameters from the request
        for i := 0 to RequestData.Count - 1 do
        begin
            // Look for source designators array
            if (Pos('"source_designators"', RequestData[i]) > 0) then
            begin
                i := ReadJSONStringArray(RequestData, i, SourceList);
            end
            // Look for destination designators array
            else if (Pos('"destination_designators"', RequestData[i]) > 0) then
            begin
                i := ReadJSONStringArray(RequestData, i, DestList);
            end
        end;
        
        if (SourceList.Count > 0) and (DestList.Count > 0) then
        begin
            Result := ApplyLayoutDuplicator(SourceList, DestList);
        end
        else
        begin
            LogScriptError('Error: Source or destination lists are empty');
            Result := '{"success": false, "error": "Source or destination lists are empty"}';
        end;
    finally
        SourceList.Free;
        DestList.Free;
    end;
end;

// The optional "outjob_path" request parameter, '' when absent or empty
function RequestOutJobPath(RequestData: TStringList): String;
var
    i: Integer;
begin
    Result := '';
    for i := 0 to RequestData.Count - 1 do
    begin
        if (Pos('"outjob_path"', RequestData[i]) > 0) then
        begin
            Result := Trim(JSONLineValue(RequestData[i]));
            break;
        end;
    end;
end;

// Function to execute get output job containers
function ExecuteGetOutputJobContainers(RequestData: TStringList): String;
begin
    Result := GetOutputJobContainers(RequestOutJobPath(RequestData), ROOT_DIR);
end;

// Function to execute run output jobs
function ExecuteRunOutputJobs(RequestData: TStringList): String;
var
    ParamValue: String;
    i: Integer;
    ContainersList: TStringList;
begin
    ContainersList := TStringList.Create;
    
    try
        // Parse parameters from the request
        for i := 0 to RequestData.Count - 1 do
        begin
            if (Pos('"container_names"', RequestData[i]) > 0) then
            begin
                i := ReadJSONStringArray(RequestData, i, ContainersList);
            end;
        end;
        
        if ContainersList.Count > 0 then
        begin
            Result := RunOutputJobs(ContainersList, RequestOutJobPath(RequestData), ROOT_DIR);
        end
        else
        begin
            LogScriptError('Error: No container names specified');
            Result := '{"success": false, "error": "No container names specified"}';
        end;
    finally
        ContainersList.Free;
    end;
end;

// Extract the create PCB footprint logic
function ExecuteCreatePCBFootprint(RequestData: TStringList): String;
var
    ParamValue: String;
    i, ValueStart: Integer;
    FootprintName: String;
    Description: String;
    PadsList: TStringList;
    CourtyardXMM: Double;
    CourtyardYMM: Double;
begin
    FootprintName := '';
    Description := '';
    CourtyardXMM := 0;
    CourtyardYMM := 0;
    PadsList := TStringList.Create;

    try
        for i := 0 to RequestData.Count - 1 do
        begin
            if (Pos('"footprint_name"', RequestData[i]) > 0) then
            begin
                ValueStart := Pos(':', RequestData[i]) + 1;
                FootprintName := JSONLineValue(RequestData[i]);
            end
            else if (Pos('"description"', RequestData[i]) > 0) then
            begin
                ValueStart := Pos(':', RequestData[i]) + 1;
                Description := JSONLineValue(RequestData[i]);
            end
            else if (Pos('"courtyard_x_mm"', RequestData[i]) > 0) then
            begin
                ValueStart := Pos(':', RequestData[i]) + 1;
                ParamValue := TrimJSON(Copy(RequestData[i], ValueStart, Length(RequestData[i])));
                CourtyardXMM := SafeStrToFloat(ParamValue);
            end
            else if (Pos('"courtyard_y_mm"', RequestData[i]) > 0) then
            begin
                ValueStart := Pos(':', RequestData[i]) + 1;
                ParamValue := TrimJSON(Copy(RequestData[i], ValueStart, Length(RequestData[i])));
                CourtyardYMM := SafeStrToFloat(ParamValue);
            end
            else if (Pos('"pads"', RequestData[i]) > 0) then
            begin
                i := ReadJSONStringArray(RequestData, i, PadsList);
            end;
        end;

        if FootprintName <> '' then
            Result := CreatePCBFootprint(FootprintName, Description, PadsList, CourtyardXMM, CourtyardYMM)
        else
            Result := '{"success": false, "error": "No footprint name provided"}';
    finally
        PadsList.Free;
    end;
end;

// Extract the search library symbol logic
function ExecuteSearchLibrarySymbol(RequestData: TStringList): String;
var
    ParamValue: String;
    i, ValueStart: Integer;
    LibraryPath: String;
    SymbolName: String;
begin
    LibraryPath := '';
    SymbolName := '';

    // Parse parameters from the request
    for i := 0 to RequestData.Count - 1 do
    begin
        // Look for library_path
        if (Pos('"library_path"', RequestData[i]) > 0) then
        begin
            ValueStart := Pos(':', RequestData[i]) + 1;
            ParamValue := Copy(RequestData[i], ValueStart, Length(RequestData[i]) - ValueStart + 1);
            LibraryPath := JSONLineValue(RequestData[i]);
        end
        // Look for symbol_name
        else if (Pos('"symbol_name"', RequestData[i]) > 0) then
        begin
            ValueStart := Pos(':', RequestData[i]) + 1;
            ParamValue := Copy(RequestData[i], ValueStart, Length(RequestData[i]) - ValueStart + 1);
            SymbolName := JSONLineValue(RequestData[i]);
        end;
    end;

    if SymbolName <> '' then
    begin
        Result := SearchLibrarySymbol(ROOT_DIR, LibraryPath, SymbolName);
    end
    else
    begin
        Result := 'ERROR: No symbol name provided for search_library_symbol';
    end;
end;

// get_erc_violations: optional "compile" parameter (default true)
function ExecuteGetERCViolations(RequestData: TStringList): String;
var
    i: Integer;
    DoCompile: Boolean;
    ParamValue: String;
begin
    DoCompile := True;
    for i := 0 to RequestData.Count - 1 do
    begin
        if (Pos('"compile"', RequestData[i]) > 0) then
        begin
            ParamValue := Copy(RequestData[i], Pos(':', RequestData[i]) + 1, Length(RequestData[i]));
            DoCompile := LowerCase(TrimJSON(ParamValue)) <> 'false';
        end;
    end;
    Result := GetERCViolations(ROOT_DIR, DoCompile);
end;

// get_component_primitives: "designators" array parameter
function ExecuteGetComponentPrimitives(RequestData: TStringList): String;
var
    ParamValue: String;
    i: Integer;
    DesignatorsList: TStringList;
begin
    DesignatorsList := TStringList.Create;
    try
        for i := 0 to RequestData.Count - 1 do
        begin
            if (Pos('"designators"', RequestData[i]) > 0) then
            begin
                i := ReadJSONStringArray(RequestData, i, DesignatorsList);
                break;
            end;
        end;

        if DesignatorsList.Count > 0 then
            Result := GetComponentPrimitivesFromList(ROOT_DIR, DesignatorsList)
        else
            Result := 'ERROR: No designators given for get_component_primitives';
    finally
        DesignatorsList.Free;
    end;
end;

// get_project_components: optional "include_parameters" (default false)
function ExecuteGetProjectComponents(RequestData: TStringList): String;
var
    i: Integer;
    IncludeParams: Boolean;
    ParamValue: String;
begin
    IncludeParams := False;
    for i := 0 to RequestData.Count - 1 do
    begin
        if (Pos('"include_parameters"', RequestData[i]) > 0) then
        begin
            ParamValue := Copy(RequestData[i], Pos(':', RequestData[i]) + 1, Length(RequestData[i]));
            IncludeParams := LowerCase(TrimJSON(ParamValue)) = 'true';
        end;
    end;
    Result := GetProjectComponents(ROOT_DIR, IncludeParams);
end;

// Function to execute a command with parameters
function ExecuteCommand(CommandName: String): String;
var
    ViewHint   : String;
    HintIdx    : Integer;
    HintStart  : Integer;
    HintValue  : String;
begin
    Result := '';

    // take_view_screenshot can target a PCB or a schematic. Pull view_type out
    // of the request so the focus helper does not always demand a PCB.
    ViewHint := '';
    if CommandName = 'take_view_screenshot' then
    begin
        for HintIdx := 0 to RequestData.Count - 1 do
        begin
            if (Pos('"view_type"', RequestData[HintIdx]) > 0) then
            begin
                HintStart := Pos(':', RequestData[HintIdx]) + 1;
                HintValue := Copy(RequestData[HintIdx], HintStart,
                                  Length(RequestData[HintIdx]) - HintStart + 1);
                ViewHint := LowerCase(TrimJSON(HintValue));
            end;
        end;
    end;

    // A focus failure is an answer, not a dialog: WriteResponse turns the
    // 'ERROR: ' prefix into success=false with this message.
    // An OutJob command that names its file needs no OutJob open in a
    // project: the command resolves and opens the named file itself.
    if not (((CommandName = 'get_output_job_containers') or
             (CommandName = 'run_output_jobs')) and
            (RequestOutJobPath(RequestData) <> '')) then
    begin
        if not EnsureDocumentFocused(CommandName, ViewHint) then
        begin
            Result := 'ERROR: ' + FocusFailure;
            Exit;
        end;
    end;

    // Direct command execution based on the command name
    case CommandName of
        'get_component_pins':
            Result := ExecuteGetComponentPins(RequestData);            
        'get_all_nets':
            Result := GetAllNets(ROOT_DIR);            
        'create_net_class':
            Result := ExecuteCreateNetClass(RequestData);            
        'get_all_component_data':
            Result := GetAllComponentData(ROOT_DIR, False);            
        'take_view_screenshot':
            Result := ExecuteTakeViewScreenshot(RequestData);            
        'get_library_symbol_reference':
            Result := GetLibrarySymbolReference(ROOT_DIR);            
        'create_schematic_symbol':
            Result := ExecuteCreateSchematicSymbol(RequestData);            
        'get_schematic_data':
            Result := GetSchematicData(ROOT_DIR);            
        'get_pcb_layers':
            Result := GetPCBLayers(ROOT_DIR);            
        'set_pcb_layer_visibility':
            Result := ExecuteSetPCBLayerVisibility(RequestData);   
        'get_pcb_layer_stackup':
            Result := GetPCBLayerStackup(ROOT_DIR);         
        'get_selected_components_coordinates':
            Result := GetSelectedComponentsCoordinates(ROOT_DIR); 
		'set_component_position':
			Result := ExecuteSetComponentPosition(RequestData);
        'move_components':
            Result := ExecuteMoveComponents(RequestData);
        'place_components':
            Result := ExecutePlaceComponents(RequestData);
        'check_placement':
            Result := ExecuteCheckPlacement(RequestData);
        'get_net_connections':
            Result := ExecuteGetNetConnections(RequestData);
        'get_symbol_primitives':
            Result := ExecuteGetSymbolPrimitives(RequestData);
        'create_symbols_batch':
            Result := ExecuteCreateSymbolsBatch(RequestData);
        'build_circuit':
            Result := BuildCircuitFromSpec(ROOT_DIR + 'circuit_spec.txt',
                                          ROOT_DIR + 'param_placement.txt');
        'get_footprint_primitives':
            Result := ExecuteGetFootprintPrimitives(RequestData);
        'create_footprints_batch':
            Result := ExecuteCreateFootprintsBatch(RequestData);
        'layout_duplicator':
            Result := GetLayoutDuplicatorComponents(True);            
        'layout_duplicator_apply':
            Result := ExecuteLayoutDuplicatorApply(RequestData);            
        'get_pcb_rules':
            Result := GetPCBRules(ROOT_DIR);
        'get_output_job_containers':
            Result := ExecuteGetOutputJobContainers(RequestData);
        'run_output_jobs':
            Result := ExecuteRunOutputJobs(RequestData);
        'search_library_symbol':
            Result := ExecuteSearchLibrarySymbol(RequestData);
        'create_pcb_footprint':
            Result := ExecuteCreatePCBFootprint(RequestData);
        'get_project_info':
            Result := GetProjectInfo(ROOT_DIR);
        'get_project_components':
            Result := ExecuteGetProjectComponents(RequestData);
        'get_erc_violations':
            Result := ExecuteGetERCViolations(RequestData);
        'get_board_summary':
            Result := GetBoardSummary(ROOT_DIR);
        'get_component_primitives':
            Result := ExecuteGetComponentPrimitives(RequestData);
    else
        LogScriptError('Error: Unknown command: ' + CommandName);
    end;
end;

// Function to extract a parameter name-value pair from a JSON line
procedure ExtractParameter(Line: String);
var
    ParamName: String;
    ParamValue: String;
    NameEnd: Integer;
    ValueStart: Integer;
begin
    // Skip command line and lines without a colon
    if (Pos('"command":', Line) > 0) or (Pos(':', Line) = 0) then
        Exit;

    // Find the parameter name
    NameEnd := Pos(':', Line) - 1;
    if NameEnd <= 0 then Exit;

    // Extract and clean the parameter name
    ParamName := Copy(Line, 1, NameEnd);
    ParamName := TrimJSON(ParamName);

    // Extract the parameter value - don't trim arrays
    ValueStart := Pos(':', Line) + 1;
    ParamValue := Copy(Line, ValueStart, Length(Line) - ValueStart + 1);

    // Decode it only if it's not an array (keeps commas, decodes escapes)
    if (Pos('[', ParamValue) = 0) then
        ParamValue := JSONLineValue(Line);

    // Add to parameters list
    if (ParamName <> '') and (ParamName <> 'command') then
        Params.Add(ParamName + '=' + ParamValue);
end;

procedure WriteResponse(Success: Boolean; Data: String; ErrorMsg: String);
var
    ActualSuccess: Boolean;
    ActualErrorMsg: String;
    ResultProps: TStringList;
begin
    // Check if Data contains an error message
    if (Pos('ERROR:', Data) = 1) then
    begin
        ActualSuccess := False;
        ActualErrorMsg := Copy(Data, 8, Length(Data)); // Remove 'ERROR: ' prefix
    end
    else
    begin
        ActualSuccess := Success;
        ActualErrorMsg := ErrorMsg;
    end;

    // Create response props
    ResultProps := TStringList.Create;
    ResponseData := TStringList.Create;
    
    try
        // Add properties
        AddJSONBoolean(ResultProps, 'success', ActualSuccess);
        if RequestId <> '' then
            AddJSONProperty(ResultProps, 'request_id', RequestId);

        if ActualSuccess then
        begin
            // For JSON responses (starting with [ or {), don't wrap in additional quotes
            if (Length(Data) > 0) and ((Data[1] = '[') or (Data[1] = '{')) then
                ResultProps.Add(JSONPairStr('result', Data, False))
            else
                AddJSONProperty(ResultProps, 'result', Data);
        end
        else
        begin
            AddJSONProperty(ResultProps, 'error', ActualErrorMsg);
        end;
        
        // Build response
        ResponseData.Text := BuildJSONObject(ResultProps);
        ResponseData.SaveToFile(RESPONSE_FILE);
    finally
        ResultProps.Free;
        ResponseData.Free;
    end;
end;

function IsScriptProject(Project: IProject): Boolean;
begin
    Result := UpperCase(ExtractFileExt(Project.DM_ProjectFullPath)) = '.PRJSCR';
end;

// A script that crashes into the debugger leaves its script project focused,
// and project-level commands (project info, ERC, ...) would then act on it.
// Remember the last design project and bring it back to the front instead.
procedure EnsureDesignProjectFocused();
var
    Project, Candidate : IProject;
    Doc : IDocument;
    ServerDoc : IServerDocument;
    LastPath, StoreFile : String;
    Store : TStringList;
    P, I : Integer;
begin
    StoreFile := ROOT_DIR + 'last_project.txt';
    Project := GetWorkspace.DM_FocusedProject;
    Store := TStringList.Create;
    try
        if (Project <> Nil) and not IsScriptProject(Project) then
        begin
            Store.Text := Project.DM_ProjectFullPath;
            Store.SaveToFile(StoreFile);
        end
        else
        begin
            LastPath := '';
            if FileExists(StoreFile) then
            begin
                Store.LoadFromFile(StoreFile);
                if Store.Count > 0 then LastPath := UpperCase(Trim(Store[0]));
            end;

            // The remembered project if it is still open, else the first design project
            Candidate := Nil;
            for P := 0 to GetWorkspace.DM_ProjectCount - 1 do
            begin
                Project := GetWorkspace.DM_Projects(P);
                if (Project <> Nil) and not IsScriptProject(Project) then
                    if (Candidate = Nil) or (UpperCase(Project.DM_ProjectFullPath) = LastPath) then
                        Candidate := Project;
            end;

            // Showing one of its already open documents makes it the focused project
            if Candidate <> Nil then
                for I := 0 to Candidate.DM_LogicalDocumentCount - 1 do
                begin
                    Doc := Candidate.DM_LogicalDocuments(I);
                    ServerDoc := Client.GetDocumentByPath(Doc.DM_FullPath);
                    if ServerDoc <> Nil then
                    begin
                        Client.ShowDocument(ServerDoc);
                        Break;
                    end;
                end;
        end;
    finally
        Store.Free;
    end;
end;

// Main procedure to run the bridge
procedure Run;
var
    CommandType: String;
    Result: String;
    i: Integer;
    Line: String;
    ValueStart: Integer;
begin
    // Initialize file paths based on script location
    InitializeFilePaths();
    RequestId := '';

    // Check if request file exists
    if not FileExists(REQUEST_FILE) then
    begin
        LogScriptError('Error: No request file found at ' + REQUEST_FILE);
        Exit;
    end;

    try
        // Initialize parameters list
        Params := TStringList.Create;
        Params.Delimiter := '=';

        // Read the request file
        RequestData := TStringList.Create;
        try
            RequestData.LoadFromFile(REQUEST_FILE);
            // Consume the request: its disappearance tells the server the
            // script started, and a duplicate launch finds nothing to re-run
            DeleteFile(REQUEST_FILE);

            // Default command type
            CommandType := '';

            // Parse command and parameters
            for i := 0 to RequestData.Count - 1 do
            begin
                Line := RequestData[i];

                // Extract command
                if Pos('"command":', Line) > 0 then
                begin
                    ValueStart := Pos(':', Line) + 1;
                    CommandType := Copy(Line, ValueStart, Length(Line) - ValueStart + 1);
                    CommandType := TrimJSON(CommandType);
                end
                else if Pos('"request_id":', Line) > 0 then
                begin
                    ValueStart := Pos(':', Line) + 1;
                    RequestId := TrimJSON(Copy(Line, ValueStart, Length(Line) - ValueStart + 1));
                end
                else
                begin
                    // Extract all other parameters
                    ExtractParameter(Line);
                end;
            end;

            // Execute the command if valid
            if CommandType <> '' then
            begin
                EnsureDesignProjectFocused();
                Result := ExecuteCommand(CommandType);

                if Result <> '' then
                begin
                    WriteResponse(True, Result, '');
                end
                else
                begin
                    WriteResponse(False, '', 'Command execution failed');
                    LogScriptError('Error: Command execution failed');
                end;
            end
            else
            begin
                WriteResponse(False, '', 'No command specified');
                LogScriptError('Error: No command specified');
            end;
        finally
            RequestData.Free;
            Params.Free;
        end;
    except
        // Simple exception handling without the specific exception type
        WriteResponse(False, '', 'Exception occurred during script execution');
        LogScriptError('Error: Exception occurred during script execution');
    end;
end;


