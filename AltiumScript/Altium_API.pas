// altium_bridge.pas
// This script acts as a bridge between the MCP server and Altium
// It reads commands from a request JSON file, executes them, and writes results to a response JSON file

const
    REQUEST_FILE = 'C:\AltiumMCP\request.json';
    RESPONSE_FILE = 'C:\AltiumMCP\response.json';
    REPLACEALL = 1;
var
    RequestData : TStringList;
    ResponseData : TStringList;
    Params : TStringList;

// Function to execute a command with parameters
function ExecuteCommand(CommandName: String): String;
var
    ParamValue: String;
    i, XOffset, YOffset, Rotation, ValueStart: Integer;
    DesignatorsList: TStringList;
    PCBAvailable, Visible: Boolean;
    SourceList, DestList, PinsList: TStringList;
    ComponentName, ViewType: String; 
begin
    Result := '';

    // For PCB-related commands, ensure PCB is available first
    if (CommandName = 'get_component_pins') or
       (CommandName = 'get_all_component_data') or
       (CommandName = 'get_selected_components_coordinates') or
       (CommandName = 'layout_duplicator') or
       (CommandName = 'get_pcb_layers') or
       (CommandName = 'set_pcb_layer_visibility') or
       (CommandName = 'move_components') or
       (CommandName = 'layout_duplicator_apply') or
       (CommandName = 'get_all_nets') or
       (CommandName = 'create_net_class') or
       (CommandName = 'get_all_components') or
       (CommandName = 'get_pcb_rules') then
    begin
        if not EnsureDocumentFocused('PCB') then
        begin
            // Early exit with error
            Result := 'ERROR: No PCB document found. Tell the user to open a PCB document in their project. Dont try any more tools until the user responds.';
            Exit;
        end;
    end
    else if (CommandName = 'create_schematic_symbol') or
            (CommandName = 'get_library_symbol_reference') then
    begin
        if not EnsureDocumentFocused('SCHLIB') then
        begin
            // Early exit with error
            Result := 'ERROR: No schematic library document found. Tell the user to open a schematic library document in their project. Dont try any more tools until the user responds.';
            Exit;
        end;
    end;

    // Process different commands based on command name
    if CommandName = 'get_component_pins' then
    begin
        // For this command, we need to manually parse the designators array
        DesignatorsList := TStringList.Create;
        
        // Look through all the RequestData lines to find designators
        for i := 0 to RequestData.Count - 1 do
        begin
            if (Pos('"designators"', RequestData[i]) > 0) then
            begin
                // Found the designators parameter
                // Parse the array in the next lines
                i := i + 1; // Move to the next line (should be '[')
                
                while (i < RequestData.Count) and (Pos(']', RequestData[i]) = 0) do
                begin
                    // This is an array element
                    // Extract the designator value
                    ParamValue := RequestData[i];
                    ParamValue := StringReplace(ParamValue, '"', '', REPLACEALL);
                    ParamValue := StringReplace(ParamValue, ',', '', REPLACEALL);
                    ParamValue := Trim(ParamValue);
                    
                    if (ParamValue <> '') and (ParamValue <> '[') then
                        DesignatorsList.Add(ParamValue);
                    
                    i := i + 1;
                end;
                
                break;
            end;
        end;
        
        if DesignatorsList.Count > 0 then
        begin
            Result := GetComponentPinsFromList(DesignatorsList);
        end
        else
        begin
            ShowMessage('Error: No designators found for get_component_pins');
            Result := '';
        end;

        DesignatorsList.Free;
    end
    else if CommandName = 'get_all_nets' then
    begin
        Result := GetAllNets;
    end
    else if CommandName = 'create_net_class' then
    begin
        // For this command, we need to extract the class name and net names list
        ComponentName := '';
        SourceList := TStringList.Create;
        
        try
            // Parse parameters from the request
            for i := 0 to RequestData.Count - 1 do
            begin
                // Look for class_name
                if (Pos('"class_name"', RequestData[i]) > 0) then
                begin
                    ValueStart := Pos(':', RequestData[i]) + 1;
                    ComponentName := Copy(RequestData[i], ValueStart, Length(RequestData[i]) - ValueStart + 1);
                    ComponentName := TrimJSON(ComponentName);
                end
                // Look for net_names array
                else if (Pos('"net_names"', RequestData[i]) > 0) then
                begin
                    // Parse the array in the next lines
                    i := i + 1; // Move to the next line (should be '[')
                    
                    while (i < RequestData.Count) and (Pos(']', RequestData[i]) = 0) do
                    begin
                        // Extract the net name
                        ParamValue := RequestData[i];
                        ParamValue := StringReplace(ParamValue, '"', '', REPLACEALL);
                        ParamValue := StringReplace(ParamValue, ',', '', REPLACEALL);
                        ParamValue := Trim(ParamValue);
                        
                        if (ParamValue <> '') and (ParamValue <> '[') then
                            SourceList.Add(ParamValue);
                        
                        i := i + 1;
                    end;
                end
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
    end
    else if CommandName = 'get_all_component_data' then
    begin
        Result := GetAllComponentData(False);
    end
    else if CommandName = 'take_view_screenshot' then
    begin
        // Extract the view type parameter
        ViewType := 'pcb';  // Default to PCB
        
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
                Break;
            end;
        end;
        
        Result := TakeViewScreenshot(ViewType);
    end
    else if CommandName = 'get_library_symbol_reference' then
    begin        
        Result := GetLibrarySymbolReference;
    end
    else if CommandName = 'create_schematic_symbol' then
    begin
        // Look for component name
        ComponentName := '';
        PinsList := TStringList.Create;
        
        // Parse parameters from the request
        for i := 0 to RequestData.Count - 1 do
        begin
            // Look for component name
            if (Pos('"symbol_name"', RequestData[i]) > 0) then
            begin
                ValueStart := Pos(':', RequestData[i]) + 1;
                ComponentName := Copy(RequestData[i], ValueStart, Length(RequestData[i]) - ValueStart + 1);
                ComponentName := TrimJSON(ComponentName);
            end
            // Look for pins array
            else if (Pos('"pins"', RequestData[i]) > 0) then
            begin
                // Parse the array in the next lines
                i := i + 1; // Move to the next line (should be '[')
                
                while (i < RequestData.Count) and (Pos(']', RequestData[i]) = 0) do
                begin
                    // Extract the pin data
                    ParamValue := RequestData[i];
                    ParamValue := StringReplace(ParamValue, '"', '', REPLACEALL);
                    ParamValue := StringReplace(ParamValue, ',', '', REPLACEALL);
                    ParamValue := Trim(ParamValue);

                    if (ParamValue <> '') and (ParamValue <> '[') then
                        PinsList.Add(ParamValue);
                    
                    i := i + 1;
                end;
            end
            // Look for description
            else if (Pos('"description"', RequestData[i]) > 0) then
            begin
                ValueStart := Pos(':', RequestData[i]) + 1;
                ParamValue := Copy(RequestData[i], ValueStart, Length(RequestData[i]) - ValueStart + 1);
                ParamValue := TrimJSON(ParamValue);
                PinsList.Add('Description=' + ParamValue);
            end;
        end;
        
        if ComponentName <> '' then
        begin
            Result := CreateSchematicSymbol(ComponentName, PinsList);
        end
        else
        begin
            ShowMessage('Error: No component name provided');
            Result := '';
        end;

        PinsList.Free;
    end
    else if CommandName = 'get_schematic_data' then
    begin
        // This command doesn't require any parameters
        Result := GetSchematicData;
    end
    else if CommandName = 'get_pcb_layers' then
    begin
        // Make sure we have a PCB document
        if not EnsureDocumentFocused('PCB') then
        begin
            Result := 'ERROR: No PCB document found. Open a PCB document first.';
            Exit;
        end;
        
        // Get PCB layers data
        Result := GetPCBLayers;
    end
    else if CommandName = 'set_pcb_layer_visibility' then
    begin
        // Make sure we have a PCB document
        if not EnsureDocumentFocused('PCB') then
        begin
            Result := 'ERROR: No PCB document found. Open a PCB document first.';
            Exit;
        end;

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
                    // Parse the array in the next lines
                    i := i + 1; // Move to the next line (should be '[')
                    
                    while (i < RequestData.Count) and (Pos(']', RequestData[i]) = 0) do
                    begin
                        // Extract the layer name
                        ParamValue := RequestData[i];
                        ParamValue := StringReplace(ParamValue, '"', '', REPLACEALL);
                        ParamValue := StringReplace(ParamValue, ',', '', REPLACEALL);
                        ParamValue := Trim(ParamValue);
                        
                        if (ParamValue <> '') and (ParamValue <> '[') then
                            SourceList.Add(ParamValue);
                        
                        i := i + 1;
                    end;
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
    end
    else if CommandName = 'get_selected_components_coordinates' then
    begin
        // Get only selected components
        Result := GetSelectedComponentsCoordinates;
    end
    else if CommandName = 'move_components' then
    begin
        // For this command, we need to extract the designators array and the offset values
        DesignatorsList := TStringList.Create;
        XOffset := 0;
        YOffset := 0;
        Rotation := 0;  // Default rotation is 0 (no change)
        
        // Parse parameters from the request
        for i := 0 to RequestData.Count - 1 do
        begin
            // Look for designators array
            if (Pos('"designators"', RequestData[i]) > 0) then
            begin
                // Parse the array in the next lines
                i := i + 1; // Move to the next line (should be '[')
                
                while (i < RequestData.Count) and (Pos(']', RequestData[i]) = 0) do
                begin
                    // Extract the designator value
                    ParamValue := RequestData[i];
                    ParamValue := StringReplace(ParamValue, '"', '', REPLACEALL);
                    ParamValue := StringReplace(ParamValue, ',', '', REPLACEALL);
                    ParamValue := Trim(ParamValue);
                    
                    if (ParamValue <> '') and (ParamValue <> '[') then
                        DesignatorsList.Add(ParamValue);
                    
                    i := i + 1;
                end;
            end
            // Look for x_offset
            else if (Pos('"x_offset"', RequestData[i]) > 0) then
            begin
                ValueStart := Pos(':', RequestData[i]) + 1;
                ParamValue := Copy(RequestData[i], ValueStart, Length(RequestData[i]) - ValueStart + 1);
                ParamValue := TrimJSON(ParamValue);
                XOffset := MilsToCoord(StrToFloat(ParamValue));
            end
            // Look for y_offset
            else if (Pos('"y_offset"', RequestData[i]) > 0) then
            begin
                ValueStart := Pos(':', RequestData[i]) + 1;
                ParamValue := Copy(RequestData[i], ValueStart, Length(RequestData[i]) - ValueStart + 1);
                ParamValue := TrimJSON(ParamValue);
                YOffset := MilsToCoord(StrToFloat(ParamValue));
            end
            // Look for rotation
            else if (Pos('"rotation"', RequestData[i]) > 0) then
            begin
                ValueStart := Pos(':', RequestData[i]) + 1;
                ParamValue := Copy(RequestData[i], ValueStart, Length(RequestData[i]) - ValueStart + 1);
                ParamValue := TrimJSON(ParamValue);
                Rotation := StrToFloat(ParamValue);
            end;
        end;
        
        if DesignatorsList.Count > 0 then
        begin
            Result := MoveComponentsByDesignators(DesignatorsList, XOffset, YOffset, Rotation);
        end
        else
        begin
            ShowMessage('Error: No designators found for move_components');
            Result := '';
        end;

        DesignatorsList.Free;
    end
    else if CommandName = 'layout_duplicator' then
    begin        
        // Get source and destination component data
        Result := GetLayoutDuplicatorComponents(True);
    end
    else if CommandName = 'layout_duplicator_apply' then
    begin
        // For this command, we need to extract the source and destination lists
        SourceList := TStringList.Create;
        DestList := TStringList.Create;
        
        // Parse parameters from the request
        for i := 0 to RequestData.Count - 1 do
        begin
            // Look for source designators array
            if (Pos('"source_designators"', RequestData[i]) > 0) then
            begin
                // Parse the array in the next lines
                i := i + 1; // Move to the next line (should be '[')
                
                while (i < RequestData.Count) and (Pos(']', RequestData[i]) = 0) do
                begin
                    // Extract the designator value
                    ParamValue := RequestData[i];
                    ParamValue := StringReplace(ParamValue, '"', '', REPLACEALL);
                    ParamValue := StringReplace(ParamValue, ',', '', REPLACEALL);
                    ParamValue := Trim(ParamValue);
                    
                    if (ParamValue <> '') and (ParamValue <> '[') then
                        SourceList.Add(ParamValue);
                    
                    i := i + 1;
                end;
            end
            // Look for destination designators array
            else if (Pos('"destination_designators"', RequestData[i]) > 0) then
            begin
                // Parse the array in the next lines
                i := i + 1; // Move to the next line (should be '[')
                
                while (i < RequestData.Count) and (Pos(']', RequestData[i]) = 0) do
                begin
                    // Extract the designator value
                    ParamValue := RequestData[i];
                    ParamValue := StringReplace(ParamValue, '"', '', REPLACEALL);
                    ParamValue := StringReplace(ParamValue, ',', '', REPLACEALL);
                    ParamValue := Trim(ParamValue);
                    
                    if (ParamValue <> '') and (ParamValue <> '[') then
                        DestList.Add(ParamValue);
                    
                    i := i + 1;
                end;
            end
        end;
        
        if (SourceList.Count > 0) and (DestList.Count > 0) then
        begin
            Result := ApplyLayoutDuplicator(SourceList, DestList);
        end
        else
        begin
            ShowMessage('Error: Source or destination lists are empty');
            Result := '{"success": false, "error": "Source or destination lists are empty"}';
        end;

        SourceList.Free;
        DestList.Free;
    end
    else if CommandName = 'get_pcb_rules' then
    begin        
        // Get all PCB rules
        Result := GetPCBRules;
    end
    else
    begin
        ShowMessage('Error: Unknown command: ' + CommandName);
        Result := '';
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

    // Trim only if it's not an array
    if (Pos('[', ParamValue) = 0) then
        ParamValue := TrimJSON(ParamValue);

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

// Main procedure to run the bridge
procedure Run;
var
    CommandType: String;
    Result: String;
    i: Integer;
    Line: String;
    ValueStart: Integer;
begin
    
    // Make sure the directory exists
    if not DirectoryExists('C:\AltiumMCP') then
        CreateDir('C:\AltiumMCP');

    // Check if request file exists
    if not FileExists(REQUEST_FILE) then
    begin
        ShowMessage('Error: No request file found at ' + REQUEST_FILE);
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
                else
                begin
                    // Extract all other parameters
                    ExtractParameter(Line);
                end;
            end;

            // Execute the command if valid
            if CommandType <> '' then
            begin
                Result := ExecuteCommand(CommandType);

                if Result <> '' then
                begin
                    WriteResponse(True, Result, '');
                end
                else
                begin
                    WriteResponse(False, '', 'Command execution failed');
                    ShowMessage('Error: Command execution failed');
                end;
            end
            else
            begin
                WriteResponse(False, '', 'No command specified');
                ShowMessage('Error: No command specified');
            end;
        finally
            RequestData.Free;
            Params.Free;
        end;
    except
        // Simple exception handling without the specific exception type
        WriteResponse(False, '', 'Exception occurred during script execution');
        ShowMessage('Error: Exception occurred during script execution');
    end;
end;


