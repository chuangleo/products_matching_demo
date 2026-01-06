import csv
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.common.exceptions import TimeoutException, NoSuchElementException
import time
import random
from urllib.parse import quote
import re
import warnings
import logging
import os

# 在文件開頭添加這些行來抑制所有警告和日誌
warnings.filterwarnings("ignore")
logging.getLogger('selenium').setLevel(logging.CRITICAL)
logging.getLogger('urllib3').setLevel(logging.CRITICAL)

# 抑制 Chrome 相關的錯誤訊息
os.environ['WDM_LOG_LEVEL'] = '0'
os.environ['WDM_PRINT_FIRST_LINE'] = 'False'

def fetch_products_for_momo(keyword, max_products=50, progress_callback=None, cancel_check=None):
    """
    使用 Selenium 從 momo 購物網抓取商品資訊
    
    Args:
        keyword (str): 搜尋關鍵字
        max_products (int): 最大抓取商品數量
        progress_callback (function): 進度回調函式，接收 (current, total, message) 參數
        cancel_check (function): 取消檢查函式，返回 True 表示需要取消
    
    Returns:
        list: 商品資訊列表，每個商品包含 id, title, price, image_url, url, platform, sku
    """
    
    products = []
    product_id = 1  # 順序編號
    driver = None
    page = 1  # 當前頁數
    seen_skus = set()  # 追蹤已經收集的 SKU，避免重複
    consecutive_empty_pages = 0  # 連續空白頁計數器
    
    try:
        # 設定 Chrome 選項
        chrome_options = Options()
        chrome_options.add_argument('--headless=new')  # 使用新的無頭模式
        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-dev-shm-usage')
        chrome_options.add_argument('--disable-gpu')
        chrome_options.add_argument('--disable-software-rasterizer')
        chrome_options.add_argument('--remote-debugging-port=9222')
        chrome_options.add_argument('--disable-setuid-sandbox')
        chrome_options.add_argument('--window-size=1920,1080')
        chrome_options.add_argument('--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36')
        
        # 禁用圖片載入以提高速度（已註解，顯示圖片）
        prefs = {
            # "profile.managed_default_content_settings.images": 2,  # 已註解，允許載入圖片
            "profile.default_content_setting_values.notifications": 2
        }
        chrome_options.add_experimental_option("prefs", prefs)
        chrome_options.add_experimental_option('excludeSwitches', ['enable-logging'])
        
        # 設定頁面載入策略（不等待全部資源）
        chrome_options.page_load_strategy = 'eager'
        
        # 初始化 WebDriver（自動下載並使用 ChromeDriver）
        try:
            # 使用 webdriver_manager 自動管理 chromedriver
            chromedriver_path = ChromeDriverManager().install()
            
            # 設定執行權限（Windows 上通常不需要，但加上確保沒問題）
            service = Service(chromedriver_path)
            driver = webdriver.Chrome(service=service, options=chrome_options)
        except Exception as driver_error:
            print(f"⚠️ ChromeDriver 初始化失敗: {driver_error}")
            print("💡 嘗試使用系統 PATH 中的 ChromeDriver...")
            # 備用方案：使用系統中的 chromedriver
            driver = webdriver.Chrome(options=chrome_options)
        
        driver.set_page_load_timeout(60)  # 增加到 60 秒
        print(f"正在搜尋 momo: {keyword}")
        
        # 📊 回報初始進度
        if progress_callback:
            progress_callback(0, max_products, f'🔍 正在搜尋 MOMO: {keyword}')
        
        # 等待頁面載入
        wait = WebDriverWait(driver, 30)  # 增加到 30 秒
        
        # 多頁抓取循環
        while len(products) < max_products:
            # 檢查是否被取消
            if cancel_check and cancel_check():
                print("❌ MOMO 搜尋已被取消")
                break
            
            # 建構搜尋 URL（包含頁數）
            encoded_keyword = quote(keyword)
            search_url = f"https://www.momoshop.com.tw/search/searchShop.jsp?keyword={encoded_keyword}&searchType=1&cateLevel=0&ent=k&sortType=1&curPage={page}"
            
            print(f"正在抓取第 {page} 頁...")
            
            # 📊 回報頁面載入進度
            if progress_callback:
                progress_callback(len(products), max_products, f'(已收集 {len(products)}/{max_products} 筆)')
            
            # 載入頁面（加入重試機制）
            retry_count = 0
            max_retries = 3
            page_loaded = False
            
            while retry_count < max_retries and not page_loaded:
                try:
                    # 檢查 driver 會話是否仍然有效
                    try:
                        _ = driver.current_url
                    except Exception as session_error:
                        print(f"⚠️ WebDriver 會話失效，重新初始化瀏覽器...")
                        try:
                            driver.quit()
                        except:
                            pass
                        driver = webdriver.Chrome(service=service, options=chrome_options)
                        driver.set_page_load_timeout(60)
                        wait = WebDriverWait(driver, 30)
                    
                    driver.get(search_url)
                    time.sleep(2)  # 等待頁面載入
                    page_loaded = True
                except Exception as e:
                    retry_count += 1
                    error_msg = str(e)
                    if "invalid session id" in error_msg:
                        print(f"⚠️ 會話失效 (嘗試 {retry_count}/{max_retries})，重新初始化瀏覽器...")
                        try:
                            driver.quit()
                        except:
                            pass
                        # 重新創建 driver
                        driver = webdriver.Chrome(service=service, options=chrome_options)
                        driver.set_page_load_timeout(60)
                        wait = WebDriverWait(driver, 30)
                        time.sleep(2)
                    elif "ERR_INTERNET_DISCONNECTED" in error_msg or "ERR_CONNECTION" in error_msg:
                        print(f"⚠️ 網路連線錯誤 (嘗試 {retry_count}/{max_retries})，等待 3 秒後重試...")
                        time.sleep(3)
                    else:
                        print(f"❌ 頁面載入錯誤: {e}")
                        break
            
            if not page_loaded:
                print(f"❌ 第 {page} 頁載入失敗，已重試 {max_retries} 次，停止抓取")
                break
            
            try:
                # 🔍 檢查視窗是否還存在
                try:
                    _ = driver.current_url
                except Exception as window_error:
                    print(f"❌ Chrome 視窗已關閉或失去連線: {window_error}")
                    break
                
                # 🆕 先檢查網頁顯示的總商品數
                try:
                    total_count_element = driver.find_element(By.CSS_SELECTOR, "span.total-txt b")
                    total_count_text = total_count_element.text
                    total_available = int(total_count_text)
                    print(f"📊 網頁顯示共有 {total_available} 件商品")
                    
                    # 如果總商品數為 0，直接停止
                    if total_available == 0:
                        print("❌ 搜尋結果為 0 件商品，停止抓取")
                        break
                    
                    # 如果總商品數少於目標數量，調整目標
                    if total_available < max_products:
                        print(f"⚠️ 總商品數 ({total_available}) 少於目標數量 ({max_products})，將抓取所有商品")
                        # 不修改 max_products，而是在抓完所有商品後自動停止
                    
                    # 如果已經抓夠了，停止
                    if len(products) >= total_available:
                        print(f"✅ 已收集全部 {total_available} 件商品，停止抓取")
                        break
                        
                except (NoSuchElementException, ValueError) as e:
                    print(f"⚠️ 無法讀取商品總數，繼續使用舊邏輯: {e}")
                
                # 嘗試查找商品元素（使用更精確的選擇器）
                selectors_to_try = [
                    "li.listAreaLi",                    # 最常見的商品列表項
                    ".listAreaUl li.listAreaLi",        # 完整路徑
                    "li.goodsItemLi",                   # 商品項目
                    ".prdListArea .goodsItemLi",        # 商品列表區域的商品項目
                    "li[data-gtm]",                     # 有 GTM 追蹤屬性的商品
                    ".goodsItemLi",                     # 商品項目類別
                    # 移除太寬泛的選擇器：".searchPrdListArea li", ".searchPrdList li"
                ]
                
                product_elements = []
                used_selector = None
                for selector in selectors_to_try:
                    try:
                        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, selector)))
                        temp_elements = driver.find_elements(By.CSS_SELECTOR, selector)
                        if temp_elements:
                            product_elements = temp_elements
                            used_selector = selector
                            break
                    except TimeoutException:
                        continue
                
                if not product_elements:
                    print("無法找到商品元素，可能頁面結構已改變或已到達最後一頁")
                    break
                
                print(f"使用選擇器 '{used_selector}' 找到 {len(product_elements)} 個元素")
                
            except TimeoutException:
                print(f"第 {page} 頁載入超時，停止抓取")
                break
            
            print(f"開始解析 {len(product_elements)} 個商品")
            page_products_count = 0
            consecutive_duplicates = 0  # 連續重複商品計數器
            max_consecutive_duplicates = 10  # 連續 10 個重複就停止該頁
            skipped_empty_elements = 0  # 記錄跳過的空元素數量
            
            # 解析每個商品
            for i, element in enumerate(product_elements):
                # 檢查是否被取消
                if cancel_check and cancel_check():
                    print("❌ MOMO 搜尋已被取消")
                    break
                
                try:
                    # 如果已經獲得足夠的商品，就停止
                    if len(products) >= max_products:
                        break
                    
                    # 🔍 快速檢查：這個元素是否真的包含商品資訊
                    # 檢查是否有標題或價格相關的文字
                    element_text = element.text.strip()
                    if not element_text or len(element_text) < 5:
                        # print(f"元素 {i+1} 沒有文字內容，跳過")
                        skipped_empty_elements += 1
                        continue
                    
                    # 提取商品標題
                    title = ""
                    title_selectors = [
                        "h3.prdName",
                        ".prdNameTitle h3.prdName",
                        ".prdName",
                        "h3",
                        "a[title]",
                        "img[alt]",
                        ".goodsName",
                        ".goodsInfo h3",
                        "a"
                    ]
                    
                    for selector in title_selectors:
                        try:
                            title_elem = element.find_element(By.CSS_SELECTOR, selector)
                            if selector == "img[alt]":
                                title = title_elem.get_attribute("alt").strip()
                            elif selector == "a[title]":
                                title = title_elem.get_attribute("title").strip()
                            else:
                                title = title_elem.text.strip()
                            
                            if title and len(title) > 5:  # 確保標題有足夠長度
                                break
                        except NoSuchElementException:
                            continue
                    
                    # 如果沒有找到標題，跳過這個商品
                    if not title:
                        continue
                    
                    # 提取價格（先用多種選擇器，若失敗則用整個元素的文字做回退）
                    price = 0
                    price_selectors = [
                        ".money .price b",
                        ".price b",
                        ".money b",
                        ".price",
                        ".money",
                        ".cost",
                        "b",
                        "strong",
                        ".goodsPrice",
                        ".priceInfo",
                        ".prodPrice",
                        ".prdPrice"
                    ]

                    for selector in price_selectors:
                        try:
                            price_elements = element.find_elements(By.CSS_SELECTOR, selector)
                            for price_elem in price_elements:
                                price_text = price_elem.text
                                if price_text and any(c.isdigit() for c in price_text):
                                    # 提取數字
                                    numbers = re.findall(r'\d+', price_text.replace(',', ''))
                                    if numbers:
                                        # 取最大的數字作為價格（避免取到折扣百分比等小數字）
                                        potential_prices = [int(num) for num in numbers if int(num) > 10]
                                        if potential_prices:
                                            price = max(potential_prices)
                                            break
                            if price > 0:
                                break
                        except NoSuchElementException:
                            continue

                    # 回退策略：用整個元素的文本抓取數字（如果先前沒抓到價格）
                    if price <= 0:
                        try:
                            full_text = element.text
                            numbers = re.findall(r'\d+', full_text.replace(',', ''))
                            if numbers:
                                potential_prices = [int(num) for num in numbers if int(num) > 10]
                                if potential_prices:
                                    price = max(potential_prices)
                        except Exception:
                            price = 0

                    # 如果還沒有找到價格，就跳過這個商品
                    if price <= 0:
                        continue
                    
                    # 提取商品連結
                    url = ""
                    try:
                        link_elem = element.find_element(By.CSS_SELECTOR, "a.goods-img-url")
                        url = link_elem.get_attribute("href")
                        if not url.startswith("http"):
                            url = "https://www.momoshop.com.tw" + url
                    except NoSuchElementException:
                        # 嘗試找其他可能的連結選擇器
                        try:
                            link_elem = element.find_element(By.CSS_SELECTOR, "a[href*='/goods/']")
                            url = link_elem.get_attribute("href")
                            if not url.startswith("http"):
                                url = "https://www.momoshop.com.tw" + url
                        except NoSuchElementException:
                            # 嘗試找任何連結
                            try:
                                link_elem = element.find_element(By.CSS_SELECTOR, "a[href]")
                                url = link_elem.get_attribute("href")
                                if url and not url.startswith("http"):
                                    url = "https://www.momoshop.com.tw" + url
                            except NoSuchElementException:
                                url = ""
                    
                    # 嘗試從隱藏 input 取得商品 id 作為 sku（momo 的 list 中常見）
                    sku = ""
                    try:
                        input_elem = element.find_element(By.CSS_SELECTOR, "input#viewProdId")
                        sku_val = input_elem.get_attribute("value")
                        if sku_val:
                            sku = sku_val
                    except NoSuchElementException:
                        sku = ""

                    # 若仍無 sku，嘗試從 url 提取 i_code 或最後一段
                    if not sku and url:
                        match = re.search(r'i_code=(\d+)', url)
                        if match:
                            sku = match.group(1)
                        else:
                            url_parts = url.rstrip('/').split('/')
                            if url_parts:
                                last_part = url_parts[-1]
                                if '?' in last_part:
                                    last_part = last_part.split('?')[0]
                                if '.' in last_part:
                                    last_part = last_part.split('.')[0]
                                sku = last_part
                    # 如果有 sku 但沒有 url，可以用 momo 的商品頁樣式組成 url
                    if not url and sku:
                        url = f"https://www.momoshop.com.tw/goods/GoodsDetail.jsp?i_code={sku}"
                    
                    # 提取商品圖片 - 使用多重策略提高成功率
                    image_url = ""
                    
                    # 圖片選擇器列表（按優先順序）
                    img_selectors = [
                        "img.goods-img",  # 2025 最新結構
                        "img.prdImg",
                        "img.goodsImg",
                        "a.goods-img-url img",
                        "div.goods-img img",
                        "img[src*='goodsImg']",
                        "img[src*='momoshop']",
                        "img[data-original*='goodsImg']",
                        "img[alt]",  # 任何有 alt 屬性的圖片
                    ]
                    
                    for selector in img_selectors:
                        try:
                            img_elem = element.find_element(By.CSS_SELECTOR, selector)
                            # 嘗試多個屬性來獲取圖片網址
                            image_url = (img_elem.get_attribute("src") or 
                                       img_elem.get_attribute("data-src") or 
                                       img_elem.get_attribute("data-original") or
                                       img_elem.get_attribute("data-lazy") or
                                       img_elem.get_attribute("data-image"))
                            
                            # 過濾掉不是商品圖片的 URL
                            if image_url and image_url != "" and image_url != "about:blank":
                                # 排除官方標籤、placeholder、icon 等非商品圖片
                                exclude_patterns = [
                                    "placeholder",
                                    "offical_tag",  # 官方標籤
                                    "official_tag",
                                    "ec-images",    # 活動標籤圖片
                                    "icon",
                                    "logo",
                                    "banner",
                                    "_tag_",
                                    "tag.png",
                                    "tag.jpg",
                                    "data:image",  # Base64 圖片
                                ]
                                
                                # 檢查是否包含排除的模式
                                if any(pattern in image_url.lower() for pattern in exclude_patterns):
                                    continue  # 跳過這個圖片，嘗試下一個
                                
                                # 處理相對路徑和協議相對路徑
                                if image_url.startswith("//"):
                                    image_url = "https:" + image_url
                                elif image_url.startswith("/"):
                                    image_url = "https://www.momoshop.com.tw" + image_url
                                elif not image_url.startswith("http"):
                                    # 如果是相對路徑但不以 / 開頭
                                    if "momoshop" not in image_url:
                                        image_url = "https://img.momoshop.com.tw/" + image_url
                                    else:
                                        image_url = "https://" + image_url
                                
                                # 確保圖片 URL 使用適當的尺寸參數
                                # MOMO 圖片通常格式為: https://imgX.momoshop.com.tw/...?t=timestamp
                                if "momoshop.com.tw" in image_url and "?" not in image_url:
                                    # 添加時間戳參數避免快取問題
                                    import datetime
                                    timestamp = datetime.datetime.now().strftime("%Y%m%d")
                                    image_url = f"{image_url}?t={timestamp}"
                                
                                break  # 找到有效圖片就停止
                        except NoSuchElementException:
                            continue
                    
                    # 如果還是沒找到，設為空字串
                    if not image_url:
                        image_url = ""
                    
                    # 確保所有必要欄位都有值才加入商品
                    if title and price > 0 and url:
                        # 使用 SKU 或 URL 檢查是否重複
                        is_duplicate = False
                        if sku and sku in seen_skus:
                            is_duplicate = True
                        elif url in [p['url'] for p in products]:
                            is_duplicate = True
                        
                        if is_duplicate:
                            #print(f"跳過重複商品: {sku or url}")
                            consecutive_duplicates += 1
                            # 如果連續重複太多，提前停止該頁解析
                            if consecutive_duplicates >= max_consecutive_duplicates:
                                print(f"⚠️ 連續 {consecutive_duplicates} 個商品都是重複，提前停止該頁解析")
                                break
                            continue
                        
                        # 找到有效新商品，重置連續重複計數
                        consecutive_duplicates = 0
                        
                        product = {
                            "id": product_id,
                            "title": title,
                            "price": price,
                            "image_url": image_url if image_url else "",
                            "url": url,
                            "platform": "momo",
                            "sku": sku
                        }
                        products.append(product)
                        if sku:
                            seen_skus.add(sku)
                        product_id += 1
                        page_products_count += 1
                        
                        # 📊 回報即時進度（每抓到一個商品就更新）
                        if progress_callback:
                            progress_callback(
                                len(products), 
                                max_products, 
                                f'📦 MOMO: 已收集 {len(products)}/{max_products} 筆商品'
                            )
                        
                        #print(f"成功解析商品 {len(products)}: {title[:50]}... (NT$ {price:,})")
                    
                    # 避免過於頻繁的操作
                    time.sleep(random.uniform(0.05, 0.1))
                    
                except Exception as e:
                    print(f"解析第 {i+1} 個商品時發生錯誤: {e}")
                    continue
            
            # 顯示詳細統計
            if skipped_empty_elements > 0:
                print(f"⚠️ 跳過 {skipped_empty_elements} 個空元素（可能是廣告、分隔符等）")
            
            print(f"第 {page} 頁找到 {len(product_elements)} 個商品元素，成功解析 {page_products_count} 個有效商品，目前總計 {len(products)} 個商品")
            
            # 🔧 改進：只有在「已達到目標數量」或「連續多頁都沒有商品」時才停止
            # 移除「商品數量少於 20 就停止」的限制，因為有些關鍵字本來商品就少
            
            # 如果這一頁沒有找到任何有效商品，檢查是否要繼續
            if page_products_count == 0:
                consecutive_empty_pages += 1
                print(f"⚠️ 第 {page} 頁沒有找到有效商品（連續 {consecutive_empty_pages} 頁為空）")
                
                # 快速判斷：第一頁就沒商品，直接停止
                if page == 1 and len(product_elements) < 10:
                    print("❌ 第一頁就沒有足夠商品元素，判定為搜尋結果為空，停止抓取")
                    break
                # 商品元素很少時停止（真的沒商品了）
                elif len(product_elements) < 10:
                    print("商品元素很少，判定為真正的最後一頁，停止抓取")
                    break
                # 🆕 如果不是第一頁，且有商品元素但解析出 0 個有效商品，直接停止（通常是都重複了）
                elif page > 1 and len(product_elements) >= 10:
                    print(f"❌ 第 {page} 頁有 {len(product_elements)} 個商品元素但解析出 0 個有效商品，判定為已到達搜尋結果末尾（可能都是重複商品），停止抓取")
                    break
                # 如果連續2頁都沒有有效商品，停止（加快判斷）
                elif consecutive_empty_pages >= 2:
                    print(f"連續 {consecutive_empty_pages} 頁都沒有有效商品，停止抓取")
                    break
                else:
                    print(f"但頁面還有商品元素，可能只是被過濾掉（例如重複SKU），繼續嘗試下一頁")
            else:
                # 重置連續空白頁計數器
                consecutive_empty_pages = 0
                    # 繼續到下一頁嘗試
                
            # 如果還需要更多商品，則跳到下一頁
            if len(products) < max_products:
                page += 1
                print(f"📄 準備抓取第 {page} 頁...")
                time.sleep(random.uniform(1, 1.5))  # 頁面間隔（從 2-3 秒減少到 1-1.5 秒）
            else:
                print(f"✅ 已達到目標數量 {max_products} 筆，停止抓取")
                break
        
        print(f"成功從 momo 獲取 {len(products)} 個唯一商品（已自動過濾重複 SKU）")
        
        # 📊 回報完成進度
        if progress_callback:
            progress_callback(len(products), max_products, f'✅ MOMO 完成！共收集 {len(products)} 筆商品')
        
        return products
        
    except Exception as e:
        error_msg = str(e)
        if "invalid session id" in error_msg:
            print(f"❌ WebDriver 會話失效（瀏覽器可能崩潰或被關閉）")
            print("💡 建議：檢查系統記憶體是否充足，或嘗試減少抓取數量")
        elif "target window already closed" in error_msg or "no such window" in error_msg:
            print(f"❌ Chrome 視窗已關閉，無法繼續抓取")
        elif "Session info: chrome" in error_msg and "Stacktrace" in error_msg:
            print(f"❌ Chrome 驅動錯誤（可能是視窗被關閉或崩潰）")
        else:
            print(f"momo Selenium 爬蟲發生錯誤: {e}")
        return products if products else []  # 返回已收集的商品
    
    finally:
        # 確保關閉瀏覽器
        if driver:
            try:
                driver.quit()
            except:
                pass


def fetch_products_for_pchome(keyword, max_products=50, progress_callback=None, cancel_check=None):
    """
    使用 Selenium 從 PChome 購物網抓取商品資訊，適應 2025年10月 的新版網頁結構。
    
    Args:
        keyword (str): 搜尋關鍵字
        max_products (int): 最大抓取商品數量
        progress_callback (function): 進度回調函式，接收 (current, total, message) 參數
        cancel_check (function): 取消檢查函式，返回 True 表示需要取消
    
    Returns:
        list: 商品資訊列表
    """
    products = []
    product_id = 1
    driver = None
    page = 1
    seen_skus = set()
    consecutive_empty_pages = 0  # 連續空白頁計數器

    try:
        chrome_options = Options()
        chrome_options.add_argument('--headless=new')  # 使用新的無頭模式
        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-dev-shm-usage')
        chrome_options.add_argument('--disable-gpu')
        chrome_options.add_argument('--disable-software-rasterizer')
        chrome_options.add_argument('--remote-debugging-port=9223')
        chrome_options.add_argument('--disable-setuid-sandbox')
        chrome_options.add_argument('--window-size=1920,1080')
        chrome_options.add_argument('--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36')
        
        prefs = {"profile.default_content_setting_values.notifications": 2}
        chrome_options.add_experimental_option("prefs", prefs)
        chrome_options.add_experimental_option('excludeSwitches', ['enable-logging'])
        
        # 設定頁面載入策略（不等待全部資源）
        chrome_options.page_load_strategy = 'eager'
        
        # 初始化 WebDriver（自動下載並使用 ChromeDriver）
        try:
            # 使用 webdriver_manager 自動管理 chromedriver
            chromedriver_path = ChromeDriverManager().install()
            
            # 設定執行權限（Windows 上通常不需要，但加上確保沒問題）
            service = Service(chromedriver_path)
            driver = webdriver.Chrome(service=service, options=chrome_options)
        except Exception as driver_error:
            print(f"⚠️ ChromeDriver 初始化失敗: {driver_error}")
            print("💡 嘗試使用系統 PATH 中的 ChromeDriver...")
            # 備用方案：使用系統中的 chromedriver
            driver = webdriver.Chrome(options=chrome_options)
        
        driver.set_page_load_timeout(60)  # 增加到 60 秒
        wait = WebDriverWait(driver, 30)  # 增加到 30 秒
        print(f"正在搜尋 PChome: {keyword}")
        
        # 📊 回報初始進度
        if progress_callback:
            progress_callback(0, max_products, f'🔍 正在搜尋 PChome: {keyword}')

        encoded_keyword = quote(keyword)
        search_url = f"https://24h.pchome.com.tw/search/?q={encoded_keyword}"
        
        # 載入初始頁面（加入重試機制）
        retry_count = 0
        max_retries = 3
        page_loaded = False
        
        while retry_count < max_retries and not page_loaded:
            try:
                # 檢查 driver 會話是否仍然有效
                try:
                    _ = driver.current_url
                except Exception as session_error:
                    print(f"⚠️ WebDriver 會話失效，重新初始化瀏覽器...")
                    try:
                        driver.quit()
                    except:
                        pass
                    driver = webdriver.Chrome(service=service, options=chrome_options)
                    driver.set_page_load_timeout(60)
                    wait = WebDriverWait(driver, 30)
                
                driver.get(search_url)
                time.sleep(2)
                page_loaded = True
            except Exception as e:
                retry_count += 1
                error_msg = str(e)
                if "invalid session id" in error_msg:
                    print(f"⚠️ 會話失效 (嘗試 {retry_count}/{max_retries})，重新初始化瀏覽器...")
                    try:
                        driver.quit()
                    except:
                        pass
                    # 重新創建 driver
                    driver = webdriver.Chrome(service=service, options=chrome_options)
                    driver.set_page_load_timeout(60)
                    wait = WebDriverWait(driver, 30)
                    time.sleep(2)
                elif "ERR_INTERNET_DISCONNECTED" in error_msg or "ERR_CONNECTION" in error_msg:
                    print(f"⚠️ 網路連線錯誤 (嘗試 {retry_count}/{max_retries})，等待 3 秒後重試...")
                    time.sleep(3)
                else:
                    print(f"❌ 頁面載入錯誤: {e}")
                    break
        
        if not page_loaded:
            print("❌ PChome 初始頁面載入失敗，停止抓取")
            return []

        while len(products) < max_products:
            # 檢查是否被取消
            if cancel_check and cancel_check():
                print("❌ PChome 搜尋已被取消")
                break
            
            print(f"正在抓取 PChome 第 {page} 頁...")
            
            # 📊 回報頁面載入進度
            if progress_callback:
                progress_callback(len(products), max_products, f'(已收集 {len(products)}/{max_products} 筆)')
            
            try:
                # 🔍 檢查 WebDriver 會話是否還有效
                try:
                    _ = driver.current_url
                except Exception as session_error:
                    print(f"⚠️ 檢測到會話失效，嘗試恢復...")
                    # 會話失效，嘗試重新創建 driver
                    try:
                        driver.quit()
                    except:
                        pass
                    print("🔄 重新初始化瀏覽器...")
                    driver = webdriver.Chrome(service=service, options=chrome_options)
                    driver.set_page_load_timeout(60)
                    wait = WebDriverWait(driver, 30)
                    # 重新載入當前頁面
                    driver.get(search_url)
                    time.sleep(3)
                
                # 等待新結構的商品項目出現
                wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "li.c-listInfoGrid__item--gridCardGray5")))
                
                # 滾動頁面以確保所有商品都載入
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(2)
                
                # 根據新結構獲取所有商品元素
                product_elements = driver.find_elements(By.CSS_SELECTOR, "li.c-listInfoGrid__item--gridCardGray5")
            except TimeoutException:
                print("頁面加載超時或找不到新結構的商品容器 (li.c-listInfoGrid__item--gridCardGray5)。")
                try:
                    driver.save_screenshot("pchome_error_screenshot.png")
                    print("已儲存錯誤截圖: pchome_error_screenshot.png")
                except Exception as e:
                    print(f"儲存截圖失敗: {e}")
                break

            print(f"第 {page} 頁找到 {len(product_elements)} 個商品元素")
            
            # 記錄這一頁成功解析的商品數
            page_products_count = 0
            consecutive_duplicates = 0  # 連續重複商品計數器
            max_consecutive_duplicates = 10  # 連續 10 個重複就停止該頁

            for element in product_elements:
                # 檢查是否被取消
                if cancel_check and cancel_check():
                    print("❌ PChome 搜尋已被取消")
                    break
                
                if len(products) >= max_products:
                    break

                try:
                    # 提取連結和 SKU
                    link_element = element.find_element(By.CSS_SELECTOR, "a.c-prodInfoV2__link")
                    url = link_element.get_attribute("href")
                    if not url.startswith("https://"):
                        url = "https://24h.pchome.com.tw" + url
                    
                    sku_match = re.search(r'/prod/(.*?)(?:\?|$)', url)
                    sku = sku_match.group(1) if sku_match else ""

                    # 提取標題
                    title_elem = element.find_element(By.CSS_SELECTOR, "h3.c-prodInfoV2__title")
                    title = title_elem.text.strip()

                    # 提取價格 - 優先抓取促銷價（打折後的價格）而非原價
                    price = 0
                    prices = []
                    installment_prices = []  # 分開記錄疑似分期的價格
                    
                    # 方法1: 找所有包含 "o-prodPrice" 的 div 元素
                    try:
                        price_divs = element.find_elements(By.CSS_SELECTOR, "div[class*='o-prodPrice']")
                        for price_div in price_divs:
                            price_text = price_div.text.strip()
                            
                            # 🚫 跳過包含分期關鍵字的文字（但記錄下來以便判斷）
                            if any(keyword in price_text for keyword in ['期', 'x', 'X', '/', '每期']):
                                # 仍然提取數字，但標記為分期價格
                                price_text_clean = price_text.replace(',', '').replace('$', '').replace('元', '').strip()
                                price_match = re.search(r'(\d+)', price_text_clean)
                                if price_match:
                                    potential_price = int(price_match.group(1))
                                    if 100 < potential_price < 10000000:
                                        installment_prices.append(potential_price)
                                continue
                            
                            # 移除逗號並提取完整的價格數字
                            price_text_clean = price_text.replace(',', '').replace('$', '').replace('元', '').strip()
                            price_match = re.search(r'(\d+)', price_text_clean)
                            if price_match:
                                potential_price = int(price_match.group(1))
                                # 只收集合理的商品價格（排除過小或過大的異常值）
                                if 100 < potential_price < 10000000:
                                    prices.append(potential_price)
                    except:
                        pass
                    
                    # 方法2: 找所有包含 $ 符號的文字（但排除分期相關）
                    if not prices:
                        try:
                            all_text = element.text
                            # 將文字按行分割，逐行檢查
                            lines = all_text.split('\n')
                            for line in lines:
                                # 🚫 跳過包含分期關鍵字的行
                                if any(keyword in line for keyword in ['期', 'x', 'X', '/', '每期', '分期']):
                                    continue
                                
                                # 找所有 $數字 的模式
                                price_matches = re.findall(r'\$[\d,]+', line)
                                for match in price_matches:
                                    price_num = int(re.sub(r'[^\d]', '', match))
                                    if 100 < price_num < 10000000:
                                        prices.append(price_num)
                        except:
                            pass
                    
                    # 方法3: 優先找「售價」元素（最準確）
                    if not prices:
                        try:
                            price_elem = element.find_element(By.CSS_SELECTOR, "div.c-prodInfoV2__salePrice")
                            price_text = price_elem.text.strip()
                            price_text_clean = price_text.replace(',', '').replace('$', '').replace('元', '').strip()
                            price_match = re.search(r'(\d+)', price_text_clean)
                            if price_match:
                                potential_price = int(price_match.group(1))
                                if potential_price > 100:
                                    prices.append(potential_price)
                        except:
                            pass
                    
                    # 智慧選擇價格：
                    # 1. 如果只有一個價格，直接使用
                    # 2. 如果有多個價格（原價+促銷價），選擇最小的（促銷價）
                    # 3. 但要確保選擇的價格不是分期付款金額
                    if prices:
                        if len(prices) == 1:
                            price = prices[0]
                        else:
                            # 有多個價格時，選擇較小的（通常是促銷價）
                            candidate_price = min(prices)
                            # 確保這個價格不是分期付款金額
                            # 如果最小價格剛好等於某個分期金額，則使用第二小的
                            if installment_prices and candidate_price in installment_prices:
                                # 排除分期金額後再選擇
                                valid_prices = [p for p in prices if p not in installment_prices]
                                if valid_prices:
                                    price = min(valid_prices)
                                else:
                                    # 如果排除後沒有價格，則取最大的（原價）
                                    price = max(prices)
                            else:
                                price = candidate_price
                    else:
                        price = 0

                    # 提取圖片 - 使用多重策略提高成功率
                    image_url = ""
                    
                    # 圖片選擇器列表（按優先順序）
                    img_selectors = [
                        "img[data-regression='store_prodImg']",  # 2025 最新結構
                        "a.c-prodInfoV2__link img",              # 連結中的圖片（優先）
                        "div.c-prodInfoV2__head img",            # 商品頭部圖片
                        "img[src*='items']",                     # PChome 商品圖片路徑
                        "img[src*='pchome.com.tw']",            # PChome 域名圖片
                        "div.c-prodInfo__img img",
                        "img[data-src*='items']",                # 延遲載入的商品圖片
                        "img[data-src*='pchome']",
                        "img[data-original*='items']",
                        "img[alt]",                              # 任何有 alt 屬性的圖片
                        "img"                                    # 最後備選：任何圖片
                    ]
                    
                    for selector in img_selectors:
                        try:
                            img_elem = element.find_element(By.CSS_SELECTOR, selector)
                            
                            # 嘗試多個屬性來獲取圖片網址（按優先順序）
                            potential_urls = [
                                img_elem.get_attribute("src"),
                                img_elem.get_attribute("data-src"),
                                img_elem.get_attribute("data-original"),
                                img_elem.get_attribute("data-lazy"),
                                img_elem.get_attribute("data-lazy-src"),
                                img_elem.get_attribute("srcset")  # 有時圖片在 srcset 中
                            ]
                            
                            for url in potential_urls:
                                if not url:
                                    continue
                                    
                                # 如果是 srcset，提取第一個 URL
                                if 'srcset' in selector or ',' in url:
                                    url = url.split(',')[0].strip().split(' ')[0]
                                
                                # 排除無效的圖片
                                if (url and url != "" and 
                                    "placeholder" not in url.lower() and 
                                    url != "about:blank" and 
                                    not url.startswith("data:image") and
                                    len(url) > 10):  # 確保 URL 有足夠長度
                                    
                                    image_url = url
                                    break
                            
                            if image_url:  # 找到有效圖片就停止
                                break
                                
                        except NoSuchElementException:
                            continue
                        except Exception as e:
                            # 忽略其他錯誤，繼續嘗試下一個選擇器
                            continue
                    
                    # 處理圖片 URL
                    if image_url:
                        # 處理相對路徑和協議相對路徑
                        if image_url.startswith("//"):
                            image_url = "https:" + image_url
                        elif image_url.startswith("/"):
                            image_url = "https://24h.pchome.com.tw" + image_url
                        elif not image_url.startswith("http"):
                            if "pchome" not in image_url:
                                image_url = "https://img.pchome.com.tw/" + image_url
                            else:
                                image_url = "https://" + image_url
                    
                    # 如果還是沒找到圖片，嘗試從 JavaScript 變數或 JSON 中提取
                    if not image_url:
                        try:
                            # 嘗試從元素的 data 屬性中找
                            for attr in ['data-image', 'data-img', 'data-pic', 'data-photo']:
                                test_url = element.get_attribute(attr)
                                if test_url and len(test_url) > 10 and test_url.startswith('http'):
                                    image_url = test_url
                                    break
                        except:
                            pass
                    
                    # 最終設為空字串（如果仍未找到）
                    if not image_url:
                        image_url = ""

                    if title and price > 0 and url and sku:
                        # 使用 SKU 或 URL 檢查是否重複
                        is_duplicate = False
                        if sku in seen_skus:
                            is_duplicate = True
                        elif url in [p['url'] for p in products]:
                            is_duplicate = True
                        
                        if is_duplicate:
                            consecutive_duplicates += 1
                            # 如果連續重複太多，提前停止該頁解析
                            if consecutive_duplicates >= max_consecutive_duplicates:
                                print(f"⚠️ 連續 {consecutive_duplicates} 個商品都是重複，提前停止該頁解析")
                                break
                            continue
                        
                        # 找到有效新商品，重置連續重複計數
                        consecutive_duplicates = 0
                        
                        seen_skus.add(sku)
                        product = {
                            "id": product_id,
                            "title": title,
                            "price": price,
                            "image_url": image_url,
                            "url": url,
                            "platform": "pchome",
                            "sku": sku
                        }
                        products.append(product)
                        product_id += 1
                        page_products_count += 1  # 記錄這一頁成功解析的商品數
                        
                        # 📊 回報即時進度（每抓到一個商品就更新）
                        if progress_callback:
                            progress_callback(
                                len(products), 
                                max_products, 
                                f'📦 PChome: 已收集 {len(products)}/{max_products} 筆商品'
                            )

                except (NoSuchElementException, ValueError) as e:
                    continue
            
            print(f"第 {page} 頁找到 {len(product_elements)} 個商品元素，成功解析 {page_products_count} 個有效商品，目前總計 {len(products)} 個商品")
            
            # 🔧 改進：智慧停止判斷
            if page_products_count == 0:
                consecutive_empty_pages += 1
                print(f"⚠️ 第 {page} 頁沒有找到有效商品（連續 {consecutive_empty_pages} 頁為空）")
                
                # 快速判斷：第一頁就沒商品，直接停止
                if page == 1 and len(product_elements) < 10:
                    print("❌ 第一頁就沒有足夠商品元素，判定為搜尋結果為空，停止抓取")
                    break
                # 商品元素很少時停止（真的沒商品了）
                elif len(product_elements) < 10:
                    print("商品元素很少，判定為真正的最後一頁，停止抓取")
                    break
                # 🆕 如果不是第一頁，且有商品元素但解析出 0 個有效商品，直接停止（通常是都重複了）
                elif page > 1 and len(product_elements) >= 10:
                    print(f"❌ 第 {page} 頁有 {len(product_elements)} 個商品元素但解析出 0 個有效商品，判定為已到達搜尋結果末尾（可能都是重複商品），停止抓取")
                    break
                # 如果連續2頁都沒有有效商品，停止（加快判斷）
                elif consecutive_empty_pages >= 2:
                    print(f"連續 {consecutive_empty_pages} 頁都沒有有效商品，停止抓取")
                    break
                else:
                    print(f"但頁面還有商品元素，可能只是被過濾掉（例如重複SKU），繼續嘗試下一頁")
            else:
                # 重置連續空白頁計數器
                consecutive_empty_pages = 0
            
            # 如果已達到目標數量就停止
            if len(products) >= max_products:
                print(f"✅ 已達到目標數量 {max_products} 筆，停止抓取")
                break

            # 點擊下一頁按鈕
            try:
                # 先滾動到頁面底部，確保下一頁按鈕可見
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(1)
                
                # 使用新的選擇器來找到下一頁按鈕
                # 根據 HTML 結構，尋找包含向右箭頭圖示的元素
                next_icon = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "i.o-iconFonts--arrowSolidRight")))
                # 點擊圖示的父元素（應該是可點擊的按鈕）
                next_page_button = next_icon.find_element(By.XPATH, "..")
                driver.execute_script("arguments[0].click();", next_page_button)
                page += 1
                time.sleep(random.uniform(3, 5))
            except (TimeoutException, NoSuchElementException):
                print("找不到下一頁按鈕，抓取結束。")
                break
        
        print(f"成功從 PChome 獲取 {len(products)} 個唯一商品。")
        
        # 📊 回報完成進度
        if progress_callback:
            progress_callback(len(products), max_products, f'✅ PChome 完成！共收集 {len(products)} 筆商品')
        
        return products

    except Exception as e:
        error_msg = str(e)
        if "invalid session id" in error_msg:
            print(f"❌ WebDriver 會話失效（瀏覽器可能崩潰或被關閉）")
            print("💡 建議：檢查系統記憶體是否充足，或嘗試減少抓取數量")
        elif "target window already closed" in error_msg or "no such window" in error_msg:
            print(f"❌ Chrome 視窗已關閉，無法繼續抓取")
        elif "Session info: chrome" in error_msg and "Stacktrace" in error_msg:
            print(f"❌ Chrome 驅動錯誤（可能是視窗被關閉或崩潰）")
        else:
            print(f"PChome Selenium 爬蟲發生錯誤: {e}")
        return products if products else []  # 返回已收集的商品

    finally:
        if driver:
            try:
                driver.quit()
            except:
                pass


def save_to_csv(products, filename, query_keyword, append_mode=True):
    """
    將商品資訊儲存為CSV格式
    
    Args:
        products (list): 商品資訊列表
        filename (str): CSV檔案名稱
        query_keyword (str): 查詢關鍵字
        append_mode (bool): True=追加模式，False=覆蓋模式
    """
    if not products:
        print(f"沒有商品資料可以儲存到 {filename}")
        return
    
    # CSV欄位定義（與你的CSV格式一致）
    fieldnames = [
        'id', 'sku', 'title', 'image', 'url', 'platform', 
        'connect', 'price', 'uncertainty_problem', 'query', 
        'annotator', 'created_at', 'updated_at'
    ]
    
    # 當前時間
    current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
    
    # 檢查檔案是否存在，以及是否需要追加
    file_exists = os.path.exists(filename)
    
    # 如果是追加模式且檔案存在，需要先讀取現有的最大 id
    start_id = 1
    if append_mode and file_exists:
        try:
            import pandas as pd
            existing_df = pd.read_csv(filename)
            if not existing_df.empty and 'id' in existing_df.columns:
                start_id = existing_df['id'].max() + 1
        except Exception as e:
            print(f"讀取現有檔案失敗，將從 id=1 開始: {e}")
            start_id = 1
    
    # 決定開啟模式：追加或覆蓋
    mode = 'a' if (append_mode and file_exists) else 'w'
    
    with open(filename, mode, newline='', encoding='utf-8') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        
        # 只有在新建檔案或覆蓋模式時才寫入表頭
        if mode == 'w':
            writer.writeheader()
        
        for i, product in enumerate(products):
            # 構建CSV行資料（匹配你的格式）
            row = {
                'id': start_id + i,  # 使用連續的 id
                'sku': product['sku'],
                'title': product['title'],
                'image': product['image_url'],
                'url': product['url'],
                'platform': product['platform'],
                'connect': '',  # 空值，如果需要可以後續填入
                'price': f"{product['price']:.2f}",
                'uncertainty_problem': '0',
                'query': query_keyword,
                'annotator': 'model_prediction',
                'created_at': current_time,
                'updated_at': current_time
            }
            writer.writerow(row)
    
    print(f"✅ 成功儲存 {len(products)} 筆商品至 {filename}")


if __name__ == "__main__":
    # 測試爬蟲
    keyword = input("輸入關鍵字: ")
    english_keyword = input("輸入關鍵字的英文名稱: ")
    num = int(input("輸入數量: "))
    
    # 抓取 MOMO 商品
    print("\n=== 開始抓取 MOMO 商品 ===")
    momo_products = fetch_products_for_momo(keyword, num)
    
    # 儲存 MOMO 商品至 CSV 檔案
    save_to_csv(momo_products, "momo.csv", english_keyword)

    if momo_products:
        print(f"\n找到 {len(momo_products)} 個 MOMO 商品：")
        for product in momo_products[:5]:  # 只顯示前5個
            print(f"ID: {product['id']}")
            print(f"標題: {product['title']}")
            print(f"價格: NT$ {product['price']:,}")
            print(f"圖片: {product['image_url']}")
            print(f"連結: {product['url']}")
            print(f"平台: {product['platform']}")
            print("-" * 50)
        if len(momo_products) > 5:
            print(f"... 以及其他 {len(momo_products) - 5} 個商品")
    else:
        print("沒有找到 MOMO 商品")

    # 抓取 PChome 商品
    print("\n=== 開始抓取 PChome 商品 ===")
    pchome_products = fetch_products_for_pchome(keyword, num)
    
    # 儲存 PChome 商品至 CSV 檔案
    save_to_csv(pchome_products, "pchome.csv", english_keyword)

    if pchome_products:
        print(f"\n找到 {len(pchome_products)} 個 PChome 商品：")
        for product in pchome_products[:5]:  # 只顯示前5個
            print(f"ID: {product['id']}")
            print(f"標題: {product['title']}")
            print(f"價格: NT$ {product['price']:,}")
            print(f"圖片: {product['image_url']}")
            print(f"連結: {product['url']}")
            print(f"平台: {product['platform']}")
            print("-" * 50)
        if len(pchome_products) > 5:
            print(f"... 以及其他 {len(pchome_products) - 5} 個商品")
    else:
        print("沒有找到 PChome 商品")
    
    print(f"\n=== 完成！===")
    print(f"MOMO 商品已儲存至: momo.csv")
    print(f"PChome 商品已儲存至: pchome.csv")