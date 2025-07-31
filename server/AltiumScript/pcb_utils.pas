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
            Result := WriteJSONToFile(OutputLines, ROOT_DIR+'\temp_pins_data.json');
        finally
            OutputLines.Free;
        end;
    finally
        ComponentsArray.Free;
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
