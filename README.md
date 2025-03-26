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

- Show layers: IPCB_Board.VisibleLayers
	+ AutoSilk.pas
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