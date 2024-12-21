import platform
import smtplib
from email.mime.text import MIMEText
import subprocess

import requests
import time
import os
from bs4 import BeautifulSoup
import logging
from datetime import datetime
import json
import re
import random
from tqdm import tqdm
import traceback


class MagazineMonitor:
    def __init__(self, search_url, download_dir="downloads"):
        self.search_url = search_url
        self.download_dir = download_dir
        self.setup_logging()
        self.setup_session()
        self.load_state()

    def setup_logging(self):
        os.makedirs('logs', exist_ok=True)
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(message)s',
            handlers=[
                logging.FileHandler(f'logs/monitor_{datetime.now().strftime("%Y%m%d")}.log'),
                logging.StreamHandler()
            ]
        )

    def setup_session(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1'
        })

    def load_state(self):
        self.state_file = 'magazine_state.json'
        if os.path.exists(self.state_file):
            with open(self.state_file, 'r') as f:
                self.state = json.load(f)
        else:
            self.state = {
                'processed_urls': [],
                'last_check': None
            }

    def save_state(self):
        with open(self.state_file, 'w') as f:
            json.dump(self.state, f, indent=2)

    def parse_magazine_page(self, html_content):
        """解析杂志搜索页面"""
        soup = BeautifulSoup(html_content, 'html.parser')
        articles = soup.find_all('article')

        magazine_data = []
        for article in articles:
            try:
                link = article.find('h2', class_='entry-title').find('a')
                title = link.text.strip()
                url = link['href']

                magazine_data.append({
                    'title': title,
                    'url': url
                })
            except Exception as e:
                logging.error(f"Error parsing article: {str(e)}")
                continue

        return magazine_data

    def extract_real_download_url(self, vk_url):
        """从 VK 页面提取实际的下载链接"""
        try:
            logging.info(f"Fetching VK page: {vk_url}")

            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
                'DNT': '1',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
                'Sec-Fetch-Dest': 'document',
                'Sec-Fetch-Mode': 'navigate',
                'Sec-Fetch-Site': 'none',
                'Sec-Fetch-User': '?1'
            }

            response = self.session.get(vk_url, headers=headers)
            response.raise_for_status()

            # 保存页面内容以便调试
            with open('debug_vk_page.html', 'w', encoding='utf-8') as f:
                f.write(response.text)

            # 直接搜索包含实际URL的input标签
            url_match = re.search(r'<input\s+name="url"\s+type="hidden"\s+value="([^"]+)"', response.text)
            if url_match:
                direct_url = url_match.group(1)
                if not direct_url.endswith('?dl=1'):
                    direct_url += '?dl=1'
                logging.info(f"Found direct download URL from input: {direct_url}")
                return direct_url

            # 备用方法：从 Docs.initDoc 中提取
            init_match = re.search(r'Docs\.initDoc\(({[^}]+})\)', response.text)
            if init_match:
                try:
                    init_data = json.loads(init_match.group(1))
                    if 'docUrl' in init_data:
                        direct_url = init_data['docUrl'].replace('\\/', '/')
                        if not direct_url.endswith('?dl=1'):
                            direct_url += '?dl=1'
                        logging.info(f"Found direct download URL from initDoc: {direct_url}")
                        return direct_url
                except json.JSONDecodeError:
                    pass

            # 最后尝试：直接搜索 .pdf 链接
            pdf_match = re.search(r'https://[^"\']+\.pdf', response.text)
            if pdf_match:
                direct_url = pdf_match.group(0)
                if not direct_url.endswith('?dl=1'):
                    direct_url += '?dl=1'
                logging.info(f"Found direct download URL from text: {direct_url}")
                return direct_url

            logging.error("Could not find direct download URL in VK page")
            return None

        except Exception as e:
            logging.error(f"Error extracting real download URL: {str(e)}")
            logging.error(f"Stack trace: {traceback.format_exc()}")
            return None

    def download_file(self, url, filename, max_retries=3):
        """下载文件，带有重试机制"""
        for attempt in range(max_retries):
            try:
                logging.info(f"Download attempt {attempt + 1} for: {url}")

                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                    'Accept': '*/*',
                    'Accept-Encoding': 'gzip, deflate, br',
                    'Connection': 'keep-alive',
                    'Referer': 'https://vk.com/',
                    'Sec-Fetch-Dest': 'document',
                    'Sec-Fetch-Mode': 'navigate',
                    'Sec-Fetch-Site': 'cross-site'
                }

                filepath = os.path.join(self.download_dir, filename)
                with self.session.get(url, stream=True, headers=headers) as r:
                    r.raise_for_status()
                    total = int(r.headers.get('content-length', 0))

                    # 检查文件类型
                    content_type = r.headers.get('content-type', '').lower()
                    if 'application/pdf' not in content_type and 'octet-stream' not in content_type:
                        raise Exception(f"Unexpected content type: {content_type}")

                    with open(filepath, 'wb') as file, tqdm(
                            desc=filename,
                            total=total,
                            unit='iB',
                            unit_scale=True,
                            unit_divisor=1024,
                    ) as pbar:
                        for data in r.iter_content(chunk_size=8192):
                            size = file.write(data)
                            pbar.update(size)

                # 验证文件大小
                if os.path.getsize(filepath) < 1000:
                    raise Exception("Downloaded file is too small")

                logging.info(f"Successfully downloaded: {filename}")
                return True

            except Exception as e:
                logging.error(f"Attempt {attempt + 1} failed: {str(e)}")
                if attempt < max_retries - 1:
                    wait_time = (attempt + 1) * 5
                    logging.info(f"Waiting {wait_time} seconds before next attempt...")
                    time.sleep(wait_time)
                else:
                    logging.error(f"All attempts failed for {url}")
                    if os.path.exists(os.path.join(self.download_dir, filename)):
                        os.remove(os.path.join(self.download_dir, filename))
                    return False

        return False

    def generate_filename(self, title, url):
        """从标题生成文件名"""
        # 提取日期信息
        date_match = re.search(r'(\w+\s+\d{1,2},?\s+20\d{2})', title)
        date_str = date_match.group(1) if date_match else "unknown_date"

        # 将日期转换为标准格式
        try:
            date_obj = datetime.strptime(date_str, '%B %d, %Y')
            date_formatted = date_obj.strftime('%Y%m%d')
        except:
            date_formatted = date_str.replace(' ', '_')

        # 提取版本信息（UK/USA）
        version = "UK" if "UK" in title else "USA" if "USA" in title else "INT"

        # 生成安全的文件名
        safe_name = f"The_Economist_{version}_{date_formatted}.pdf"
        return safe_name

    def process_magazine(self, magazine_data):
        """处理单个杂志页面并下载文件"""
        os.makedirs(self.download_dir, exist_ok=True)

        if magazine_data['url'] in self.state['processed_urls']:
            logging.info(f"Already processed: {magazine_data['title']}")
            return False

        try:
            logging.info(f"Processing magazine: {magazine_data['title']}")

            # 获取杂志详情页
            response = self.session.get(magazine_data['url'])
            response.raise_for_status()

            # 添加随机延迟
            time.sleep(random.uniform(2, 5))

            # 查找 VK 链接
            soup = BeautifulSoup(response.text, 'html.parser')
            content_area = soup.find('div', class_='entry-content')
            if not content_area:
                logging.error("Could not find entry-content div")
                return False

            # 查找所有VK链接
            links = content_area.find_all('a', href=True)
            vk_links = [link['href'] for link in links if 'vk.com' in link['href'] and '/doc/' in link['href']]

            if vk_links:
                logging.info(f"Found {len(vk_links)} VK links")

                for vk_link in vk_links:
                    logging.info(f"Processing VK link: {vk_link}")
                    # 获取实际的下载链接
                    direct_url = self.extract_real_download_url(vk_link)

                    if direct_url:
                        logging.info(f"Got direct URL: {direct_url}")
                        filename = self.generate_filename(magazine_data['title'], direct_url)

                        # 检查是否已下载
                        if os.path.exists(os.path.join(self.download_dir, filename)):
                            logging.info(f"File already exists: {filename}")
                            self.state['processed_urls'].append(magazine_data['url'])
                            self.save_state()
                            return True

                        # 下载文件
                        success = self.download_file(direct_url, filename, max_retries=3)
                        if success:
                            self.state['processed_urls'].append(magazine_data['url'])
                            self.save_state()
                            logging.info("Download successful, preparing to send notifications...")

                            # 添加下载成功通知
                            self.desktop_notify("下载成功", f"杂志已下载：{filename}")
                            #self.email_notify("下载成功通知", f"《{magazine_data['title']}》已成功下载至 {self.download_dir}")

                            logging.info(f"Successfully processed {magazine_data['title']}")
                            return True
                    else:
                        logging.error(f"Could not extract direct download link from VK page")
            else:
                logging.warning(f"No VK links found for {magazine_data['title']}")

            return False

        except Exception as e:
            logging.error(f"Error processing {magazine_data['url']}: {str(e)}")
            return False

    def desktop_notify(self, title, message):
        """发送桌面通知 (使用 osascript)"""
        os_platform = platform.system()
        if os_platform == "Darwin":  # macOS
            try:
                # 使用 osascript 发送通知
                script = f'display notification "{message}" with title "{title}"'
                subprocess.run(["osascript", "-e", script], check=True)
                logging.info(f"Notification sent: {title} - {message}")
            except Exception as e:
                logging.error(f"Error sending notification: {str(e)}")
        elif os_platform == "Linux":  # Linux
            os.system(f'notify-send "{title}" "{message}"')
        else:
            logging.info(f"Notification: {title} - {message}")

    def email_notify(self, subject, message):
        """发送邮件通知"""
        if not self.email_config:
            return

        try:
            msg = MIMEText(message)
            msg['Subject'] = subject
            msg['From'] = self.email_config['sender']
            msg['To'] = self.email_config['receiver']

            with smtplib.SMTP_SSL(self.email_config['smtp_server'], self.email_config['smtp_port']) as server:
                server.login(self.email_config['sender'], self.email_config['password'])
                server.send_message(msg)

            logging.info(f"Email sent: {subject}")
        except Exception as e:
            logging.error(f"Failed to send email: {str(e)}")

    def run_once(self):
        """执行一次检查"""
        logging.info("Starting magazine check...")
        try:
            response = self.session.get(self.search_url)
            response.raise_for_status()

            magazines = self.parse_magazine_page(response.text)

            for magazine in magazines:
                if magazine['url'] not in self.state['processed_urls']:
                    logging.info(f"Found new magazine: {magazine['title']}")
                    self.process_magazine(magazine)

            logging.info("Check completed")

        except Exception as e:
            logging.error(f"Error in check: {str(e)}")
            raise

if __name__ == "__main__":
    SEARCH_URL = "https://freemagazines.top/?s=economist"
    monitor = MagazineMonitor(SEARCH_URL)
    monitor.run_once()
