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

// Helper function to convert orientation to string value
Function OrientationToStr(ARotate : TRotationBy90) : String;
Begin
    Result := '';

    Case ARotate Of
        eRotate0   : Result := '0';
        eRotate90  : Result := '90';
        eRotate180 : Result := '180';
        eRotate270 : Result := '270';
    End;
End;

// Function to get all component data from the PCB
function GetAllComponentData: String;
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
begin
    Result := '';

    // Retrieve the current board
    Board := PCBServer.GetCurrentPCBBoard;
    If Board = Nil Then
    begin
        ShowMessage('Error: No board is currently open');
        Exit;
    end;

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

    // No need to count components since we removed progress tracking

    // Iterate through all components and collect data
    i := 0;
    Component := Iterator.FirstPCBObject;
    while Component <> Nil do
    begin
        // Optional progress tracking
        // We'll skip the ShowMessage as it could be annoying with many components

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

        // Add comma since we don't know if it's the last component yet
        OutputLines.Add('  },');

        // Move to next component
        Component := Iterator.NextPCBObject;
        i := i + 1;
    end;

    // Clean up the iterator
    Board.BoardIterator_Destroy(Iterator);

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

// Function to execute a command with parameters
function ExecuteCommand(CommandName: String): String;
var
    ParamValue: String;
begin
    Result := '';

    // Process different commands based on command name
    if CommandName = 'get_all_component_data' then
    begin
        // This command doesn't require any parameters
        Result := GetAllComponentData;
    end
    else if CommandName = 'get_schematic_data' then
    begin
        // This command doesn't require any parameters
        Result := GetSchematicData;
    end
    else if CommandName = 'some_other_command' then
    begin
        // Example of handling multiple parameters
        if (Params.IndexOfName('param1') >= 0) and (Params.IndexOfName('param2') >= 0) then
        begin
            Result := ExecuteSomeOtherCommand(Params.Values['param1'], Params.Values['param2']);
        end
        else
        begin
            ShowMessage('Error: Missing required parameters for some_other_command');
            Result := '';
        end;
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

    // Extract and clean the parameter value
    ValueStart := Pos(':', Line) + 1;
    ParamValue := Copy(Line, ValueStart, Length(Line) - ValueStart + 1);
    ParamValue := TrimJSON(ParamValue);

    // Add to parameters list
    if (ParamName <> '') and (ParamName <> 'command') then
        Params.Add(ParamName + '=' + ParamValue);
end;

// Function to write response to file
procedure WriteResponse(Success: Boolean; Data: String; ErrorMsg: String);
begin
    ResponseData := TStringList.Create;
    ResponseData.Add('{');

    if Success then
    begin
        // For JSON array responses (starting with [), don't wrap in additional quotes
        if (Length(Data) > 0) and (Data[1] = '[') then
        begin
            ResponseData.Add('  "success": true,');
            ResponseData.Add('  "result": ' + Data);
        end
        else
        begin
            ResponseData.Add('  "success": true,');
            ResponseData.Add('  "result": "' + Data + '"');
        end;
    end
    else
    begin
        ResponseData.Add('  "success": false,');
        ResponseData.Add('  "error": "' + ErrorMsg + '"');
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

