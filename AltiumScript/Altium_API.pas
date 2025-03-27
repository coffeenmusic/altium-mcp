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

// Utility function to ensure PCB document is open and focused
function EnsurePCBDocumentFocused: Boolean;
var
    I           : Integer;
    Project     : IProject;
    Doc         : IDocument;
    Board       : IPCB_Board;
    PCBFound    : Boolean;
begin
    Result := False;
    PCBFound := False;

    // Retrieve the current project
    Project := GetWorkspace.DM_FocusedProject;
    If Project = Nil Then
    begin
        // No project is open
        Exit;
    end;

    // Check if a PCB document is already focused
    Board := PCBServer.GetCurrentPCBBoard;
    If Board <> Nil Then
    begin
        Result := True;
        Exit;
    end;

    // Try to find and focus a PCB document
    For I := 0 to Project.DM_LogicalDocumentCount - 1 Do
    Begin
        Doc := Project.DM_LogicalDocuments(I);
        If Doc.DM_DocumentKind = 'PCB' Then
        Begin
            PCBFound := True;
            // Open and focus the PCB document
            Doc.DM_OpenAndFocusDocument;

            // Verify that the PCB is now focused
            Board := PCBServer.GetCurrentPCBBoard;
            If Board <> Nil Then
            begin
                Result := True;
                Exit;
            end;
        End;
    End;

    // No PCB document found or couldn't be focused
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

function TrimJSON(InputStr: String): String;
begin
  // Remove quotes and commas
  Result := InputStr;
  Result := RemoveChar(Result, '"');
  Result := RemoveChar(Result, ',');
  // Trim whitespace
  Result := Trim(Result);
end;

// JSON helper functions for Altium scripts

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
function JSONPair(const Name, Value: String; IsString: Boolean): String;
begin
    if IsString then
        Result := '"' + Name + '": "' + JSONEscapeString(Value) + '"'
    else
        Result := '"' + Name + '": ' + Value;
end;

// Function to build a JSON object from a list of pairs
function JSONObject(const Pairs: TStringList): String;
var
    i: Integer;
begin
    Result := '{';
    for i := 0 to Pairs.Count - 1 do
    begin
        Result := Result + Pairs[i];
        if i < Pairs.Count - 1 then
            Result := Result + ', ';
    end;
    Result := Result + '}';
end;

// Function to create a basic JSON object with key-value pairs
function CreateJSONObject(const Names, Values: TStringList; AreStrings: TStringList): String;
var
    i: Integer;
    Pairs: TStringList;
begin
    Pairs := TStringList.Create;
    try
        for i := 0 to Names.Count - 1 do
        begin
            if i < Values.Count then
            begin
                if i < AreStrings.Count then
                    Pairs.Add(JSONPair(Names[i], Values[i], StrToBool(AreStrings[i])))
                else
                    Pairs.Add(JSONPair(Names[i], Values[i], True)); // Default to string
            end;
        end;
        Result := JSONObject(Pairs);
    finally
        Pairs.Free;
    end;
end;

// Helper function to get pins data for a component
procedure GetPinsForJSON(Board: IPCB_Board, Component: IPCB_Component, OutputLines: TStringList);
var
    GrpIter     : IPCB_GroupIterator;
    Pad         : IPCB_Pad;
    xorigin, yorigin : Integer;
    x, y, rotation    : String;
    NetName     : String;
    PinCount, PinsProcessed : Integer;
begin
    // Get board origin
    xorigin := Board.XOrigin;
    yorigin := Board.YOrigin;
    
    // Create pad iterator
    GrpIter := Component.GroupIterator_Create;
    GrpIter.SetState_FilterAll;
    GrpIter.AddFilter_ObjectSet(MkSet(ePadObject));

    // Count pins
    PinCount := 0;
    Pad := GrpIter.FirstPCBObject;
    while Pad <> Nil do
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
    PinsProcessed := 0; // Use a separate counter variable
    Pad := GrpIter.FirstPCBObject;
    while Pad <> Nil do
    begin
        if Pad.InComponent then
        begin
            // Get pad coordinates relative to board origin
            x := FloatToStr(CoordToMils(Pad.x - xorigin));
            y := FloatToStr(CoordToMils(Pad.y - yorigin));
            rotation := FloatToStr(Pad.Rotation);
            
            // Get net name if connected
            if Pad.Net <> Nil then
                NetName := Pad.Net.Name
            else
                NetName := '';

            // Add pin data to JSON
            OutputLines.Add('        {');
            OutputLines.Add('          "name": "' + Pad.Name + '",');             
            OutputLines.Add('          "net": "' + NetName + '",');
            OutputLines.Add('          "x": ' + x + ',');
            OutputLines.Add('          "y": ' + y + ',');
            OutputLines.Add('          "rotation": ' + rotation + ',');
            OutputLines.Add('          "layer": "' + Layer2String(Pad.Layer) + '",');
            OutputLines.Add('          "width": ' + FloatToStr(CoordToMils(Pad.XSizeOnLayer[Pad.Layer])) + ',');
            OutputLines.Add('          "height": ' + FloatToStr(CoordToMils(Pad.YSizeOnLayer[Pad.Layer])) + ',');
            OutputLines.Add('          "shape": "' + ShapeToString(Pad.ShapeOnLayer[Pad.Layer]) + '"');
            
            // Increment counter
            PinsProcessed := PinsProcessed + 1;
            
            // Add comma if not the last pin
            if PinsProcessed < PinCount then
                OutputLines.Add('        },')
            else
                OutputLines.Add('        }');
        end;
        
        Pad := GrpIter.NextPCBObject;
    end;
    
    // Clean up iterator
    Component.GroupIterator_Destroy(GrpIter);
end;

// Function to move components by X and Y offsets
function MoveComponentsByDesignators(DesignatorsList: TStringList; XOffset, YOffset: TCoord): String;
var
    Board       : IPCB_Board;
    Component   : IPCB_Component;
    OutputLines : TStringList;
    Designator  : String;
    i           : Integer;
    MovedCount  : Integer;
    MissingDesignators : TStringList;
begin
    Result := '';
    MovedCount := 0;
    
    // Retrieve the current board
    Board := PCBServer.GetCurrentPCBBoard;
    if Board = nil then
    begin
        Result := 'ERROR: No PCB document is currently active';
        Exit;
    end;
    
    // Create output stringlist for the result
    OutputLines := TStringList.Create;
    MissingDesignators := TStringList.Create;
    
    try
        // Start transaction
        PCBServer.PreProcess;
        
        // Process each designator
        for i := 0 to DesignatorsList.Count - 1 do
        begin
            Designator := Trim(DesignatorsList[i]);
            
            // Use direct function to get component by designator
            Component := Board.GetPcbComponentByRefDes(Designator);
            
            if Component <> Nil then
            begin
                // Begin modify
                PCBServer.SendMessageToRobots(Component.I_ObjectAddress, c_Broadcast, PCBM_BeginModify, c_NoEventData);
                
                // Move the component by the specified offsets
                Component.MoveByXY(XOffset, YOffset);
                
                // End modify
                PCBServer.SendMessageToRobots(Component.I_ObjectAddress, c_Broadcast, PCBM_EndModify, c_NoEventData);
                
                MovedCount := MovedCount + 1;
            end
            else
            begin
                // Keep track of components that weren't found
                MissingDesignators.Add(Designator);
            end;
        end;
        
        // End transaction
        PCBServer.PostProcess;
        
        // Update PCB document
        Client.SendMessage('PCB:Zoom', 'Action=Redraw', 255, Client.CurrentView);
        
        // Create result JSON
        OutputLines.Add('{');
        OutputLines.Add('  "moved_count": ' + IntToStr(MovedCount) + ',');
        
        // Add missing designators if any
        if MissingDesignators.Count > 0 then
        begin
            OutputLines.Add('  "missing_designators": [');
            for i := 0 to MissingDesignators.Count - 1 do
            begin
                if i < MissingDesignators.Count - 1 then
                    OutputLines.Add('    "' + MissingDesignators[i] + '",')
                else
                    OutputLines.Add('    "' + MissingDesignators[i] + '"');
            end;
            OutputLines.Add('  ]');
        end
        else
        begin
            OutputLines.Add('  "missing_designators": []');
        end;
        
        OutputLines.Add('}');
        
        // Create final result
        Result := OutputLines.Text;
    finally
        OutputLines.Free;
        MissingDesignators.Free;
    end;
end;

// Function to get all component data from the PCB
function GetAllComponentData(SelectedOnly: Boolean = False): String;
var
    Board       : IPCB_Board;
    Iterator    : IPCB_BoardIterator;
    Component   : IPCB_Component;
    TempFile    : String;
    Rect        : TCoordRect;
    xorigin, yorigin : Integer;
    OutputLines : TStringList;
    Designator, Name, Footprint, Layer, Description : String;
    x, y, width, height, rotation : String;
    i : Integer;
    ComponentCount : Integer;
begin
    Result := '';

    // Retrieve the current board
    Board := PCBServer.GetCurrentPCBBoard;

    // Get board origin coordinates
    xorigin := Board.XOrigin;
    yorigin := Board.YOrigin;

    // Create output stringlist
    OutputLines := TStringList.Create;
    OutputLines.Add('['); // Start JSON array

    // Create an iterator to find all components
    Iterator := Board.BoardIterator_Create;
    Iterator.AddFilter_ObjectSet(MkSet(eComponentObject));
    Iterator.AddFilter_IPCB_LayerSet(LayerSet.AllLayers);
    Iterator.AddFilter_Method(eProcessAll);

    // Count components (selected only if SelectedOnly is true)
    ComponentCount := 0;
    Component := Iterator.FirstPCBObject;
    while Component <> Nil do
    begin
        if (not SelectedOnly) or (SelectedOnly and Component.Selected) then
            ComponentCount := ComponentCount + 1;
        Component := Iterator.NextPCBObject;
    end;

    // Reset iterator
    Board.BoardIterator_Destroy(Iterator);
    Iterator := Board.BoardIterator_Create;
    Iterator.AddFilter_ObjectSet(MkSet(eComponentObject));
    Iterator.AddFilter_IPCB_LayerSet(LayerSet.AllLayers);
    Iterator.AddFilter_Method(eProcessAll);

    // If no components match the selection criteria, return empty array
    if ComponentCount = 0 then
    begin
        OutputLines.Add(']');
        Result := OutputLines.Text;
        OutputLines.Free;
        Board.BoardIterator_Destroy(Iterator);
        Exit;
    end;

    // Iterate through components and collect data
    i := 0;
    Component := Iterator.FirstPCBObject;
    while Component <> Nil do
    begin
        // Process either all components or only selected ones
        if (not SelectedOnly) or (SelectedOnly and Component.Selected) then
        begin
            // Get basic component properties
            Designator := Component.Name.Text;
            Name := Component.Identifier;
            Description := Component.SourceDescription;
            Footprint := Component.Pattern;
            Layer := Layer2String(Component.Layer);

            // Get position and dimensions
            Rect := Component.BoundingRectangleNoNameComment;
            x := FloatToStr(CoordToMils(Component.x - xorigin));
            y := FloatToStr(CoordToMils(Component.y - yorigin));
            width := FloatToStr(CoordToMils(Rect.Right - Rect.Left));
            height := FloatToStr(CoordToMils(Rect.Bottom - Rect.Top));
            rotation := FloatToStr(Component.Rotation);

            // Build component JSON
            OutputLines.Add('  {');
            OutputLines.Add('    "designator": "' + StringReplace(Designator, '"', '\"', REPLACEALL) + '",');
            OutputLines.Add('    "name": "' + StringReplace(Name, '"', '\"', REPLACEALL) + '",');
            OutputLines.Add('    "description": "' + StringReplace(Description, '"', '\"', REPLACEALL) + '",');
            OutputLines.Add('    "footprint": "' + StringReplace(Footprint, '"', '\"', REPLACEALL) + '",');
            OutputLines.Add('    "layer": "' + StringReplace(Layer, '"', '\"', REPLACEALL) + '",');
            OutputLines.Add('    "x": ' + x + ',');
            OutputLines.Add('    "y": ' + y + ',');
            OutputLines.Add('    "width": ' + width + ',');
            OutputLines.Add('    "height": ' + height + ',');
            OutputLines.Add('    "rotation": ' + rotation);

            // Add comma if not the last component
            i := i + 1;
            if i < ComponentCount then
                OutputLines.Add('  },')
            else
                OutputLines.Add('  }');
        end;

        // Move to next component
        Component := Iterator.NextPCBObject;
    end;

    // Clean up the iterator
    Board.BoardIterator_Destroy(Iterator);

    // Close JSON array
    OutputLines.Add(']');

    // Use a temporary file to build the JSON data
    TempFile := 'C:\AltiumMCP\temp_component_data.json';

    try
        // Save to a temporary file
        OutputLines.SaveToFile(TempFile);

        // Load back the complete JSON data
        OutputLines.Clear;
        OutputLines.LoadFromFile(TempFile);
        Result := OutputLines.Text;

        // Clean up the temporary file
        if FileExists(TempFile) then
            DeleteFile(TempFile);
    finally
        OutputLines.Free;
    end;
end;

// Function to get selected components with coordinates
function GetSelectedComponentsCoordinates: String;
var
    Board       : IPCB_Board;
    Component   : IPCB_Component;
    TempFile    : String;
    Rect        : TCoordRect;
    xorigin, yorigin : Integer;
    OutputLines : TStringList;
    Designator  : String;
    x, y, width, height, rotation : String;
    i           : Integer;
    SelectedCount, ComponentsProcessed : Integer;
begin
    Result := '';

    // Retrieve the current board
    Board := PCBServer.GetCurrentPCBBoard;

    // Get board origin coordinates
    xorigin := Board.XOrigin;
    yorigin := Board.YOrigin;

    // Create output stringlist
    OutputLines := TStringList.Create;
    OutputLines.Add('['); // Start JSON array

    // Count selected components that are actually component objects
    SelectedCount := 0;
    for i := 0 to Board.SelectecObjectCount - 1 do
    begin
        if Board.SelectecObject[i].ObjectId = eComponentObject then
            SelectedCount := SelectedCount + 1;
    end;

    // If no components are selected, return empty array
    if SelectedCount = 0 then
    begin
        OutputLines.Add(']');
        Result := OutputLines.Text;
        OutputLines.Free;
        Exit;
    end;

    // Process each selected component
    ComponentsProcessed := 0; // Use a separate counter variable
    for i := 0 to Board.SelectecObjectCount - 1 do
    begin
        // Only process selected components
        if Board.SelectecObject[i].ObjectId = eComponentObject then
        begin
            // Cast to component type
            Component := Board.SelectecObject[i];
            
            // Get basic component properties
            Designator := Component.Name.Text;

            // Get position and dimensions
            Rect := Component.BoundingRectangleNoNameComment;
            x := FloatToStr(CoordToMils(Component.x - xorigin));
            y := FloatToStr(CoordToMils(Component.y - yorigin));
            width := FloatToStr(CoordToMils(Rect.Right - Rect.Left));
            height := FloatToStr(CoordToMils(Rect.Bottom - Rect.Top));
            rotation := FloatToStr(Component.Rotation);

            // Build component JSON
            OutputLines.Add('  {');
            OutputLines.Add('    "designator": "' + StringReplace(Designator, '"', '\"', REPLACEALL) + '",');
            OutputLines.Add('    "x": ' + x + ',');
            OutputLines.Add('    "y": ' + y + ',');
            OutputLines.Add('    "width": ' + width + ',');
            OutputLines.Add('    "height": ' + height + ',');
            OutputLines.Add('    "rotation": ' + rotation);

            // Increment counter
            ComponentsProcessed := ComponentsProcessed + 1;
            
            // Add comma if not the last selected component
            if ComponentsProcessed < SelectedCount then
                OutputLines.Add('  },')
            else
                OutputLines.Add('  }');
        end;
    end;

    // Close JSON array
    OutputLines.Add(']');

    // Use a temporary file to build the JSON data
    TempFile := 'C:\AltiumMCP\temp_selected_components.json';

    try
        // Save to a temporary file
        OutputLines.SaveToFile(TempFile);

        // Load back the complete JSON data
        OutputLines.Clear;
        OutputLines.LoadFromFile(TempFile);
        Result := OutputLines.Text;

        // Clean up the temporary file
        if FileExists(TempFile) then
            DeleteFile(TempFile);
    finally
        OutputLines.Free;
    end;
end;

// Function to get all PCB rules
function GetPCBRules: String;
Var
    Board         : IPCB_Board;
    Rule          : IPCB_Rule;
    BoardIterator : IPCB_BoardIterator;
    TempFile      : String;
    OutputLines   : TStringList;
    FirstRule     : Boolean;
begin
    Result := '';

    // Retrieve the current board
    Board := PCBServer.GetCurrentPCBBoard;
    If Board = Nil Then Exit;

    // Create output stringlist
    OutputLines := TStringList.Create;
    OutputLines.Add('['); // Start JSON array

    // Retrieve the iterator
    BoardIterator := Board.BoardIterator_Create;
    BoardIterator.AddFilter_ObjectSet(MkSet(eRuleObject));
    BoardIterator.AddFilter_LayerSet(AllLayers);
    BoardIterator.AddFilter_Method(eProcessAll);

    // Search for Rule and for each rule found
    Rule := BoardIterator.FirstPCBObject;

    // Flag to track if we've processed at least one rule
    FirstRule := True;

    While (Rule <> Nil) Do
    Begin
        // Add comma before each rule except the first one
        if not FirstRule then
            OutputLines.Add('  },')
        else
            FirstRule := False;

        // Add rule object with descriptor
        OutputLines.Add('  {');
        OutputLines.Add('    "descriptor": "' + StringReplace(Rule.Descriptor, '"', '\"', REPLACEALL) + '"');

        // Move to next rule
        Rule := BoardIterator.NextPCBObject;
    End;

    // Close the last rule object and the JSON array
    if not FirstRule then
        OutputLines.Add('  }');
    OutputLines.Add(']');

    // Clean up the iterator
    Board.BoardIterator_Destroy(BoardIterator);

    // Use a temporary file to build the JSON data
    TempFile := 'C:\AltiumMCP\temp_rules_data.json';

    try
        // Save to a temporary file
        OutputLines.SaveToFile(TempFile);

        // Load back the complete JSON data
        OutputLines.Clear;
        OutputLines.LoadFromFile(TempFile);
        Result := OutputLines.Text;

        // Clean up the temporary file
        if FileExists(TempFile) then
            DeleteFile(TempFile);
    finally
        OutputLines.Free;
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
    Parameter, NextParameter   : ISch_Parameter;
    TempFile    : String;
    Rect        : TCoordRect;
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
    If Project = Nil Then
    begin
        ShowMessage('Error: No project is currently open');
        Exit;
    end;

    // Create output stringlist
    OutputLines := TStringList.Create;
    OutputLines.Add('['); // Start JSON array

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

            If CurrentSch <> Nil Then
            Begin
                // Get schematic components
                Iterator := CurrentSch.SchIterator_Create;
                Iterator.AddFilter_ObjectSet(MkSet(eSchComponent));

                Component := Iterator.FirstSchObject;
                While Component <> Nil Do
                Begin
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

                    // Start component JSON
                    OutputLines.Add('  {');
                    OutputLines.Add('    "designator": "' + StringReplace(Designator, '"', '\"', REPLACEALL) + '",');
                    OutputLines.Add('    "sheet": "' + StringReplace(Sheet, '\', '\\', REPLACEALL) + '",');
                    OutputLines.Add('    "schematic_x": ' + x + ',');
                    OutputLines.Add('    "schematic_y": ' + y + ',');
                    OutputLines.Add('    "schematic_width": ' + width + ',');
                    OutputLines.Add('    "schematic_height": ' + height + ',');
                    OutputLines.Add('    "schematic_rotation": ' + rotation + ',');

                    // Get parameters
                    OutputLines.Add('    "parameters": {');

                    // Create parameter iterator
                    PIterator := Component.SchIterator_Create;
                    PIterator.AddFilter_ObjectSet(MkSet(eParameter));

                    Parameter := PIterator.FirstSchObject;

                    // Check if there are any parameters
                    if Parameter = nil then
                        // No parameters, just close the object
                        OutputLines.Add('    }')
                    else
                    begin
                        // Process all parameters
                        while Parameter <> nil do
                        begin
                            // Get this parameter's info
                            ParameterName := Parameter.Name;
                            ParameterValue := Parameter.Text;

                            // Escape special characters for JSON
                            ParameterName := StringReplace(ParameterName, '\', '\\', REPLACEALL);
                            ParameterName := StringReplace(ParameterName, '"', '\"', REPLACEALL);

                            ParameterValue := StringReplace(ParameterValue, '\', '\\', REPLACEALL);
                            ParameterValue := StringReplace(ParameterValue, '"', '\"', REPLACEALL);

                            // Get the next parameter to check if this is the last one
                            NextParameter := PIterator.NextSchObject;

                            // Add this parameter
                            if NextParameter <> nil then
                                OutputLines.Add('      "' + ParameterName + '": "' + ParameterValue + '",')
                            else
                                OutputLines.Add('      "' + ParameterName + '": "' + ParameterValue + '"');

                            // Move to next parameter
                            Parameter := NextParameter;
                        end;

                        // Close the parameters object
                        OutputLines.Add('    }');
                    end;

                    Component.SchIterator_Destroy(PIterator);

                    // Add comma since we don't know if it's the last component yet
                    OutputLines.Add('  },');

                    // Move to next component
                    Component := Iterator.NextSchObject;
                    ComponentCount := ComponentCount + 1;
                End;

                CurrentSch.SchIterator_Destroy(Iterator);
            End;
        End;
    End;

    // Fix the last component's closing brace (remove the trailing comma)
    if OutputLines.Count > 1 then
    begin
        // Get the last line
        i := OutputLines.Count - 1;
        if Pos('},', OutputLines[i]) > 0 then
        begin
            // Replace the comma with just a closing brace
            OutputLines[i] := StringReplace(OutputLines[i], '},', '}', REPLACEALL);
        end;
    end;

    // Close JSON array
    OutputLines.Add(']');

    // Use a temporary file to build the JSON data
    TempFile := 'C:\AltiumMCP\temp_schematic_data.json';

    try
        // Save to a temporary file
        OutputLines.SaveToFile(TempFile);

        // Load back the complete JSON data
        OutputLines.Clear;
        OutputLines.LoadFromFile(TempFile);
        Result := OutputLines.Text;

        // Clean up the temporary file
        if FileExists(TempFile) then
            DeleteFile(TempFile);
    finally
        OutputLines.Free;
    end;
end;

// Function to get pin data for specified components
function GetComponentPinsFromList(DesignatorsList: TStringList): String;
var
    Board       : IPCB_Board;
    Component   : IPCB_Component;
    TempFile    : String;
    OutputLines : TStringList;
    Designator  : String;
    i           : Integer;
begin
    Result := '';

    // Retrieve the current board
    Board := PCBServer.GetCurrentPCBBoard;

    // Create output stringlist
    OutputLines := TStringList.Create;
    OutputLines.Add('[');

    // Process each designator
    for i := 0 to DesignatorsList.Count - 1 do
    begin
        Designator := Trim(DesignatorsList[i]);
        
        // Use direct function to get component by designator
        Component := Board.GetPcbComponentByRefDes(Designator);
        
        if Component <> Nil then
        begin
            // Add component to JSON
            OutputLines.Add('  {');
            OutputLines.Add('    "designator": "' + Component.Name.Text + '",');
            OutputLines.Add('    "pins": [');
            
            // Get pin data
            GetPinsForJSON(Board, Component, OutputLines);
            
            // Close pins array
            OutputLines.Add('    ]');
            
            // Close component object
            if i < DesignatorsList.Count - 1 then
                OutputLines.Add('  },')
            else
                OutputLines.Add('  }');
        end
        else
        begin
            // Component not found, add empty entry
            OutputLines.Add('  {');
            OutputLines.Add('    "designator": "' + Designator + '",');
            OutputLines.Add('    "pins": []');
            
            // Close component object
            if i < DesignatorsList.Count - 1 then
                OutputLines.Add('  },')
            else
                OutputLines.Add('  }');
        end;
    end;

    // Close JSON array
    OutputLines.Add(']');

    // Use a temporary file to build the JSON data
    TempFile := 'C:\AltiumMCP\temp_pins_data.json';

    try
        // Save to a temporary file
        OutputLines.SaveToFile(TempFile);

        // Load back the complete JSON data
        OutputLines.Clear;
        OutputLines.LoadFromFile(TempFile);
        Result := OutputLines.Text;

        // Clean up the temporary file
        if FileExists(TempFile) then
            DeleteFile(TempFile);
    finally
        OutputLines.Free;
    end;
end;

// Function to execute a command with parameters
function ExecuteCommand(CommandName: String): String;
var
    ParamValue: String;
    i, XOffset, YOffset, ValueStart: Integer;
    DesignatorsList: TStringList;
    PCBAvailable: Boolean;
begin
    Result := '';

    // For PCB-related commands, ensure PCB is available first
    if (CommandName = 'get_component_pins') or
       (CommandName = 'get_all_component_data') or
       (CommandName = 'get_selected_components_coordinates') then
    begin
        PCBAvailable := EnsurePCBDocumentFocused;
        if not PCBAvailable then
        begin
            // Early exit with error
            Result := 'ERROR: No PCB document found. Tell the user to open a PCB document in their project. Dont try any more tools until the user responds.';
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
    else if CommandName = 'get_schematic_data' then
    begin
        // This command doesn't require any parameters
        Result := GetSchematicData;
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
            end;
        end;
        
        if DesignatorsList.Count > 0 then
        begin
            Result := MoveComponentsByDesignators(DesignatorsList, XOffset, YOffset);
        end
        else
        begin
            ShowMessage('Error: No designators found for move_components');
            Result := '';
        end;

        DesignatorsList.Free;
    end
    else if CommandName = 'get_pcb_rules' then
    begin
        // Make sure a PCB is available
        PCBAvailable := EnsurePCBDocumentFocused;
        if not PCBAvailable then
        begin
            Result := 'ERROR: No PCB document found or could not be focused';
            Exit;
        end;
        
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

    ResponseData := TStringList.Create;
    ResponseData.Add('{');

    if ActualSuccess then
    begin
        // For JSON responses (starting with [ or {), don't wrap in additional quotes
        if (Length(Data) > 0) and ((Data[1] = '[') or (Data[1] = '{')) then
        begin
            ResponseData.Add('  "success": true,');
            ResponseData.Add('  "result": ' + Data);
        end
        else
        begin
            ResponseData.Add('  "success": true,');
            ResponseData.Add('  "result": "' + StringReplace(Data, '"', '\"', REPLACEALL) + '"');
        end;
    end
    else
    begin
        ResponseData.Add('  "success": false,');
        ResponseData.Add('  "error": "' + StringReplace(ActualErrorMsg, '"', '\"', REPLACEALL) + '"');
    end;

    ResponseData.Add('}');
    ResponseData.SaveToFile(RESPONSE_FILE);
    ResponseData.Free;
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


