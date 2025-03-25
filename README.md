TODO:
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

function GetSrcPositions(Project: IProject, SrcDes: String): TStringList;
Var
    I           : Integer;
    Doc         : IDocument;
    CurrentSch  : ISch_Document;
    SrcData        : TStringList;
    Cmp: ISch_Component;
    CmpIterator, PIterator   : ISch_Iterator;
    CmpDes: ISch_Designator;
    CmpName, ParamName, ParamText: string;
    Hidden: boolean;
    Parameter: ISch_Parameter;
    CmpX, CmpY, Px, Py, Dx, Dy: Integer;
Begin
    SrcData := TStringList.Create;

    For I := 0 to Project.DM_LogicalDocumentCount - 1 Do
    Begin
        Doc := Project.DM_LogicalDocuments(I);
        If Doc.DM_DocumentKind = 'SCH' Then
        Begin
            Client.OpenDocument('SCH',Doc.DM_FullPath); // Open Document
            CurrentSch := SchServer.GetSchDocumentByPath(Doc.DM_FullPath);

            // Look for components only
            CmpIterator := CurrentSch.SchIterator_Create;
            CmpIterator.AddFilter_ObjectSet(MkSet(eSchComponent));

            Try
                Cmp := CmpIterator.FirstSchObject;
                While Cmp <> Nil Do
                Begin
                    //ReportList.Add(AComponent.Designator.Name + ' ' + AComponent.Designator.Text);
                    CmpDes := Cmp.Designator;
                    if (CmpDes <> nil) and (Cmp.Designator.Text = SrcDes) then
                    begin
                        Try
                            SrcRot := Cmp.Orientation;

                            CmpX := Cmp.Location.X;
                            CmpY := Cmp.Location.Y;

                            Dx := CmpDes.Location.X;
                            Dy := CmpDes.Location.Y;

                            SrcData.Add('DESIGNATOR'+';'+IntToStr(Dx-CmpX)+';'+IntToStr(Dy-CmpY)+';'+IntToStr(CmpDes.Justification)+';'+IntToStr(CmpDes.Orientation));

                            PIterator := Cmp.SchIterator_Create;
                            PIterator.AddFilter_ObjectSet(MkSet(eParameter));

                            Parameter := PIterator.FirstSchObject;
                            While Parameter <> Nil Do
                            Begin
                                if Parameter.IsHidden = False then
                                begin
                                    ParamName := Parameter.Name;
                                    ParamText := Parameter.Text;
                                    Px := Parameter.Location.X;
                                    Py := Parameter.Location.Y;

                                    SrcData.Add(ParamName+';'+IntToStr(Px-CmpX)+';'+IntToStr(Py-CmpY)+';'+IntToStr(Parameter.Justification)+';'+IntToStr(CmpDes.Orientation));
                                end;

                                Parameter := PIterator.NextSchObject;
                            End;
                        Finally
                            Cmp.SchIterator_Destroy(PIterator);
                        End;

                        result := SrcData;
                        exit;
                    end;

                    Cmp := CmpIterator.NextSchObject;
                End;
            Finally
                CurrentSch.SchIterator_Destroy(CmpIterator);
            End;
        End;
    End;

    SrcData.Free;
End;

- Go to sheet with component designator
- Flip to layout
- Get screenshot of either schematic or layout
- Board.ChooseLocation(x, y, 'Test');
- Zoom to selected objects:
### Get Selected Objects
for i := 0 to Board.SelectecObjectCount - 1 do
begin
  if Board.SelectecObject[i].ObjectId = eTrackObject then
  begin
	 result := Board.SelectecObject[i];
	 exit;
  end;
end;

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