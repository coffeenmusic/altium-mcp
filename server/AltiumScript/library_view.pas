// Exact-library presentation changes only: no PreProcess, object edits or saves.

function LVAction(Command, Parameters: String): String;
var Props: TStringList;
begin
    Props := TStringList.Create;
    try
        Client.SendMessage(Command,Parameters,255,Client.CurrentView);
        AddJSONProperty(Props,'command',Command); AddJSONProperty(Props,'parameters',Parameters);
        Result := BuildJSONObject(Props);
    finally Props.Free; end;
end;

function LVSchContent(Comp: ISch_Component): String;
var Props: TStringList;
begin
    Props := TStringList.Create;
    try
        AddJSONProperty(Props,'component_uid',Comp.UniqueId);
        Props.Add('"metadata":'+LibraryComponentSnapshot(Comp));
        Props.Add('"objects":'+UPSnapshot(Comp));
        Result := BuildJSONObject(Props);
    finally Props.Free; end;
end;

function LVFramePhysicalFootprint(Lib: IPCB_Library; Comp: IPCB_LibComponent): String;
var
    Iter: IPCB_GroupIterator; Prim: IPCB_Primitive; R: TCoordRect;
    Props: TStringList;
    MinX, MinY, MaxX, MaxY, Span, Margin, CX, CY: TCoord;
    HaveBounds: Boolean;
begin
    HaveBounds := False;
    Iter := Comp.GroupIterator_Create;
    try
        Prim := Iter.FirstPCBObject;
        while Prim<>nil do
        begin
            ReportProgress;
            // Drafting annotations can extend far beyond the rendered model.
            if Prim.ObjectId<>eTextObject then
            begin
                R := Prim.BoundingRectangle;
                if not HaveBounds then
                begin MinX := R.Left; MaxX := R.Right; MinY := R.Bottom; MaxY := R.Top; HaveBounds := True; end
                else
                begin
                    if R.Left<MinX then MinX := R.Left;
                    if R.Right>MaxX then MaxX := R.Right;
                    if R.Bottom<MinY then MinY := R.Bottom;
                    if R.Top>MaxY then MaxY := R.Top;
                end;
            end;
            Prim := Iter.NextPCBObject;
        end;
    finally Comp.GroupIterator_Destroy(Iter); end;
    if not HaveBounds then begin Result := ''; Exit; end;
    Span := MaxX-MinX; if MaxY-MinY>Span then Span := MaxY-MinY;
    Margin := Span div 3; if Margin<MilsToCoord(100) then Margin := MilsToCoord(100);
    CX := MinX+(MaxX-MinX) div 2; CY := MinY+(MaxY-MinY) div 2;
    Span := Span div 2+Margin;
    Lib.Board.GraphicalView_ZoomOnRect(CX-Span,CY-Span,CX+Span,CY+Span);
    Lib.Board.GraphicalView_ZoomRedraw;
    Props := TStringList.Create;
    try
        AddJSONProperty(Props,'command','IPCB_Board.GraphicalView_ZoomOnRect');
        AddJSONProperty(Props,'parameters','physical_bounds');
        Result := BuildJSONObject(Props);
    finally Props.Free; end;
end;

function ExecuteSetLibraryView(Request: TStringList): String;
var
    Spec, Props, Selected, Commands: TStringList;
    Doc: IServerDocument;
    SchLib: ISch_Lib; Comp, Other: ISch_Component;
    PcbLib: IPCB_Library; Footprint, OtherFootprint: IPCB_LibComponent;
    Path, Name, Kind, SpecPath, Mode, Camera, ErrorText, BeforeInventory, AfterInventory,
    BeforeContent, AfterContent, InitialName, ViewName, FrameAction: String;
    I, Matches, Part, Display: Integer;
    IsSch, WasModified, Opened, HaveInventory, HaveContent: Boolean;
begin
    Spec := TStringList.Create; Props := TStringList.Create;
    Selected := TStringList.Create; Commands := TStringList.Create;
    ECObjects := TInterfaceList.Create; UPObjects := TInterfaceList.Create;
    Doc := nil; SchLib := nil; PcbLib := nil; Comp := nil; Footprint := nil;
    Path := ''; Name := ''; Kind := ''; ErrorText := ''; InitialName := ''; ViewName := '';
    BeforeInventory := '[]'; AfterInventory := '[]'; BeforeContent := '{}'; AfterContent := '{}';
    IsSch := False; WasModified := False; Opened := False; HaveInventory := False; HaveContent := False;
    try
      try
        repeat
            SpecPath := '';
            for I := 0 to Request.Count-1 do if Pos('"spec_file"',Request[I])>0 then SpecPath := JSONLineValue(Request[I]);
            if not FileExists(SpecPath) then begin ErrorText := 'Spec file not found'; Break; end;
            Spec.LoadFromFile(SpecPath); Path := EditValue(Spec,'library_path'); Name := EditValue(Spec,'component_name');
            Kind := DocumentKindForPath(Path); IsSch := Kind='SCHLIB';
            if (Kind<>'SCHLIB') and (Kind<>'PCBLIB') then begin ErrorText := 'Expected SchLib or PcbLib'; Break; end;
            if SchServer<>nil then
                if SchServer.ProcessControl.ProcessDepth<>0 then begin ErrorText := 'Schematic transaction is active; view navigation refused'; Break; end;
            if (FindOpenDocument(Path)=nil) and not FileExists(Path) then begin ErrorText := 'Library is neither saved nor open'; Break; end;
            Doc := OpenLibraryDocument(Kind,Path);
            if Doc=nil then begin ErrorText := 'Cannot open a unique library buffer'; Break; end;
            Opened := True; WasModified := Doc.Modified;
            if (NormalizedPath(Doc.FileName)<>NormalizedPath(Path)) or (UpperCase(Doc.Kind)<>Kind) then begin ErrorText := 'Library identity mismatch'; Break; end;
            if IsSch then
            begin
                SchLib := SchServer.GetSchDocumentByPath(Path);
                if SchLib=nil then begin ErrorText := 'SchLib unavailable'; Break; end;
                if (SchLib.ObjectId<>33) or (NormalizedPath(SchLib.DocumentName)<>NormalizedPath(Path)) then begin ErrorText := 'SchLib identity mismatch'; Break; end;
                if SchServer.ProcessControl.ProcessDepth<>0 then begin ErrorText := 'Schematic transaction is active'; Break; end;
                if SchLib.CurrentSchComponent<>nil then InitialName := SchLib.CurrentSchComponent.LibReference;
                if Name='' then Name := InitialName;
                BeforeInventory := ECSchInventory(SchLib,Name,Comp,Other,Matches); HaveInventory := True;
                if Matches<>1 then begin ErrorText := 'Symbol missing or ambiguous'; Break; end;
                Part := Comp.CurrentPartID; Display := Comp.DisplayMode;
                if EditHas(Spec,'part_id') then Part := StrToInt(Spec.Values['part_id']);
                if EditHas(Spec,'display_mode') then Display := StrToInt(Spec.Values['display_mode']);
                if (Part<1) or (Part>Comp.PartCount) then begin ErrorText := 'part_id exceeds the symbol part count'; Break; end;
                if (Display<0) or (Display>=Comp.DisplayModeCount) then begin ErrorText := 'display_mode exceeds the symbol display mode count'; Break; end;
                if not SameText(InitialName,Name) then SchLib.CurrentSchComponent := Comp;
                Comp := SchLib.CurrentSchComponent;
                if Comp=nil then begin ErrorText := 'Focused symbol unavailable'; Break; end;
                if not SameText(Comp.LibReference,Name) then begin ErrorText := 'Focused symbol identity mismatch'; Break; end;
                BeforeContent := LVSchContent(Comp); HaveContent := True;
                if EditHas(Spec,'part_id') and (SchLib.GetState_CurrentSchComponentPartId<>Part) then SchLib.SetState_CurrentSchComponentPartId(Part);
                if EditHas(Spec,'display_mode') and (SchLib.GetState_CurrentSchComponentDisplayMode<>Display) then SchLib.SetState_CurrentSchComponentDisplayMode(Display);
                SchLib.GraphicallyInvalidate;
                AddJSONProperty(Selected,'component_name',SchLib.CurrentSchComponent.LibReference);
                AddJSONInteger(Selected,'part_id',SchLib.GetState_CurrentSchComponentPartId);
                AddJSONInteger(Selected,'display_mode',SchLib.GetState_CurrentSchComponentDisplayMode);
                AddJSONInteger(Selected,'part_count',SchLib.CurrentSchComponent.PartCount);
                AddJSONInteger(Selected,'display_mode_count',SchLib.CurrentSchComponent.DisplayModeCount);
                if Spec.Values['fit']='1' then Commands.Add(LVAction('SCH:Zoom','Action=All'));
                AfterContent := LVSchContent(SchLib.CurrentSchComponent);
            end
            else
            begin
                PcbLib := GetPcbLibSafe(0);
                if PcbLib=nil then begin ErrorText := 'PcbLib unavailable'; Break; end;
                if NormalizedPath(PcbLib.Board.FileName)<>NormalizedPath(Path) then begin ErrorText := 'PcbLib identity mismatch'; Break; end;
                if PcbLib.CurrentComponent<>nil then InitialName := PcbLib.CurrentComponent.Name;
                if Name='' then Name := InitialName;
                BeforeInventory := ECPcbInventory(PcbLib,Name,Footprint,OtherFootprint,Matches); HaveInventory := True;
                if Matches<>1 then begin ErrorText := 'Footprint missing or ambiguous'; Break; end;
                if not SameText(InitialName,Name) then PcbLib.CurrentComponent := Footprint;
                if PcbLib.CurrentComponent=nil then begin ErrorText := 'Focused footprint unavailable'; Break; end;
                if not SameText(PcbLib.CurrentComponent.Name,Name) then begin ErrorText := 'Focused footprint identity mismatch'; Break; end;
                BeforeContent := GetFootprintPrimitives(ROOT_DIR,'',Name); HaveContent := True;
                Mode := Spec.Values['view_mode']; Camera := Spec.Values['camera_to'];
                if Mode='2d' then Commands.Add(LVAction('PCB:SwitchTo2D',''));
                if Mode='3d' then Commands.Add(LVAction('PCB:SwitchTo3D',''));
                // Library Zoom All resets camera orientation. Frame first;
                // apply the requested pose last so it cannot be overwritten.
                if Spec.Values['fit']='1' then
                begin
                    if (Camera<>'') or (Mode='3d') then
                    begin
                        FrameAction := LVFramePhysicalFootprint(PcbLib,Footprint);
                        if FrameAction='' then begin ErrorText := 'No physical bounds to frame; use fit=False for an empty or text-only footprint'; Break; end;
                        Commands.Add(FrameAction);
                    end
                    else Commands.Add(LVAction('PCB:Zoom','Action=All'));
                end;
                if Camera<>'' then Commands.Add(LVAction('PCB:ResetCamera','To='+Camera));
                AddJSONProperty(Selected,'component_name',PcbLib.CurrentComponent.Name);
                AfterContent := GetFootprintPrimitives(ROOT_DIR,'',Name);
            end;
            if Client.CurrentView=nil then begin ErrorText := 'No active document view after navigation'; Break; end;
            if Client.CurrentView.OwnerDocument=nil then begin ErrorText := 'No active view owner'; Break; end;
            if NormalizedPath(Client.CurrentView.OwnerDocument.FileName)<>NormalizedPath(Path) then begin ErrorText := 'View owner identity mismatch'; Break; end;
            ViewName := Client.CurrentView.ViewName;
            if Doc.Modified<>WasModified then ErrorText := 'View navigation changed Modified';
        until True;
      except ErrorText := ExceptionMessage; end;
      if HaveInventory then
      begin
          try
              if IsSch then AfterInventory := ECSchInventory(SchLib,Name,Comp,Other,Matches)
              else AfterInventory := ECPcbInventory(PcbLib,Name,Footprint,OtherFootprint,Matches);
          except ErrorText := 'After inventory failed: '+ExceptionMessage; end;
      end;
      if not HaveContent then AfterContent := BeforeContent;
      AddJSONBoolean(Props,'success',ErrorText=''); AddJSONBoolean(Props,'saved',False);
      AddJSONProperty(Props,'library_path',Path); AddJSONProperty(Props,'document_kind',Kind);
      AddJSONProperty(Props,'previous_component',InitialName); AddJSONProperty(Props,'view_name',ViewName);
      AddJSONBoolean(Props,'was_modified',WasModified);
      if Doc<>nil then AddJSONBoolean(Props,'is_modified',Doc.Modified);
      AddJSONBoolean(Props,'may_have_view_changes',Opened);
      if ErrorText<>'' then AddJSONProperty(Props,'error',ErrorText);
      Props.Add('"selected":'+BuildJSONObject(Selected)); Props.Add('"actions":'+BuildJSONArray(Commands));
      Props.Add('"inventory_before":'+BeforeInventory); Props.Add('"inventory_after":'+AfterInventory);
      Props.Add('"content_before":'+BeforeContent); Props.Add('"content_after":'+AfterContent);
      Result := BuildJSONObject(Props);
    finally Spec.Free; Props.Free; Selected.Free; Commands.Free; ECObjects.Free; UPObjects.Free; end;
end;
