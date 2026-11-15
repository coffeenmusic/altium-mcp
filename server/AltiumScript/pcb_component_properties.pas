// Placed component kinds only. Never edits PcbLib, output settings or documents on disk.

function PCPPBom(Kind: Integer): Integer;
begin
    Result := -1;
    if (Kind=eComponentKind_Standard) or (Kind=eComponentKind_Mechanical) or (Kind=eComponentKind_NetTie_BOM) then Result := 1;
    if (Kind=eComponentKind_Graphical) or (Kind=eComponentKind_NetTie_NoBOM) or (Kind=eComponentKind_Standard_NoBOM) then Result := 0;
end;

function PCPPPlacement(Kind: Integer): Integer;
begin
    Result := -1;
    if Kind=eComponentKind_Standard then Result := 1;
    if (Kind=eComponentKind_Graphical) or (Kind=eComponentKind_Mechanical) or
       (Kind=eComponentKind_NetTie_BOM) or (Kind=eComponentKind_NetTie_NoBOM) then Result := 0;
    // Standard (No BOM) requires an output-generator option, not a component flag.
end;

function PCPPMatches(Kind: Integer; Spec: TStringList): Boolean;
begin
    Result := True;
    if Spec.IndexOfName('graphical')>=0 then
        Result := Result and ((Kind=eComponentKind_Graphical)=(Spec.Values['graphical']='1'));
    if Spec.IndexOfName('include_in_bom')>=0 then
        Result := Result and (PCPPBom(Kind)=StrToInt(Spec.Values['include_in_bom']));
    if Spec.IndexOfName('include_in_pick_place')>=0 then
        Result := Result and (PCPPPlacement(Kind)=StrToInt(Spec.Values['include_in_pick_place']));
end;

function PCPPResolve(Current: Integer; Spec: TStringList): Integer;
var I, Candidate, Matches: Integer;
begin
    Result := -1;
    if Spec.IndexOfName('component_kind')>=0 then
    begin
        Candidate := LibraryComponentKindValue(Spec.Values['component_kind']);
        if PCPPMatches(Candidate,Spec) then Result := Candidate;
    end
    else if Spec.Values['graphical']='1' then
    begin if PCPPMatches(eComponentKind_Graphical,Spec) then Result := eComponentKind_Graphical; end
    else if (Spec.Values['graphical']='0') and (Current=eComponentKind_Graphical) then
    begin if PCPPMatches(eComponentKind_Standard,Spec) then Result := eComponentKind_Standard; end
    else if PCPPMatches(Current,Spec) then Result := Current
    else
    begin
        Matches := 0;
        for I := 0 to 3 do
        begin
            case I of
                0: Candidate := eComponentKind_Standard;
                1: Candidate := eComponentKind_Mechanical;
                2: Candidate := eComponentKind_Graphical;
                3: Candidate := eComponentKind_Standard_NoBOM;
            end;
            if PCPPMatches(Candidate,Spec) then begin Result := Candidate; Matches := Matches+1; end;
        end;
        if Matches<>1 then Result := -1;
    end;
end;

function PCPPChild(Prim: IPCB_Primitive): String;
var Props: TStringList; R: TCoordRect;
begin
    Props := TStringList.Create;
    try
        AddJSONProperty(Props,'object_address',IntToStr(Prim.I_ObjectAddress));
        AddJSONInteger(Props,'object_id',Prim.ObjectId);
        AddJSONInteger(Props,'layer_code',Prim.Layer);
        AddJSONBoolean(Props,'moveable',Prim.Moveable);
        R := Prim.BoundingRectangle;
        AddJSONInteger(Props,'left',R.Left); AddJSONInteger(Props,'right',R.Right);
        AddJSONInteger(Props,'bottom',R.Bottom); AddJSONInteger(Props,'top',R.Top);
        if (Prim.ObjectId=ePadObject) or (Prim.ObjectId=eTrackObject) or (Prim.ObjectId=eArcObject) then
        begin
            if Prim.Net<>nil then AddJSONProperty(Props,'net',Prim.Net.Name) else AddJSONProperty(Props,'net','');
        end;
        if Prim.ObjectId=ePadObject then
        begin
            AddJSONProperty(Props,'name',Prim.Name);
            AddJSONInteger(Props,'x',Prim.X); AddJSONInteger(Props,'y',Prim.Y);
            AddJSONNumber(Props,'rotation',Prim.Rotation);
            AddJSONInteger(Props,'top_x',Prim.TopXSize); AddJSONInteger(Props,'top_y',Prim.TopYSize);
            AddJSONInteger(Props,'mid_x',Prim.MidXSize); AddJSONInteger(Props,'mid_y',Prim.MidYSize);
            AddJSONInteger(Props,'bottom_x',Prim.BotXSize); AddJSONInteger(Props,'bottom_y',Prim.BotYSize);
            AddJSONInteger(Props,'top_shape',Prim.TopShape); AddJSONInteger(Props,'hole_size',Prim.HoleSize);
            AddJSONBoolean(Props,'plated',Prim.Plated);
            AddJSONInteger(Props,'mask',Prim.SolderMaskExpansion); AddJSONInteger(Props,'paste',Prim.PasteMaskExpansion);
        end;
        if Prim.ObjectId=eTrackObject then
        begin
            AddJSONInteger(Props,'x1',Prim.X1); AddJSONInteger(Props,'y1',Prim.Y1);
            AddJSONInteger(Props,'x2',Prim.X2); AddJSONInteger(Props,'y2',Prim.Y2);
            AddJSONInteger(Props,'width',Prim.Width);
        end;
        if Prim.ObjectId=eArcObject then
        begin
            AddJSONInteger(Props,'cx',Prim.XCenter); AddJSONInteger(Props,'cy',Prim.YCenter);
            AddJSONInteger(Props,'radius',Prim.Radius); AddJSONInteger(Props,'width',Prim.LineWidth);
            AddJSONNumber(Props,'start_angle',Prim.StartAngle); AddJSONNumber(Props,'end_angle',Prim.EndAngle);
        end;
        Result := BuildJSONObject(Props);
    finally Props.Free; end;
end;

function PCPPSnapshot(Comp: IPCB_Component): String;
var Props, Children: TStringList; Iter: IPCB_GroupIterator; Prim: IPCB_Primitive; Kind, Value: Integer;
begin
    Props := TStringList.Create; Children := TStringList.Create;
    try
        Kind := Comp.ComponentKind;
        AddJSONProperty(Props,'designator',Comp.Name.Text);
        AddJSONProperty(Props,'object_address',IntToStr(Comp.I_ObjectAddress));
        AddJSONProperty(Props,'component_kind',LibraryComponentKindName(Kind));
        AddJSONInteger(Props,'component_kind_code',Kind);
        AddJSONBoolean(Props,'graphical',Kind=eComponentKind_Graphical);
        Value := PCPPBom(Kind);
        if Value>=0 then AddJSONBoolean(Props,'include_in_bom',Value=1) else Props.Add('"include_in_bom": null');
        Value := PCPPPlacement(Kind);
        if Value>=0 then AddJSONBoolean(Props,'include_in_pick_place',Value=1) else Props.Add('"include_in_pick_place": null');
        AddJSONProperty(Props,'inclusion_source','component_kind_policy');
        if Kind=eComponentKind_Standard_NoBOM then AddJSONProperty(Props,'pick_place_policy','depends_on_output_settings')
        else if Value=1 then AddJSONProperty(Props,'pick_place_policy','eligible')
        else if Value=0 then AddJSONProperty(Props,'pick_place_policy','excluded')
        else AddJSONProperty(Props,'pick_place_policy','unknown');
        AddJSONProperty(Props,'pattern',Comp.Pattern); AddJSONProperty(Props,'comment',Comp.Comment.Text);
        AddJSONProperty(Props,'source_unique_id',Comp.SourceUniqueId); AddJSONProperty(Props,'source_designator',Comp.SourceDesignator);
        AddJSONInteger(Props,'x_coord',Comp.X); AddJSONInteger(Props,'y_coord',Comp.Y);
        AddJSONNumber(Props,'rotation',Comp.Rotation); AddJSONInteger(Props,'layer_code',Comp.Layer);
        AddJSONInteger(Props,'height_coord',Comp.Height);
        AddJSONBoolean(Props,'moveable',Comp.Moveable); AddJSONBoolean(Props,'primitive_lock',Comp.PrimitiveLock);
        Iter := Comp.GroupIterator_Create;
        try
            Prim := Iter.FirstPCBObject;
            while Prim<>nil do
            begin ReportProgress; Children.Add(PCPPChild(Prim)); Prim := Iter.NextPCBObject; end;
        finally Comp.GroupIterator_Destroy(Iter); end;
        Props.Add(BuildJSONArray(Children,'children'));
        Result := BuildJSONObject(Props);
    finally Props.Free; Children.Free; end;
end;

function PCPPFind(Board: IPCB_Board; Designator, Address: String; var Matches: Integer): IPCB_Component;
var Iter: IPCB_BoardIterator; Comp: IPCB_Component;
begin
    Result := nil; Matches := 0;
    Iter := Board.BoardIterator_Create;
    try
        Iter.AddFilter_ObjectSet(MkSet(eComponentObject)); Iter.AddFilter_LayerSet(AllLayers); Iter.AddFilter_Method(eProcessAll);
        Comp := Iter.FirstPCBObject;
        while Comp<>nil do
        begin
            ReportProgress;
            if ((Address='') and SameText(Comp.Name.Text,Designator)) or
               ((Address<>'') and (IntToStr(Comp.I_ObjectAddress)=Address)) then
            begin Result := Comp; Matches := Matches+1; end;
            Comp := Iter.NextPCBObject;
        end;
    finally Board.BoardIterator_Destroy(Iter); end;
end;

function ExecutePCBComponentProperties(Request: TStringList): String;
var
    Spec, Props, Addresses, Kinds, BeforeItems, AfterItems: TStringList;
    Doc: IServerDocument; Board: IPCB_Board; Comp: IPCB_Component;
    SpecPath, Path, ErrorText: String;
    I, Count, Matches, Target, Updated: Integer;
    Editing, WasModified, HaveBoard: Boolean;
begin
    Spec := TStringList.Create; Props := TStringList.Create; Addresses := TStringList.Create;
    Kinds := TStringList.Create; BeforeItems := TStringList.Create; AfterItems := TStringList.Create;
    Editing := False; WasModified := False; HaveBoard := False; Updated := 0; ErrorText := ''; Path := ''; Doc := nil;
    try
      try
        repeat
            SpecPath := '';
            for I := 0 to Request.Count-1 do if Pos('"spec_file"',Request[I])>0 then SpecPath := JSONLineValue(Request[I]);
            if not FileExists(SpecPath) then begin ErrorText := 'Spec file missing'; Break; end;
            Spec.LoadFromFile(SpecPath);
            Path := FootprintEditValue(Spec,'pcb_path');
            if DocumentKindForPath(Path)<>'PCB' then begin ErrorText := 'Only an explicit PcbDoc target is supported'; Break; end;
            Count := StrToIntDef(Spec.Values['count'],0);
            if (Count<1) or (Count>1000) then begin ErrorText := 'Invalid component count'; Break; end;
            if (FindOpenDocument(Path)=nil) and not FileExists(Path) then begin ErrorText := 'PCB is neither open nor present on disk'; Break; end;
            Doc := OpenLibraryDocument('PCB',Path);
            if Doc=nil then begin ErrorText := 'Cannot open a unique PCB buffer'; Break; end;
            WasModified := Doc.Modified;
            Board := GetBoardSafe(0);
            if Board=nil then begin ErrorText := 'No PCB board focused'; Break; end;
            // IsLibrary reports true even for a new PCB in this AD24 script runtime.
            // Use the registered document kind plus the exact board identity instead.
            if (UpperCase(Doc.Kind)<>'PCB') or (NormalizedPath(Board.FileName)<>NormalizedPath(Path)) then
            begin ErrorText := 'Focused PCB identity/kind mismatch'; Break; end;
            HaveBoard := True;
            // Resolve every exact selector and target kind before any mutation.
            for I := 0 to Count-1 do
            begin
                Comp := PCPPFind(Board,FootprintEditValue(Spec,'designator.'+IntToStr(I)),'',Matches);
                if Matches<>1 then begin ErrorText := 'Missing or ambiguous PCB designator: '+FootprintEditValue(Spec,'designator.'+IntToStr(I)); Break; end;
                Addresses.Add(IntToStr(Comp.I_ObjectAddress));
                BeforeItems.Add(PCPPSnapshot(Comp));
                Target := Comp.ComponentKind;
                if Spec.Values['mutating']='1' then Target := PCPPResolve(Target,Spec);
                if Target<0 then begin ErrorText := 'Inclusion constraints are contradictory, ambiguous or output-dependent; specify component_kind. Standard (No BOM) Pick & Place depends on output settings'; Break; end;
                Kinds.Add(IntToStr(Target));
            end;
            if ErrorText<>'' then Break;
            for I := 0 to Count-1 do
            begin
                Comp := PCPPFind(Board,'',Addresses[I],Matches);
                if Matches<>1 then begin ErrorText := 'Preflight component disappeared'; Break; end;
                Target := StrToInt(Kinds[I]);
                if Comp.ComponentKind<>Target then
                begin
                    if not Editing then begin PCBServer.PreProcess; Editing := True; end;
                    PCBServer.SendMessageToRobots(Comp.I_ObjectAddress,c_Broadcast,PCBM_BeginModify,c_NoEventData);
                    Updated := Updated+1;
                    try Comp.ComponentKind := Target;
                    finally PCBServer.SendMessageToRobots(Comp.I_ObjectAddress,c_Broadcast,PCBM_EndModify,c_NoEventData); end;
                end;
            end;
        until True;
      except ErrorText := 'PCB component property API failure: '+ExceptionMessage; end;
      if Editing then begin PCBServer.PostProcess; Editing := False; end;
      if Updated>0 then begin Board.SetState_DocumentHasChanged; MarkDocumentModified(Path); Board.ViewManager_FullUpdate; end;
      if HaveBoard then
          try
              for I := 0 to Addresses.Count-1 do
              begin Comp := PCPPFind(Board,'',Addresses[I],Matches); if Matches=1 then AfterItems.Add(PCPPSnapshot(Comp)); end;
          except ErrorText := ErrorText+' After snapshot failed: '+ExceptionMessage; end;
      AddJSONBoolean(Props,'success',ErrorText=''); AddJSONBoolean(Props,'saved',False);
      AddJSONProperty(Props,'pcb_path',Path); AddJSONBoolean(Props,'changed',Updated>0);
      AddJSONInteger(Props,'updated_count',Updated); AddJSONBoolean(Props,'was_modified',WasModified);
      if Doc<>nil then AddJSONBoolean(Props,'is_modified',Doc.Modified);
      AddJSONBoolean(Props,'may_have_partial_changes',(Updated>0) and (ErrorText<>''));
      if ErrorText<>'' then AddJSONProperty(Props,'error',ErrorText);
      Props.Add(BuildJSONArray(BeforeItems,'before')); Props.Add(BuildJSONArray(AfterItems,'after'));
      Result := BuildJSONObject(Props);
    finally
        if Editing then PCBServer.PostProcess;
        Spec.Free; Props.Free; Addresses.Free; Kinds.Free; BeforeItems.Free; AfterItems.Free;
    end;
end;
