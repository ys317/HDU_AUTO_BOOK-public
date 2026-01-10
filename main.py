import requests
import yaml
import random
from datetime import datetime, timedelta
import json
import os
import logging
import time

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.wait import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

logging.basicConfig(
                    format='%(asctime)s,%(msecs)d %(name)s %(levelname)s %(message)s',
                    datefmt='%H:%M:%S',
                    level=logging.DEBUG)

time_zone = 8  # 时区

def get_seats_with_config(user_config, date_config, seat_config):
    seat_name = date_config['name']
    if seat_name == "自定义":
        return user_config['自定义']
    return list(range(seat_config[seat_name]['begin'], seat_config[seat_name]['end']))

class SeatAutoBooker:
    def __init__(self, booker_config):
        self.json = None
        self.resp = None
        self.user_data = None

        logging.info('Creating SeatAutoBooker object')

        # === 1. 改回从环境变量读取 (安全) ===
        try:
            self.un = os.environ["SCHOOL_ID"].strip()
            self.pd = os.environ["PASSWORD"].strip()
        except KeyError:
            logging.error("未找到环境变量 SCHOOL_ID 或 PASSWORD，请在 GitHub Secrets 中配置！")
            exit(1)

        self.SCKey = os.environ.get("SCKEY", "")

        chrome_options = Options()
        # === 2. 恢复 Headless 模式 (服务器必须用这个) ===
        chrome_options.add_argument('--headless') 
        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-dev-shm-usage')
        # 增加防检测参数
        chrome_options.add_argument('--disable-blink-features=AutomationControlled')
        
        # 自动安装驱动
        service = Service(ChromeDriverManager().install())
        self.driver = webdriver.Chrome(service=service, options=chrome_options)
        
        self.wait = WebDriverWait(self.driver, 20, 0.5) # 延长等待时间适应服务器网络
        self.cookie = None
        self.cfg = booker_config

    def book_favorite_seat(self, user_config, seat_config):
        the_day_after_tomorrow = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'][(datetime.now().weekday() + 2) % 7]
        seat_type = seat_config[user_config[the_day_after_tomorrow]['name']]["type"]
        
        # 调整时间判断
        if seat_type == "自习室":
            start_time = datetime.now().replace(hour=20-time_zone, minute=0, second=0, microsecond=0)
            end_time = datetime.now().replace(hour=20-time_zone, minute=15, second=0, microsecond=0)
        else:
            start_time = datetime.now().replace(hour=21-time_zone, minute=0, second=0, microsecond=0)
            end_time = datetime.now().replace(hour=21-time_zone, minute=15, second=0, microsecond=0)
        
        start_time = start_time - timedelta(minutes=self.cfg["cron-delta-minutes"])
        
        # === 3. 恢复时间检查 (防止被封号) ===
        # 如果你想强制测试，可以在 GitHub Actions 环境变量里加一个 FORCE_RUN=true
        if os.environ.get("FORCE_RUN") != "true":
            if datetime.now() < start_time or datetime.now() > end_time:
                 print(f"当前时间 {datetime.now()} 未到预约开放时间，脚本停止。")
                 return
        
        logging.info('Booking favorite seat')
        retry_sleep_time = timedelta(minutes=self.cfg["cron-delta-minutes"]).seconds*2/(self.cfg["max-retry"]-2) - 10
        for tried_times in range(self.cfg["max-retry"]):
            try:
                code, msg = self._book_favorite_seat(user_config, seat_config, tried_times)
                print(f"尝试结果: {msg}")
                if str(code) == "0" or "成功" in msg:
                    return
            except Exception as e:
                logging.exception(e)
                time.sleep(retry_sleep_time)

    def _book_favorite_seat(self, user_config, seat_config, tried_times=0):
        # ... (此处保持你本地调试好的代码逻辑不变) ...
        the_day_after_tomorrow = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'][(datetime.now().weekday() + 2) % 7]
        date_config = user_config[the_day_after_tomorrow]
        seats = get_seats_with_config(user_config, date_config, seat_config)
        today_0_clock = datetime.strptime(datetime.now().strftime("%Y-%m-%d 00:00:00"), "%Y-%m-%d %H:%M:%S")
        book_time = today_0_clock + timedelta(days=2) + timedelta(hours=date_config['开始时间'])
        delta = book_time - self.cfg["start-time"]
        total_seconds = delta.days * 24 * 3600 + delta.seconds
        
        if date_config['name'] == '自定义' and tried_times<self.cfg["max-retry"]/3*2:
            seat = seats[0]
        else:
            seat = random.choice(seats)
            
        data = f"beginTime={total_seconds}&duration={3600 * date_config['持续小时数']}&&seats[0]={seat}&seatBookers[0]={self.user_data['uid']}"
        headers = self.cfg["headers"]
        headers['Cookie'] = self.cookie
        print("发送请求:", data)
        self.resp = requests.post(self.cfg["target"], data=data, headers=headers)
        
        print(f"服务器返回: {self.resp.text}") # 打印结果
        
        self.json = json.loads(self.resp.text)
        return self.json["CODE"], self.json["MESSAGE"] + " 座位:{}".format(seat)

    def login(self):
        logging.info('Login in (GitHub Actions Mode)...')
        try:
            # 1. 访问 SSO 注销 (防止缓存)
            try:
                self.driver.get("https://sso.hdu.edu.cn/cas/logout")
                time.sleep(1)
            except: pass
            self.driver.delete_all_cookies()

            # 2. 打开登录页
            self.driver.get("https://hdu.huitu.zhishulib.com/")
            time.sleep(10) # 服务器网络慢，多等等

            # 3. 定位并输入
            self.wait.until(EC.presence_of_element_located((By.NAME, "username")))
            user_input = self.driver.find_element(By.NAME, "username")
            pwd_input = self.driver.find_element(By.CSS_SELECTOR, "input[type='password']")
            login_btn = self.driver.find_element(By.CSS_SELECTOR, "button[type='submit']")

            user_input.clear()
            user_input.send_keys(self.un)
            pwd_input.clear()
            pwd_input.send_keys(self.pd)

            # 4. 点击登录
            self.driver.execute_script("arguments[0].click();", login_btn)
            
            # 5. 等待跳转
            print("正在等待登录跳转...")
            time.sleep(8)
            
            # 6. 获取 Cookie
            cookie_list = self.driver.get_cookies()
            self.cookie = ";".join([item["name"] + "=" + item["value"] + "" for item in cookie_list])
            self.cfg["headers"]['Cookie'] = self.cookie

            if len(cookie_list) > 0:
                logging.info("Cookie 获取成功")
            else:
                logging.error("Cookie 获取失败")
                return -1
        except Exception as e:
            logging.error(f"登录失败: {e}")
            return -1
        return 0

    def get_user_info(self):
        # ... (保持原样) ...
        headers = self.cfg["headers"]
        headers['Cookie'] = self.cookie
        try:
            resp = requests.get("https://hdu.huitu.zhishulib.com/Seat/Index/searchSeats?LAB_JSON=1", headers=headers)
            if "未登录" in resp.text: return -1
            self.user_data = resp.json()['DATA']
        except Exception as e:
            logging.error(f"获取用户信息失败: {e}")
            return -1
        print("获取用户信息成功")
        return 0
    
    def wechatNotice(self, message, desp=None):
        pass 

def is_booking_enable(date_cfg):
    return date_cfg['启用']

if __name__ == "__main__":
    if not os.path.exists("user_config.yml"):
        print("配置文件缺失")
        exit(1)
        
    with open("user_config.yml", 'r', encoding='utf-8') as f:
        user_config = yaml.safe_load(f)
    with open("config/basic_config.yml", 'r', encoding='utf-8') as f:
        basic_config = yaml.safe_load(f)
    with open("config/seat_config.yml", 'r', encoding='utf-8') as f:
        seat_config = yaml.safe_load(f)

    # 计算日期逻辑...
    the_day_after_tomorrow = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'][(datetime.now().weekday() + 2) % 7]
    
    # 如果设置了强制运行，忽略启用检查
    if os.environ.get("FORCE_RUN") != "true":
        if not is_booking_enable(user_config[the_day_after_tomorrow]):
            print(f"配置设定为不预约 {the_day_after_tomorrow}")
            exit(0)

    s = SeatAutoBooker(basic_config["SeatAutoBooker"])
    if s.login() == 0 and s.get_user_info() == 0:
        s.book_favorite_seat(user_config, seat_config)
    
    s.driver.quit()
