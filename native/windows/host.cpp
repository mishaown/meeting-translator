// Thin native WebView2 host. Python owns recording, inference and the loopback server.
#include <windows.h>
#include <shellapi.h>
#include <wrl.h>
#include <WebView2.h>
#include <algorithm>
#include <filesystem>
#include <iostream>
#include <string>

using Microsoft::WRL::Callback;
using Microsoft::WRL::ComPtr;

class WindowHost {
public:
    HWND window = nullptr;
    std::wstring origin;
    std::wstring profile;
    bool smoke = false;
    int exit_code = 0;
    HANDLE parent = nullptr;

    ~WindowHost() {
        if (controller) controller->Close();
        view.Reset();
        controller.Reset();
        environment.Reset();
        if (loader) FreeLibrary(loader);
        if (parent) CloseHandle(parent);
    }

    void fail(const wchar_t* message, HRESULT error = E_FAIL) {
        if (closing) return;
        std::wcerr << message << L" (HRESULT 0x" << std::hex
                   << static_cast<unsigned long>(error) << L")\n";
        exit_code = 1;
        if (!smoke) MessageBoxW(window, message, L"Meeting Translator", MB_OK | MB_ICONERROR);
        if (window) DestroyWindow(window);
        else PostQuitMessage(exit_code);
    }

    void resize() {
        if (!controller || !window) return;
        RECT bounds{};
        GetClientRect(window, &bounds);
        controller->put_Bounds(bounds);
    }

    static LRESULT CALLBACK procedure(HWND hwnd, UINT message, WPARAM wparam, LPARAM lparam) {
        auto* self = reinterpret_cast<WindowHost*>(GetWindowLongPtrW(hwnd, GWLP_USERDATA));
        if (message == WM_NCCREATE) {
            self = static_cast<WindowHost*>(reinterpret_cast<CREATESTRUCTW*>(lparam)->lpCreateParams);
            SetWindowLongPtrW(hwnd, GWLP_USERDATA, reinterpret_cast<LONG_PTR>(self));
        }
        if (self) {
            switch (message) {
            case WM_SIZE:
                self->resize();
                return 0;
            case WM_GETMINMAXINFO: {
                auto* limits = reinterpret_cast<MINMAXINFO*>(lparam);
                const UINT dpi = GetDpiForWindow(hwnd);
                limits->ptMinTrackSize = {MulDiv(900, dpi, 96), MulDiv(650, dpi, 96)};
                return 0;
            }
            case WM_DPICHANGED: {
                const auto* suggested = reinterpret_cast<RECT*>(lparam);
                SetWindowPos(hwnd, nullptr, suggested->left, suggested->top,
                             suggested->right - suggested->left, suggested->bottom - suggested->top,
                             SWP_NOZORDER | SWP_NOACTIVATE);
                return 0;
            }
            case WM_TIMER:
                if (wparam == 1) self->fail(L"The desktop interface did not finish loading within 40 seconds.");
                else if (wparam == 2 && self->parent && WaitForSingleObject(self->parent, 0) == WAIT_OBJECT_0) DestroyWindow(hwnd);
                return 0;
            case WM_CLOSE:
                DestroyWindow(hwnd);
                return 0;
            case WM_DESTROY:
                self->closing = true;
                if (self->controller) self->controller->Close();
                PostQuitMessage(self->exit_code);
                return 0;
            }
        }
        return DefWindowProcW(hwnd, message, wparam, lparam);
    }

    bool local_url(const std::wstring& uri) const {
        return uri == origin || uri.rfind(origin + L"/", 0) == 0
            || uri.rfind(origin + L"?", 0) == 0 || uri.rfind(origin + L"#", 0) == 0;
    }

    HRESULT attach(ICoreWebView2Controller* created) {
        controller = created;
        HRESULT status = controller->get_CoreWebView2(&view);
        if (FAILED(status)) return status;
        resize();
        controller->put_IsVisible(TRUE);
        ComPtr<ICoreWebView2Settings> settings;
        if (SUCCEEDED(view->get_Settings(&settings))) {
            settings->put_IsStatusBarEnabled(FALSE);
            settings->put_AreDevToolsEnabled(FALSE);
        }
        status = view->add_NavigationStarting(
            Callback<ICoreWebView2NavigationStartingEventHandler>(
                [this](ICoreWebView2*, ICoreWebView2NavigationStartingEventArgs* args) -> HRESULT {
                    LPWSTR raw = nullptr;
                    if (SUCCEEDED(args->get_Uri(&raw))) {
                        const std::wstring uri(raw ? raw : L"");
                        CoTaskMemFree(raw);
                        if (!local_url(uri)) {
                            args->put_Cancel(TRUE);
                            BOOL user = FALSE;
                            args->get_IsUserInitiated(&user);
                            if (user && (uri.rfind(L"https://", 0) == 0 || uri.rfind(L"http://", 0) == 0))
                                ShellExecuteW(window, L"open", uri.c_str(), nullptr, nullptr, SW_SHOWNORMAL);
                        }
                    }
                    return S_OK;
                }).Get(), &navigation_starting);
        if (FAILED(status)) return status;
        if (smoke) {
            status = view->add_NavigationCompleted(
                Callback<ICoreWebView2NavigationCompletedEventHandler>(
                    [this](ICoreWebView2*, ICoreWebView2NavigationCompletedEventArgs* args) -> HRESULT {
                        BOOL success = FALSE;
                        args->get_IsSuccess(&success);
                        if (!success) {
                            fail(L"The local desktop page could not be loaded.");
                            return S_OK;
                        }
                        const HRESULT check = view->ExecuteScript(
                            L"!!document.getElementById('start') && !!document.getElementById('setup-open')",
                            Callback<ICoreWebView2ExecuteScriptCompletedHandler>(
                                [this](HRESULT error, LPCWSTR value) -> HRESULT {
                                    if (FAILED(error) || !value || std::wstring(value) != L"true")
                                        fail(L"The desktop page failed its interface check.", error);
                                    else {
                                        KillTimer(window, 1);
                                        PostMessageW(window, WM_CLOSE, 0, 0);
                                    }
                                    return S_OK;
                                }).Get());
                        if (FAILED(check)) fail(L"The desktop interface check could not start.", check);
                        return S_OK;
                    }).Get(), &navigation_completed);
            if (FAILED(status)) return status;
        }
        return view->Navigate(origin.c_str());
    }

    int run(HINSTANCE instance, int show) {
        WNDCLASSW klass{};
        klass.lpfnWndProc = procedure;
        klass.hInstance = instance;
        klass.lpszClassName = L"MeetingTranslatorNativeWindow";
        klass.hCursor = LoadCursorW(nullptr, IDC_ARROW);
        klass.hIcon = LoadIconW(nullptr, IDI_APPLICATION);
        RegisterClassW(&klass);
        RECT work{};
        SystemParametersInfoW(SPI_GETWORKAREA, 0, &work, 0);
        const UINT dpi = GetDpiForSystem();
        window = CreateWindowExW(0, klass.lpszClassName, L"Meeting Translator",
            WS_OVERLAPPEDWINDOW, CW_USEDEFAULT, CW_USEDEFAULT,
            std::min<int>(MulDiv(1280, dpi, 96), work.right - work.left - 40),
            std::min<int>(MulDiv(850, dpi, 96), work.bottom - work.top - 40),
            nullptr, nullptr, instance, this);
        if (!window) return 1;
        if (!smoke) ShowWindow(window, show);
        else SetTimer(window, 1, 40000, nullptr);
        if (parent) SetTimer(window, 2, 1000, nullptr);

        wchar_t executable[32768]{};
        GetModuleFileNameW(nullptr, executable, 32768);
        const auto dll = std::filesystem::path(executable).parent_path() / L"WebView2Loader.dll";
        loader = LoadLibraryW(dll.c_str());
        using CreateEnvironment = HRESULT(STDAPICALLTYPE*)(PCWSTR, PCWSTR, ICoreWebView2EnvironmentOptions*, ICoreWebView2CreateCoreWebView2EnvironmentCompletedHandler*);
        const auto create = loader ? reinterpret_cast<CreateEnvironment>(GetProcAddress(loader, "CreateCoreWebView2EnvironmentWithOptions")) : nullptr;
        if (!create) fail(L"The ARM64 WebView2 loader is missing or could not be loaded.");
        else {
            const HRESULT status = create(nullptr, profile.c_str(), nullptr,
                Callback<ICoreWebView2CreateCoreWebView2EnvironmentCompletedHandler>(
                    [this](HRESULT error, ICoreWebView2Environment* created) -> HRESULT {
                        if (closing) return S_OK;
                        if (FAILED(error) || !created) {
                            fail(L"WebView2 could not start. Install the Microsoft Edge WebView2 ARM64 Runtime.", error);
                            return S_OK;
                        }
                        environment = created;
                        const HRESULT status = environment->CreateCoreWebView2Controller(window,
                            Callback<ICoreWebView2CreateCoreWebView2ControllerCompletedHandler>(
                                [this](HRESULT error, ICoreWebView2Controller* created) -> HRESULT {
                                    if (closing) return S_OK;
                                    if (FAILED(error) || !created) fail(L"The desktop window could not be initialized.", error);
                                    else {
                                        const HRESULT status = attach(created);
                                        if (FAILED(status)) fail(L"The desktop interface could not be attached.", status);
                                    }
                                    return S_OK;
                                }).Get());
                        if (FAILED(status)) fail(L"The desktop controller could not start.", status);
                        return S_OK;
                    }).Get());
            if (FAILED(status)) fail(L"The WebView2 environment could not start.", status);
        }
        MSG message{};
        while (GetMessageW(&message, nullptr, 0, 0) > 0) {
            TranslateMessage(&message);
            DispatchMessageW(&message);
        }
        return exit_code;
    }

private:
    HMODULE loader = nullptr;
    bool closing = false;
    ComPtr<ICoreWebView2Environment> environment;
    ComPtr<ICoreWebView2Controller> controller;
    ComPtr<ICoreWebView2> view;
    EventRegistrationToken navigation_starting{};
    EventRegistrationToken navigation_completed{};
};

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE, PWSTR, int show) {
    int count = 0;
    LPWSTR* arguments = CommandLineToArgvW(GetCommandLineW(), &count);
    if (!arguments || (count != 4 && count != 5)) return 2;
    const std::wstring origin(arguments[1]);
    const std::wstring profile(arguments[2]);
    const std::wstring parent_id(arguments[3]);
    const bool smoke = count == 5 && std::wstring(arguments[4]) == L"--smoke-test";
    LocalFree(arguments);
    const std::wstring prefix = L"http://127.0.0.1:";
    if (origin.rfind(prefix, 0) != 0) return 2;
    const std::wstring port = origin.substr(prefix.size());
    if (port.empty() || port.size() > 5 || port.find_first_not_of(L"0123456789") != std::wstring::npos) return 2;
    if (std::stoi(port) < 1 || std::stoi(port) > 65535) return 2;
    if (parent_id.empty() || parent_id.size() > 10 || parent_id.find_first_not_of(L"0123456789") != std::wstring::npos) return 2;
    if (FAILED(CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED))) return 1;
    SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
    int result = 1;
    {
        WindowHost host;
        host.origin = origin;
        host.profile = profile;
        host.smoke = smoke;
        host.parent = OpenProcess(SYNCHRONIZE, FALSE, static_cast<DWORD>(std::stoull(parent_id)));
        if (!host.parent) {
            CoUninitialize();
            return 1;
        }
        result = host.run(instance, show);
    }
    CoUninitialize();
    return result;
}
