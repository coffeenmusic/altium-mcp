// altium_bridge.pas
// This script acts as a bridge between the MCP server and Altium
// It reads commands from a request JSON file, executes them, and writes results to a response JSON file

const
    REQUEST_FILE = 'C:\AltiumMCP\request.json';
    RESPONSE_FILE = 'C:\AltiumMCP\response.json';

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

// Function to get the description of a component
function GetComponentDescription(CmpDesignator: String): String;
var
    Board       : IPCB_Board;
    Component   : IPCB_Component;
begin
    Result := '';
    
    // Retrieve the current board
    Board := PCBServer.GetCurrentPCBBoard;
    If Board = Nil Then 
    begin
        ShowMessage('Error: No board is currently open');
        Exit;
    end;
    
    // Get the component directly by its designator
    Component := Board.GetPcbComponentByRefDes(CmpDesignator);
    
    // If component found, get its description
    If Component <> Nil Then
        Result := Component.SourceDescription
    else
        ShowMessage('Error: Component ' + CmpDesignator + ' not found on the current board');
end;

// Function to get all component designators from the current board
function GetAllDesignators: String;
var
    Board       : IPCB_Board;
    Iterator    : IPCB_BoardIterator;
    Component   : IPCB_Component;
    Designators : TStringList;
    I           : Integer;
    TempFile    : String;
    Lines       : TStringList;
begin
    Result := '';
    
    // Retrieve the current board
    Board := PCBServer.GetCurrentPCBBoard;
    If Board = Nil Then 
    begin
        ShowMessage('Error: No board is currently open');
        Exit;
    end;
    
    // Create a string list to store designators
    Designators := TStringList.Create;
    Lines := TStringList.Create;
    
    try
        // Create an iterator to find all components
        Iterator := Board.BoardIterator_Create;
        Iterator.AddFilter_ObjectSet(MkSet(eComponentObject));
        Iterator.AddFilter_IPCB_LayerSet(LayerSet.AllLayers);
        Iterator.AddFilter_Method(eProcessAll);
        
        // Iterate through all components and add their designators to the list
        Component := Iterator.FirstPCBObject;
        while Component <> Nil do
        begin
            Designators.Add(Component.Name.Text);
            Component := Iterator.NextPCBObject;
        end;
        
        // Clean up the iterator
        Board.BoardIterator_Destroy(Iterator);
        
        // Sort the designators for easier use
        Designators.Sort;
        
        // Use a temporary file to build the JSON array to avoid string manipulation issues
        TempFile := 'C:\AltiumMCP\temp_designators.json';
        
        // Start the JSON array
        Lines.Add('[');
        
        // Add the designators
        for I := 0 to Designators.Count - 1 do
        begin
            if I < Designators.Count - 1 then
                Lines.Add('  "' + Designators[I] + '",')
            else
                Lines.Add('  "' + Designators[I] + '"');
        end;
        
        // Close the JSON array
        Lines.Add(']');
        
        // Save to a temporary file
        Lines.SaveToFile(TempFile);
        
        // Load back the complete JSON array
        Lines.Clear;
        Lines.LoadFromFile(TempFile);
        Result := Lines.Text;
        
        // Clean up the temporary file
        if FileExists(TempFile) then
            DeleteFile(TempFile);
    finally
        Designators.Free;
        Lines.Free;
    end;
end;

// Placeholder for future commands
function ExecuteSomeOtherCommand(Param1: String; Param2: String): String;
begin
    // This is just a placeholder for demonstration
    Result := 'Executed command with params: ' + Param1 + ' and ' + Param2;
end;

// Function to execute a command with parameters
function ExecuteCommand(CommandName: String): String;
var
    ParamValue: String;
begin
    Result := '';
    
    // Process different commands based on command name
    if CommandName = 'get_cmp_description' then
    begin
        // Check if required parameter exists
        if Params.IndexOfName('cmp_designator') >= 0 then
        begin
            ParamValue := Params.Values['cmp_designator'];
            Result := GetComponentDescription(ParamValue);
        end
        else
        begin
            ShowMessage('Error: Missing required parameter "cmp_designator"');
            Result := '';
        end;
    end
    else if CommandName = 'get_all_designators' then
    begin
        // This command doesn't require any parameters
        Result := GetAllDesignators;
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