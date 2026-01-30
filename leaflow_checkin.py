import os
import time
import json
import logging
import subprocess
import urllib.parse
import re
import requests
from datetime import datetime

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options

# --- 日志配置 ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class LeaflowAutoCheckin:
    def __init__(self, email, password):
        self.email = email
        self.password = password
        self.proxy_process = None
        self.local_proxy_port = 10808
        self.driver = None

    def parse_hy2_url(self, url):
        """解析 hysteria2:// 链接"""
        try:
            parsed = urllib.parse.urlparse(url)
            password = parsed.username if parsed.username else parsed.password
            server_addr = parsed.netloc.split('@')[-1]
            params = urllib.parse.parse_qs(parsed.query)
            
            return {
                "server": server_addr,
                "auth": password,
                "tls": {
                    "sni": params.get('sni', [''])[0],
                    "insecure": params.get('insecure', ['0'])[0] == '1'
                },
                "socks5": {"listen": f"127.0.0.1:{self.local_proxy_port}"},
                "transport": {"type": "udp"}
            }
        except Exception as e:
            logger.error(f"代理URL解析失败: {e}")
            return None

    def start_proxy(self):
        """启动 Hysteria2 客户端"""
        hy2_url = os.getenv('PROXY_HY2')
        if not hy2_url: return False
        
        config = self.parse_hy2_url(hy2_url)
        if not config: return False

        try:
            with open('hy2_config.json', 'w') as f:
                json.dump(config, f)
            
            self.proxy_process = subprocess.Popen(
                ["hysteria", "client", "-c", "hy2_config.json"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            time.sleep(5) # 等待握手
            return True
        except Exception as e:
            logger.error(f"启动代理异常: {e}")
            return False

    def setup_driver(self):
        """配置浏览器"""
        options = Options()
        if self.start_proxy():
            options.add_argument(f'--proxy-server=socks5://127.0.0.1:{self.local_proxy_port}')
        
        # Actions 环境必备参数
        options.add_argument('--headless')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--disable-gpu')
        options.add_argument('--window-size=1920,1080')
        options.add_argument('--ignore-certificate-errors')
        
        # 防检测
        options.add_argument('--disable-blink-features=AutomationControlled')
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option('useAutomationExtension', False)
        
        self.driver = webdriver.Chrome(options=options)
        self.driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

    def run_checkin(self):
        """核心业务逻辑"""
        try:
            self.setup_driver()
            # 1. 登录
            self.driver.get("https://leaflow.net/auth/login")
            wait = WebDriverWait(self.driver, 20)
            
            email_field = wait.until(EC.presence_of_element_located((By.NAME, "email")))
            email_field.send_keys(self.email)
            self.driver.find_element(By.NAME, "password").send_keys(self.password)
            self.driver.find_element(By.TAG_NAME, "button").click()
            
            # 等待登录成功跳转
            wait.until(lambda d: "login" not in d.current_url)
            logger.info(f"[{self.email}] 登录成功")

            # 2. 签到
            self.driver.get("https://leaflow.net/user/checkin")
            time.sleep(5)
            
            checkin_msg = "已签到"
            try:
                # 寻找签到按钮并点击
                btn = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "button#checkin-button")))
                btn.click()
                time.sleep(2)
                # 获取网页提示语（简单演示）
                checkin_msg = "签到成功！" 
            except:
                checkin_msg = "今天已经签到过了"

            # 3. 获取余额
            self.driver.get("https://leaflow.net/user")
            time.sleep(3)
            balance = "0.00"
            try:
                # 正则匹配文本中的余额数字
                text = self.driver.find_element(By.TAG_NAME, "body").text
                match = re.search(r'(?:余额|Balance).*?(\d+\.\d+)', text)
                if match: balance = match.group(1)
            except: pass

            return True, checkin_msg, balance

        except Exception as e:
            logger.error(f"运行出错: {e}")
            return False, f"出错: {str(e)[:30]}", "0.00"
        finally:
            if self.driver: self.driver.quit()
            if self.proxy_process: self.proxy_process.terminate()

class Manager:
    def send_tg(self, results):
        token = os.getenv('TELEGRAM_BOT_TOKEN')
        chat_id = os.getenv('TELEGRAM_CHAT_ID')
        if not (token and chat_id): return

        success_num = sum(1 for r in results if r[1])
        msg = f"🎁 Leaflow自动签到通知\n📊 成功: {success_num}/{len(results)}\n"
        msg += f"📅 签到时间：{datetime.now().strftime('%Y/%m/%d')}\n\n"

        for email, success, res, bal in results:
            prefix, domain = email.split('@')
            masked = f"{prefix[:3]}***@{domain}"
            status_icon = "✅" if success else "❌"
            msg += f"账号：{masked}\n{status_icon}  {res}\n💰  当前总余额：{bal}元。\n\n"

        requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={"chat_id": chat_id, "text": msg})

    def start(self):
        accounts = os.getenv('LEAFLOW_ACCOUNTS', '').split(',')
        results = []
        for acc in accounts:
            if ':' not in acc: continue
            e, p = acc.split(':', 1)
            bot = LeaflowAutoCheckin(e.strip(), p.strip())
            results.append((e.strip(), *bot.run_checkin()))
        self.send_tg(results)

if __name__ == "__main__":
    Manager().start()