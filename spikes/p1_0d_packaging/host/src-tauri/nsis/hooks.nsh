; Stop a running agent before files are replaced or removed.
; 1. --shutdown asks the agent to stop over IPC, waits for its lock, kills it as a last resort.
;    The agent is a console-subsystem build, so a broken agent exits instead of blocking on a dialog.
; 2. taskkill is the backstop for an agent that cannot even start --shutdown (only the current
;    user's processes can be killed without elevation).
!macro JOBFORGE_STOP_AGENT REASON
  IfFileExists "$INSTDIR\agent\jobforge-agent.exe" 0 +2
    nsExec::ExecToLog '"$INSTDIR\agent\jobforge-agent.exe" --shutdown ${REASON}'
  nsExec::ExecToLog 'taskkill /F /IM jobforge-agent.exe'
!macroend

!macro NSIS_HOOK_PREINSTALL
  !insertmacro JOBFORGE_STOP_AGENT update
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  !insertmacro JOBFORGE_STOP_AGENT uninstall
!macroend
