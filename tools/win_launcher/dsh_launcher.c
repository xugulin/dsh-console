/* DSH 控制台 —— Windows 启动器（真正的 .exe，不依赖 .bat / .vbs）
 *
 * 为什么值得单独做一个 exe：
 *   * .bat 双击会先闪一个黑框；.vbs 能藏起来，但杀毒软件对它比对 exe 敏感得多，
 *     而且用户右键"以管理员身份运行"之类的操作对 .vbs 也不直观；
 *   * exe 可以按**子系统**编译：图形版用 WINDOWS 子系统 = 天生没有控制台，
 *     不需要任何"隐藏窗口"的技巧；
 *   * 出问题时能弹一个原生对话框把原因说清楚（.bat 在没控制台时只会静默退出）。
 *
 * 它做的事和 Start-DSH-Console.bat 完全一致（顺序也一致，便于对照排错）：
 *   1. 把 HOME / USERPROFILE 关进包内的 home\（不污染系统）
 *   2. 补齐 Windows 的标准用户目录（桌面/文档/下载…），否则原生文件对话框会报"位置不可用"
 *   3. PATH 前置包内的 node 与 harness\win\bin
 *   4. 用包内的 python.exe 跑 app\main.py，并把命令行参数原样转交
 *   5. 等子进程结束，返回它的退出码
 *
 * 前端靠**自身文件名**决定（一个二进制复制成三个名字就够）：
 *   * ...Terminal.exe  → --tui
 *   * ...Web-UI.exe    → --web
 *   * 其它             → 图形控制台
 *
 * 编译：不链接 CRT（/nodefaultlib），只用 kernel32 + user32 的十来个函数，
 * 所以不需要 mingw 头文件，clang + lld-link 就能产出 PE。详见 tools/build_win_launcher.py
 */

typedef unsigned char u8;
typedef unsigned short u16;
typedef unsigned int u32;
typedef int i32;
typedef unsigned long long u64;
typedef unsigned short wchar;      /* Windows 上 wchar_t 是 16 位 */
typedef void *handle;

#define NULLP ((void *)0)
#define CREATE_UNICODE_ENVIRONMENT 0x00000400u
#define INFINITE 0xFFFFFFFFu
#define MB_OK 0x00000000u
#define MB_ICONERROR 0x00000010u
#define MB_SETFOREGROUND 0x00010000u
#define CREATE_NO_WINDOW 0x08000000u

/* 图形版用 pythonw.exe（GUI 子系统，**根本没有控制台**），控制台版用 python.exe
   （TUI 需要终端，Debug 版需要输出）。由构建脚本 -DDSH_GUI=1/0 决定。

   为什么必须分开：python.exe 是**控制台**程序，从一个没有控制台的 GUI 进程里启动它，
   Windows 会给它**新分配一个控制台窗口**——用户就看到黑框了。实测（wine）正是如此：
   GUI 版拿到子进程退出码 120，因为 wine 没有显示驱动、分配控制台失败。 */
#ifndef DSH_GUI
#define DSH_GUI 0
#endif

typedef struct {
    u32 cb;
    wchar *lpReserved;
    wchar *lpDesktop;
    wchar *lpTitle;
    u32 dwX, dwY, dwXSize, dwYSize, dwXCountChars, dwYCountChars;
    u32 dwFillAttribute, dwFlags;
    u16 wShowWindow, cbReserved2;
    u8 *lpReserved2;
    handle hStdInput, hStdOutput, hStdError;
} STARTUPINFOW;

typedef struct {
    handle hProcess, hThread;
    u32 dwProcessId, dwThreadId;
} PROCESS_INFORMATION;

__declspec(dllimport) u32 __stdcall GetModuleFileNameW(handle, wchar *, u32);
__declspec(dllimport) wchar * __stdcall GetCommandLineW(void);
__declspec(dllimport) i32 __stdcall SetEnvironmentVariableW(const wchar *, const wchar *);
__declspec(dllimport) u32 __stdcall GetEnvironmentVariableW(const wchar *, wchar *, u32);
__declspec(dllimport) i32 __stdcall CreateDirectoryW(const wchar *, void *);
__declspec(dllimport) i32 __stdcall CreateProcessW(const wchar *, wchar *, void *, void *,
                                                   i32, u32, void *, const wchar *,
                                                   STARTUPINFOW *, PROCESS_INFORMATION *);
__declspec(dllimport) u32 __stdcall WaitForSingleObject(handle, u32);
__declspec(dllimport) i32 __stdcall GetExitCodeProcess(handle, u32 *);
__declspec(dllimport) i32 __stdcall CloseHandle(handle);
__declspec(dllimport) u32 __stdcall GetLastError(void);
__declspec(dllimport) void __stdcall ExitProcess(u32);
__declspec(dllimport) i32 __stdcall MessageBoxW(handle, const wchar *, const wchar *, u32);

/* 没有 CRT，编译器有时仍会为结构体初始化生成这两个调用，自己提供一份。 */
void *memcpy(void *d, const void *s, u64 n) {
    u8 *dp = (u8 *)d; const u8 *sp = (const u8 *)s;
    while (n--) *dp++ = *sp++;
    return d;
}
void *memset(void *d, i32 c, u64 n) {
    u8 *dp = (u8 *)d;
    while (n--) *dp++ = (u8)c;
    return d;
}

#define DIRBUF 4096
#define BIGBUF 32768
#define ENVBUF 8192

static wchar g_dir[DIRBUF];        /* 自身所在目录，带结尾反斜杠 */
static wchar g_home[DIRBUF];       /* <dir>home */
static wchar g_tmp[ENVBUF];
static wchar g_cmd[BIGBUF];

static u32 wlen(const wchar *s) { u32 n = 0; while (s[n]) n++; return n; }

static void wcpy(wchar *d, const wchar *s) { while ((*d++ = *s++) != 0) {} }

static void wcat(wchar *d, const wchar *s) {
    while (*d) d++;
    while ((*d++ = *s++) != 0) {}
}

static void wcatc(wchar *d, wchar c) { while (*d) d++; *d++ = c; *d = 0; }

/* 大小写不敏感的子串查找（只用于判断自身文件名里有没有 "terminal"/"web-ui"） */
static i32 wcontains_ci(const wchar *hay, const wchar *needle) {
    u32 i, j;
    for (i = 0; hay[i]; i++) {
        for (j = 0; needle[j]; j++) {
            wchar a = hay[i + j], b = needle[j];
            if (!a) return 0;
            if (a >= 'A' && a <= 'Z') a = (wchar)(a + 32);
            if (b >= 'A' && b <= 'Z') b = (wchar)(b + 32);
            if (a != b) break;
        }
        if (!needle[j]) return 1;
    }
    return 0;
}

/* 失败时把话说清楚：图形版没有控制台，只能靠对话框 */
static void fail(const wchar *what) {
    /* static 而不是局部：>4KB 的栈帧会让编译器生成 __chkstk 调用，
       而我们不链 CRT、没有这个符号。单线程程序用 static 缓冲区正好。 */
    static wchar msg[2048];
    msg[0] = 0;
    wcat(msg, L"启动 DSH 控制台失败。\n\n");
    wcat(msg, what);
    wcat(msg, L"\n\n包目录：\n");
    wcat(msg, g_dir);
    wcat(msg, L"\n\n如果是缺运行时，请先双击包内的 "
              L"Install-Windows-Runtime.bat 补装 node / Python / PySide6。");
    MessageBoxW(NULLP, msg, L"DSH 控制台", MB_OK | MB_ICONERROR | MB_SETFOREGROUND);
    ExitProcess(1);
}

/* 把包内某个相对路径补成绝对路径：g_dir + rel → out */
static void join(wchar *out, const wchar *rel) {
    out[0] = 0;
    wcat(out, g_dir);
    wcat(out, rel);
}

/* 环境变量：值不需要了（值是拼在 g_tmp 里的） */
static void setenv_str(const wchar *name, const wchar *value) {
    SetEnvironmentVariableW(name, value);
}

static void setup_environment(void) {
    /* 先把真实家目录记下来（排查用），再把 HOME/USERPROFILE 指进包里 */
    g_tmp[0] = 0;
    GetEnvironmentVariableW(L"USERPROFILE", g_tmp, ENVBUF);
    setenv_str(L"DSH_CONSOLE_REAL_HOME", g_tmp);

    g_home[0] = 0;
    wcat(g_home, g_dir);
    wcat(g_home, L"home");

    setenv_str(L"HOME", g_home);
    setenv_str(L"USERPROFILE", g_home);
    /* HOMEDRIVE/HOMEPATH 指到空串会让某些程序拼出 "\Users\..." 这种怪路径，直接删掉 */
    SetEnvironmentVariableW(L"HOMEDRIVE", NULLP);
    SetEnvironmentVariableW(L"HOMEPATH", NULLP);

    g_tmp[0] = 0; wcat(g_tmp, g_home); wcat(g_tmp, L"\\.dsh");
    setenv_str(L"DSH_HOME", g_tmp);
    g_tmp[0] = 0; wcat(g_tmp, g_home); wcat(g_tmp, L"\\.config");
    setenv_str(L"XDG_CONFIG_HOME", g_tmp);
    g_tmp[0] = 0; wcat(g_tmp, g_home); wcat(g_tmp, L"\\.cache");
    setenv_str(L"XDG_CACHE_HOME", g_tmp);
    g_tmp[0] = 0; wcat(g_tmp, g_home); wcat(g_tmp, L"\\.local\\share");
    setenv_str(L"XDG_DATA_HOME", g_tmp);

    /* DSH_CONSOLE_PORTABLE 让控制台知道自己跑在便携包里（不带结尾反斜杠，和 .bat 一致） */
    g_tmp[0] = 0;
    wcat(g_tmp, g_dir);
    if (wlen(g_tmp) > 3) g_tmp[wlen(g_tmp) - 1] = 0;   /* 去掉结尾的 \ */
    setenv_str(L"DSH_CONSOLE_PORTABLE", g_tmp);

    /* PATH 前置包内的 node 与 harness\win\bin */
    g_tmp[0] = 0;
    wcat(g_tmp, g_dir); wcat(g_tmp, L"runtime\\win\\node;");
    wcat(g_tmp, g_dir); wcat(g_tmp, L"harness\\win\\bin;");
    static wchar old[ENVBUF];
    old[0] = 0;
    GetEnvironmentVariableW(L"PATH", old, ENVBUF);
    wcat(g_tmp, old);
    setenv_str(L"PATH", g_tmp);

    /* 中文提示别变成问号 */
    setenv_str(L"PYTHONUTF8", L"1");
    setenv_str(L"PYTHONIOENCODING", L"utf-8");
}

static void make_dirs(void) {
    static const wchar *dirs[] = {
        L"home", L"home\\Desktop", L"home\\Documents", L"home\\Downloads",
        L"home\\Pictures", L"home\\Music", L"home\\Videos",
        L"home\\AppData\\Local", L"home\\AppData\\Roaming", L"home\\AppData\\LocalLow",
        L"run",
    };
    u32 i;
    for (i = 0; i < sizeof(dirs) / sizeof(dirs[0]); i++) {
        join(g_tmp, dirs[i]);
        CreateDirectoryW(g_tmp, NULLP);      /* 已存在会失败，正是我们要的 */
    }
}

/* 跳过命令行里的第一个参数（自己的 exe 路径），剩下原样转交。
   **不重新拼接**：用户写的引号、空格、重定向都保持原样。 */
static const wchar *args_after_exe(const wchar *line) {
    i32 in_quotes = 0;
    while (*line) {
        if (*line == '"') in_quotes = !in_quotes;
        else if (*line == ' ' && !in_quotes) break;
        line++;
    }
    while (*line == ' ') line++;
    return line;
}

/* 拼命令行。改用什么 python 就重拼一次（pythonw 不在时要退回 python）。 */
static void build_cmd(const wchar *py, const wchar *app, const wchar *front) {
    g_cmd[0] = 0;
    wcatc(g_cmd, '"'); wcat(g_cmd, py);  wcatc(g_cmd, '"'); wcatc(g_cmd, ' ');
    wcatc(g_cmd, '"'); wcat(g_cmd, app); wcatc(g_cmd, '"');
    wcat(g_cmd, front);
    const wchar *extra = args_after_exe(GetCommandLineW());
    if (*extra) { wcatc(g_cmd, ' '); wcat(g_cmd, extra); }
}

void launcher_entry(void) {
    /* ---- 1. 自身路径 + **自身文件名** */
    u32 n = GetModuleFileNameW(NULLP, g_dir, DIRBUF);
    if (n == 0 || n >= DIRBUF) fail(L"取不到自身路径（GetModuleFileNameW 失败）。");
    i32 cut = -1;
    u32 i;
    for (i = 0; i < n; i++) if (g_dir[i] == '\\' || g_dir[i] == '/') cut = (i32)i;
    if (cut < 0) fail(L"自身路径里没有目录分隔符，无法定位包目录。");

    /* 文件名必须在**截断成目录之前**取走。
       ⚠️ 实测踩过这个坑：先截断、再从 g_dir 里"找最后一个 \ 之后的部分"，
       拿到的其实是**目录名**（DSH-Console-Windows），于是按名字判断前端的分支
       永远不命中——Start-DSH-Terminal.exe 起的是图形控制台，--tui 根本没传。 */
    static wchar exe_name[256];
    u32 k2 = 0, fi;
    for (fi = (u32)cut + 1; fi < n && k2 < 255; fi++) exe_name[k2++] = g_dir[fi];
    exe_name[k2] = 0;

    g_dir[cut + 1] = 0;                      /* 保留结尾的 \ */

    /* ---- 2. 环境与目录 */
    setup_environment();
    make_dirs();

    /* ---- 3. 前端由**自身文件名**决定（同一个二进制复制成几个名字就够） */
    const wchar *front = L"";
    if (wcontains_ci(exe_name, L"terminal")) front = L" --tui";
    else if (wcontains_ci(exe_name, L"web-ui")) front = L" --web";

    /* ---- 4. 拼命令行并起子进程
       顺序要紧：**先定下用哪个 python，再拼命令行**。反过来的话命令行里
       python 的路径还是空的（实测：控制台版因此把空字符串当成了 argv[0]）。 */
    static wchar py[DIRBUF], app[DIRBUF];
    join(app, L"app\\main.py");
#if DSH_GUI
    join(py, L"runtime\\win\\python\\pythonw.exe");
#else
    join(py, L"runtime\\win\\python\\python.exe");
#endif
    build_cmd(py, app, front);

    STARTUPINFOW si;
    PROCESS_INFORMATION pi;
    memset(&si, 0, sizeof(si));
    memset(&pi, 0, sizeof(pi));
    si.cb = (u32)sizeof(si);

    u32 flags = CREATE_UNICODE_ENVIRONMENT;

    if (!CreateProcessW(py, g_cmd, NULLP, NULLP, 0, flags, NULLP, NULLP, &si, &pi)) {
        u32 err = GetLastError();
#if DSH_GUI
        /* 包里没有 pythonw.exe 也别让用户卡住：退回 python.exe，
           并用 CREATE_NO_WINDOW 明确要求"别给子进程建控制台窗口"。 */
        join(py, L"runtime\\win\\python\\python.exe");
        build_cmd(py, app, front);
        if (CreateProcessW(py, g_cmd, NULLP, NULLP, 0,
                           flags | CREATE_NO_WINDOW, NULLP, NULLP, &si, &pi)) {
            goto spawned;
        }
        err = GetLastError();
#endif
        static wchar what[1024];
        what[0] = 0;
        wcat(what, L"无法启动包内的 Python：\n");
        wcat(what, py);
        wcat(what, L"\n\n系统错误码（GetLastError）：");
        /* 手写整数转十进制，没有 CRT 的 sprintf 可用 */
        wchar num[16];
        u32 k = 0, v = err;
        if (v == 0) num[k++] = '0';
        while (v) { num[k++] = (wchar)('0' + (v % 10)); v /= 10; }
        while (k) wcatc(what, num[--k]);
        fail(what);
    }

spawned:
    /* ---- 5. 等它结束，原样返回退出码 */
    WaitForSingleObject(pi.hProcess, INFINITE);
    u32 code = 0;
    GetExitCodeProcess(pi.hProcess, &code);
    CloseHandle(pi.hThread);
    CloseHandle(pi.hProcess);
    ExitProcess(code);
}
