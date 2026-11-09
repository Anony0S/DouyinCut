// The updater launches Setup before shutting down. Never write installation
// files until the owning application process (and its runtime) has exited.
function UpdateOpenProcess(Access: LongWord; Inherit: Boolean; ProcessId: LongWord): THandle;
  external 'OpenProcess@kernel32.dll stdcall';
function UpdateWaitForSingleObject(Handle: THandle; Milliseconds: LongWord): LongWord;
  external 'WaitForSingleObject@kernel32.dll stdcall';
function UpdateCloseHandle(Handle: THandle): Boolean;
  external 'CloseHandle@kernel32.dll stdcall';

function WaitForUpdateExit(): Boolean;
var
  ParentId: Integer;
  Parent: THandle;
begin
  Result := False;
  ParentId := StrToIntDef(ExpandConstant('{param:UPDATEPID|0}'), -1);
  if ParentId = 0 then
  begin
    Result := True;
    Exit;
  end;
  if ParentId < 0 then
    Exit;
  Parent := UpdateOpenProcess($00100000, False, ParentId);
  if Parent = 0 then
  begin
    { ERROR_INVALID_PARAMETER means that process has already exited. }
    Result := DLLGetLastError() = 87;
  end
  else
  begin
    Result := UpdateWaitForSingleObject(Parent, 120000) = 0;
    UpdateCloseHandle(Parent);
  end;
  if not Result then
    MsgBox('DouyinCut 未能在规定时间内安全退出。已取消更新，请完全退出后重试。', mbError, MB_OK);
end;
