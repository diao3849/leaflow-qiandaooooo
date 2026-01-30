#!/usr/bin/env python3
import os
import time
import logging
import json
import subprocess
import urllib.parse
from datetime import datetime
import requests

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.action_chains import ActionChains
from selenium.common.exceptions import TimeoutException, NoSuchElementException

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class LeaflowAutoCheckin:
    def __init__(self, email, password):
        self.email = email
        self.password = password
        self.telegram_bot_token = os.getenv('TELEGRAM_BOT_TOKEN', '')
        self.telegram_chat_id = os.getenv('TELEGRAM_CHAT_ID', '')
        self.proxy_process = None
        self.local_proxy_port = 10808
        
        if not self.email or not self.password:
            raise ValueError("邮箱和密码不能为空")
        
        self.driver = None
        
    def parse_hy2_url(self, url):
        """解析 hysteria2:// 链接为配置字典"""
        try:
            parsed = urllib.parse.urlparse(url)
            password = parsed.username if parsed.username else parsed.password
            server_addr = parsed.netloc.split('@')[-1]
            params = urllib.parse.parse_qs(parsed.query)
            
            config = {
                "server": server_addr,
                "auth": password,
                "tls": {
                    "sni": params.get('sni', [''])[0],
                    "insecure": params.get('insecure', ['0'])[0] == '1'
                },
                "socks5": {
                    "listen": f"127.0.0.1:{self.local_proxy_port}"
                },
                "transport": {
                    "type": "udp",
                    "udp": {"hop": True} if 'hop' in params else {}
                }
            }
            return config
        except Exception as e:
            logger.error(f"解析代理URL失败: {e}")
            return None

    def start_proxy(self):
        """启动 Hysteria2 客户端进程"""
        hy2_url = os.getenv('PROXY_HY2')
        if not hy2_url:
            return False

        config = self.parse_hy2_url(hy2_url)
        if not config: return False

        try:
            with open('hy2_config.json', 'w') as f:
                json.dump(config, f)
            
            logger.info("正在启动 Hysteria2 代理转换器...")
            # 注意：系统需要已安装 hysteria 命令
            self.proxy_process = subprocess.Popen(
                ["hysteria", "client", "-c", "hy2_config.json"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            time.sleep(5)  # 等待连接建立
            return True
        except Exception as e:
            logger.error(f"启动代理进程异常: {e}")
            return False

    def setup_driver(self):
        """设置驱动并绑定代理"""
        chrome_options = Options()
        
        # 尝试启动 Hy2 代理并应用
        if self.start_proxy():
            logger.info(f"代理已就绪: socks5://127.0.0.1:{self.local_proxy_port}")
            chrome_options.add_argument(f'--proxy-server=socks5://127.0.0.1:{self.local_proxy_port}')

        if os.getenv('GITHUB_ACTIONS') or True: # 强制开启无头模式
            chrome_options.add_argument('--headless')
            chrome_options.add_argument('--no-sandbox')
            chrome_options.add_argument('--disable-dev-shm-usage')
            chrome_options.add_argument('--disable-gpu')
            chrome_options.add_argument('--window-size=1920,1080')
            chrome_options.add_argument('--ignore-certificate-errors')
        
        chrome_options.add_argument('--disable-blink-features=AutomationControlled')
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option('useAutomationExtension', False)
        
        self.driver = webdriver.Chrome(options=chrome_options)
        self.driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

    def login(self):
        logger.info(f"开始登录 [{self.email}]")
        self.driver.get("https://leaflow.net/login")
        time.sleep(5)
        
        try:
            # 邮箱输入
            email_input = WebDriverWait(self.driver, 20).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='text'], input[type='email']"))
            )
            email_input.send_keys(self.email)
            
            # 密码输入
            pass_input = self.driver.find_element(By.CSS_SELECTOR, "input[type='password']")
            pass_input.send_keys(self.password)
            
            # 登录按钮
            login_btn = self.driver.find_element(By.CSS_SELECTOR, "button[type='submit'], .login-btn")
            login_btn.click()
            
            WebDriverWait(self.driver, 20).until(lambda d: "login" not in d.current_url)
            logger.info("登录跳转成功")
            return True
        except Exception as e:
            logger.error(f"登录失败: {e}")
            return False

    def checkin(self):
        logger.info("执行签到流程...")
        self.driver.get("https://checkin.leaflow.net")
        time.sleep(8) # 给足够时间加载
        
        try:
            # 尝试定位签到按钮
            btn = WebDriverWait(self.driver, 15).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "button.checkin-btn, button[class*='checkin']"))
            )
            
            if "已签到" in btn.text:
                return "今天已经签到过了"
            
            btn.click()
            time.sleep(3)
            return "签到操作完成"
        except Exception as e:
            return f"签到失败或找不到按钮: {str(e)[:50]}"

    def get_balance(self):
        try:
            self.driver.get("https://leaflow.net/dashboard")
            time.sleep(3)
            body = self.driver.find_element(By.TAG_NAME, "body").text
            import re
            m = re.search(r'(¥|￥|余额)\s*(\d+\.?\d*)', body)
            return f"{m.group(2)}元" if m else "未知"
        except:
            return "获取失败"

    def run(self):
        try:
            self.setup_driver()
            if self.login():
                res = self.checkin()
                bal = self.get_balance()
                return True, res, bal
            return False, "登录失败", "0"
        except Exception as e:
            return False, str(e), "0"
        finally:
            if self.driver: self.driver.quit()
            if self.proxy_process: self.proxy_process.terminate()

class MultiAccountManager:
    def __init__(self):
        self.accounts = []
        raw = os.getenv('LEAFLOW_ACCOUNTS', '')
        for pair in raw.split(','):
            if ':' in pair:
                e, p = pair.split(':', 1)
                self.accounts.append({'email': e.strip(), 'password': p.strip()})

    def send_tg(self, results):
        token = os.getenv('TELEGRAM_BOT_TOKEN')
        chat_id = os.getenv('TELEGRAM_CHAT_ID')
        if not token or not chat_id: return
        
        msg = f"🎁 Leaflow 签到报告\n📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
        for email, success, res, bal in results:
            status = "✅" if success else "❌"
            msg += f"账号: {email[:3]}***\n{status} 状态: {res}\n💰 余额: {bal}\n\n"
        
        requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={"chat_id": chat_id, "text": msg})

    def run_all(self):
        final_results = []
        for acc in self.accounts:
            bot = LeaflowAutoCheckin(acc['email'], acc['password'])
            success, res, bal = bot.run()
            final_results.append((acc['email'], success, res, bal))
            time.sleep(5)
        self.send_tg(final_results)

if __name__ == "__main__":
    MultiAccountManager().run_all()