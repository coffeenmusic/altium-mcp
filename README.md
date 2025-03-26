TODO:
- log response time of each tool
- Go to schematic sheet
Function ProcessNonVariant(Project: IProject);
Var
    I           : Integer;
    Doc         : IDocument;
    CurrentSch  : ISch_Document;
Begin
    Project := GetWorkspace.DM_FocusedProject;
    If Project = Nil Then Exit;

    NoPlaceList := TStringList.Create;

    For I := 0 to Project.DM_LogicalDocumentCount - 1 Do
    Begin
        Doc := Project.DM_LogicalDocuments(I);
        If Doc.DM_DocumentKind = 'SCH' Then
        Begin
             CurrentSch := SchServer.GetSchDocumentByPath(Doc.DM_FullPath);
             If CurrentSch <> Nil Then
             Begin
                  Client.OpenDocument('SCH',Doc.DM_FullPath); // Open Document
                  Client.ShowDocument(Doc.DM_ServerDocument);
                  FindNoPlaceSymbols(CurrentSch);

                  CheckOtherSymbols(CurrentSch);
             End;
        End;
    End;
End;

- Get pads of component
function update_dataset_for_object(Board: IPCB_Board, Dataset: TStringList, ObjID: Integer, TypeName: String): TStringList;
var
    Iterator       : IPCB_BoardIterator;
    Obj            : IPCB_ObjectClass;
    xo, yo: Integer;
    x, y, l, r, t, b, layer, Designator, Info : String;
    Rec: TCoordRect;
    Shape: String;
begin
    xo := Board.XOrigin;
    yo := Board.YOrigin;

    // Create the iterator that will look for Component Body objects only
    Iterator := Board.BoardIterator_Create;
    Iterator.AddFilter_ObjectSet(MkSet(ObjID));
    Iterator.AddFilter_IPCB_LayerSet(MkSet(eTopLayer, eBottomLayer, eMultiLayer));
    Iterator.AddFilter_Method(eProcessAll);

    Obj := Iterator.FirstPCBObject;
    While (Obj <> Nil) Do
    Begin
        layer := Layer2String(Obj.Layer);

        Rec := Get_Obj_Rect(Obj);
        l := FloatToStr(CoordToMils(Rec.Left - xo));
        r := FloatToStr(CoordToMils(Rec.Right - xo));
        t := FloatToStr(CoordToMils(Rec.Top - yo));
        b := FloatToStr(CoordToMils(Rec.Bottom - yo));

        x := FloatToStr(CoordToMils(Rec.Left - xo) + ((CoordToMils(Rec.Right - xo)-CoordToMils(Rec.Left - xo))/2));
        y := FloatToStr(CoordToMils(Rec.Bottom - yo) + ((CoordToMils(Rec.Top - yo)-CoordToMils(Rec.Bottom - yo))/2));
        if Obj.ObjectID <> eComponentBodyObject then
        begin
            x := FloatToStr(CoordToMils(Obj.x - xo));
            y := FloatToStr(CoordToMils(Obj.y - yo));
        end;

        Designator := 'Unknown';
        Info := '';
        if Obj.ObjectId = eComponentObject then
        begin
            Designator := Obj.Name.Text;
            Info := 'Rotation:'+FloatToStr(Obj.Rotation);
        end
        else if Obj.ObjectId = ePadObject then
        begin
            Designator := Obj.Name;

            Info := 'InComponent:'+BoolToStr(Obj.InComponent);
            Shape := ShapeToString(Obj.ShapeOnLayer[Obj.Layer]);
            if (Shape = 'Rounded Rectangle') or (Shape = 'Rounded Rectangular') then
            begin
                 if Obj.CornerRadius[Obj.Layer] = 0 then
                 begin
                     Shape := 'Rectangular';
                 end
                 else
                 begin
                     Info := Info + ';CornerRadius:' + FloatToStr(Obj.CornerRadius[Obj.Layer]);
                 end;
            end;
            Info := Info + ';Shape:' + Shape;

            Info := Info + ';PadWidth:' + IntToStr(CoordToMils(Obj.XSizeOnLayer[Obj.Layer]));
            Info := Info + ';PadHeight:' + IntToStr(CoordToMils(Obj.YSizeOnLayer[Obj.Layer]));
            Info := Info + ';Rotation:'+FloatToStr(Obj.Rotation);
            Info := Info + ';HoleType:' + IntToStr(Obj.HoleType);
            Info := Info + ';HoleSize:' + FloatToStr(CoordToMils(Obj.HoleSize));
            Info := Info + ';HoleRotation:'+FloatToStr(Obj.HoleRotation);
            if (Obj.InComponent) and (Obj.Component <> nil) then Info := Info + ';CmpDesignator:'+ Obj.Component.Name.Text;
        end
        else if Obj.ObjectId = eComponentBodyObject then
        begin
            if Obj.Component <> nil then
            begin
                Designator := Obj.Component.Name.Text;
                Info := 'Rotation:'+FloatToStr(Obj.Component.Rotation);
                layer := Layer2String(Obj.Component.Layer);
                x := FloatToStr(CoordToMils(Obj.Component.x - xo));
                y := FloatToStr(CoordToMils(Obj.Component.y - yo));
            end;
        end;

        Dataset.Add(TypeName+','+Designator+','+layer+','+x+','+y+','+l+','+r+','+t+','+b+','+Info);

        Obj := Iterator.NextPCBObject;
    End;
    Board.BoardIterator_Destroy(Iterator);

    result := Dataset;
end;

- Show layers: IPCB_Board.VisibleLayers
- Go to sheet with component designator
- Flip to layout
- Get screenshot of either schematic or layout
- Board.ChooseLocation(x, y, 'Test');
- Zoom to selected objects:


 procedure Run;
var
    ProcessLauncher : IProcessLauncher;
    Parameters : String;
begin
    Parameters := 'Apply=True|Expr=(Objectkind=''Component'') and (Name = ' + '''' + AnsiUpperCase('U3') + '''' + ')|Index=1';
    Parameters := Parameters + '|Zoom=True';
    Parameters := Parameters + '|Select=True';
    Parameters := Parameters + '|Mask=True' ;

    ProcessLauncher := Client;
    ProcessLauncher.PostMessage('PCB:RunQuery', 'Clear', Length('Clear'), Client.CurrentView);
    ProcessLauncher.PostMessage('PCB:RunQuery', Parameters, Length(Parameters), Client.CurrentView);
end;