Unicode true
!include "MUI2.nsh"
!define APP_NAME "砚台论文工作台"
!ifndef VERSION
!define VERSION "1.1.0"
!endif
!ifndef APP_SOURCE
!define APP_SOURCE "..\..\dist\Yantai"
!endif
!ifndef OUTPUT_FILE
!define OUTPUT_FILE "Yantai-${VERSION}-setup.exe"
!endif
Name "${APP_NAME}"
OutFile "${OUTPUT_FILE}"
InstallDir "$LOCALAPPDATA\Programs\Yantai"
RequestExecutionLevel user
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "SimpChinese"
Section "安装工作台"
  SetOutPath "$INSTDIR"
  File /r "${APP_SOURCE}\*"
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  CreateShortcut "$DESKTOP\砚台论文工作台.lnk" "$INSTDIR\Yantai.exe"
  CreateDirectory "$SMPROGRAMS\砚台论文工作台"
  CreateShortcut "$SMPROGRAMS\砚台论文工作台\砚台论文工作台.lnk" "$INSTDIR\Yantai.exe"
  CreateShortcut "$SMPROGRAMS\砚台论文工作台\卸载.lnk" "$INSTDIR\Uninstall.exe"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Yantai" "DisplayName" "${APP_NAME}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Yantai" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Yantai" "DisplayVersion" "${VERSION}"
SectionEnd
Section "Uninstall"
  Delete "$DESKTOP\砚台论文工作台.lnk"
  RMDir /r "$SMPROGRAMS\砚台论文工作台"
  RMDir /r "$INSTDIR"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Yantai"
  MessageBox MB_OK "程序已卸载。论文数据仍保存在当前用户的 Yantai 数据目录。"
SectionEnd
