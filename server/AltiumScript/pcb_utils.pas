// Function to get all unique net names from the current PCB document
function GetAllNets(ROOT_DIR: String): String;
var
    Board       : IPCB_Board;
    Net         : IPCB_Net;
    Iterator    : IPCB_BoardIterator;
    NetsArray   : TStringList; 
    OutputLines : TStringList;
begin
    // Initialize empty array result in case no board is found
    Result := '[]';
    
    // Retrieve the current board
    Board := PCBServer.GetCurrentPCBBoard;
    if Board = nil then Exit;

    // Create array for storing unique nets
    NetsArray := TStringList.Create;
    // Set Duplicates property to prevent duplicate net names
    NetsArray.Duplicates := dupIgnore;
    NetsArray.Sorted := True;
    
    try
        // Create the iterator that will look for Net objects only
        Iterator := Board.BoardIterator_Create;
        Iterator.AddFilter_ObjectSet(MkSet(eNetObject));
        Iterator.AddFilter_LayerSet(AllLayers);
        Iterator.AddFilter_Method(eProcessAll);

        // Search for Net objects and get their Net Name values
        Net := Iterator.FirstPCBObject;
        while (Net <> nil) do
        begin
            // Add each net name to the list, duplicates will be ignored
            NetsArray.Add('"' + JSONEscapeString(Net.Name) + '"');
            Net := Iterator.NextPCBObject;
        end;
        Board.BoardIterator_Destroy(Iterator);
        
        // Build the final JSON array
        OutputLines := TStringList.Create;
        try
            OutputLines.Text := BuildJSONArray(NetsArray);
            Result := WriteJSONToFile(OutputLines, ROOT_DIR+'\temp_nets_data.json');
        finally
            OutputLines.Free;
        end;
    finally
        NetsArray.Free;
    end;
end;

// Function to create a net class and add nets to it
function CreateNetClass(ClassName: String; NetNames: TStringList): String;
var
    Board       : IPCB_Board;
    ClassExists : Boolean;
    NetClass    : IPCB_ObjectClass;
    ClassIterator : IPCB_BoardIterator;
    i           : Integer;
    ResultProps : TStringList;
    AddedCount  : Integer;
    OutputLines : TStringList;
begin
    // Initialize result
    ResultProps := TStringList.Create;
    AddedCount := 0;
    ClassExists := False;
    
    try
        // Retrieve the current board
        Board := PCBServer.GetCurrentPCBBoard;
        if (Board = nil) then
        begin
            AddJSONBoolean(ResultProps, 'success', False);
            AddJSONProperty(ResultProps, 'error', 'No PCB document is currently active');
            
            OutputLines := TStringList.Create;
            try
                OutputLines.Text := BuildJSONObject(ResultProps);
                Result := OutputLines.Text;
            finally
                OutputLines.Free;
            end;
            Exit;
        end;
        
        // Search for existing class with the same name
        ClassIterator := Board.BoardIterator_Create;
        ClassIterator.SetState_FilterAll;
        ClassIterator.AddFilter_ObjectSet(MkSet(eClassObject));
        
        NetClass := ClassIterator.FirstPCBObject;
        while (NetClass <> nil) do
        begin
            if (NetClass.MemberKind = eClassMemberKind_Net) and (NetClass.Name = ClassName) then
            begin
                ClassExists := True;
                Break;
            end;
            NetClass := ClassIterator.NextPCBObject;
        end;
        
        // If class doesn't exist, create it
        if not ClassExists then
        begin
            PCBServer.PreProcess;
            NetClass := PCBServer.PCBClassFactoryByClassMember(eClassMemberKind_Net);
            NetClass.SuperClass := False;
            NetClass.Name := ClassName;
            Board.AddPCBObject(NetClass);
            PCBServer.PostProcess;
        end;
        
        // Add nets to the class
        PCBServer.PreProcess;
        for i := 0 to NetNames.Count - 1 do
        begin
            // Add each net to the class
            if NetClass.AddMemberByName(NetNames[i]) then
                AddedCount := AddedCount + 1;
        end;
        PCBServer.PostProcess;
        
        // Clean up iterator
        Board.BoardIterator_Destroy(ClassIterator);
        
        // Build result JSON
        AddJSONBoolean(ResultProps, 'success', True);
        AddJSONProperty(ResultProps, 'class_name', ClassName);
        AddJSONBoolean(ResultProps, 'class_created', not ClassExists);
        AddJSONInteger(ResultProps, 'nets_added', AddedCount);
        
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

// Function to get detailed layer stackup information
function GetPCBLayerStackup(ROOT_DIR): String;
var
    Board           : IPCB_Board;
    LayerIterator   : IPCB_LayerObjectIterator;
    LayerObject     : IPCB_LayerObject;
    StackupArray    : TStringList;
    LayerProps      : TStringList;
    OutputLines     : TStringList;
    TotalThickness  : Double;
    LayerCount      : Integer;
begin
    Result := '';

    // Retrieve the current board
    Board := PCBServer.GetCurrentPCBBoard;
    if (Board = nil) then
    begin
        Result := '{"error": "No PCB document is currently active"}';
        Exit;
    end;

    // Create arrays for stackup data
    StackupArray := TStringList.Create;
    TotalThickness := 0;
    LayerCount := 0;
    
    try
        // Get the electrical layer iterator
        LayerIterator := Board.ElectricalLayerIterator;
        
        // Process each electrical layer
        while LayerIterator.Next do
        begin
            LayerObject := LayerIterator.LayerObject;
            
            // Create layer properties
            LayerProps := TStringList.Create;
            try
                // Basic layer information
                AddJSONProperty(LayerProps, 'layer_name', LayerObject.Name);
                AddJSONProperty(LayerProps, 'layer_id', Layer2String(LayerObject.LayerID));
                AddJSONProperty(LayerProps, 'material_type', 'Copper');
                AddJSONNumber(LayerProps, 'copper_thickness_mils', LayerObject.CopperThickness / 10000);
                AddJSONNumber(LayerProps, 'copper_thickness_um', LayerObject.CopperThickness / 254);
                
                // Add copper thickness to total
                TotalThickness := TotalThickness + (LayerObject.CopperThickness / 10000);
                
                // Dielectric information (if present)
                if LayerObject.Dielectric.DielectricType <> eNoDielectric then
                begin
                    case LayerObject.Dielectric.DielectricType of
                        eCore: AddJSONProperty(LayerProps, 'dielectric_type', 'Core');
                        ePrePreg: AddJSONProperty(LayerProps, 'dielectric_type', 'PrePreg');
                        eSurfaceMaterial: AddJSONProperty(LayerProps, 'dielectric_type', 'Surface Material');
                    else
                        AddJSONProperty(LayerProps, 'dielectric_type', 'Unknown');
                    end;
                    
                    AddJSONProperty(LayerProps, 'dielectric_material', LayerObject.Dielectric.DielectricMaterial);
                    AddJSONNumber(LayerProps, 'dielectric_height_mils', LayerObject.Dielectric.DielectricHeight / 10000);
                    AddJSONNumber(LayerProps, 'dielectric_height_um', LayerObject.Dielectric.DielectricHeight / 254);
                    AddJSONNumber(LayerProps, 'dielectric_constant', LayerObject.Dielectric.DielectricConstant);
                    
                    // Add dielectric thickness to total
                    TotalThickness := TotalThickness + (LayerObject.Dielectric.DielectricHeight / 10000);
                end
                else
                begin
                    AddJSONProperty(LayerProps, 'dielectric_type', 'No Dielectric');
                    AddJSONProperty(LayerProps, 'dielectric_material', '');
                    AddJSONNumber(LayerProps, 'dielectric_height_mils', 0);
                    AddJSONNumber(LayerProps, 'dielectric_height_um', 0);
                    AddJSONNumber(LayerProps, 'dielectric_constant', 0);
                end;
                
                // Add layer order
                AddJSONInteger(LayerProps, 'layer_order', LayerCount + 1);
                
                // Add to stackup array
                StackupArray.Add(BuildJSONObject(LayerProps, 1));
                LayerCount := LayerCount + 1;
            finally
                LayerProps.Free;
            end;
        end;
        
        // Create final stackup object with summary
        LayerProps := TStringList.Create;
        try
            AddJSONInteger(LayerProps, 'total_layers', LayerCount);
            AddJSONNumber(LayerProps, 'total_thickness_mils', TotalThickness);
            AddJSONNumber(LayerProps, 'total_thickness_mm', TotalThickness * 0.0254);
            AddJSONProperty(LayerProps, 'board_name', ExtractFileName(Board.FileName));
            
            // Add the layers array
            LayerProps.Add(BuildJSONArray(StackupArray, 'layers'));
            
            // Build the final JSON
            OutputLines := TStringList.Create;
            try
                OutputLines.Text := BuildJSONObject(LayerProps);
                Result := WriteJSONToFile(OutputLines, ROOT_DIR+'\temp_stackup_data.json');
            finally
                OutputLines.Free;
            end;
        finally
            LayerProps.Free;
        end;
    finally
        StackupArray.Free;
    end;
end;

// Function to get all layer information from the PCB
function GetPCBLayers(ROOT_DIR: String): String;
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
                Result := WriteJSONToFile(OutputLines, ROOT_DIR+'\temp_layers_data.json');
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

// Function to get all PCB rules
function GetPCBRules(ROOT_DIR: String): String;
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
                AddJSONProperty(RuleProps, 'rule_kind', Rule.GetState_ShortDescriptorString);
                AddJSONProperty(RuleProps, 'filter1', Rule.Scope1Expression);
                AddJSONProperty(RuleProps, 'filter2', Rule.Scope2Expression);

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
            Result := WriteJSONToFile(OutputLines, ROOT_DIR+'\temp_rules_data.json');
        finally
            OutputLines.Free;
        end;
    finally
        RulesArray.Free;
    end;
end;

// Function to get all component data from the PCB
function GetAllComponentData(ROOT_DIR: String, SelectedOnly: Boolean = False): String;
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
                    AddJSONNumber(ComponentProps, 'height', CoordToMils(Rect.Top - Rect.Bottom));
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
            Result := WriteJSONToFile(OutputLines, ROOT_DIR+'\temp_component_data.json');
        finally
            OutputLines.Free;
        end;
    finally
        ComponentsArray.Free;
    end;
end;

// Example refactored function using the new JSON utilities
function GetSelectedComponentsCoordinates(ROOT_DIR: String): String;
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
                    AddJSONProperty(ComponentProps, 'layer', Layer2String(Component.Layer));
                    AddJSONProperty(ComponentProps, 'footprint', Component.Pattern);
                    AddJSONNumber(ComponentProps, 'x', CoordToMils(Component.x - xorigin));
                    AddJSONNumber(ComponentProps, 'y', CoordToMils(Component.y - yorigin));
                    AddJSONNumber(ComponentProps, 'width', CoordToMils(Rect.Right - Rect.Left));
                    AddJSONNumber(ComponentProps, 'height', CoordToMils(Rect.Top - Rect.Bottom));
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
        Result := WriteJSONToFile(OutputLines, ROOT_DIR+'\temp_selected_components.json');
    finally
        ComponentsArray.Free;
        OutputLines.Free;
    end;
end;

// Function to get pin data for specified components
function GetComponentPinsFromList(ROOT_DIR: String; DesignatorsList: TStringList): String;
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
    CompX, CompY    : Double;
    CompRad         : Double;
    AbsDX, AbsDY    : Double;
    RelDX, RelDY    : Double;
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

                    // Component placement info so pin data is self-contained
                    CompX := CoordToMils(Component.x - xorigin);
                    CompY := CoordToMils(Component.y - yorigin);
                    CompRad := Component.Rotation * Pi / 180;
                    AddJSONNumber(CompProps, 'x', CompX);
                    AddJSONNumber(CompProps, 'y', CompY);
                    AddJSONNumber(CompProps, 'rotation', Component.Rotation);
                    AddJSONProperty(CompProps, 'layer', Layer2String(Component.Layer));

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

                                // Pad offset from the component origin in the
                                // footprint's rotation-0 frame: un-rotate the
                                // current offset, and un-mirror X for parts on
                                // the bottom side. Predicted pad position after
                                // placement = origin + (mirror-x if bottom,
                                // then rotate CCW by rotation) applied to dx/dy.
                                AbsDX := CoordToMils(Pad.x - xorigin) - CompX;
                                AbsDY := CoordToMils(Pad.y - yorigin) - CompY;
                                RelDX := AbsDX * Cos(CompRad) + AbsDY * Sin(CompRad);
                                RelDY := -AbsDX * Sin(CompRad) + AbsDY * Cos(CompRad);
                                if (Component.Layer = eBottomLayer) then
                                begin
                                    RelDX := -(AbsDX * Cos(CompRad) - AbsDY * Sin(CompRad));
                                    RelDY := AbsDX * Sin(CompRad) + AbsDY * Cos(CompRad);
                                end;
                                // Round away trig noise (0.0001 mil resolution)
                                RelDX := Round(RelDX * 10000) / 10000;
                                RelDY := Round(RelDY * 10000) / 10000;
                                AddJSONNumber(PinProps, 'dx', RelDX);
                                AddJSONNumber(PinProps, 'dy', RelDY);

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
            Result := WriteJSONToFile(OutputLines, ROOT_DIR+'\temp_pins_data.json');
        finally
            OutputLines.Free;
        end;
    finally
        ComponentsArray.Free;
    end;
end;

// Set absolute position of a single component
function SetComponentPosition(Designator: String; NewX, NewY: Float; Rotation: Float): String;
var
    Board: IPCB_Board;
    Component: IPCB_Component;
    ResultProps: TStringList;
    xorigin, yorigin: TCoord;
begin
    Board := PCBServer.GetCurrentPCBBoard;
    if (Board = nil) then
    begin
        Result := '{"success": false, "error": "No PCB document is currently active"}';
        Exit;
    end;
    
    Component := Board.GetPcbComponentByRefDes(Designator);
    if (Component = nil) then
    begin
        Result := '{"success": false, "error": "Component not found: ' + Designator + '"}';
        Exit;
    end;
    
    // Get board origin
    xorigin := Board.XOrigin;
    yorigin := Board.YOrigin;
    
    ResultProps := TStringList.Create;
    try
        PCBServer.PreProcess;
        PCBServer.SendMessageToRobots(Component.I_ObjectAddress, c_Broadcast, PCBM_BeginModify, c_NoEventData);
        
        // Set absolute position using MoveToXY
        // Add origin back since input coordinates are relative to origin
        Component.MoveToXY(MilsToCoord(NewX) + xorigin, MilsToCoord(NewY) + yorigin);
        
        // Set rotation if specified (use -1 to keep current)
        if (Rotation >= 0) then
            Component.Rotation := Rotation;
        
        PCBServer.SendMessageToRobots(Component.I_ObjectAddress, c_Broadcast, PCBM_EndModify, c_NoEventData);
        PCBServer.PostProcess;
        
        Client.SendMessage('PCB:Zoom', 'Action=Redraw', 255, Client.CurrentView);
        
        AddJSONProperty(ResultProps, 'designator', Designator);
        AddJSONProperty(ResultProps, 'new_x', FloatToStr(NewX), False);
        AddJSONProperty(ResultProps, 'new_y', FloatToStr(NewY), False);
        AddJSONProperty(ResultProps, 'rotation', FloatToStr(Component.Rotation), False);
        
        Result := '{"success": true, "result": ' + BuildJSONObject(ResultProps) + '}';
    finally
        ResultProps.Free;
    end;
end;

// Create a PCB footprint (SMD pads + silkscreen + courtyard) in the active PcbLib
function CreatePCBFootprint(FootprintName: String; Description: String; PadsList: TStringList; CourtyardXMM: Double; CourtyardYMM: Double): String;
var
    PcbLib      : IPCB_Library;
    LibComp     : IPCB_Component;
    Pad         : IPCB_Pad;
    Track       : IPCB_Track;
    ResultProps : TStringList;
    OutputLines : TStringList;
    i, j        : Integer;
    PadData     : String;
    PadNum      : String;
    XMM, YMM    : Double;
    WMM, HMM    : Double;
    ShapeStr    : String;
    PadShape    : TShape;
    PadCount    : Integer;
    MaxX, MaxY  : Double;
    MinX, MinY  : Double;
    CrtX1, CrtY1, CrtX2, CrtY2 : Double;
    TrackWidth  : TCoord;
    FieldStart  : Integer;
    Fields      : TStringList;
    SilkLayer   : TLayer;
begin
    PcbLib := PCBServer.GetCurrentPCBLibrary;
    if PcbLib = nil then
    begin
        Result := '{"success": false, "error": "No PCB library document is currently active. Open a .PcbLib file first."}';
        Exit;
    end;

    ResultProps := TStringList.Create;
    Fields := TStringList.Create;
    PadCount := 0;
    MaxX := -1e9; MaxY := -1e9;
    MinX :=  1e9; MinY :=  1e9;
    SilkLayer := String2Layer('Top Overlay');

    try
        LibComp := PCBServer.CreatePCBLibComp;
        LibComp.Name := FootprintName;

        PcbLib.RegisterComponent(LibComp);

        for i := 0 to PadsList.Count - 1 do
        begin
            PadData := Trim(PadsList[i]);
            if (PadData = '') then continue;

            // Parse pipe-delimited fields manually
            Fields.Clear;
            FieldStart := 1;
            for j := 1 to Length(PadData) + 1 do
            begin
                if (j > Length(PadData)) or (PadData[j] = '|') then
                begin
                    Fields.Add(Trim(Copy(PadData, FieldStart, j - FieldStart)));
                    FieldStart := j + 1;
                end;
            end;

            if Fields.Count < 5 then continue;

            PadNum := Fields[0];
            XMM := SafeStrToFloat(Fields[1]);
            YMM := SafeStrToFloat(Fields[2]);
            WMM := SafeStrToFloat(Fields[3]);
            HMM := SafeStrToFloat(Fields[4]);

            if Fields.Count >= 6 then
                ShapeStr := Fields[5]
            else
                ShapeStr := 'Rect';

            if ShapeStr = 'Round' then
                PadShape := eRounded
            else if ShapeStr = 'Oval' then
                PadShape := eRoundedRectangle
            else
                PadShape := eRectangular;

            Pad := PCBServer.PCBObjectFactory(ePadObject, eNoDimension, eCreate_Default);
            Pad.Name := PadNum;
            Pad.Mode := ePadMode_Simple;
            Pad.HoleSize := 0;
            Pad.x := MMsToCoord(XMM);
            Pad.y := MMsToCoord(YMM);
            Pad.Layer := eTopLayer;
            Pad.TopXSize := MMsToCoord(WMM);
            Pad.TopYSize := MMsToCoord(HMM);
            Pad.TopShape := PadShape;

            LibComp.AddPCBObject(Pad);
            PCBServer.SendMessageToRobots(Pad.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, c_NoEventData);

            if (XMM - WMM/2) < MinX then MinX := XMM - WMM/2;
            if (YMM - HMM/2) < MinY then MinY := YMM - HMM/2;
            if (XMM + WMM/2) > MaxX then MaxX := XMM + WMM/2;
            if (YMM + HMM/2) > MaxY then MaxY := YMM + HMM/2;

            PadCount := PadCount + 1;
        end;

        // Compute courtyard extents
        if (CourtyardXMM > 0) and (CourtyardYMM > 0) then
        begin
            CrtX1 := -CourtyardXMM; CrtX2 :=  CourtyardXMM;
            CrtY1 := -CourtyardYMM; CrtY2 :=  CourtyardYMM;
        end
        else
        begin
            CrtX1 := MinX - 0.25; CrtX2 := MaxX + 0.25;
            CrtY1 := MinY - 0.25; CrtY2 := MaxY + 0.25;
        end;

        TrackWidth := MMsToCoord(0.1);

        // Courtyard on Mechanical 15
        Track := PCBServer.PCBObjectFactory(eTrackObject, eNoDimension, eCreate_Default);
        Track.Layer := ILayer.MechanicalLayer(15);
        Track.x1 := MMsToCoord(CrtX1); Track.y1 := MMsToCoord(CrtY1);
        Track.x2 := MMsToCoord(CrtX2); Track.y2 := MMsToCoord(CrtY1);
        Track.Width := TrackWidth;
        LibComp.AddPCBObject(Track);
        PCBServer.SendMessageToRobots(Track.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, c_NoEventData);

        Track := PCBServer.PCBObjectFactory(eTrackObject, eNoDimension, eCreate_Default);
        Track.Layer := ILayer.MechanicalLayer(15);
        Track.x1 := MMsToCoord(CrtX1); Track.y1 := MMsToCoord(CrtY2);
        Track.x2 := MMsToCoord(CrtX2); Track.y2 := MMsToCoord(CrtY2);
        Track.Width := TrackWidth;
        LibComp.AddPCBObject(Track);
        PCBServer.SendMessageToRobots(Track.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, c_NoEventData);

        Track := PCBServer.PCBObjectFactory(eTrackObject, eNoDimension, eCreate_Default);
        Track.Layer := ILayer.MechanicalLayer(15);
        Track.x1 := MMsToCoord(CrtX1); Track.y1 := MMsToCoord(CrtY1);
        Track.x2 := MMsToCoord(CrtX1); Track.y2 := MMsToCoord(CrtY2);
        Track.Width := TrackWidth;
        LibComp.AddPCBObject(Track);
        PCBServer.SendMessageToRobots(Track.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, c_NoEventData);

        Track := PCBServer.PCBObjectFactory(eTrackObject, eNoDimension, eCreate_Default);
        Track.Layer := ILayer.MechanicalLayer(15);
        Track.x1 := MMsToCoord(CrtX2); Track.y1 := MMsToCoord(CrtY1);
        Track.x2 := MMsToCoord(CrtX2); Track.y2 := MMsToCoord(CrtY2);
        Track.Width := TrackWidth;
        LibComp.AddPCBObject(Track);
        PCBServer.SendMessageToRobots(Track.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, c_NoEventData);

        // Silkscreen on TopOverlay (inset 0.1mm from courtyard)
        Track := PCBServer.PCBObjectFactory(eTrackObject, eNoDimension, eCreate_Default);
        Track.Layer := SilkLayer;
        Track.x1 := MMsToCoord(CrtX1+0.1); Track.y1 := MMsToCoord(CrtY1+0.1);
        Track.x2 := MMsToCoord(CrtX2-0.1); Track.y2 := MMsToCoord(CrtY1+0.1);
        Track.Width := TrackWidth;
        LibComp.AddPCBObject(Track);
        PCBServer.SendMessageToRobots(Track.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, c_NoEventData);

        Track := PCBServer.PCBObjectFactory(eTrackObject, eNoDimension, eCreate_Default);
        Track.Layer := SilkLayer;
        Track.x1 := MMsToCoord(CrtX1+0.1); Track.y1 := MMsToCoord(CrtY2-0.1);
        Track.x2 := MMsToCoord(CrtX2-0.1); Track.y2 := MMsToCoord(CrtY2-0.1);
        Track.Width := TrackWidth;
        LibComp.AddPCBObject(Track);
        PCBServer.SendMessageToRobots(Track.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, c_NoEventData);

        // Left silk — split to mark pin 1 (gap at top-left corner for pin 1 indicator)
        Track := PCBServer.PCBObjectFactory(eTrackObject, eNoDimension, eCreate_Default);
        Track.Layer := SilkLayer;
        Track.x1 := MMsToCoord(CrtX1+0.1); Track.y1 := MMsToCoord(CrtY1+0.1);
        Track.x2 := MMsToCoord(CrtX1+0.1); Track.y2 := MMsToCoord(CrtY2-0.6);
        Track.Width := TrackWidth;
        LibComp.AddPCBObject(Track);
        PCBServer.SendMessageToRobots(Track.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, c_NoEventData);

        // Right silk
        Track := PCBServer.PCBObjectFactory(eTrackObject, eNoDimension, eCreate_Default);
        Track.Layer := SilkLayer;
        Track.x1 := MMsToCoord(CrtX2-0.1); Track.y1 := MMsToCoord(CrtY1+0.1);
        Track.x2 := MMsToCoord(CrtX2-0.1); Track.y2 := MMsToCoord(CrtY2-0.1);
        Track.Width := TrackWidth;
        LibComp.AddPCBObject(Track);
        PCBServer.SendMessageToRobots(Track.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, c_NoEventData);

        // Register with library board, navigate, and refresh
        PCBServer.SendMessageToRobots(PcbLib.Board.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, LibComp.I_ObjectAddress);
        PcbLib.CurrentComponent := LibComp;
        PcbLib.Board.ViewManager_FullUpdate;

        AddJSONBoolean(ResultProps, 'success', True);
        AddJSONProperty(ResultProps, 'footprint_name', FootprintName);
        AddJSONInteger(ResultProps, 'pad_count', PadCount);
        AddJSONNumber(ResultProps, 'courtyard_width_mm', CrtX2 - CrtX1);
        AddJSONNumber(ResultProps, 'courtyard_height_mm', CrtY2 - CrtY1);

        OutputLines := TStringList.Create;
        try
            OutputLines.Text := BuildJSONObject(ResultProps);
            Result := OutputLines.Text;
        finally
            OutputLines.Free;
        end;
    finally
        ResultProps.Free;
        Fields.Free;
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

// Minimum primitive-to-primitive distance between two components in mils,
// ignoring text primitives (designator/comment strings float over neighbors
// and would poison the measurement). 0 = touching or overlapping.
function ComponentMinDistance(Board: IPCB_Board; CompA, CompB: IPCB_Component): Double;
var
    ItA, ItB : IPCB_GroupIterator;
    PA, PB   : IPCB_Primitive;
    D, MinD  : Integer;
begin
    MinD := 2147483647;

    ItA := CompA.GroupIterator_Create;
    ItA.SetState_FilterAll;
    PA := ItA.FirstPCBObject;
    while (PA <> nil) do
    begin
        if (PA.ObjectId <> eTextObject) then
        begin
            ItB := CompB.GroupIterator_Create;
            ItB.SetState_FilterAll;
            PB := ItB.FirstPCBObject;
            while (PB <> nil) do
            begin
                if (PB.ObjectId <> eTextObject) then
                begin
                    D := Board.PrimPrimDistance(PA, PB);
                    if (D < MinD) then MinD := D;
                end;

                // Early exit once touching - it cannot get closer
                if (MinD = 0) then
                    PB := nil
                else
                    PB := ItB.NextPCBObject;
            end;
            CompB.GroupIterator_Destroy(ItB);
        end;

        if (MinD = 0) then
            PA := nil
        else
            PA := ItA.NextPCBObject;
    end;
    CompA.GroupIterator_Destroy(ItA);

    Result := CoordToMils(MinD);
end;

// Check component placement for overlaps and clearance violations.
// Targets are the given designators (or the current selection when the list
// is empty); each target is checked against every other component on the same
// side of the board. Bounding boxes act as a fast prefilter; close pairs are
// measured precisely with Board.PrimPrimDistance (minimum distance between
// any two primitives of the components, 0 = touching/overlapping).
function CheckPlacement(DesignatorsList: TStringList; ClearanceMils: Double): String;
var
    Board           : IPCB_Board;
    Iterator        : IPCB_BoardIterator;
    Target, Other   : IPCB_Component;
    Targets         : TStringList;
    Processed       : TStringList;
    MissingArray    : TStringList;
    ViolationsArray : TStringList;
    VProps          : TStringList;
    ResultProps     : TStringList;
    RectA, RectB    : TCoordRect;
    Designator      : String;
    i               : Integer;
    OverlapX, OverlapY : Double;
    Separation      : Double;
    DistMils        : Double;
    PairsChecked    : Integer;
    IsOverlap       : Boolean;
begin
    Board := PCBServer.GetCurrentPCBBoard;
    if (Board = nil) then
    begin
        Result := 'ERROR: No PCB document is currently active';
        Exit;
    end;

    if (ClearanceMils <= 0) then
        ClearanceMils := 6;

    Targets := TStringList.Create;
    Processed := TStringList.Create;
    MissingArray := TStringList.Create;
    ViolationsArray := TStringList.Create;
    ResultProps := TStringList.Create;
    PairsChecked := 0;

    try
        // Build the target list: explicit designators, or current selection
        if (DesignatorsList.Count > 0) then
        begin
            for i := 0 to DesignatorsList.Count - 1 do
            begin
                Designator := Trim(DesignatorsList[i]);
                if (Board.GetPcbComponentByRefDes(Designator) <> nil) then
                    Targets.Add(Designator)
                else
                    MissingArray.Add('"' + JSONEscapeString(Designator) + '"');
            end;
        end
        else
        begin
            for i := 0 to Board.SelectecObjectCount - 1 do
                if (Board.SelectecObject[i].ObjectId = eComponentObject) then
                    Targets.Add(Board.SelectecObject[i].Name.Text);
        end;

        if (Targets.Count = 0) then
        begin
            Result := 'ERROR: No components to check (no designators given and no components selected)';
            Exit;
        end;

        // Check each target against every other component on the same side
        for i := 0 to Targets.Count - 1 do
        begin
            Target := Board.GetPcbComponentByRefDes(Targets[i]);
            RectA := Target.BoundingRectangleNoNameComment;

            Iterator := Board.BoardIterator_Create;
            Iterator.AddFilter_ObjectSet(MkSet(eComponentObject));
            Iterator.AddFilter_IPCB_LayerSet(LayerSet.AllLayers);
            Iterator.AddFilter_Method(eProcessAll);

            Other := Iterator.FirstPCBObject;
            while (Other <> nil) do
            begin
                if (Other.Name.Text <> Target.Name.Text) and
                   (Other.Layer = Target.Layer) and
                   (Processed.IndexOf(Other.Name.Text) < 0) then
                begin
                    RectB := Other.BoundingRectangleNoNameComment;

                    // Bounding-box overlap/separation in mils (negative = gap)
                    OverlapX := CoordToMils(Min(RectA.Right, RectB.Right) - Max(RectA.Left, RectB.Left));
                    OverlapY := CoordToMils(Min(RectA.Top, RectB.Top) - Max(RectA.Bottom, RectB.Bottom));

                    if (OverlapX > 0) and (OverlapY > 0) then
                        Separation := 0
                    else
                        Separation := Max(-OverlapX, -OverlapY);

                    // Only measure precisely when the prefilter says "close"
                    if (Separation < ClearanceMils + 25) then
                    begin
                        PairsChecked := PairsChecked + 1;
                        DistMils := ComponentMinDistance(Board, Target, Other);
                        IsOverlap := (OverlapX > 0) and (OverlapY > 0);

                        if (IsOverlap) or (DistMils < ClearanceMils) then
                        begin
                            VProps := TStringList.Create;
                            try
                                AddJSONProperty(VProps, 'a', Target.Name.Text);
                                AddJSONProperty(VProps, 'b', Other.Name.Text);
                                AddJSONProperty(VProps, 'layer', Layer2String(Target.Layer));
                                if IsOverlap then
                                    AddJSONProperty(VProps, 'type', 'bounding_box_overlap')
                                else
                                    AddJSONProperty(VProps, 'type', 'clearance');
                                AddJSONNumber(VProps, 'distance_mils', Round(DistMils * 100) / 100);
                                if IsOverlap then
                                begin
                                    AddJSONNumber(VProps, 'overlap_x_mils', Round(OverlapX * 100) / 100);
                                    AddJSONNumber(VProps, 'overlap_y_mils', Round(OverlapY * 100) / 100);
                                end;
                                AddJSONNumber(VProps, 'b_x', CoordToMils(Other.x - Board.XOrigin));
                                AddJSONNumber(VProps, 'b_y', CoordToMils(Other.y - Board.YOrigin));
                                ViolationsArray.Add(BuildJSONObject(VProps, 2));
                            finally
                                VProps.Free;
                            end;
                        end;
                    end;
                end;

                Other := Iterator.NextPCBObject;
            end;

            Board.BoardIterator_Destroy(Iterator);
            Processed.Add(Target.Name.Text);
        end;

        AddJSONInteger(ResultProps, 'checked_count', Targets.Count);
        AddJSONNumber(ResultProps, 'clearance_mils', ClearanceMils);
        AddJSONInteger(ResultProps, 'close_pairs_measured', PairsChecked);
        AddJSONInteger(ResultProps, 'violation_count', ViolationsArray.Count);

        if (MissingArray.Count > 0) then
            ResultProps.Add(BuildJSONArray(MissingArray, 'missing_designators'))
        else
            ResultProps.Add('"missing_designators": []');

        if (ViolationsArray.Count > 0) then
            ResultProps.Add(BuildJSONArray(ViolationsArray, 'violations', 1))
        else
            ResultProps.Add('"violations": []');

        Result := BuildJSONObject(ResultProps);
    finally
        Targets.Free;
        Processed.Free;
        MissingArray.Free;
        ViolationsArray.Free;
        ResultProps.Free;
    end;
end;

// Get all pads on every net touched by the given components (or the current
// selection when the list is empty). Returns a flat board-wide pad list -
// including pads of components outside the target set - so the caller can
// group by net and compute airline lengths. Coordinates in mils relative to
// the board origin.
function GetNetConnections(ROOT_DIR: String; DesignatorsList: TStringList): String;
var
    Board       : IPCB_Board;
    Component   : IPCB_Component;
    Iterator    : IPCB_BoardIterator;
    GrpIter     : IPCB_GroupIterator;
    Pad         : IPCB_Pad;
    NetNames    : TStringList;
    TargetNames : TStringList;
    PadsArray   : TStringList;
    PadProps    : TStringList;
    ResultProps : TStringList;
    NamesArray  : TStringList;
    OutputLines : TStringList;
    Designator  : String;
    xorigin, yorigin : Integer;
    i           : Integer;
begin
    Board := PCBServer.GetCurrentPCBBoard;
    if (Board = nil) then
    begin
        Result := 'ERROR: No PCB document is currently active';
        Exit;
    end;

    xorigin := Board.XOrigin;
    yorigin := Board.YOrigin;

    NetNames := TStringList.Create;
    TargetNames := TStringList.Create;
    PadsArray := TStringList.Create;
    ResultProps := TStringList.Create;
    NamesArray := TStringList.Create;

    try
        // Build the target component list: explicit designators, or selection
        if (DesignatorsList.Count > 0) then
        begin
            for i := 0 to DesignatorsList.Count - 1 do
            begin
                Designator := Trim(DesignatorsList[i]);
                if (Board.GetPcbComponentByRefDes(Designator) <> nil) then
                    TargetNames.Add(Designator);
            end;
        end
        else
        begin
            for i := 0 to Board.SelectecObjectCount - 1 do
                if (Board.SelectecObject[i].ObjectId = eComponentObject) then
                    TargetNames.Add(Board.SelectecObject[i].Name.Text);
        end;

        if (TargetNames.Count = 0) then
        begin
            Result := 'ERROR: No components found (no designators given and no components selected)';
            Exit;
        end;

        // Collect the set of nets touched by the target components
        for i := 0 to TargetNames.Count - 1 do
        begin
            Component := Board.GetPcbComponentByRefDes(TargetNames[i]);
            GrpIter := Component.GroupIterator_Create;
            GrpIter.SetState_FilterAll;
            GrpIter.AddFilter_ObjectSet(MkSet(ePadObject));

            Pad := GrpIter.FirstPCBObject;
            while (Pad <> nil) do
            begin
                if (Pad.Net <> nil) then
                    if (NetNames.IndexOf(Pad.Net.Name) < 0) then
                        NetNames.Add(Pad.Net.Name);
                Pad := GrpIter.NextPCBObject;
            end;

            Component.GroupIterator_Destroy(GrpIter);
        end;

        // One board-wide pass: emit every pad on any of those nets
        Iterator := Board.BoardIterator_Create;
        Iterator.AddFilter_ObjectSet(MkSet(ePadObject));
        Iterator.AddFilter_IPCB_LayerSet(LayerSet.AllLayers);
        Iterator.AddFilter_Method(eProcessAll);

        Pad := Iterator.FirstPCBObject;
        while (Pad <> nil) do
        begin
            if (Pad.Net <> nil) then
            begin
                if (NetNames.IndexOf(Pad.Net.Name) >= 0) then
                begin
                    PadProps := TStringList.Create;
                    try
                        AddJSONProperty(PadProps, 'net', Pad.Net.Name);
                        if (Pad.Component <> nil) then
                            AddJSONProperty(PadProps, 'designator', Pad.Component.Name.Text)
                        else
                            AddJSONProperty(PadProps, 'designator', '');
                        AddJSONProperty(PadProps, 'pin', Pad.Name);
                        AddJSONNumber(PadProps, 'x', CoordToMils(Pad.x - xorigin));
                        AddJSONNumber(PadProps, 'y', CoordToMils(Pad.y - yorigin));
                        PadsArray.Add(BuildJSONObject(PadProps, 2));
                    finally
                        PadProps.Free;
                    end;
                end;
            end;
            Pad := Iterator.NextPCBObject;
        end;

        Board.BoardIterator_Destroy(Iterator);

        // Build the result - include the resolved targets so the caller
        // knows which components were analyzed when using the selection
        for i := 0 to TargetNames.Count - 1 do
            NamesArray.Add('"' + JSONEscapeString(TargetNames[i]) + '"');
        ResultProps.Add(BuildJSONArray(NamesArray, 'targets'));
        NamesArray.Clear;

        for i := 0 to NetNames.Count - 1 do
            NamesArray.Add('"' + JSONEscapeString(NetNames[i]) + '"');

        if (NamesArray.Count > 0) then
            ResultProps.Add(BuildJSONArray(NamesArray, 'net_names'))
        else
            ResultProps.Add('"net_names": []');

        if (PadsArray.Count > 0) then
            ResultProps.Add(BuildJSONArray(PadsArray, 'pads', 1))
        else
            ResultProps.Add('"pads": []');

        // Potentially large (plane nets) - go through the temp-file path
        OutputLines := TStringList.Create;
        try
            OutputLines.Text := BuildJSONObject(ResultProps);
            Result := WriteJSONToFile(OutputLines, ROOT_DIR+'\temp_net_connections.json');
        finally
            OutputLines.Free;
        end;
    finally
        NetNames.Free;
        TargetNames.Free;
        PadsArray.Free;
        ResultProps.Free;
        NamesArray.Free;
    end;
end;

// Place multiple components at absolute positions in a single transaction.
// Each entry in PlacementsList is 'Designator|X|Y|Rotation|Layer' where X/Y
// are mils relative to the board origin, Rotation is degrees CCW (-1 = keep
// current) and Layer is 'top', 'bottom' or '' (keep current side).
function PlaceComponentsFromList(PlacementsList: TStringList): String;
var
    Board            : IPCB_Board;
    Component        : IPCB_Component;
    ResultProps      : TStringList;
    MissingArray     : TStringList;
    PlacedArray      : TStringList;
    CompProps        : TStringList;
    Entry            : String;
    Designator       : String;
    FieldValue       : String;
    LayerStr         : String;
    NewX, NewY       : Double;
    Rotation         : Double;
    xorigin, yorigin : TCoord;
    i                : Integer;
    PlacedCount      : Integer;
begin
    Board := PCBServer.GetCurrentPCBBoard;
    if (Board = nil) then
    begin
        Result := 'ERROR: No PCB document is currently active';
        Exit;
    end;

    xorigin := Board.XOrigin;
    yorigin := Board.YOrigin;

    ResultProps := TStringList.Create;
    MissingArray := TStringList.Create;
    PlacedArray := TStringList.Create;
    PlacedCount := 0;

    try
        // Single transaction for the whole batch (one undo step)
        PCBServer.PreProcess;

        for i := 0 to PlacementsList.Count - 1 do
        begin
            Entry := Trim(PlacementsList[i]);
            if (Entry <> '') then
            begin
                Designator := Trim(GetFieldFromPipeString(Entry, 0));
                NewX := SafeStrToFloat(GetFieldFromPipeString(Entry, 1));
                NewY := SafeStrToFloat(GetFieldFromPipeString(Entry, 2));

                // Rotation is optional: missing/empty field means keep current
                FieldValue := Trim(GetFieldFromPipeString(Entry, 3));
                if (FieldValue <> '') then
                    Rotation := SafeStrToFloat(FieldValue)
                else
                    Rotation := -1;

                LayerStr := LowerCase(Trim(GetFieldFromPipeString(Entry, 4)));

                Component := Board.GetPcbComponentByRefDes(Designator);

                if (Component <> nil) then
                begin
                    PCBServer.SendMessageToRobots(Component.I_ObjectAddress, c_Broadcast, PCBM_BeginModify, c_NoEventData);

                    // Change layer first: flipping mirrors the footprint, so
                    // position and rotation are applied afterwards to keep the
                    // requested values authoritative
                    if (LayerStr = 'top') and (Component.Layer = eBottomLayer) then
                        Component.Layer := eTopLayer
                    else if (LayerStr = 'bottom') and (Component.Layer = eTopLayer) then
                        Component.Layer := eBottomLayer;

                    Component.MoveToXY(MilsToCoord(NewX) + xorigin, MilsToCoord(NewY) + yorigin);

                    if (Rotation >= 0) then
                        Component.Rotation := Rotation;

                    PCBServer.SendMessageToRobots(Component.I_ObjectAddress, c_Broadcast, PCBM_EndModify, c_NoEventData);

                    // Report the final state as Altium sees it
                    CompProps := TStringList.Create;
                    try
                        AddJSONProperty(CompProps, 'designator', Component.Name.Text);
                        AddJSONNumber(CompProps, 'x', CoordToMils(Component.x - xorigin));
                        AddJSONNumber(CompProps, 'y', CoordToMils(Component.y - yorigin));
                        AddJSONNumber(CompProps, 'rotation', Component.Rotation);
                        AddJSONProperty(CompProps, 'layer', Layer2String(Component.Layer));
                        PlacedArray.Add(BuildJSONObject(CompProps, 2));
                    finally
                        CompProps.Free;
                    end;

                    PlacedCount := PlacedCount + 1;
                end
                else
                begin
                    MissingArray.Add('"' + JSONEscapeString(Designator) + '"');
                end;
            end;
        end;

        PCBServer.PostProcess;

        // Update PCB document
        Client.SendMessage('PCB:Zoom', 'Action=Redraw', 255, Client.CurrentView);

        AddJSONInteger(ResultProps, 'placed_count', PlacedCount);

        if (MissingArray.Count > 0) then
            ResultProps.Add(BuildJSONArray(MissingArray, 'missing_designators'))
        else
            ResultProps.Add('"missing_designators": []');

        if (PlacedArray.Count > 0) then
            ResultProps.Add(BuildJSONArray(PlacedArray, 'components', 1))
        else
            ResultProps.Add('"components": []');

        Result := BuildJSONObject(ResultProps);
    finally
        ResultProps.Free;
        MissingArray.Free;
        PlacedArray.Free;
    end;
end;
