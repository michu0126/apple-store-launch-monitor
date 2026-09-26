"""Windows launcher. Uses an installed browser; does not bundle a browser."""
import datetime
import json
import logging
import os
from pathlib import Path
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk
import webbrowser
import winreg

DATA = Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'AppleStoreMonitor'
DATA.mkdir(parents=True, exist_ok=True)
logging.basicConfig(filename=DATA / 'desktop.log', level=logging.INFO,
                    format='%(asctime)s %(levelname)s %(message)s', encoding='utf-8')


def browsers():
    found = {}
    for name, executable, relative in (
        ('Edge', 'msedge.exe', 'Microsoft/Edge/Application/msedge.exe'),
        ('Chrome', 'chrome.exe', 'Google/Chrome/Application/chrome.exe'),
    ):
        candidates = []
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(hive, 'SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\App Paths\\' + executable) as key:
                    candidates.append(Path(winreg.QueryValue(key, None)))
            except OSError:
                pass
        for variable in ('PROGRAMFILES', 'PROGRAMFILES(X86)', 'LOCALAPPDATA'):
            if os.environ.get(variable):
                candidates.append(Path(os.environ[variable]) / relative)
        for path in candidates:
            if path.is_file():
                found[name] = path
                break
    return found


def main():
    import ctypes
    mutex = ctypes.windll.kernel32.CreateMutexW(None, False, 'Local\\AppleStoreMonitorDesktop')
    if ctypes.windll.kernel32.GetLastError() == 183:
        ctypes.windll.user32.MessageBoxW(None, '监控程序已经运行，请使用已打开的窗口。', 'Apple 门店监控', 0)
        return
    root = tk.Tk()
    root.title('Apple 全国门店监控')
    root.geometry('620x370')
    root.minsize(620, 370)
    available = browsers()
    events = queue.Queue()
    stop = threading.Event()
    runtime = {}
    settings_path = DATA / 'settings.json'
    try:
        settings = json.loads(settings_path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        settings = {}
    panel = ttk.Frame(root, padding=24)
    panel.pack(fill='both', expand=True)
    ttk.Label(panel, text='Apple 全国直营店监控', font=('Microsoft YaHei UI', 18)).pack(anchor='w')
    ttk.Label(panel, text='使用电脑已安装的浏览器 · 确认后准备购物袋 · 手动提交和付款').pack(anchor='w', pady=(8, 18))
    row = ttk.Frame(panel)
    row.pack(fill='x')
    ttk.Label(row, text='浏览器').pack(side='left')
    selected = settings.get('browser')
    choice = tk.StringVar(value=selected if selected in available else next(iter(available), ''))
    browser_box = ttk.Combobox(row, textvariable=choice, values=list(available), state='readonly', width=12)
    browser_box.pack(side='left', padx=10)
    ttk.Label(row, text='取货日期 YYYY/MM/DD').pack(side='left', padx=(15, 5))
    date = tk.StringVar(value=settings.get('date', datetime.date.today().strftime('%Y/%m/%d')))
    date_box = ttk.Entry(row, textvariable=date, width=12)
    date_box.pack(side='left')
    status = tk.StringVar(value='就绪。首次启动会联网获取匹配的浏览器驱动。')
    ttk.Label(panel, textvariable=status, wraplength=560).pack(anchor='w', pady=20)
    buttons = ttk.Frame(panel)
    buttons.pack(fill='x')

    def start():
        if choice.get() not in available:
            messagebox.showerror('缺少浏览器', '请先安装 Microsoft Edge 或 Google Chrome。')
            return
        try:
            target = datetime.datetime.strptime(date.get(), '%Y/%m/%d').date()
            if target < datetime.date.today():
                raise ValueError('取货日期已过去，请选择今天或未来日期。')
        except ValueError as error:
            messagebox.showerror('日期无效', str(error))
            return
        date.set(target.strftime('%Y/%m/%d'))
        settings_path.write_text(json.dumps({'browser': choice.get(), 'date': date.get()}), encoding='utf-8')
        os.environ['DB_PATH'] = str(DATA / ('monitor-' + target.isoformat() + '.sqlite3'))
        os.environ['PICKUP_DATE'] = date.get()
        start_button.config(state='disabled')
        browser_box.config(state='disabled')
        date_box.config(state='disabled')
        status.set('正在启动本机页面和浏览器，请稍候…')
        browser_name, binary = choice.get(), str(available[choice.get()])

        def work():
            server = None
            monitor = None
            try:
                from selenium import webdriver
                from selenium.webdriver.chrome.options import Options as ChromeOptions
                from selenium.webdriver.edge.options import Options as EdgeOptions
                from selenium.webdriver.chrome.service import Service as ChromeService
                from selenium.webdriver.edge.service import Service as EdgeService
                from container_worker import Monitor
                import container_worker
                container_worker.CHUNK = 1
                from web_server import Handler, ThreadingHTTPServer, initialize, set_state, worker_action

                class DesktopMonitor(Monitor):
                    scanning = False

                    def checkpoint(self):
                        if stop.is_set():
                            raise RuntimeError('程序正在退出')
                        if self.scanning:
                            state = worker_action({'action': 'status'})['state']
                            if not state.get('monitoring', False):
                                raise RuntimeError('监控已暂停')

                    def scan(self, product):
                        self.scanning = True
                        try:
                            self.checkpoint()
                            return super().scan(product)
                        finally:
                            self.scanning = False

                    def connect(self):
                        if stop.is_set():
                            raise RuntimeError('程序正在退出')
                        if self.driver:
                            try:
                                self.driver.current_url
                                return
                            except Exception:
                                try:
                                    self.driver.quit()
                                except Exception:
                                    pass
                                self.driver = None
                        set_state('worker_message', '正在启动本机 ' + browser_name + '，首次可能需要下载驱动')
                        options = EdgeOptions() if browser_name == 'Edge' else ChromeOptions()
                        options.binary_location = binary
                        options.add_argument('--user-data-dir=' + str(DATA / ('profile-' + browser_name)))
                        options.add_argument('--lang=zh-CN')
                        options.add_argument('--window-size=1440,1000')
                        service = EdgeService() if browser_name == 'Edge' else ChromeService()
                        service.creation_flags = 0x08000000
                        factory = webdriver.Edge if browser_name == 'Edge' else webdriver.Chrome
                        self.driver = factory(options=options, service=service)
                        self.driver.set_page_load_timeout(12)
                        self.monitor_handle = self.driver.current_window_handle
                        events.put(('status', browser_name + ' 已连接，正在监控。'))

                initialize()
                set_state('worker_seen', 0)
                set_state('desktop_browser', browser_name)
                set_state('monitoring', False)
                runtime['set_state'] = set_state
                runtime['worker_action'] = worker_action
                server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
                runtime['url'] = 'http://127.0.0.1:' + str(server.server_address[1])
                threading.Thread(target=server.serve_forever, daemon=True).start()
                events.put(('ready', runtime['url']))
                monitor = DesktopMonitor()
                runtime['monitor'] = monitor
                monitor.connect()
                set_state('monitoring', True)
                next_scan = 0
                while not stop.is_set():
                    try:
                        state = worker_action({'action': 'status'})
                        monitor.open_account(state['state'])
                        if monitor.handle_job(state):
                            set_state('monitoring', False)
                        elif not state['state'].get('monitoring', True):
                            worker_action({'action': 'heartbeat', 'message': '监控已暂停，可以在浏览器中登录或操作。'})
                        elif time.monotonic() >= next_scan:
                            monitor.cycle()
                            next_scan = time.monotonic() + 8
                    except Exception as error:
                        logging.exception('Browser monitoring failed')
                        worker_action({'action': 'heartbeat', 'message': '本机浏览器连接或检查失败：' + str(error)[:300]})
                        events.put(('status', '浏览器启动或检查失败，详见监控页面及 desktop.log。'))
                    stop.wait(.5)
            except Exception as error:
                logging.exception('Desktop startup failed')
                events.put(('error', str(error)))
            finally:
                if monitor and monitor.driver:
                    try:
                        monitor.driver.quit()
                    except Exception:
                        pass
                if server:
                    server.shutdown()
                    server.server_close()
                events.put(('stopped', ''))
        runtime['thread'] = threading.Thread(target=work, daemon=True)
        runtime['thread'].start()

    start_button = ttk.Button(buttons, text='启动监控', command=start)
    start_button.pack(side='left')
    open_button = ttk.Button(buttons, text='打开监控页面', state='disabled', command=lambda: webbrowser.open(runtime['url']))
    open_button.pack(side='left', padx=10)
    def toggle_monitor():
        if runtime.get('set_state'):
            state = runtime['worker_action']({'action': 'status'})['state']
            runtime['set_state']('monitoring', not state.get('monitoring', False))
    pause_button = ttk.Button(buttons, text='暂停监控', state='disabled', command=toggle_monitor)
    pause_button.pack(side='left', padx=5)
    ttk.Button(buttons, text='打开数据与日志目录', command=lambda: os.startfile(DATA)).pack(side='left')
    ttk.Label(panel, text='请保持本程序运行。Apple 登录使用独立浏览器窗口，不读取日常浏览器资料。\n关闭窗口将停止监控；登录配置与监控数据保存在本机。', wraplength=560).pack(anchor='w', pady=18)

    def close():
        stop.set()
        status.set('正在停止监控并关闭专用浏览器，请稍候…')
        start_button.config(state='disabled')
        if not runtime.get('thread') or not runtime['thread'].is_alive():
            root.destroy()

    def poll():
        if runtime.get('worker_action') and not stop.is_set():
            state = runtime['worker_action']({'action': 'status'})['state']
            pause_button.config(state='normal', text='暂停监控' if state.get('monitoring') else '继续监控')
            if state.get('worker_seen', 0):
                status.set(state.get('worker_message', ''))
        try:
            while True:
                kind, value = events.get_nowait()
                if kind == 'ready':
                    open_button.config(state='normal')
                    status.set('监控页面已启动；正在连接 ' + choice.get() + '。')
                    webbrowser.open(value)
                elif kind == 'error':
                    status.set('启动失败：' + value)
                elif kind == 'status':
                    status.set(value)
                elif kind == 'stopped' and stop.is_set():
                    root.destroy()
                    return
        except queue.Empty:
            pass
        root.after(300, poll)
    root.protocol('WM_DELETE_WINDOW', close)
    poll()
    root.mainloop()


if __name__ == '__main__':
    if '--self-test' in sys.argv:
        from web_server import ROOT, CATALOG
        assert (ROOT / 'web' / 'index.html').is_file()
        assert CATALOG
        from selenium.webdriver.common.selenium_manager import SeleniumManager
        assert SeleniumManager._get_binary().is_file()
        (DATA / 'self-test.json').write_text(json.dumps({'ok': True, 'browsers': list(browsers()), 'catalog_count': len(CATALOG)}), encoding='utf-8')
    else:
        main()
