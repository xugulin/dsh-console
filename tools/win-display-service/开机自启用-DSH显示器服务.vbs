' 开机自启：拉起 DSH 显示器服务。
'
' 放在「启动」文件夹里（shell:startup）即可，删除本文件就是取消自启。
' 为什么需要它：Linux 上这个服务由 systemd 单元托管，Windows 上没有对应机制，
' 重启后不拉起来的话，插件面板会一直停在「显示器还没有打开」。
'
' 说明：这里写的是便携包的绝对路径。整个包换目录后，改这一行即可。
Set shell = CreateObject("WScript.Shell")
shell.Run """D:\AI\DSH控制台\启动显示器服务-Silent.vbs""", 0, False
