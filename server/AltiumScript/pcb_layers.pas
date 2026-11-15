// Shared PCB layer aliases. Legacy Layer writes remain limited to Mechanical 1..16.

function PCBLayerKey(Name: String): String;
begin
    Result := UpperCase(Trim(Name));
    Result := StringReplace(Result, ' ', '', REPLACEALL);
    Result := StringReplace(Result, #9, '', REPLACEALL);
    Result := StringReplace(Result, '-', '', REPLACEALL);
    Result := StringReplace(Result, '_', '', REPLACEALL);
    if Copy(Result, 1, 1) = 'E' then Delete(Result, 1, 1);
end;

function PCBMechanicalNumber(Name: String): Integer;
var Key: String;
begin
    Key := PCBLayerKey(Name);
    if Copy(Key, 1, 10) = 'MECHANICAL' then Delete(Key, 1, 10)
    else if Copy(Key, 1, 4) = 'MECH' then Delete(Key, 1, 4)
    else begin Result := -1; Exit; end;
    if Copy(Key, 1, 5) = 'LAYER' then Delete(Key, 1, 5);
    Result := StrToIntDef(Key, -1);
    if (Result < 1) or (Result > 1024) then Result := -1;
end;

function PCBCanonicalLayerName(Name: String): String;
var Key: String; N: Integer;
begin
    Result := Trim(Name);
    Key := PCBLayerKey(Name);
    N := PCBMechanicalNumber(Name);
    if N > 0 then Result := 'Mechanical ' + IntToStr(N)
    else if Key = 'MULTILAYER' then Result := 'Multi-Layer'
    else if Key = 'NOLAYER' then Result := 'No Layer'
    else if Key = 'TOPLAYER' then Result := 'Top Layer'
    else if Key = 'BOTTOMLAYER' then Result := 'Bottom Layer'
    else if Key = 'TOPOVERLAY' then Result := 'Top Overlay'
    else if Key = 'BOTTOMOVERLAY' then Result := 'Bottom Overlay'
    else if Key = 'TOPPASTE' then Result := 'Top Paste'
    else if Key = 'BOTTOMPASTE' then Result := 'Bottom Paste'
    else if (Key = 'TOPSOLDER') or (Key = 'TOPSOLDERMASK') then Result := 'Top Solder Mask'
    else if (Key = 'BOTTOMSOLDER') or (Key = 'BOTTOMSOLDERMASK') then Result := 'Bottom Solder Mask'
    else if Key = 'DRILLGUIDE' then Result := 'Drill Guide'
    else if Key = 'DRILLDRAWING' then Result := 'Drill Drawing'
    else if (Key = 'KEEPOUT') or (Key = 'KEEPOUTLAYER') then Result := 'Keep Out Layer'
    else
        for N := 1 to 30 do
            if (Key = 'MID' + IntToStr(N)) or (Key = 'MIDLAYER' + IntToStr(N)) then
                Result := 'Mid Layer ' + IntToStr(N);
end;

function PCBLayerFromName(Name: String): Integer;
var N: Integer;
begin
    Name := PCBCanonicalLayerName(Name);
    Result := String2Layer(Name);
    if Name = 'Multi-Layer' then Result := eMultiLayer
    else if Name = 'No Layer' then Result := eNoLayer
    else if Name = 'Top Layer' then Result := eTopLayer
    else if Name = 'Bottom Layer' then Result := eBottomLayer;
    N := PCBMechanicalNumber(Name);
    if N > 16 then Result := eNoLayer
    else if N > 0 then
        case N of
            1: Result := eMechanical1;
            2: Result := eMechanical2;
            3: Result := eMechanical3;
            4: Result := eMechanical4;
            5: Result := eMechanical5;
            6: Result := eMechanical6;
            7: Result := eMechanical7;
            8: Result := eMechanical8;
            9: Result := eMechanical9;
            10: Result := eMechanical10;
            11: Result := eMechanical11;
            12: Result := eMechanical12;
            13: Result := eMechanical13;
            14: Result := eMechanical14;
            15: Result := eMechanical15;
            16: Result := eMechanical16;
        end;
end;

function PCBLayerName(Layer: Integer): String;
begin
    Result := PCBCanonicalLayerName(Layer2String(Layer));
end;
