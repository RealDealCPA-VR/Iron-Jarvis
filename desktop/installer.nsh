; Iron Jarvis — NSIS customization (electron-builder `nsis.include`).
;
; An orphaned frozen daemon (ironjarvis.exe from a crashed session) holds file
; locks inside resources/daemon; extraction then fails per-file and the app
; boots half-installed (the v1.124.0 truncated-update incident). electron-
; builder's own running-app check only knows about "Iron Jarvis.exe" — it has
; no idea the app spawns a daemon child. Kill every ironjarvis.exe before any
; file is touched; the image name is unique to Iron Jarvis, so this is safe.

!macro customInit
  nsExec::Exec 'taskkill /F /T /IM ironjarvis.exe'
  Pop $0
!macroend
