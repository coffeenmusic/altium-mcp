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