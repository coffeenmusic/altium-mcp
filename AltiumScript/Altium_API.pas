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
    

// Modify the EnsureDocumentFocused function to handle all document types
// and return more detailed information
function EnsureDocumentFocused(DocumentKind: String): Boolean;
var
    I           : Integer;
    Project     : IProject;
    Doc         : IDocument;
    DocFound    : Boolean;
    CurrentDoc  : IServerDocument;
    LogMessage  : String;
begin
    Result := False;
    DocFound := False;
    LogMessage := 'Attempting to focus ' + DocumentKind + ' document';
    
    // Log the current focused document first
    if DocumentKind = 'PCB' then
    begin
        if PCBServer <> nil then
            LogMessage := LogMessage + '. Current PCB: ' + BoolToStr(PCBServer.GetCurrentPCBBoard <> nil, True);
    end
    else if DocumentKind = 'SCH' then
    begin
        if SchServer <> nil then
            LogMessage := LogMessage + '. Current SCH: ' + BoolToStr(SchServer.GetCurrentSchDocument <> nil, True);
    end
    else if DocumentKind = 'SCHLIB' then
    begin
        if SchServer <> nil then
        begin
            CurrentDoc := SchServer.GetCurrentSchDocument;
            LogMessage := LogMessage + '. Current SCHLIB: ' + BoolToStr((CurrentDoc <> nil) and (CurrentDoc.ObjectID = eSchLib), True);
        end;
    end;
    
    // ShowMessage(LogMessage); // For debugging
    
    // Retrieve the current project
    Project := GetWorkspace.DM_FocusedProject;
    If Project = Nil Then
    begin
        // No project is open
        Exit;
    end;

    // Check if the correct document type is already focused
    if (DocumentKind = 'PCB') and (PCBServer <> Nil) then
    begin
        if PCBServer.GetCurrentPCBBoard <> Nil then
        begin
            Result := True;
            Exit;
        end;
    end
    else if (DocumentKind = 'SCH') and (SchServer <> Nil) then
    begin
        CurrentDoc := SchServer.GetCurrentSchDocument;
        if CurrentDoc <> Nil then
        begin
            Result := True;
            Exit;
        end;
    end
    else if (DocumentKind = 'SCHLIB') and (SchServer <> Nil) then
    begin
        CurrentDoc := SchServer.GetCurrentSchDocument;
        if (CurrentDoc <> Nil) and (CurrentDoc.ObjectId = eSchLib) then
        begin
            Result := True;
            Exit;
        end;
    end;

    // Try to find and focus the required document type
    For I := 0 to Project.DM_LogicalDocumentCount - 1 Do
    Begin
        Doc := Project.DM_LogicalDocuments(I);
        If Doc.DM_DocumentKind = DocumentKind Then
        Begin
            DocFound := True;
            // Try to open and focus the document
            Doc.DM_OpenAndFocusDocument;
            // Give it a moment to focus
            Sleep(500);

            // Verify that the document is now focused
            if DocumentKind = 'PCB' then
            begin
                if PCBServer.GetCurrentPCBBoard <> Nil then
                begin
                    Result := True;
                    // ShowMessage('Successfully focused PCB document');
                    Exit;
                end;
            end
            else if DocumentKind = 'SCH' then
            begin
                CurrentDoc := SchServer.GetCurrentSchDocument;
                if (CurrentDoc <> Nil) then
                begin
                    Result := True;
                    // ShowMessage('Successfully focused SCH document');
                    Exit;
                end;
            end
            else if DocumentKind = 'SCHLIB' then
            begin
                CurrentDoc := SchServer.GetCurrentSchDocument;
                if (CurrentDoc <> Nil) and (CurrentDoc.ObjectID = eSchLib) then
                begin
                    Result := True;
                    // ShowMessage('Successfully focused SCHLIB document');
                    Exit;
                end;
            end;
        End;
    End;

    // No matching document found or couldn't be focused
    if not DocFound then
    begin
        ShowMessage('Error: No ' + DocumentKind + ' document found in the project.');
    end
    else
    begin
        ShowMessage('Error: Found ' + DocumentKind + ' document but could not focus it.');
    end;
    
    Result := False;
end;

// Helper function to remove characters from a string
function RemoveChar(const S: String; C: Char): String;
var
  I: Integer;
begin
  Result := '';
  for I := 1 to Length(S) do
    if S[I] <> C then
      Result := Result + S[I];
end;

// JSON utility functions for Altium MCP Bridge

function TrimJSON(InputStr: String): String;
begin
  // Remove quotes and commas
  Result := InputStr;
  Result := RemoveChar(Result, '"');
  Result := RemoveChar(Result, ',');
  // Trim whitespace
  Result := Trim(Result);
end;

// Helper function to escape JSON strings
function JSONEscapeString(const S: String): String;
begin
    Result := StringReplace(S, '\', '\\', REPLACEALL);
    Result := StringReplace(Result, '"', '\"', REPLACEALL);
    Result := StringReplace(Result, #13#10, '\n', REPLACEALL);
    Result := StringReplace(Result, #10, '\n', REPLACEALL);
    Result := StringReplace(Result, #9, '\t', REPLACEALL);
end;

// Function to create a JSON name-value pair
function JSONPairStr(const Name, Value: String; IsString: Boolean): String;
begin
    if IsString then
        Result := '"' + JSONEscapeString(Name) + '": "' + JSONEscapeString(Value) + '"'
    else
        Result := '"' + JSONEscapeString(Name) + '": ' + Value;
end;

// Function to build a JSON object from a list of pairs
function BuildJSONObject(Pairs: TStringList; IndentLevel: Integer = 0): String;
var
    i: Integer;
    Output: TStringList;
    Indent, ChildIndent: String;
begin
    // Create indent strings based on level
    Indent := StringOfChar(' ', IndentLevel * 2);
    ChildIndent := StringOfChar(' ', (IndentLevel + 1) * 2);
    
    Output := TStringList.Create;
    try
        Output.Add(Indent + '{');
        
        for i := 0 to Pairs.Count - 1 do
        begin
            if i < Pairs.Count - 1 then
                Output.Add(ChildIndent + Pairs[i] + ',')
            else
                Output.Add(ChildIndent + Pairs[i]);
        end;
        
        Output.Add(Indent + '}');
        
        Result := Output.Text;
    finally
        Output.Free;
    end;
end;

// Function to build a JSON array from a list of items
function BuildJSONArray(Items: TStringList; ArrayName: String = ''; IndentLevel: Integer = 0): String;
var
    i: Integer;
    Output: TStringList;
    Indent, ChildIndent: String;
begin
    // Create indent strings based on level
    Indent := StringOfChar(' ', IndentLevel * 2);
    ChildIndent := StringOfChar(' ', (IndentLevel + 1) * 2);
    
    Output := TStringList.Create;
    try
        if ArrayName <> '' then
            Output.Add(Indent + '"' + JSONEscapeString(ArrayName) + '": [')
        else
            Output.Add(Indent + '[');
        
        for i := 0 to Items.Count - 1 do
        begin
            if i < Items.Count - 1 then
                Output.Add(ChildIndent + Items[i] + ',')
            else
                Output.Add(ChildIndent + Items[i]);
        end;
        
        Output.Add(Indent + ']');
        
        Result := Output.Text;
    finally
        Output.Free;
    end;
end;

// Function to write JSON to a file and return as string
function WriteJSONToFile(JSON: TStringList; FileName: String = ''): String;
var
    TempFile: String;
begin
    // Use provided filename or generate temp filename
    if FileName = '' then
        TempFile := 'C:\AltiumMCP\temp_json_output.json'
    else
        TempFile := FileName;
    
    try
        // Save to file
        JSON.SaveToFile(TempFile);
        
        // Load back the complete JSON data
        JSON.Clear;
        JSON.LoadFromFile(TempFile);
        Result := JSON.Text;
        
        // Clean up temporary file if auto-generated
        if (FileName = '') and FileExists(TempFile) then
            DeleteFile(TempFile);
    except
        Result := '{"error": "Failed to write JSON to file"}';
    end;
end;

// Helper function to add a simple property to a JSON object
procedure AddJSONProperty(List: TStringList; Name: String; Value: String; IsString: Boolean = True);
begin
    List.Add(JSONPairStr(Name, Value, IsString));
end;

// Helper to add a numeric property
procedure AddJSONNumber(List: TStringList; Name: String; Value: Double);
begin
    List.Add(JSONPairStr(Name, FloatToStr(Value), False));
end;

// Helper to add an integer property
procedure AddJSONInteger(List: TStringList; Name: String; Value: Integer);
begin
    List.Add(JSONPairStr(Name, IntToStr(Value), False));
end;

// Helper to add a boolean property
procedure AddJSONBoolean(List: TStringList; Name: String; Value: Boolean);
begin
    if Value then
        List.Add(JSONPairStr(Name, 'true', False))
    else
        List.Add(JSONPairStr(Name, 'false', False));
end;

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

// Add a screenshot function that supports both PCB and SCH views
function TakeViewScreenshot(ViewType: String): String;
var
    Board          : IPCB_Board;
    SchDoc         : ISch_Document;
    ResultProps    : TStringList;
    OutputLines    : TStringList;
    ClassName      : String;
    DocType        : String;
    WindowFound    : Boolean;
    
    // For screenshot thread
    ThreadStarted  : Boolean;
    ScreenshotResult : String;
begin
    // Default result
    Result := '{"success": false, "error": "Failed to initialize screenshot capture"}';
    
    // Determine what type of document we need to focus
    if LowerCase(ViewType) = 'pcb' then
    begin
        DocType := 'PCB';
        ClassName := 'View_Graphical';
    end
    else if LowerCase(ViewType) = 'sch' then
    begin
        DocType := 'SCH';
        ClassName := 'SchView';
    end
    else
    begin
        Result := '{"success": false, "error": "Invalid view type: ' + ViewType + '. Must be ''pcb'' or ''sch''"}';
        Exit;
    end;
    
    // Ensure the correct document type is focused
    WindowFound := EnsureDocumentFocused(DocType);
    
    if not WindowFound then
    begin
        Result := '{"success": false, "error": "Could not focus a ' + DocType + ' document. Please open one first."}';
        Exit;
    end;
    
    // Give the UI time to update
    Sleep(500);
    
    // Build the command to call the external screenshot utility
    // This part depends on how your C# server calls Altium for screenshots
    
    // Create result JSON
    ResultProps := TStringList.Create;
    try
        // Add successful result properties
        AddJSONBoolean(ResultProps, 'success', True);
        AddJSONProperty(ResultProps, 'view_type', ViewType);
        AddJSONProperty(ResultProps, 'class_filter', ClassName);
        AddJSONBoolean(ResultProps, 'window_found', WindowFound);
        
        // Add signal to the server that it can now capture the screenshot
        AddJSONBoolean(ResultProps, 'ready_for_capture', True);
        
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

// Function to get all layer information from the PCB
function GetPCBLayers: String;
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
    Board := PCBServer.GetCurrentPCBBoard;
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
                Result := WriteJSONToFile(OutputLines, 'C:\AltiumMCP\temp_layers_data.json');
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
    Board := PCBServer.GetCurrentPCBBoard;
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
    Board := PCBServer.GetCurrentPCBBoard;
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

// Function to get all component data from the PCB
function GetAllComponentData(SelectedOnly: Boolean = False): String;
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
    Board := PCBServer.GetCurrentPCBBoard;
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
                    AddJSONNumber(ComponentProps, 'height', CoordToMils(Rect.Bottom - Rect.Top));
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
            Result := WriteJSONToFile(OutputLines, 'C:\AltiumMCP\temp_component_data.json');
        finally
            OutputLines.Free;
        end;
    finally
        ComponentsArray.Free;
    end;
end;

// Example refactored function using the new JSON utilities
function GetSelectedComponentsCoordinates: String;
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
    Board := PCBServer.GetCurrentPCBBoard;
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
                    AddJSONNumber(ComponentProps, 'x', CoordToMils(Component.x - xorigin));
                    AddJSONNumber(ComponentProps, 'y', CoordToMils(Component.y - yorigin));
                    AddJSONNumber(ComponentProps, 'width', CoordToMils(Rect.Right - Rect.Left));
                    AddJSONNumber(ComponentProps, 'height', CoordToMils(Rect.Bottom - Rect.Top));
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
        Result := WriteJSONToFile(OutputLines, 'C:\AltiumMCP\temp_selected_components.json');
    finally
        ComponentsArray.Free;
        OutputLines.Free;
    end;
end;

// Function to duplicate selected objects of a specific type
function DuplicateSelectedObjects(Board: IPCB_Board; ObjectSet: TSet): TObjectList;
var
    Iterator       : IPCB_BoardIterator;
    OrigObj, NewObj : IPCB_Primitive;
    DuplicatedObjects : TObjectList;
    temp: String;
begin
    // Create object list to store duplicated objects
    DuplicatedObjects := CreateObject(TObjectList);
    DuplicatedObjects.OwnsObjects := False; // Don't destroy objects when list is freed
    
    // Create iterator for the specified object type
    Iterator := Board.BoardIterator_Create;
    Iterator.AddFilter_ObjectSet(ObjectSet);
    Iterator.AddFilter_IPCB_LayerSet(LayerSet.AllLayers);
    Iterator.AddFilter_Method(eProcessAll);

    PCBServer.PreProcess;
    
    OrigObj := Iterator.FirstPCBObject;
    while (OrigObj <> Nil) do
    begin
        if OrigObj.Selected then
        begin
            // Replicate the object
            NewObj := PCBServer.PCBObjectFactory(OrigObj.ObjectId, eNoDimension, eCreate_Default);
            NewObj := OrigObj.Replicate;

            // Add to board
            PCBServer.SendMessageToRobots(NewObj.I_ObjectAddress, c_Broadcast, PCBM_BeginModify, c_NoEventData);
            Board.AddPCBObject(NewObj);
            PCBServer.SendMessageToRobots(NewObj.I_ObjectAddress, c_Broadcast, PCBM_EndModify, c_NoEventData);

            // Send board registration message
            //PCBServer.SendMessageToRobots(Board.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, NewObj.I_ObjectAddress);
            
            // Add to our list of duplicated objects
            DuplicatedObjects.Add(NewObj);
        end;
        
        OrigObj := Iterator.NextPCBObject;
    end;
    
    Board.BoardIterator_Destroy(Iterator);

    PCBServer.PostProcess;

    Board.ViewManager_FullUpdate();  
    
    Result := DuplicatedObjects;
end;

// Function to get source and destination component lists with pin data
function GetLayoutDuplicatorComponents(SelectedOnly: Boolean = True): String;
var
    Board          : IPCB_Board;
    Iterator       : IPCB_BoardIterator;
    SourceCmps     : TStringList;
    ResultProps    : TStringList;
    SourceArray    : TStringList;
    DestArray      : TStringList;
    Component      : IPCB_Component;
    CompProps      : TStringList;
    PinsArray      : TStringList;
    GrpIter        : IPCB_GroupIterator;
    Pad            : IPCB_Pad;
    i, j           : Integer;
    PinCount       : Integer;
    NetName        : String;
    xorigin, yorigin : Integer;
    PinProps       : TStringList;
    OutputLines    : TStringList;
    
    // For duplicated objects
    DuplicatedObjects : TObjectList;
    Obj               : IPCB_Primitive;
begin
    // Retrieve the current board
    Board := PCBServer.GetCurrentPCBBoard;
    if (Board = Nil) then
    begin
        Result := '{"success": false, "message": "No PCB document is currently active"}';
        Exit;
    end;

    // Get board origin coordinates
    xorigin := Board.XOrigin;
    yorigin := Board.YOrigin;

    // Create result properties
    ResultProps := TStringList.Create;
    SourceCmps := TStringList.Create;
    SourceArray := TStringList.Create;
    
    try
        // Get selected components as source
        Iterator := Board.BoardIterator_Create;
        Iterator.AddFilter_ObjectSet(MkSet(eComponentObject));
        Iterator.AddFilter_LayerSet(MkSet(eTopLayer, eBottomLayer));
        Iterator.AddFilter_Method(eProcessAll);

        Component := Iterator.FirstPCBObject;
        while (Component <> Nil) do
        begin
            if (Component.Selected = True) then
                SourceCmps.Add(Component.Name.Text);

            Component := Iterator.NextPCBObject;
        end;
        Board.BoardIterator_Destroy(Iterator);

        // Check if any source components were selected
        if (SourceCmps.Count = 0) then
        begin
            AddJSONBoolean(ResultProps, 'success', False);
            AddJSONProperty(ResultProps, 'message', 'No source components selected. Please select source components first.');
            
            OutputLines := TStringList.Create;
            try
                OutputLines.Text := BuildJSONObject(ResultProps);
                Result := OutputLines.Text;
            finally
                OutputLines.Free;
            end;
            
            Exit;
        end;
        
        // Duplicate all object types in one call
        DuplicatedObjects := DuplicateSelectedObjects(Board, MkSet(eTrackObject, eArcObject, eViaObject, ePolyObject, eRegionObject, eFillObject));
        
        // Deselect all original objects to avoid duplicating them again
        Iterator := Board.BoardIterator_Create;
        Iterator.AddFilter_ObjectSet(MkSet(eTrackObject, eArcObject, eViaObject, ePolyObject, eRegionObject, eFillObject));
        Iterator.AddFilter_IPCB_LayerSet(LayerSet.AllLayers);
        Iterator.AddFilter_Method(eProcessAll);

        PCBServer.PreProcess;
        Obj := Iterator.FirstPCBObject;
        while (Obj <> Nil) do
        begin
            // Only deselect objects that are not in our duplicated list
            if Obj.Selected and (DuplicatedObjects.IndexOf(Obj) < 0) then
                Obj.Selected := False;
            
            Obj := Iterator.NextPCBObject;
        end;
        PCBServer.PostProcess;
        Board.BoardIterator_Destroy(Iterator);

        // Select only the duplicated objects
        for i := 0 to DuplicatedObjects.Count - 1 do
        begin
            Obj := DuplicatedObjects[i];
            if (Obj <> nil) then
                Obj.Selected := True;
        end;

        // Add source components to JSON
        for i := 0 to SourceCmps.Count - 1 do
        begin
            Component := Board.GetPcbComponentByRefDes(SourceCmps[i]);
            if (Component <> nil) then
            begin
                // Create component properties
                CompProps := TStringList.Create;
                PinsArray := TStringList.Create;
                
                try
                    // Add component properties
                    AddJSONProperty(CompProps, 'designator', Component.Name.Text);
                    AddJSONProperty(CompProps, 'description', Component.SourceDescription);
                    AddJSONProperty(CompProps, 'footprint', Component.Pattern);
                    AddJSONNumber(CompProps, 'rotation', Component.Rotation);
                    AddJSONProperty(CompProps, 'layer', Layer2String(Component.Layer));
                    
                    // Add pin data
                    // Create pad iterator
                    GrpIter := Component.GroupIterator_Create;
                    GrpIter.SetState_FilterAll;
                    GrpIter.AddFilter_ObjectSet(MkSet(ePadObject));

                    // Process each pad
                    Pad := GrpIter.FirstPCBObject;
                    while (Pad <> Nil) do
                    begin
                        if Pad.InComponent then
                        begin
                            // Get net name if connected
                            if (Pad.Net <> Nil) then
                                NetName := JSONEscapeString(Pad.Net.Name)
                            else
                                NetName := '';

                            // Create pin properties
                            PinProps := TStringList.Create;
                            try
                                AddJSONProperty(PinProps, 'name', Pad.Name);
                                AddJSONProperty(PinProps, 'net', NetName);
                                AddJSONNumber(PinProps, 'x', CoordToMils(Pad.x - xorigin));
                                AddJSONNumber(PinProps, 'y', CoordToMils(Pad.y - yorigin));
                                AddJSONProperty(PinProps, 'layer', Layer2String(Pad.Layer));
                                
                                // Add to pins array
                                PinsArray.Add(BuildJSONObject(PinProps, 3));
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
                    
                    // Add to source array
                    SourceArray.Add(BuildJSONObject(CompProps, 2));
                finally
                    CompProps.Free;
                    PinsArray.Free;
                end;
            end;
        end;

        // Reset selection for destination components
        Client.SendMessage('PCB:DeSelect', 'Scope=All', 255, Client.CurrentView);
        
        // Have the user select destination components
        Client.SendMessage('PCB:Select', 'Scope=InsideArea | ObjectKind=Component', 255, Client.CurrentView);
        
        // Get the newly selected components (destination)
        SourceCmps.Clear();
        DestArray := TStringList.Create();
        
        try
            // Get newly selected components
            Iterator := Board.BoardIterator_Create;
            Iterator.AddFilter_ObjectSet(MkSet(eComponentObject));
            Iterator.AddFilter_LayerSet(MkSet(eTopLayer, eBottomLayer));
            Iterator.AddFilter_Method(eProcessAll);

            Component := Iterator.FirstPCBObject;
            while (Component <> Nil) do
            begin
                if (Component.Selected = True) then
                    SourceCmps.Add(Component.Name.Text);

                Component := Iterator.NextPCBObject;
            end;
            Board.BoardIterator_Destroy(Iterator);
            
            // Add destination components to JSON
            for i := 0 to SourceCmps.Count - 1 do
            begin
                Component := Board.GetPcbComponentByRefDes(SourceCmps[i]);
                if (Component <> nil) then
                begin
                    // Create component properties
                    CompProps := TStringList.Create;
                    PinsArray := TStringList.Create;
                    
                    try
                        // Add component properties
                        AddJSONProperty(CompProps, 'designator', Component.Name.Text);
                        AddJSONProperty(CompProps, 'description', Component.SourceDescription);
                        AddJSONProperty(CompProps, 'footprint', Component.Pattern);
                        AddJSONNumber(CompProps, 'rotation', Component.Rotation);
                        AddJSONProperty(CompProps, 'layer', Layer2String(Component.Layer));
                        
                        // Add pin data
                        // Create pad iterator
                        GrpIter := Component.GroupIterator_Create;
                        GrpIter.SetState_FilterAll;
                        GrpIter.AddFilter_ObjectSet(MkSet(ePadObject));

                        // Process each pad
                        Pad := GrpIter.FirstPCBObject;
                        while (Pad <> Nil) do
                        begin
                            if Pad.InComponent then
                            begin
                                // Get net name if connected
                                if (Pad.Net <> Nil) then
                                    NetName := JSONEscapeString(Pad.Net.Name)
                                else
                                    NetName := '';

                                // Create pin properties
                                PinProps := TStringList.Create;
                                try
                                    AddJSONProperty(PinProps, 'name', Pad.Name);
                                    AddJSONProperty(PinProps, 'net', NetName);
                                    AddJSONNumber(PinProps, 'x', CoordToMils(Pad.x - xorigin));
                                    AddJSONNumber(PinProps, 'y', CoordToMils(Pad.y - yorigin));
                                    AddJSONProperty(PinProps, 'layer', Layer2String(Pad.Layer));
                                    
                                    // Add to pins array
                                    PinsArray.Add(BuildJSONObject(PinProps, 3));
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
                        
                        // Add to destination array
                        DestArray.Add(BuildJSONObject(CompProps, 2));
                    finally
                        CompProps.Free;
                        PinsArray.Free;
                    end;
                end;
            end;
            
            // Now select all duplicated objects
            for i := 0 to DuplicatedObjects.Count - 1 do
            begin
                Obj := DuplicatedObjects[i];
                if (Obj <> nil) then
                    Obj.Selected := True;
            end;
            
            // Add all arrays to result
            AddJSONBoolean(ResultProps, 'success', True);
            ResultProps.Add(BuildJSONArray(SourceArray, 'source_components'));
            ResultProps.Add(BuildJSONArray(DestArray, 'destination_components'));
            AddJSONProperty(ResultProps, 'message', 'Successfully duplicated objects. Match each source and destination designator using the part descriptions, pin data, and other information. Then call layout_duplicator_apply and pass the source and destination lists in matching order.');
            
            // Build final JSON
            OutputLines := TStringList.Create;
            try
                OutputLines.Text := BuildJSONObject(ResultProps);
                Result := OutputLines.Text;
            finally
                OutputLines.Free;
            end;
        finally
            DestArray.Free;
        end;
    finally
        ResultProps.Free;
        SourceCmps.Free;
        SourceArray.Free;
    end;
end;

// Function to check if two points are within tolerance
function CheckWithTolerance(X1, Y1, X2, Y2 : TCoord) : Boolean;
begin
    if (Abs(X1 - X2) <= Tolerance) and (Abs(Y1 - Y2) <= Tolerance) then
        Result := True
    else
        Result := False;
end;

// Function to apply layout duplication with provided source and destination lists
function ApplyLayoutDuplicator(SourceList: TStringList; DestList: TStringList): String;
var
    Board          : IPCB_Board;
    CmpSrc, CmpDst : IPCB_Component;
    NameSrc, NameDst : TPCB_String;
    i              : Integer;
    ResultProps    : TStringList;
    MovedCount     : Integer;
    OutputLines    : TStringList;
    PadIterator    : IPCB_GroupIterator;
    Pad            : IPCB_Pad;
    ProcessedNets  : TStringList;
    Tolerance      : TCoord;

    // For net tracing
    SIter          : IPCB_SpatialIterator;
    ConnectedPrim  : IPCB_Primitive;
    TraceStack     : TStringList;
    X, Y, NextX, NextY : TCoord;
    StackSize      : Integer;
    PointInfo      : String;
    Net            : IPCB_Net;

    // For polygon processing
    PolyIterator   : IPCB_BoardIterator;
    Polygon        : IPCB_Primitive;
    PadRect        : TCoordRect;
    PolyRect       : TCoordRect;
    Overlapping    : Boolean;
    PolygonCount   : Integer;
begin
    // Retrieve the current board
    Board := PCBServer.GetCurrentPCBBoard;
    if (Board = Nil) then
    begin
        Result := '{"success": false, "error": "No PCB document is currently active"}';
        Exit;
    end;

    // Create result properties
    ResultProps := TStringList.Create;
    MovedCount := 0;
    PolygonCount := 0;

    // Create list to track processed nets and points to avoid infinite loops
    ProcessedNets := TStringList.Create;
    ProcessedNets.Duplicates := dupIgnore;  // Ignore duplicate entries

    // Create stack for tracking points to process
    TraceStack := TStringList.Create;

    // Set a small tolerance for connection checking (1 mil)
    Tolerance := MilsToCoord(1);

    try
        PCBServer.PreProcess;

        for i := 0 to SourceList.Count - 1 do
        begin
            if (i < DestList.Count) then
            begin
                NameSrc := SourceList.Get(i);
                CmpSrc := Board.GetPcbComponentByRefDes(NameSrc);

                NameDst := DestList.Get(i);
                CmpDst := Board.GetPcbComponentByRefDes(NameDst);

                if ((CmpSrc <> nil) and (CmpDst <> nil)) then
                begin
                    // Begin modify component
                    CmpDst.BeginModify;

                    // Move Destination Components to Match Source Components
                    CmpDst.Rotation := CmpSrc.Rotation;
                    CmpDst.Layer_V6 := CmpSrc.Layer_V6;
                    CmpDst.x := CmpSrc.x;
                    CmpDst.y := CmpSrc.y;
                    CmpDst.Selected := True;

                    // End modify component
                    CmpDst.EndModify;

                    // Graphically invalidate the component
                    Board.ViewManager_GraphicallyInvalidatePrimitive(CmpDst);

                    // Register component with the board
                    Board.DispatchMessage(Board.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, CmpDst.I_ObjectAddress);

                    // Clear the processed nets list for this component
                    ProcessedNets.Clear;

                    // Process all pads in the destination component
                    PadIterator := CmpDst.GroupIterator_Create;
                    PadIterator.AddFilter_ObjectSet(MkSet(ePadObject));

                    Pad := PadIterator.FirstPCBObject;
                    while Pad <> nil do
                    begin
                        // Skip if pad has no net or we've already processed this net
                        if (Pad.Net <> nil) and (ProcessedNets.IndexOf(Pad.Net.Name) < 0) then
                        begin
                            Net := Pad.Net;

                            // Add to processed nets
                            ProcessedNets.Add(Net.Name);

                            // Clear the stack
                            TraceStack.Clear;

                            // Add initial pad position to stack
                            TraceStack.Add(IntToStr(Pad.x) + ',' + IntToStr(Pad.y));

                            // Process until stack is empty
                            while TraceStack.Count > 0 do
                            begin
                                // Pop a point from the stack
                                StackSize := TraceStack.Count;
                                PointInfo := TraceStack[StackSize - 1];
                                TraceStack.Delete(StackSize - 1);

                                // Skip if we've already processed this point
                                if ProcessedNets.IndexOf(PointInfo) >= 0 then
                                    Continue;

                                // Mark this point as processed
                                ProcessedNets.Add(PointInfo);

                                // Extract X,Y from the point info
                                X := StrToInt(Copy(PointInfo, 1, Pos(',', PointInfo) - 1));
                                Y := StrToInt(Copy(PointInfo, Pos(',', PointInfo) + 1, Length(PointInfo)));

                                // Find all objects connected to this point for thorough checking
                                SIter := Board.SpatialIterator_Create;
                                SIter.AddFilter_ObjectSet(MkSet(eTrackObject, eArcObject, eViaObject));
                                SIter.AddFilter_Area(X - Tolerance, Y - Tolerance, X + Tolerance, Y + Tolerance);

                                // Perform a first pass to find all connected objects
                                ConnectedPrim := SIter.FirstPCBObject;
                                while ConnectedPrim <> nil do
                                begin
                                    // Process all selected primitives at this junction point
                                    if ConnectedPrim.Selected then
                                    begin
                                        // Check endpoints more precisely
                                        if ConnectedPrim.ObjectId = eTrackObject then
                                        begin
                                            // Precise check with both endpoints
                                            if CheckWithTolerance(ConnectedPrim.x1, ConnectedPrim.y1, X, Y) or
                                               CheckWithTolerance(ConnectedPrim.x2, ConnectedPrim.y2, X, Y) then
                                            begin
                                                // Apply the correct net to this primitive
                                                ConnectedPrim.BeginModify;
                                                ConnectedPrim.Net := Net;
                                                ConnectedPrim.EndModify;

                                                // Graphically invalidate this primitive
                                                Board.ViewManager_GraphicallyInvalidatePrimitive(ConnectedPrim);

                                                // Register primitive with the board
                                                Board.DispatchMessage(Board.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, ConnectedPrim.I_ObjectAddress);

                                                // Force net connectivity recalculation
                                                if ConnectedPrim.InNet then
                                                begin
                                                    ConnectedPrim.Net.ConnectivelyInValidate;
                                                end;

                                                // Add other endpoint to stack if not the current point
                                                if CheckWithTolerance(ConnectedPrim.x1, ConnectedPrim.y1, X, Y) then
                                                begin
                                                    // Add x2,y2 to the stack
                                                    TraceStack.Add(IntToStr(ConnectedPrim.x2) + ',' + IntToStr(ConnectedPrim.y2));
                                                end
                                                else
                                                begin
                                                    // Add x1,y1 to the stack
                                                    TraceStack.Add(IntToStr(ConnectedPrim.x1) + ',' + IntToStr(ConnectedPrim.y1));
                                                end;
                                            end;
                                        end
                                        else if ConnectedPrim.ObjectId = eArcObject then
                                        begin
                                            // Precise check with both endpoints
                                            if CheckWithTolerance(ConnectedPrim.StartX, ConnectedPrim.StartY, X, Y) or
                                               CheckWithTolerance(ConnectedPrim.EndX, ConnectedPrim.EndY, X, Y) then
                                            begin
                                                // Apply the correct net to this primitive
                                                ConnectedPrim.BeginModify;
                                                ConnectedPrim.Net := Net;
                                                ConnectedPrim.EndModify;

                                                // Graphically invalidate this primitive
                                                Board.ViewManager_GraphicallyInvalidatePrimitive(ConnectedPrim);

                                                // Register primitive with the board
                                                Board.DispatchMessage(Board.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, ConnectedPrim.I_ObjectAddress);

                                                // Force net connectivity recalculation
                                                if ConnectedPrim.InNet then
                                                begin
                                                    ConnectedPrim.Net.ConnectivelyInValidate;
                                                end;

                                                // Add other endpoint to stack if not the current point
                                                if CheckWithTolerance(ConnectedPrim.StartX, ConnectedPrim.StartY, X, Y) then
                                                begin
                                                    // Add EndX,EndY to the stack
                                                    TraceStack.Add(IntToStr(ConnectedPrim.EndX) + ',' + IntToStr(ConnectedPrim.EndY));
                                                end
                                                else
                                                begin
                                                    // Add StartX,StartY to the stack
                                                    TraceStack.Add(IntToStr(ConnectedPrim.StartX) + ',' + IntToStr(ConnectedPrim.StartY));
                                                end;
                                            end;
                                        end
                                        else if ConnectedPrim.ObjectId = eViaObject then
                                        begin
                                            // Vias only have a single point, so just check if it's at our current point
                                            if CheckWithTolerance(ConnectedPrim.x, ConnectedPrim.y, X, Y) then
                                            begin
                                                ConnectedPrim.BeginModify;
                                                ConnectedPrim.Net := Net;
                                                ConnectedPrim.EndModify;

                                                // Graphically invalidate this primitive
                                                Board.ViewManager_GraphicallyInvalidatePrimitive(ConnectedPrim);

                                                // Register primitive with the board
                                                Board.DispatchMessage(Board.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, ConnectedPrim.I_ObjectAddress);

                                                // Force net connectivity recalculation
                                                if ConnectedPrim.InNet then
                                                begin
                                                    ConnectedPrim.Net.ConnectivelyInValidate;
                                                end;
                                            end;
                                        end;
                                    end;

                                    ConnectedPrim := SIter.NextPCBObject;
                                end;

                                Board.SpatialIterator_Destroy(SIter);
                            end;

                            // Process polygons, regions, and fills overlapping with this pad
                            // Get pad's bounding rectangle
                            PadRect := Pad.BoundingRectangle;

                            // Create iterator for polygons, regions, and fills
                            PolyIterator := Board.BoardIterator_Create;
                            PolyIterator.AddFilter_ObjectSet(MkSet(ePolyObject, eRegionObject, eFillObject));
                            PolyIterator.AddFilter_LayerSet(AllLayers);
                            PolyIterator.AddFilter_Method(eProcessAll);

                            // Process each selected polygon, region, or fill
                            Polygon := PolyIterator.FirstPCBObject;
                            while Polygon <> nil do
                            begin
                                if Polygon.Selected and ((Polygon.Net = nil) or (Polygon.Net.Name <> Net.Name)) and (Polygon.Layer = Pad.Layer) then
                                begin
                                    // Get polygon's bounding rectangle
                                    PolyRect := Polygon.BoundingRectangle;

                                    // Check if rectangles overlap
                                    Overlapping := False;
                                    if (PadRect.Left <= PolyRect.Right + Tolerance) and
                                       (PadRect.Right >= PolyRect.Left - Tolerance) and
                                       (PadRect.Bottom <= PolyRect.Top + Tolerance) and
                                       (PadRect.Top >= PolyRect.Bottom - Tolerance) then
                                    begin
                                        // For polygon, use PointInPolygon
                                        if Polygon.ObjectId = ePolyObject then
                                        begin
                                            // Check if pad center is inside polygon
                                            X := (PadRect.Left + PadRect.Right) div 2;
                                            Y := (PadRect.Bottom + PadRect.Top) div 2;

                                            if Polygon.PointInPolygon(X, Y) then
                                                Overlapping := True
                                            else
                                            begin
                                                // Check pad corners
                                                if Polygon.PointInPolygon(PadRect.Left, PadRect.Bottom) or
                                                   Polygon.PointInPolygon(PadRect.Left, PadRect.Top) or
                                                   Polygon.PointInPolygon(PadRect.Right, PadRect.Bottom) or
                                                   Polygon.PointInPolygon(PadRect.Right, PadRect.Top) then
                                                    Overlapping := True;
                                            end;
                                        end
                                        // For regions and fills, use distance checking
                                        else if Board.PrimPrimDistance(Pad, Polygon) <= Tolerance then
                                            Overlapping := True;

                                        if Overlapping then
                                        begin
                                            // Assign this pad's net to the polygon
                                            Polygon.BeginModify;
                                            Polygon.Net := Net;
                                            Polygon.EndModify;

                                            // Graphically invalidate
                                            Board.ViewManager_GraphicallyInvalidatePrimitive(Polygon);

                                            // Register with board
                                            Board.DispatchMessage(Board.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, Polygon.I_ObjectAddress);

                                            // Invalidate net connectivity
                                            if Polygon.InNet then
                                            begin
                                                Polygon.Net.ConnectivelyInValidate;
                                            end;

                                            Inc(PolygonCount);
                                        end;
                                    end;
                                end;

                                Polygon := PolyIterator.NextPCBObject;
                            end;

                            Board.BoardIterator_Destroy(PolyIterator);

                            // Invalidate the net as a whole after processing all its primitives
                            Net.ConnectivelyInValidate;
                        end;

                        Pad := PadIterator.NextPCBObject;
                    end;

                    CmpDst.GroupIterator_Destroy(PadIterator);

                    MovedCount := MovedCount + 1;
                end;
            end;
        end;

        PCBServer.PostProcess;

        // Force redraw of the view
        Client.SendMessage('PCB:Zoom', 'Action=Redraw', 255, Client.CurrentView);

        // Update connectivity
        ResetParameters;
        AddStringParameter('Action', 'RebuildConnectivity');
        RunProcess('PCB:UpdateConnectivity');

        // Run full update
        Board.ViewManager_FullUpdate;

        // Create result JSON
        AddJSONBoolean(ResultProps, 'success', True);
        AddJSONInteger(ResultProps, 'moved_count', MovedCount);
        AddJSONInteger(ResultProps, 'polygon_count', PolygonCount);
        AddJSONProperty(ResultProps, 'message', 'Successfully duplicated layout and applied nets for ' + IntToStr(MovedCount) +
                        ' components and ' + IntToStr(PolygonCount) + ' polygons/regions/fills.');

        // Build final JSON
        OutputLines := TStringList.Create;
        try
            OutputLines.Text := BuildJSONObject(ResultProps);
            Result := OutputLines.Text;
        finally
            OutputLines.Free;
        end;
    finally
        TraceStack.Free;
        ProcessedNets.Free;
        ResultProps.Free;
    end;
end;

// Function to get all PCB rules
function GetPCBRules: String;
Var
    Board         : IPCB_Board;
    Rule          : IPCB_Rule;
    BoardIterator : IPCB_BoardIterator;
    RulesArray    : TStringList;
    RuleProps     : TStringList;
    OutputLines   : TStringList;
begin
    // Retrieve the current board
    Board := PCBServer.GetCurrentPCBBoard;
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
            Result := WriteJSONToFile(OutputLines, 'C:\AltiumMCP\temp_rules_data.json');
        finally
            OutputLines.Free;
        end;
    finally
        RulesArray.Free;
    end;
end;

function CreateSchematicSymbol(SymbolName: String; PinsList: TStringList): String;
var
    CurrentLib       : ISch_Lib;
    SchComponent     : ISch_Component;
    SchPin           : ISch_Pin;
    R                : ISch_Rectangle;
    I, PinCount      : Integer;
    PinData          : TStringList;
    PinName, PinNum  : String;
    PinType          : String;
    PinOrient        : String;
    PinX, PinY       : Integer;
    PinElec          : TPinElectrical;
    PinOrientation   : TRotationBy90;
    MinX, MaxX, MinY, MaxY : Integer;
    CenterX, CenterY : Integer;
    Padding          : Integer;
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

    // Parse the pins list for description
    for I := 0 to PinsList.Count - 1 do
    begin
        if (Pos('Description=', PinsList[I]) = 1) then
        begin
            Description := Copy(PinsList[I], 13, Length(PinsList[I]) - 12);
            Break;
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

    // Define the LibReference and component description
    SchComponent.LibReference := SymbolName;
    SchComponent.ComponentDescription := Description;
    SchComponent.Designator.Text := 'U';

    // First pass - collect pin data for sizing the rectangle
    MinX := 9999; MaxX := -9999; MinY := 9999; MaxY := -9999;
    PinCount := 0;

    for I := 0 to PinsList.Count - 1 do
    begin
        // Skip if this is the description line
        if (Pos('Description=', PinsList[I]) = 1) then Continue;

        // Parse the pin data
        PinData := TStringList.Create;
        try
            PinData.Delimiter := '|';
            PinData.DelimitedText := PinsList[I];

            if (PinData.Count >= 6) then
            begin
                // Get pin coordinates and orientation
                PinX := StrToInt(PinData[4]);
                PinY := StrToInt(PinData[5]);
                PinOrient := PinData[3];

                // Track overall min/max for all pins
                MinX := Min(MinX, PinX);
                MaxX := Max(MaxX, PinX);
                MinY := Min(MinY, PinY);
                MaxY := Max(MaxY, PinY);

                PinCount := PinCount + 1;
            end;
        finally
            PinData.Free;
        end;
    end;

    // Set rectangle to cover all pins with padding
    if (PinCount > 0) then
    begin
        // Set rectangle at origin (0,0) with width and height based on pin positions
        MinX := 0;  // Always start at 0,0
        MinY := 0;

        // Right edge should be the maximum X of all pins (typically right-side pins)
        MaxX := MaxX;

        // Top edge should be the maximum Y of all pins plus padding
        MaxY := MaxY + 100;  // Add 100 mils to the highest pin
    end
    else
    begin
        // Default rectangle if no pins
        MinX := 0;
        MinY := 0;
        MaxX := 1000;
        MaxY := 1000;
    end;

    // Create a rectangle for the component body
    R := SchServer.SchObjectFactory(eRectangle, eCreate_Default);
    if (R = Nil) Then
    begin
        Result := 'ERROR: Failed to create rectangle';
        Exit;
    end;

    // Define the rectangle parameters using determined boundaries
    R.LineWidth := eSmall;
    R.Location := Point(MilsToCoord(MinX), MilsToCoord(MinY));
    R.Corner := Point(MilsToCoord(MaxX), MilsToCoord(MaxY));
    R.AreaColor := $00B0FFFF; // Yellow (BGR format)
    R.Color := $00FF0000;     // Blue (BGR format)
    R.IsSolid := True;
    R.OwnerPartId := SchComponent.CurrentPartID;
    R.OwnerPartDisplayMode := SchComponent.DisplayMode;

    // Add the rectangle to the component
    SchComponent.AddSchObject(R);

    // Second pass - add pins to the component
    for I := 0 to PinsList.Count - 1 do
    begin
        // Skip if this is the description line
        if (Pos('Description=', PinsList[I]) = 1) then Continue;

        // Parse the pin data
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

                // Set ownership
                SchPin.OwnerPartId := SchComponent.CurrentPartID;
                SchPin.OwnerPartDisplayMode := SchComponent.DisplayMode;

                // Add the pin to the component
                SchComponent.AddSchObject(SchPin);
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

// Function to get all schematic component data
function GetSchematicData: String;
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
            Result := WriteJSONToFile(OutputLines, 'C:\AltiumMCP\temp_schematic_data.json');
        finally
            OutputLines.Free;
        end;
    finally
        ComponentsArray.Free;
    end;
end;

// Function to get pin data for specified components
function GetComponentPinsFromList(DesignatorsList: TStringList): String;
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
begin
    // Retrieve the current board
    Board := PCBServer.GetCurrentPCBBoard;
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
            Result := WriteJSONToFile(OutputLines, 'C:\AltiumMCP\temp_pins_data.json');
        finally
            OutputLines.Free;
        end;
    finally
        ComponentsArray.Free;
    end;
end;

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
       (CommandName = 'get_pcb_rules') then
    begin
        if not EnsureDocumentFocused('PCB') then
        begin
            // Early exit with error
            Result := 'ERROR: No PCB document found. Tell the user to open a PCB document in their project. Dont try any more tools until the user responds.';
            Exit;
        end;
    end
    else if (CommandName = 'create_schematic_symbol') then
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
    else if CommandName = 'get_all_component_data' then
    begin
        // This command doesn't require any parameters
        Result := GetAllComponentData(False);
    end
    // In your ExecuteCommand function, add a case for the screenshot command:
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


