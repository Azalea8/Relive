Remove-Item -Recurse -Force dist/ReLive -ErrorAction Ignore

.venv/Scripts/python -m PyInstaller `
  --onedir `
  --windowed `
  --name ReLive `
  --icon=Relive.ico `
  --hidden-import PySide6.QtCore `
  --hidden-import PySide6.QtGui `
  --hidden-import PySide6.QtWidgets `
  --hidden-import httpx `
  --exclude-module tkinter `
  --exclude-module matplotlib `
  main.py

New-Item -ItemType Directory -Force dist/ReLive/bin | Out-Null

Copy-Item `
  bin/* `
  dist/ReLive/bin/ `
  -Recurse `
  -Force

Copy-Item `
  Relive.ico `
  dist/ReLive/ `
  -Force