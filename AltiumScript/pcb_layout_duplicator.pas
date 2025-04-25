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
    i, j           : Integer;
    ResultProps    : TStringList;
    MovedCount     : Integer;
    OutputLines    : TStringList;
    PadIterator    : IPCB_GroupIterator;
    Pad            : IPCB_Pad;
    ProcessedPoints: TStringList;
    Tolerance      : TCoord;

    // For faster tracking of processed primitives
    ProcessedObjects : TStringList;

    // For net tracing
    SelectedObjects : TObjectList;
    ConnectedPrim   : IPCB_Primitive;
    TraceStack      : TStringList;
    X, Y, NextX, NextY : TCoord;
    StackSize       : Integer;
    PointInfo       : String;
    Net             : IPCB_Net;
    ObjectAddress   : Integer;

    // For polygon processing
    Polygon        : IPCB_Primitive;
    PadRect        : TCoordRect;
    PolyRect       : TCoordRect;
    Overlapping    : Boolean;
    PolygonCount   : Integer;

    // For net invalidation
    NetsToInvalidate: TStringList;
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

    // Create list to track processed points
    ProcessedPoints := TStringList.Create;
    ProcessedPoints.Duplicates := dupIgnore;  // Ignore duplicate entries

    // Create list to track processed primitives (using object address as string)
    ProcessedObjects := TStringList.Create;
    ProcessedObjects.Duplicates := dupIgnore;  // Ignore duplicate entries

    // Create list for net invalidation (using net names)
    NetsToInvalidate := TStringList.Create;
    NetsToInvalidate.Duplicates := dupIgnore;

    // Create stack for tracking points to process
    TraceStack := TStringList.Create;

    // Set a small tolerance for connection checking (1 mil)
    Tolerance := MilsToCoord(1);

    // Create collection for all selected objects
    SelectedObjects := TObjectList.Create; // Don't own objects
    SelectedObjects.OwnsObjects := False; // Don't destroy objects when list is freed

    try
        // Begin board modification
        PCBServer.PreProcess;

        // Collect all selected objects first - OPTIMIZATION #1
        // This is our biggest optimization - collecting all selected objects once
        for i := 0 to Board.SelectecObjectCount - 1 do
        begin
            ConnectedPrim := Board.SelectecObject[i];

            // Only collect relevant object types
            if (ConnectedPrim.ObjectId = eTrackObject) or
               (ConnectedPrim.ObjectId = eArcObject) or
               (ConnectedPrim.ObjectId = eViaObject) or
               (ConnectedPrim.ObjectId = ePolyObject) or
               (ConnectedPrim.ObjectId = eRegionObject) or
               (ConnectedPrim.ObjectId = eFillObject) then
            begin
                SelectedObjects.Add(ConnectedPrim);
            end;
        end;

        // Process component pairs
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

                    // Register component with the board
                    Board.DispatchMessage(Board.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, CmpDst.I_ObjectAddress);

                    // Clear the processed points list for this component
                    ProcessedPoints.Clear;

                    // Clear processed objects list
                    ProcessedObjects.Clear;

                    // Clear nets to invalidate
                    NetsToInvalidate.Clear;

                    // Process all pads in the destination component
                    PadIterator := CmpDst.GroupIterator_Create;
                    PadIterator.AddFilter_ObjectSet(MkSet(ePadObject));

                    Pad := PadIterator.FirstPCBObject;
                    while Pad <> nil do
                    begin
                        // Skip if pad has no net
                        if (Pad.Net <> nil) then
                        begin
                            Net := Pad.Net;

                            // Track this net for final invalidation
                            if NetsToInvalidate.IndexOf(Net.Name) < 0 then
                                NetsToInvalidate.Add(Net.Name);

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
                                if ProcessedPoints.IndexOf(PointInfo) >= 0 then
                                    Continue;

                                // Mark this point as processed
                                ProcessedPoints.Add(PointInfo);

                                // Extract X,Y from the point info
                                X := StrToInt(Copy(PointInfo, 1, Pos(',', PointInfo) - 1));
                                Y := StrToInt(Copy(PointInfo, Pos(',', PointInfo) + 1, Length(PointInfo)));

                                // Process all selected objects - OPTIMIZATION
                                for j := SelectedObjects.Count - 1 downto 0 do
                                begin
                                    ConnectedPrim := SelectedObjects[j];

                                    // Skip if already processed
                                    ObjectAddress := ConnectedPrim.I_ObjectAddress;
                                    if ProcessedObjects.IndexOf(IntToStr(ObjectAddress)) >= 0 then
                                        Continue;

                                    // Skip if already processed (using object address as identifier)
                                    ObjectAddress := ConnectedPrim.I_ObjectAddress;
                                    if ProcessedObjects.IndexOf(IntToStr(ObjectAddress)) >= 0 then
                                        Continue;

                                    // Check if primitive is at this point
                                    if ConnectedPrim.ObjectId = eTrackObject then
                                    begin
                                        // Check both endpoints
                                        if CheckWithTolerance(ConnectedPrim.x1, ConnectedPrim.y1, X, Y) or
                                           CheckWithTolerance(ConnectedPrim.x2, ConnectedPrim.y2, X, Y) then
                                        begin
                                            // Apply the net
                                            ConnectedPrim.BeginModify;
                                            ConnectedPrim.Net := Net;
                                            ConnectedPrim.EndModify;

                                            // Register primitive with the board
                                            Board.DispatchMessage(Board.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, ConnectedPrim.I_ObjectAddress);

                                            // Mark as processed
                                            ProcessedObjects.Add(IntToStr(ObjectAddress));

                                            // Add other endpoint to stack
                                            if CheckWithTolerance(ConnectedPrim.x1, ConnectedPrim.y1, X, Y) then
                                                TraceStack.Add(IntToStr(ConnectedPrim.x2) + ',' + IntToStr(ConnectedPrim.y2))
                                            else
                                                TraceStack.Add(IntToStr(ConnectedPrim.x1) + ',' + IntToStr(ConnectedPrim.y1));

                                            // Remove from selected objects to speed up future searches
                                            SelectedObjects.Delete(j);
                                        end;
                                    end
                                    else if ConnectedPrim.ObjectId = eArcObject then
                                    begin
                                        // Check both endpoints
                                        if CheckWithTolerance(ConnectedPrim.StartX, ConnectedPrim.StartY, X, Y) or
                                           CheckWithTolerance(ConnectedPrim.EndX, ConnectedPrim.EndY, X, Y) then
                                        begin
                                            // Apply the net
                                            ConnectedPrim.BeginModify;
                                            ConnectedPrim.Net := Net;
                                            ConnectedPrim.EndModify;

                                            // Register primitive with the board
                                            Board.DispatchMessage(Board.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, ConnectedPrim.I_ObjectAddress);

                                            // Mark as processed
                                            ProcessedObjects.Add(IntToStr(ObjectAddress));

                                            // Add other endpoint to stack
                                            if CheckWithTolerance(ConnectedPrim.StartX, ConnectedPrim.StartY, X, Y) then
                                                TraceStack.Add(IntToStr(ConnectedPrim.EndX) + ',' + IntToStr(ConnectedPrim.EndY))
                                            else
                                                TraceStack.Add(IntToStr(ConnectedPrim.StartX) + ',' + IntToStr(ConnectedPrim.StartY));

                                            // Remove from selected objects
                                            SelectedObjects.Delete(j);
                                        end;
                                    end
                                    else if ConnectedPrim.ObjectId = eViaObject then
                                    begin
                                        // Check single point
                                        if CheckWithTolerance(ConnectedPrim.x, ConnectedPrim.y, X, Y) then
                                        begin
                                            ConnectedPrim.BeginModify;
                                            ConnectedPrim.Net := Net;
                                            ConnectedPrim.EndModify;

                                            // Register primitive with the board
                                            Board.DispatchMessage(Board.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, ConnectedPrim.I_ObjectAddress);

                                            // Mark as processed
                                            ProcessedObjects.Add(IntToStr(ObjectAddress));

                                            // Remove from selected objects
                                            SelectedObjects.Delete(j);

                                            // Mark as processed rather than removing
                                            //ProcessedObjects.Add(IntToStr(ObjectAddress));
                                        end;
                                    end;
                                end;
                            end;

                            // Process polygons for this pad - using filtered list
                            PadRect := Pad.BoundingRectangle;

                            // Process each selected polygon from our collected objects
                            for j := SelectedObjects.Count - 1 downto 0 do
                            begin
                                Polygon := SelectedObjects[j];

                                // Skip if already processed
                                ObjectAddress := Polygon.I_ObjectAddress;
                                if ProcessedObjects.IndexOf(IntToStr(ObjectAddress)) >= 0 then
                                    Continue;

                                // Check if it's a polygon type on the same layer
                                if ((Polygon.ObjectId = ePolyObject) or
                                    (Polygon.ObjectId = eRegionObject) or
                                    (Polygon.ObjectId = eFillObject)) and
                                    (Polygon.Layer = Pad.Layer) then
                                begin
                                    // Get polygon's bounding rectangle
                                    PolyRect := Polygon.BoundingRectangle;

                                    // Faster bounding box check
                                    if (PadRect.Left <= PolyRect.Right + Tolerance) and
                                       (PadRect.Right >= PolyRect.Left - Tolerance) and
                                       (PadRect.Bottom <= PolyRect.Top + Tolerance) and
                                       (PadRect.Top >= PolyRect.Bottom - Tolerance) then
                                    begin
                                        Overlapping := False;

                                        // For polygon, use PointInPolygon
                                        if Polygon.ObjectId = ePolyObject then
                                        begin
                                            // Check pad center and corners
                                            X := (PadRect.Left + PadRect.Right) div 2;
                                            Y := (PadRect.Bottom + PadRect.Top) div 2;

                                            if Polygon.PointInPolygon(X, Y) or
                                               Polygon.PointInPolygon(PadRect.Left, PadRect.Bottom) or
                                               Polygon.PointInPolygon(PadRect.Left, PadRect.Top) or
                                               Polygon.PointInPolygon(PadRect.Right, PadRect.Bottom) or
                                               Polygon.PointInPolygon(PadRect.Right, PadRect.Top) then
                                            begin
                                                Overlapping := True;
                                            end;
                                        end
                                        // For regions and fills, use distance checking
                                        else if Board.PrimPrimDistance(Pad, Polygon) <= Tolerance then
                                        begin
                                            Overlapping := True;
                                        end;

                                        if Overlapping then
                                        begin
                                            // Assign pad's net to the polygon
                                            Polygon.BeginModify;
                                            Polygon.Net := Net;
                                            Polygon.EndModify;

                                            // Register with board
                                            Board.DispatchMessage(Board.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, Polygon.I_ObjectAddress);

                                            // Mark as processed
                                            ProcessedObjects.Add(IntToStr(ObjectAddress));

                                            // Remove from the selected objects
                                            SelectedObjects.Delete(j);

                                            Inc(PolygonCount);
                                        end;
                                    end;
                                end;
                            end;
                        end;

                        Pad := PadIterator.NextPCBObject;
                    end;

                    CmpDst.GroupIterator_Destroy(PadIterator);

                    MovedCount := MovedCount + 1;
                end;
            end;
        end;

        // End board modification
        PCBServer.PostProcess;

        // Force redraw of the view - once at the end
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
        NetsToInvalidate.Free;
        ProcessedObjects.Free;
        ProcessedPoints.Free;
        TraceStack.Free;
        ResultProps.Free;
    end;
end;
