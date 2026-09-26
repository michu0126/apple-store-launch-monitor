"""Docker worker: inspect Apple China with a visible Firefox WebDriver session."""
import datetime
import os
import re
import time

from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.support.ui import WebDriverWait

from web_server import CATALOG, LAUNCH_DATE, set_state, worker_action, pickup_matches_date

STORES = ['香港广场', '南京东路', '上海环贸 iapm', '浦东', '静安', '环球港', '五角场', '七宝']
CHUNK = 8


def launch_pickup(text):
    match = re.search(r'(?:星期.\s*)?(?:(?:\d{4}[/-]\d{1,2}[/-]\d{1,2})|(?:\d{1,2}\s*月\s*\d{1,2}\s*日)|今日|明日)[^，。；;]*?可取货', text)
    pickup = match.group(0) if match else ''
    on_day = pickup_matches_date(pickup)
    return pickup, on_day


class Monitor:
    def __init__(self):
        self.driver = None
        self.monitor_handle = None
        self.account_seen = 0

    def connect(self):
        if self.driver:
            try:
                _ = self.driver.current_url
                return
            except WebDriverException:
                try:
                    self.driver.quit()
                except Exception:
                    pass
        options = Options()
        options.set_preference('intl.accept_languages', 'zh-CN,zh')
        options.set_preference('dom.webnotifications.enabled', False)
        options.add_argument('-width=1440')
        options.add_argument('-height=1000')
        endpoint = os.environ.get('WEBDRIVER_URL', 'http://firefox:4444/wd/hub')
        while True:
            try:
                self.driver = webdriver.Remote(command_executor=endpoint, options=options)
                self.driver.set_page_load_timeout(35)
                self.monitor_handle = self.driver.current_window_handle
                return
            except WebDriverException as error:
                set_state('worker_message', '等待 Docker Firefox 就绪：' + str(error)[:180])
                time.sleep(5)

    def monitor_tab(self):
        self.connect()
        if self.monitor_handle not in self.driver.window_handles:
            self.driver.switch_to.new_window('tab')
            self.monitor_handle = self.driver.current_window_handle
        self.driver.switch_to.window(self.monitor_handle)

    def navigate(self, url):
        self.monitor_tab()
        try:
            self.driver.get(url)
        except TimeoutException:
            self.driver.execute_script('window.stop()')
        WebDriverWait(self.driver, 15).until(lambda d: d.execute_script('return document.readyState') in ('interactive', 'complete'))

    def checkpoint(self):
        pass

    def text_button(self, pattern, root=None):
        self.checkpoint()
        root = root or self.driver
        for button in root.find_elements(By.CSS_SELECTOR, 'button'):
            try:
                if button.is_displayed() and re.search(pattern, button.text.strip()):
                    return button
            except WebDriverException:
                continue
        return None

    def open_availability(self):
        deadline = time.time() + 6
        while time.time() < deadline:
            button = self.text_button(r'查看供货情况')
            if button:
                self.driver.execute_script('arguments[0].click()', button)
                return WebDriverWait(self.driver, 15).until(lambda d: next((x for x in d.find_elements(By.CSS_SELECTOR, '[role="dialog"]') if x.is_displayed()), None))
            time.sleep(.25)
        body = self.driver.find_element(By.TAG_NAME, 'body').text
        if '添加到购物袋' in body or '结账时会显示你所在地区的送货详情' in body:
            return None
        raise RuntimeError('Apple 页面没有可读取的门店取货入口')

    def select_shanghai(self, dialog):
        if re.search(r'前往此地附近的 Apple Store 零售店取货：\s*上海', dialog.text):
            return
        button = self.text_button(r'^(选择地点|上海)$', dialog)
        if button:
            self.driver.execute_script('arguments[0].click()', button)
        for label in ('上海', '黄浦区'):
            chosen = WebDriverWait(self.driver, 10).until(lambda d: self.text_button(r'^' + label + r'$'))
            self.driver.execute_script('arguments[0].click()', chosen)
        WebDriverWait(self.driver, 10).until(lambda d: '上海' in dialog.text)

    def radio_text(self, element):
        return self.driver.execute_script("""
          const el=arguments[0], id=el.id, label=id?document.querySelector(`label[for="${CSS.escape(id)}"]`):null;
          return (el.getAttribute('aria-label')||label?.innerText||el.closest('label')?.innerText||el.parentElement?.innerText||'').replace(/\\s+/g,' ').trim();
        """, element)

    def read_stores(self, dialog):
        self.checkpoint()
        time.sleep(.7)
        radios = dialog.find_elements(By.CSS_SELECTOR, '[role="radio"],input[type="radio"]')
        if not any('Apple ' in self.radio_text(row) for row in radios):
            summary = self.text_button(r'家零售店今日无货', dialog)
            if summary:
                self.driver.execute_script('arguments[0].click()', summary)
                time.sleep(.4)
                radios = dialog.find_elements(By.CSS_SELECTOR, '[role="radio"],input[type="radio"]')
        result = []
        for store in STORES:
            row = next((item for item in radios if 'Apple ' + store in self.radio_text(item)), None)
            if not row:
                continue
            text = self.radio_text(row)
            pickup, on_day = launch_pickup(text)
            available = '可取货' in text and not re.search(r'暂无供应|不可取货', text) and row.is_enabled() and on_day
            result.append({'store': store, 'available': bool(available), 'pickup': pickup})
        if not result and '家零售店今日无货' in dialog.text:
            return [{'store': store, 'available': False, 'pickup': ''} for store in STORES]
        if not result:
            raise RuntimeError('未能读取上海直营店列表')
        return result

    def scan(self, product):
        self.navigate(product['url'])
        self.checkpoint()
        body = self.driver.find_element(By.TAG_NAME, 'body').text
        if re.search(r'Page Not Found|页面未找到|无法访问此网站', body):
            raise RuntimeError('Apple 拒绝了本次查询')
        dialog = self.open_availability()
        if not dialog:
            return [{'store': store, 'available': False, 'pickup': ''} for store in STORES]
        self.select_shanghai(dialog)
        return self.read_stores(dialog)

    def prepare(self, product, store_name, expected_pickup):
        self.navigate(product['url'])
        dialog = self.open_availability()
        if not dialog:
            raise RuntimeError('首发日门店取货入口已关闭')
        self.select_shanghai(dialog)
        stores = self.read_stores(dialog)
        store = next((item for item in stores if item['store'] == store_name), None)
        if not store or not store['available'] or store['pickup'] != expected_pickup:
            raise RuntimeError('首发日门店库存或取货时间已变化')
        radios = dialog.find_elements(By.CSS_SELECTOR, '[role="radio"],input[type="radio"]')
        row = next((item for item in radios if 'Apple ' + store_name in self.radio_text(item)), None)
        if not row:
            raise RuntimeError('无法选择已确认的 Apple Store')
        self.driver.execute_script('arguments[0].click()', row)
        time.sleep(.3)
        confirm = self.text_button(r'选择此零售店|选取此零售店|继续|完成|确认', dialog)
        if confirm and confirm.is_enabled():
            self.driver.execute_script('arguments[0].click()', confirm)
        add = WebDriverWait(self.driver, 10).until(lambda d: self.text_button(r'^添加到购物袋$'))
        self.driver.execute_script('arguments[0].click()', add)
        time.sleep(3)
        self.driver.get('https://www.apple.com.cn/shop/bag')

    def open_account(self, state):
        requested = float(state.get('account_requested', 0) or 0)
        if requested <= self.account_seen:
            return
        self.account_seen = requested
        self.connect()
        self.driver.switch_to.new_window('tab')
        self.driver.get('https://secure.www.apple.com.cn/shop/account/home')
        set_state('account', 'opened')

    def handle_job(self, status):
        claimed = worker_action({'action': 'claim'}).get('job')
        if not claimed:
            return False
        product = next((item for item in status['catalog'] if item['part'] == claimed['part']), None)
        try:
            if not product:
                raise RuntimeError('商品目录中已找不到该配置')
            self.prepare(product, claimed['store'], claimed['pickup'])
            worker_action({'action': 'job', 'id': claimed['id'], 'status': 'completed',
                           'message': '目标日期库存已复核，商品已加入 Apple 购物袋。请在执行下单的浏览器窗口核对并完成提交与付款。'})
            self.monitor_handle = None
        except Exception as error:
            worker_action({'action': 'job', 'id': claimed['id'], 'status': 'failed', 'message': '复核失败：' + str(error)[:420]})
        return True

    def cycle(self):
        status = worker_action({'action': 'status'})
        state = status['state']
        self.open_account(state)
        if self.handle_job(status):
            return
        if not state.get('monitoring', True):
            worker_action({'action': 'heartbeat', 'cursor': state.get('cursor', 0), 'message': 'Docker 监控已暂停'})
            return
        start = int(state.get('cursor', 0)) % len(CATALOG)
        checked = failures = available = 0
        for offset in range(CHUNK):
            product = CATALOG[(start + offset) % len(CATALOG)]
            try:
                stores = self.scan(product)
                worker_action({'action': 'stock', 'part': product['part'], 'stores': stores})
                checked += 1
                available += sum(1 for item in stores if item['available'])
            except Exception:
                failures += 1
        cursor = (start + CHUNK) % len(CATALOG)
        message = f'浏览器在线 · 本轮检查 {checked}/{CHUNK} 个配置'
        if failures:
            message += f'，{failures} 个查询失败并保持未知'
        if available:
            message += f'，发现 {available} 个首发日可取货门店选项'
        worker_action({'action': 'heartbeat', 'cursor': cursor, 'message': message})

    def run(self):
        while True:
            started = time.time()
            try:
                self.cycle()
            except Exception as error:
                worker_action({'action': 'heartbeat', 'message': 'Docker 监控暂时失败：' + str(error)[:240]})
                self.driver = None
            time.sleep(max(5, 60 - (time.time() - started)))


if __name__ == '__main__':
    Monitor().run()
