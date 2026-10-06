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
    Result := StringReplace(Result, #13, '\n', REPLACEALL);
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
    if Not(AnsiEndsStr('.json', LowerCase(FileName))) then
    begin
        TempFile := Path + 'temp_json_output.json';
    end
    else
    begin
        TempFile := FileName;
    end;
    
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

// Locale-safe StrToFloat: normalizes '.' to the system decimal separator before parsing.
// Mirrors AddJSONNumber which does the reverse on output (fwolter PR #3).
function SafeStrToFloat(S: String): Double;
begin
    S := StringReplace(S, '.', DecimalSeparator, REPLACEALL);
    Result := StrToFloat(S);
end;

// Helper function to add a simple property to a JSON object
procedure AddJSONProperty(List: TStringList; Name: String; Value: String; IsString: Boolean = True);
begin
    List.Add(JSONPairStr(Name, Value, IsString));
end;

// Helper to add a numeric property
procedure AddJSONNumber(List: TStringList; Name: String; Value: Double);
begin
    List.Add(JSONPairStr(Name, StringReplace(FloatToStr(Value), ',', '.', REPLACEALL), False));
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

// Decode JSON string escapes: \\ \" \/ \n \r \t and \uXXXX
function JSONUnescape(S: String): String;
var
    i : Integer;
    C : String;
begin
    Result := '';
    i := 1;
    while (i <= Length(S)) do
    begin
        C := Copy(S, i, 1);
        if (C = '\') and (i < Length(S)) then
        begin
            i := i + 1;
            C := Copy(S, i, 1);
            if (C = 'n') then
                Result := Result + #10
            else if (C = 'r') then
                Result := Result + #13
            else if (C = 't') then
                Result := Result + #9
            else if (C = 'u') and (i + 4 <= Length(S)) then
            begin
                Result := Result + Chr(StrToInt('$' + Copy(S, i + 1, 4)));
                i := i + 4;
            end
            else
                Result := Result + C;
        end
        else
            Result := Result + C;
        i := i + 1;
    end;
end;

// The string value of a '"key": "value",' request line: the trailing comma
// and surrounding quotes removed and JSON escapes decoded. Unlike TrimJSON
// it keeps commas and quotes inside the value, so file paths arrive intact.
function JSONLineValue(Line: String): String;
var
    S : String;
begin
    S := Trim(Copy(Line, Pos(':', Line) + 1, Length(Line)));
    if (Copy(S, Length(S), 1) = ',') then
        S := Trim(Copy(S, 1, Length(S) - 1));
    if (Length(S) >= 2) and (Copy(S, 1, 1) = '"') and (Copy(S, Length(S), 1) = '"') then
        S := Copy(S, 2, Length(S) - 2);
    Result := JSONUnescape(S);
end;
