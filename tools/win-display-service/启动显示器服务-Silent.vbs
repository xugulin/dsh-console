' 无窗口启动 DSH 显示器服务 —— 开机自启请用这个，不会闪黑框。
' （与「启动DSH网页界面-Silent.vbs」同一套路：把 .bat 包在 VBScript 里跑。）
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
here = fso.GetParentFolderName(WScript.ScriptFullName)
' 告诉 .bat 别 pause：隐藏运行时等一个看不见的按键会留下赖着不走的 cmd 进程
shell.Environment("Process")("DSH_NO_PAUSE") = "1"
shell.Run """" & here & "\启动显示器服务.bat""", 0, False
