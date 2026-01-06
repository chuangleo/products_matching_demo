import streamlit as st
import pandas as pd
import numpy as np
import torch
from sentence_transformers import SentenceTransformer
import google.generativeai as genai
import os
import json
import time
import sys
import threading
from datetime import datetime
from product_scraper import fetch_products_for_momo, fetch_products_for_pchome, save_to_csv
from similarity_calculator import calculate_all_similarities
from dotenv import load_dotenv

# ============= 全局線程鎖（用於搜尋記錄） =============
log_lock = threading.Lock()

# ============= 用戶峰值追蹤系統 =============
users_lock = threading.Lock()  # 線程鎖
USER_TIMEOUT = 300  # 用戶超時時間（秒），超過此時間視為離線
USERS_FILE = "active_users.json"  # 用戶追蹤文件

# 載入環境變數
load_dotenv()

# ============= 頁面配置 =============
st.set_page_config(
    page_title="購物比價小幫手",
    page_icon="🛒",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ============= 搜尋記錄功能 =============
def log_search_query(keyword, user_session_id, momo_count=0, pchome_count=0):
    """
    記錄用戶搜尋詞到 JSON 文件（線程安全版本）
    
    Args:
        keyword: 搜尋關鍵字
        user_session_id: 用戶 Session ID
        momo_count: MOMO 搜尋結果數量
        pchome_count: PChome 搜尋結果數量
    """
    log_file = "search_logs.json"
    
    try:
        print(f"🔍 log_search_query 被調用: keyword={keyword}, user={user_session_id}")
        
        # 使用鎖確保線程安全
        with log_lock:
            print(f"🔒 獲得鎖，準備寫入文件: {log_file}")
            
            # 讀取現有記錄
            if os.path.exists(log_file):
                try:
                    with open(log_file, 'r', encoding='utf-8') as f:
                        logs = json.load(f)
                    print(f"📖 讀取到 {len(logs)} 筆現有記錄")
                except json.JSONDecodeError:
                    logs = []
                    print("⚠️ JSON 解析失敗，創建新列表")
            else:
                logs = []
                print("📝 文件不存在，創建新列表")
            
            # 添加新記錄
            log_entry = {
                "timestamp": datetime.now().isoformat(),
                "user_session_id": user_session_id,
                "keyword": keyword,
                "momo_results": momo_count,
                "pchome_results": pchome_count
            }
            logs.append(log_entry)
            print(f"➕ 添加新記錄，現在共 {len(logs)} 筆")
            
            # 寫入文件
            with open(log_file, 'w', encoding='utf-8') as f:
                json.dump(logs, f, ensure_ascii=False, indent=2)
            print(f"💾 成功寫入文件: {log_file}")
    
    except Exception as e:
        # 靜默處理錯誤，不影響主程式
        print(f"❌ 記錄搜尋失敗: {e}")
        import traceback
        traceback.print_exc()

# ============= 用戶峰值追蹤功能 =============
def update_user_peak(user_session_id, action='join'):
    """
    更新用戶峰值記錄（跨進程版本，使用文件存儲）
    
    Args:
        user_session_id: 用戶 Session ID
        action: 'join' 加入或 'leave' 離開
    """
    peak_file = "user_peak.json"
    
    try:
        with users_lock:
            current_time = time.time()
            
            # 從文件讀取當前在線用戶
            if os.path.exists(USERS_FILE):
                try:
                    with open(USERS_FILE, 'r', encoding='utf-8') as f:
                        content = f.read().strip()
                        active_users = json.loads(content) if content else {}
                except (json.JSONDecodeError, ValueError):
                    active_users = {}
            else:
                active_users = {}
            
            # 清理超時用戶（超過 USER_TIMEOUT 秒未活動）
            timeout_users = [uid for uid, last_time in active_users.items() 
                           if current_time - last_time > USER_TIMEOUT]
            for uid in timeout_users:
                del active_users[uid]
                print(f"⏱️ 用戶超時移除: {uid[:8]}...")
            
            # 更新當前在線用戶
            if action == 'join':
                is_new = user_session_id not in active_users
                active_users[user_session_id] = current_time
                if is_new:
                    print(f"👤 新用戶加入: {user_session_id[:8]}...")
                else:
                    print(f"🔄 用戶活動更新: {user_session_id[:8]}...")
            elif action == 'leave':
                if user_session_id in active_users:
                    del active_users[user_session_id]
                    print(f"👋 用戶離開: {user_session_id[:8]}...")
            
            # 寫回文件
            with open(USERS_FILE, 'w', encoding='utf-8') as f:
                json.dump(active_users, f, ensure_ascii=False, indent=2)
            
            current_online = len(active_users)
            user_list = [uid[:8] + "..." for uid in list(active_users.keys())[:3]]
            print(f"📊 當前在線人數: {current_online} | 在線用戶: {user_list}")
            
            # 讀取現有峰值記錄
            if os.path.exists(peak_file):
                try:
                    with open(peak_file, 'r', encoding='utf-8') as f:
                        content = f.read().strip()
                        if content:
                            peak_data = json.loads(content)
                        else:
                            peak_data = {"peak_users": 0, "peak_timestamp": None, "current_online": 0}
                except (json.JSONDecodeError, ValueError):
                    peak_data = {"peak_users": 0, "peak_timestamp": None, "current_online": 0}
            else:
                peak_data = {"peak_users": 0, "peak_timestamp": None, "current_online": 0}
            
            # 更新當前在線人數
            peak_data["current_online"] = current_online
            
            # 檢查是否創造新高峰
            if current_online > peak_data.get("peak_users", 0):
                peak_data["peak_users"] = current_online
                peak_data["peak_timestamp"] = datetime.now().isoformat()
                print(f"🎉 新的峰值紀錄！{current_online} 人同時在線")
            
            # 寫入文件
            with open(peak_file, 'w', encoding='utf-8') as f:
                json.dump(peak_data, f, ensure_ascii=False, indent=2)
                
    except Exception as e:
        print(f"❌ 更新用戶峰值失敗: {e}")
        import traceback
        traceback.print_exc()

# ============= 全域樣式設計 (CSS) =============
st.markdown("""
    <style>
    /* 引入 Google Fonts: Noto Sans TC */
    @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+TC:wght@300;400;500;700&display=swap');

    html, body, [class*="css"] {
        font-family: 'Noto Sans TC', sans-serif;
        color: #333;
    }

    /* 背景優化 */
    .stApp {
        background-color: #f4f7f6;
    }

    /* 標題樣式 */
    h1, h2, h3 {
        font-weight: 700 !important;
        color: #2c3e50;
    }

    /* 側邊欄美化 */
    [data-testid="stSidebar"] {
        background-color: #ffffff;
        border-right: 1px solid #e0e0e0;
        box-shadow: 2px 0 10px rgba(0,0,0,0.02);
    }

    /* 按鈕優化 */
    .stButton>button {
        border-radius: 50px;
        font-weight: 600;
        border: none;
        box-shadow: 0 4px 6px rgba(50, 50, 93, 0.11), 0 1px 3px rgba(0, 0, 0, 0.08);
        transition: all 0.2s;
    }
    .stButton>button:hover {
        transform: translateY(-2px);
        box-shadow: 0 7px 14px rgba(50, 50, 93, 0.1), 0 3px 6px rgba(0, 0, 0, 0.08);
    }
    
    /* 主要按鈕 (Primary) */
    button[kind="primary"] {
        background: linear-gradient(90deg, #4b6cb7 0%, #182848 100%);
        border: none;
    }

    /* 自定義商品卡片容器 */
    .product-card {
        background: white;
        border-radius: 16px;
        padding: 24px;
        box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.1), 0 8px 10px -6px rgba(0, 0, 0, 0.1);
        margin-bottom: 24px;
        border: 1px solid #edf2f7;
        transition: transform 0.2s ease;
    }
    .product-card:hover {
        border-color: #cbd5e0;
    }

    /* 平台標籤 */
    .badge {
        display: inline-block;
        padding: 4px 12px;
        border-radius: 20px;
        font-size: 0.85rem;
        font-weight: 600;
        margin-bottom: 12px;
    }
    .badge-momo {
        background-color: #fff0f5;
        color: #d61f69;
        border: 1px solid #fecdd3;
    }
    .badge-pchome {
        background-color: #eef2ff;
        color: #3730a3;
        border: 1px solid #c7d2fe;
    }

    /* 價格顯示 */
    .price-tag {
        font-family: 'Roboto', sans-serif;
        font-size: 1.5rem;
        font-weight: 800;
        color: #e53e3e;
        margin: 8px 0;
    }
    .price-symbol {
        font-size: 0.9rem;
        color: #718096;
        font-weight: normal;
    }

    /* 結果比對卡片 */
    .match-result-container {
        background: linear-gradient(to right, #ffffff, #fafffd);
        border-left: 6px solid #48bb78;
        border-radius: 8px;
        box-shadow: 0 4px 6px rgba(0,0,0,0.05);
        padding: 20px;
        margin-top: 20px;
    }
    
    .ai-reasoning-box {
        background-color: #f7fafc;
        border-radius: 8px;
        padding: 12px 16px;
        margin-top: 12px;
        border-left: 4px solid #4299e1;
        font-size: 0.95rem;
        line-height: 1.6;
        color: #2d3748;
    }

    /* 進度條樣式微調 */
    .stProgress > div > div > div > div {
        background-image: linear-gradient(to right, #4facfe 0%, #00f2fe 100%);
    }
    
    /* 圖片容器 */
    .img-container {
        width: 100%;
        height: 200px;
        display: flex;
        align-items: center;
        justify-content: center;
        overflow: hidden;
        background-color: #f9f9f9;
        border-radius: 8px;
        margin-bottom: 10px;
        border: 1px solid #e0e0e0;
    }
    .img-container img {
        max-height: 100%;
        max-width: 100%;
        width: auto;
        height: auto;
        object-fit: contain;
        display: block;
    }
    </style>
""", unsafe_allow_html=True)

# ============= 安全配置：從環境變數或 Streamlit secrets 載入 =============
def get_api_key():
    """
    安全地獲取 API Key
    優先順序：Streamlit Secrets > 環境變數 > 側邊欄輸入
    """
    # 1. 嘗試從 Streamlit Secrets 讀取（部署到 Streamlit Cloud 時使用）
    try:
        if hasattr(st, 'secrets') and 'GEMINI_API_KEY' in st.secrets:
            return st.secrets['GEMINI_API_KEY']
    except:
        pass
    
    # 2. 嘗試從環境變數讀取（本地開發時使用）
    api_key = os.getenv('GEMINI_API_KEY')
    if api_key:
        return api_key
    
    # 3. 如果都沒有，返回 None（稍後會要求用戶輸入）
    return None

GEMINI_API_KEY = get_api_key()
GEMINI_MODEL = os.getenv('GEMINI_MODEL', 'gemini-2.5-flash')
MODEL_PATH = os.getenv('MODEL_PATH', os.path.join("models", "models20-multilingual-e5-large_fold_1"))

# 如果沒有 API Key，顯示警告並要求輸入
if not GEMINI_API_KEY:
    st.sidebar.warning("⚠️ 未設定 Gemini API Key")
    GEMINI_API_KEY = st.sidebar.text_input(
        "請輸入 Gemini API Key", 
        type="password",
        help="API Key 不會被保存，僅在當前會話中使用"
    )
    if not GEMINI_API_KEY:
        st.error("請設定 Gemini API Key 才能使用 AI 驗證功能")
        st.info("""
        **設定方式：**
        1. 在專案目錄創建 `.env` 檔案
        2. 添加：`GEMINI_API_KEY=你的API金鑰`
        3. 重新啟動應用程式
        """)
        st.stop()

genai.configure(api_key=GEMINI_API_KEY)

@st.cache_resource
def load_model(path):
    if not os.path.exists(path):
        st.error(f"找不到模型路徑：{path}")
        return None
    return SentenceTransformer(path)

def load_local_data():
    """載入本地預設資料（僅用於初始化示例）"""
    # 先嘗試從根目錄讀取
    momo_path = "momo.csv"
    pchome_path = "pchome.csv"
    
    # 如果根目錄沒有，再試 dataset/test/
    if not os.path.exists(momo_path):
        momo_path = os.path.join("dataset", "test", "momo.csv")
        pchome_path = os.path.join("dataset", "test", "pchome.csv")
    
    try:
        # 直接讀取 CSV，使用第一行作為表頭
        momo_df = pd.read_csv(momo_path, sep=',')
        pchome_df = pd.read_csv(pchome_path, sep=',')
        
        # 移除 dtype=str，讓 pandas 自動推斷類型
        # 確保價格欄位是數值型
        if 'price' in momo_df.columns:
            momo_df['price'] = pd.to_numeric(momo_df['price'], errors='coerce')
        if 'price' in pchome_df.columns:
            pchome_df['price'] = pd.to_numeric(pchome_df['price'], errors='coerce')
            
        return momo_df, pchome_df
    except Exception as e:
        return pd.DataFrame(), pd.DataFrame()

def calculate_similarities_in_memory(momo_df, pchome_df, model, direction="momo_to_pchome"):
    """在內存中計算相似度（不寫入文件）
    
    Args:
        momo_df: MOMO 商品資料
        pchome_df: PChome 商品資料
        model: 語意模型
        direction: 比對方向，"momo_to_pchome" 或 "pchome_to_momo"
    """
    if momo_df.empty or pchome_df.empty:
        return {}
    
    try:
        # 準備文本
        momo_texts = [prepare_text(title, 'momo') for title in momo_df['title']]
        pchome_texts = [prepare_text(title, 'pchome') for title in pchome_df['title']]
        
        # 計算嵌入向量
        momo_embeddings = get_batch_embeddings(model, momo_texts)
        pchome_embeddings = get_batch_embeddings(model, pchome_texts)
        
        # 計算相似度
        similarities = {}
        threshold = 0.739465
        
        if direction == "momo_to_pchome":
            # MOMO → PChome（預設）
            for idx, momo_row in momo_df.iterrows():
                momo_id = str(momo_row['id'])
                momo_emb = momo_embeddings[idx].unsqueeze(0)
                
                # 計算與所有 PChome 商品的相似度
                cos_similarities = torch.nn.functional.cosine_similarity(
                    momo_emb, pchome_embeddings, dim=1
                ).numpy()
                
                # 找出超過門檻的商品
                matches = []
                for pchome_idx, score in enumerate(cos_similarities):
                    if score >= threshold:
                        pchome_row = pchome_df.iloc[pchome_idx]
                        matches.append({
                            'target_id': str(pchome_row['id']),
                            'target_title': pchome_row['title'],
                            'target_price': pchome_row.get('price'),
                            'target_image': pchome_row.get('image', ''),
                            'target_url': pchome_row.get('url', ''),
                            'similarity': float(score)
                        })
                
                # 按相似度排序
                matches.sort(key=lambda x: x['similarity'], reverse=True)
                similarities[momo_id] = matches
        else:
            # PChome → MOMO
            for idx, pchome_row in pchome_df.iterrows():
                pchome_id = str(pchome_row['id'])
                pchome_emb = pchome_embeddings[idx].unsqueeze(0)
                
                # 計算與所有 MOMO 商品的相似度
                cos_similarities = torch.nn.functional.cosine_similarity(
                    pchome_emb, momo_embeddings, dim=1
                ).numpy()
                
                # 找出超過門檻的商品
                matches = []
                for momo_idx, score in enumerate(cos_similarities):
                    if score >= threshold:
                        momo_row = momo_df.iloc[momo_idx]
                        matches.append({
                            'target_id': str(momo_row['id']),
                            'target_title': momo_row['title'],
                            'target_price': momo_row.get('price'),
                            'target_image': momo_row.get('image', ''),
                            'target_url': momo_row.get('url', ''),
                            'similarity': float(score)
                        })
                
                # 按相似度排序
                matches.sort(key=lambda x: x['similarity'], reverse=True)
                similarities[pchome_id] = matches
        
        return similarities
    except Exception as e:
        st.error(f"計算相似度時發生錯誤: {e}")
        return {}

def prepare_text(title, platform):
    return ("query: " if platform == 'momo' else "passage: ") + str(title)

def get_single_embedding(model, text):
    return model.encode([text], convert_to_tensor=True).cpu()

def get_batch_embeddings(model, texts):
    return model.encode(texts, convert_to_tensor=True).cpu()

def gemini_verify_match(momo_title, pchome_title, similarity_score, momo_price=0, pchome_price=0):
    prompt = f"""你是一個電商產品匹配專家。請判斷以下兩個商品是否為同一個產品。

商品 A (Momo)：{momo_title}
商品 A 價格：NT$ {momo_price:,.0f}
商品 B (PChome)：{pchome_title}
商品 B 價格：NT$ {pchome_price:,.0f}
第一階段相似度：{similarity_score:.4f}

請嚴格依照以下規則判斷：

**核心匹配規則**：
1. **品牌與型號**：必須完全一致（注意：不同語言的品牌名稱，如 "Logitech" 和 "羅技" 是同一品牌）。
2. **規格變體**：主要規格（如容量 128G vs 256G）不同視為「不同商品」。
3. **顏色差異**：**相同產品的不同顏色，一律視為「相同商品」**（例如：黑色 iPhone 和白色 iPhone 視為同一商品）。**判斷理由中請明確說明顏色差異**，格式如：「相同商品(顏色不同)。MOMO: 黑色 vs PChome: 白色」。如果有顏色代碼，也請列出。
4. **包裝數量差異**：**相同產品的不同包裝數量，一律視為「相同商品」**（例如：60包衛生紙 vs 10包衛生紙視為同一商品）。
5. **口味差異**：**相同產品的不同口味，一律視為「相同商品」**。特別注意：如果一個商品標示多種口味選項（如「香辣+鹽焗」），另一個商品只標示其中一種口味（如「鹽焗」），視為相同商品的不同口味選項。
6. **福利品 vs 全新品**：**相同產品的福利品與全新品，一律視為「相同商品」**。福利品通常標示為「福利品」「展示品」「整新品」「二手」等。**判斷理由中必須特別註記福利品資訊**。

**嚴格排除規則（以下情況視為不同商品，絕對不可匹配）**：
1. **組合包 vs 單品**：單品 ≠ 多品項組合包/套組（關鍵字：「組合」「套組」「+其他商品」「贈品」，但注意：同商品的「×2」「×3」「多入」屬於包裝數量差異，應視為相同）
2. **原廠 vs 副廠/相容配件**：原廠商品 ≠ 副廠/相容/通用商品（關鍵字：「副廠」「相容」「適用」「通用」「compatible」）
3. **限量/特殊版本 vs 一般版本**：一般商品 ≠ 限量版/特殊版本（但不包括福利品，福利品應視為相同商品）

**判斷理由格式要求**：
- 如果兩個商品是相同產品但包裝數量不同，請在理由中加上「單件價格比較」
- 格式範例：「相同商品(包裝量不同)。單價：MOMO $19.98/包 vs PChome $23.90/包」
- **如果兩個商品是相同產品但顏色不同，請在理由中明確說明顏色差異**
- 格式範例：「相同商品(顏色不同)。MOMO: 米白(FD4328-100) vs PChome: 米白酒紅(FD4328-107)」
- **如果其中一個商品是福利品，必須特別註記**
- 格式範例：「相同商品(福利品)。MOMO: 全新 vs PChome: 福利品」或「相同商品(福利品)。注意PChome為展示品」
- 計算方式：從商品標題中提取數量（如「60包」「10包」「3串」），用總價除以數量得到單價
- **重要：單價比較時必須使用「MOMO」和「PChome」作為平台名稱，不可使用 A/B 或其他代號**
- 如果無法提取數量，則不顯示單價資訊

請回傳純 JSON 格式：
{{
    "is_match": true 或 false,
    "confidence": "high" 或 "medium" 或 "low",
    "reasoning": "請用繁體中文簡述判斷理由 (50字以內，包裝量不同時請用'MOMO'和'PChome'標示單價)"
}}
"""
    try:
        model = genai.GenerativeModel(GEMINI_MODEL)
        response = model.generate_content(prompt)
        text = response.text.strip()
        if '```json' in text:
            text = text.split('```json')[1].split('```')[0].strip()
        elif '```' in text:
            text = text.split('```')[1].split('```')[0].strip()
        return json.loads(text)
    except Exception as e:
        return {"is_match": False, "confidence": "low", "reasoning": f"API 錯誤: {str(e)}"}

def gemini_verify_batch(match_pairs, direction="momo_to_pchome"):
    """批次驗證商品配對（一次處理一個來源商品的所有候選商品）
    
    Args:
        match_pairs: list of dict, 每個 dict 包含 {'momo_title', 'pchome_title', 'momo_price', 'pchome_price', 'similarity'}
        direction: 比對方向，"momo_to_pchome" 或 "pchome_to_momo"
    
    Returns:
        list of dict: 每個結果包含 {'is_match', 'confidence', 'reasoning'}
    """
    if not match_pairs:
        return []
    
    # 根據比對方向設定平台名稱
    if direction == "momo_to_pchome":
        platform_a = "MOMO"
        platform_b = "PChome"
    else:
        platform_a = "PChome"
        platform_b = "MOMO"
    
    # 構建批次 prompt
    prompt = f"""你是一個電商產品匹配專家。以下是「一個 {platform_a} 商品」與「多個 {platform_b} 候選商品」的比對任務。

**重要提示**：
- 這些 {platform_b} 商品都是同一個 {platform_a} 商品的潛在匹配候選
- **請獨立判斷每一個配對，不要受其他配對結果影響**
- **即使其中某個商品已經匹配，其他商品仍可能同樣匹配**（不同賣家販售相同商品是正常的）
- **即使所有商品都不匹配也完全正常**（請不要因為候選數量多就強行找出匹配）
- 可能的結果：0 個匹配、1 個匹配、或多個匹配，都是合理的
- 每個配對都應該獨立地通過相同的嚴格標準

請嚴格依照以下規則判斷：

**核心匹配規則**：
1. **品牌與型號**：必須完全一致（注意：不同語言的品牌名稱，如 "Logitech" 和 "羅技" 是同一品牌）。
2. **規格變體**：主要規格（如容量 128G vs 256G）不同視為「不同商品」。
3. **顏色差異**：**相同產品的不同顏色，一律視為「相同商品」**（例如：黑色 iPhone 和白色 iPhone 視為同一商品）。**判斷理由中請明確說明顏色差異**，格式如：「相同商品(顏色不同)。{platform_a}: 黑色 vs {platform_b}: 白色」。如果有顏色代碼，也請列出。
4. **包裝數量差異**：**相同產品的不同包裝數量，一律視為「相同商品」**（例如：60包衛生紙 vs 10包衛生紙視為同一商品，請在理由中提供單件價格比較）。
5. **口味差異**：**相同產品的不同口味，一律視為「相同商品」**。特別注意：如果一個商品標示多種口味選項（如「香辣+鹽焗」），另一個商品只標示其中一種口味（如「鹽焗」），視為相同商品的不同口味選項。
6. **福利品 vs 全新品**：**相同產品的福利品與全新品，一律視為「相同商品」**。福利品通常標示為「福利品」「展示品」「整新品」「二手」等。**判斷理由中必須特別註記福利品資訊**。

**嚴格排除規則（以下情況視為不同商品，絕對不可匹配）**：
1. **組合包 vs 單品**：單品 ≠ 多品項組合包/套組（關鍵字：「組合」「套組」「+其他商品」「贈品」，但注意：同商品的「×2」「×3」「多入」屬於包裝數量差異，應視為相同）
2. **原廠 vs 副廠/相容配件**：原廠商品 ≠ 副廠/相容/通用商品（關鍵字：「副廠」「相容」「適用」「通用」「compatible」）
3. **限量/特殊版本 vs 一般版本**：一般商品 ≠ 限量版/特殊版本（但不包括福利品，福利品應視為相同商品）

**判斷理由格式要求（針對包裝數量不同的情況）**：
- 如果是相同商品但包裝數量不同，請計算並顯示單件價格
- 格式範例：「相同商品(包裝量不同)。單價：{platform_a} $19.98/包 vs {platform_b} $23.90/包」
- **如果是相同商品但顏色不同，請明確說明顏色差異**
- 格式範例：「相同商品(顏色不同)。{platform_a}: 米白(FD4328-100) vs {platform_b}: 米白酒紅(FD4328-107)」
- **如果其中一個商品是福利品，必須特別註記**
- 格式範例：「相同商品(福利品)。{platform_a}: 全新 vs {platform_b}: 福利品」或「相同商品(福利品)。注意{platform_b}為展示品」
- 從商品標題提取數量資訊（如「60包」「10包」「90抽x10包」「3串」），用總價除以數量計算單價
- 如果標題中有多個數字（如「90抽x60包」），優先使用「包」「入」「盒」「組」「串」等單位的數量
- **重要：單價比較時必須明確使用「{platform_a}」和「{platform_b}」作為平台名稱，不可使用 A/B 或商品A/商品B 等代號**

---

"""
    
    # 添加每組商品配對（包含價格資訊）
    for i, pair in enumerate(match_pairs, 1):
        momo_price = pair.get('momo_price', 0)
        pchome_price = pair.get('pchome_price', 0)
        prompt += f"""【配對 {i}】
商品 A ({platform_a})：{pair['momo_title']}
商品 A 價格：NT$ {momo_price:,.0f}
商品 B ({platform_b})：{pair['pchome_title']}
商品 B 價格：NT$ {pchome_price:,.0f}
第一階段相似度：{pair['similarity']:.4f}

"""
    
    prompt += f"""請針對以上 {len(match_pairs)} 組商品配對，分別判斷並回傳純 JSON 陣列格式：
[
    {{"is_match": true/false, "confidence": "high/medium/low", "reasoning": "繁體中文理由(50字內，包裝量不同時用'{platform_a}'和'{platform_b}'標示單價)"}},
    {{"is_match": true/false, "confidence": "high/medium/low", "reasoning": "繁體中文理由(50字內，包裝量不同時用'{platform_a}'和'{platform_b}'標示單價)"}},
    ...
]

請確保陣列中有 {len(match_pairs)} 個結果，順序對應上述配對順序。"""
    
    try:
        model = genai.GenerativeModel(GEMINI_MODEL)
        response = model.generate_content(prompt)
        text = response.text.strip()
        
        # 解析 JSON
        if '```json' in text:
            text = text.split('```json')[1].split('```')[0].strip()
        elif '```' in text:
            text = text.split('```')[1].split('```')[0].strip()
        
        results = json.loads(text)
        
        # 確保返回正確數量的結果
        if len(results) != len(match_pairs):
            # 如果數量不匹配，返回預設錯誤結果
            return [{"is_match": False, "confidence": "low", "reasoning": "批次處理錯誤"} for _ in match_pairs]
        
        return results
    
    except Exception as e:
        # 發生錯誤時，返回相應數量的錯誤結果
        return [{"is_match": False, "confidence": "low", "reasoning": f"API 錯誤: {str(e)}"} for _ in match_pairs]

# ============= 初始化 Session State =============
if 'momo_df' not in st.session_state:
    # 嘗試載入示例數據，如果沒有就用空 DataFrame
    momo_df, pchome_df = load_local_data()
    st.session_state.momo_df = momo_df
    st.session_state.pchome_df = pchome_df
if 'scraping_done' not in st.session_state:
    st.session_state.scraping_done = False
if 'similarities' not in st.session_state:
    st.session_state.similarities = {}
if 'user_session_id' not in st.session_state:
    # 為每個用戶生成唯一 ID（使用 UUID 確保絕對唯一性）
    import uuid
    st.session_state.user_session_id = str(uuid.uuid4())
    print(f"🆕 創建新用戶 ID: {st.session_state.user_session_id}")
if 'cancel_search' not in st.session_state:
    st.session_state.cancel_search = False
if 'is_searching' not in st.session_state:
    st.session_state.is_searching = False

# 每次頁面運行時更新用戶活動狀態（表示用戶仍在線）
update_user_peak(st.session_state.user_session_id, 'join')

# ============= 搜尋商品函數 =============
def handle_product_search(keyword, model, momo_progress_placeholder, momo_status_placeholder, pchome_progress_placeholder, pchome_status_placeholder):
    """處理商品搜尋的函數（多用戶安全版本 + 並行爬取 + 進度條）"""
    if not keyword:
        st.error("請填寫商品名稱！")
        return False
    
    # 設置搜尋狀態
    st.session_state.is_searching = True
    st.session_state.cancel_search = False
    
    # 固定參數
    max_products = 100

    
    # 使用多線程和隊列
    import threading
    import queue
    
    # 創建隊列來傳遞進度信息
    momo_queue = queue.Queue()
    pchome_queue = queue.Queue()
    
    # 存儲結果的容器
    results = {'momo': None, 'pchome': None}
    
    # 使用線程安全的標誌來控制取消（避免在子線程中訪問 session_state）
    cancel_flag = {'value': False}
    
    # 取消檢查函數
    def is_cancelled():
        return cancel_flag['value']
    
    def fetch_momo():
        try:
            # 定義回調函數 - 將進度放入隊列
            def momo_callback(current, total, message):
                momo_queue.put({'current': current, 'total': total, 'message': message})
            
            results['momo'] = fetch_products_for_momo(keyword, max_products, momo_callback, is_cancelled)
            momo_queue.put({'done': True})  # 標記完成
        except Exception as e:
            results['momo'] = []
            momo_queue.put({'error': str(e)})
    
    def fetch_pchome():
        try:
            # 定義回調函數 - 將進度放入隊列
            def pchome_callback(current, total, message):
                pchome_queue.put({'current': current, 'total': total, 'message': message})
            
            results['pchome'] = fetch_products_for_pchome(keyword, max_products, pchome_callback, is_cancelled)
            pchome_queue.put({'done': True})  # 標記完成
        except Exception as e:
            results['pchome'] = []
            pchome_queue.put({'error': str(e)})
    
    # 創建並啟動線程
    momo_thread = threading.Thread(target=fetch_momo, daemon=True)
    pchome_thread = threading.Thread(target=fetch_pchome, daemon=True)
    
    momo_thread.start()
    pchome_thread.start()
    
    # 輪詢隊列並更新 UI
    momo_done = False
    pchome_done = False
    
    while not (momo_done and pchome_done):
        # 檢查是否被取消（同步 session_state 到 cancel_flag）
        if st.session_state.cancel_search:
            cancel_flag['value'] = True
            print("❌ 用戶取消搜尋")
            momo_status_placeholder.warning("⚠️ 搜尋已被取消")
            pchome_status_placeholder.warning("⚠️ 搜尋已被取消")
            st.session_state.is_searching = False
            return False
        
        # 更新 MOMO 進度
        if not momo_done:
            try:
                momo_data = momo_queue.get_nowait()
                if 'done' in momo_data:
                    momo_done = True
                elif 'error' in momo_data:
                    momo_status_placeholder.error(f"❌ 錯誤: {momo_data['error']}")
                    momo_done = True
                elif 'current' in momo_data:
                    progress = min(momo_data['current'] / momo_data['total'], 1.0)
                    momo_progress_placeholder.progress(progress)
                    momo_status_placeholder.info(momo_data['message'])
            except queue.Empty:
                pass
        
        # 更新 PChome 進度
        if not pchome_done:
            try:
                pchome_data = pchome_queue.get_nowait()
                if 'done' in pchome_data:
                    pchome_done = True
                elif 'error' in pchome_data:
                    pchome_status_placeholder.error(f"❌ 錯誤: {pchome_data['error']}")
                    pchome_done = True
                elif 'current' in pchome_data:
                    progress = min(pchome_data['current'] / pchome_data['total'], 1.0)
                    pchome_progress_placeholder.progress(progress)
                    pchome_status_placeholder.info(pchome_data['message'])
            except queue.Empty:
                pass
        
        # 短暫休眠避免過度輪詢
        time.sleep(0.1)
    
    # 等待線程完全結束
    momo_thread.join(timeout=1)
    pchome_thread.join(timeout=1)
    
    # 清除進度條
    momo_progress_placeholder.empty()
    pchome_progress_placeholder.empty()
    
    # 處理 MOMO 結果
    momo_products = results['momo']
    if momo_products:
        momo_status_placeholder.success(f"✅ 找到 {len(momo_products)} 件商品")
        # 直接轉換為 DataFrame 存入 session state
        st.session_state.momo_df = pd.DataFrame(momo_products)
        # 重命名 image_url 為 image（匹配顯示代碼的欄位名稱）
        if 'image_url' in st.session_state.momo_df.columns:
            st.session_state.momo_df.rename(columns={'image_url': 'image'}, inplace=True)
        if 'price' in st.session_state.momo_df.columns:
            st.session_state.momo_df['price'] = pd.to_numeric(st.session_state.momo_df['price'], errors='coerce')
    else:
        momo_status_placeholder.warning("⚠️ 沒有找到相關商品")
        st.session_state.momo_df = pd.DataFrame()
    
    # 處理 PChome 結果
    pchome_products = results['pchome']
    if pchome_products:
        pchome_status_placeholder.success(f"✅ 找到 {len(pchome_products)} 件商品")
        # 直接轉換為 DataFrame 存入 session state
        st.session_state.pchome_df = pd.DataFrame(pchome_products)
        # 重命名 image_url 為 image（匹配顯示代碼的欄位名稱）
        if 'image_url' in st.session_state.pchome_df.columns:
            st.session_state.pchome_df.rename(columns={'image_url': 'image'}, inplace=True)
        if 'price' in st.session_state.pchome_df.columns:
            st.session_state.pchome_df['price'] = pd.to_numeric(st.session_state.pchome_df['price'], errors='coerce')
    else:
        pchome_status_placeholder.warning("⚠️ 沒有找到相關商品")
        st.session_state.pchome_df = pd.DataFrame()
    
    st.markdown("---")
    
    if not st.session_state.momo_df.empty and not st.session_state.pchome_df.empty:
        st.success("✅ 搜尋完成！")
        
        # 在內存中計算相似度（不寫入文件）
        st.markdown("---")
        st.markdown("### 🔍 正在分析商品...")
        
        calc_progress = st.progress(0, text="處理中，請稍候...")
        
        try:
            calc_progress.progress(30, text="找尋相似產品中...")
            # 在內存中計算相似度，傳入比對方向
            st.session_state.similarities = calculate_similarities_in_memory(
                st.session_state.momo_df,
                st.session_state.pchome_df,
                model,
                direction=st.session_state.get('match_direction', 'momo_to_pchome')
            )
            
            calc_progress.progress(100, text="完成！")
            time.sleep(0.3)
            calc_progress.empty()
            
            st.success("✅ 商品資料準備完成！現在可以選擇商品進行比價了！")
            
            # 記錄搜尋（在 rerun 之前）
            print(f"📝 正在記錄搜尋: {keyword}")
            log_search_query(
                keyword=keyword,
                user_session_id=st.session_state.user_session_id,
                momo_count=len(st.session_state.momo_df),
                pchome_count=len(st.session_state.pchome_df)
            )
            print(f"✅ 搜尋記錄完成")
            
            time.sleep(1)
            st.rerun()
                
        except Exception as e:
            calc_progress.empty()
            st.error(f"計算相似度時發生錯誤: {e}")
    else:
        st.error("搜尋失敗，請重試")
    
    # 重置搜尋狀態
    st.session_state.is_searching = False
    st.session_state.cancel_search = False
    
    return True

# ============= UI 介面 =============

# 頁首區塊
col_header_left, col_header_right = st.columns([3, 1])

with col_header_left:
    st.markdown("# 🛒 購物比價小幫手")
    st.markdown("### 幫您在 MOMO 和 PChome 找到相同商品")

with col_header_right:
    # 搜尋欄在右上角
    with st.form("search_form", clear_on_submit=False):
        # 比對方向選擇
        match_direction = st.radio(
            "比對方向",
            options=["momo_to_pchome", "pchome_to_momo"],
            format_func=lambda x: "📦 MOMO → PChome" if x == "momo_to_pchome" else "📦 PChome → MOMO",
            horizontal=True,
            label_visibility="collapsed"
        )
        search_keyword = st.text_input("商品名稱", placeholder="例如：dyson 吸塵器", label_visibility="collapsed")
        search_button = st.form_submit_button("🔍 搜尋", use_container_width=True, type="primary")

# 處理搜尋（在主畫面中間顯示進度）
if search_button and search_keyword:
    # 儲存比對方向到 session state
    st.session_state.match_direction = match_direction
    
    # 創建置中的進度顯示區域
    st.markdown("<br>", unsafe_allow_html=True)
    
    # 使用 3:6:3 比例，讓進度條在中間，兩側留白
    _, center_col, _ = st.columns([2, 8, 2])
    
    with center_col:
        st.markdown("""
            <div style='text-align: center; padding: 30px 0 20px 0;'>
                <h3 style='color: #1f77b4; margin: 0;'>
                    🚀 正在搜尋商品中
                </h3>
            </div>
        """, unsafe_allow_html=True)
        
        # 取消按鈕
        if st.button("❌ 取消搜尋", use_container_width=True, type="secondary"):
            st.session_state.cancel_search = True
            st.warning("⚠️ 正在取消搜尋...")
            time.sleep(0.5)
            st.rerun()
        
        # 進度條區域
        prog_col1, prog_col2 = st.columns(2)
        
        with prog_col1:
            st.markdown("""
                <div style='text-align: center; padding: 12px; background: linear-gradient(135deg, #fff0f5 0%, #ffe0f0 100%); border-radius: 12px; margin-bottom: 10px; box-shadow: 0 2px 8px rgba(255, 107, 157, 0.15);'>
                    <h4 style='color: #ff6b9d; margin: 0; font-size: 16px;'>📦 MOMO</h4>
                </div>
            """, unsafe_allow_html=True)
            momo_progress = st.empty()
            momo_status = st.empty()
        
        with prog_col2:
            st.markdown("""
                <div style='text-align: center; padding: 12px; background: linear-gradient(135deg, #fff5f0 0%, #ffe8d9 100%); border-radius: 12px; margin-bottom: 10px; box-shadow: 0 2px 8px rgba(255, 102, 0, 0.15);'>
                    <h4 style='color: #ff6600; margin: 0; font-size: 16px;'>📦 PChome</h4>
                </div>
            """, unsafe_allow_html=True)
            pchome_progress = st.empty()
            pchome_status = st.empty()
    
    # 需要先載入模型
    temp_model = load_model(MODEL_PATH)
    if temp_model:
        # 使用剛創建的 placeholder 執行搜尋
        handle_product_search(search_keyword, temp_model, momo_progress, momo_status, pchome_progress, pchome_status)

st.markdown("---")

# ============= 比對模式（唯一頁面）=============
# 載入資料
momo_df = st.session_state.momo_df
pchome_df = st.session_state.pchome_df

# 載入資源
with st.spinner("系統準備中，請稍候..."):
    model = load_model(MODEL_PATH)

if model is None:
    st.stop()

# ============= 檢查商品資料 =============
if momo_df.empty and pchome_df.empty:
    st.warning("📦 目前系統中還沒有任何商品資料，請點擊上方「🔍 搜尋商品」按鈕來新增商品。")
    st.stop()
elif momo_df.empty:
    st.warning("⚠️ 目前 MOMO 購物網沒有商品資料，請搜尋商品以新增資料。")
    st.stop()
elif pchome_df.empty:
    st.warning("⚠️ 目前 PChome 購物網沒有商品資料，請搜尋商品以新增資料。")
    st.stop()

# 所有 MOMO 商品（不分類別）
momo_products_in_query = momo_df.reset_index(drop=True)
pchome_candidates_pool = pchome_df.reset_index(drop=True)

# 固定相似度門檻為 0.739465
threshold = 0.739465

# 初始化選中的商品索引
if 'selected_product_index' not in st.session_state:
    st.session_state.selected_product_index = None
if 'dialog_open' not in st.session_state:
    st.session_state.dialog_open = False
if 'dialog_key' not in st.session_state:
    st.session_state.dialog_key = 0

# ============= 比對結果 Dialog 函數 =============
@st.dialog("🔍 商品比價結果", width="large")
def show_comparison_dialog(selected_product_row, dialog_key):
    """顯示商品比對結果"""
    
    # 第一步：完全清空對話框內容
    clear_placeholder = st.empty()
    with clear_placeholder:
        st.markdown("")
    
    # 使用商品ID和dialog_key組合作為唯一標識
    unique_key = f"{selected_product_row.get('id', 0)}_{dialog_key}_{int(time.time() * 1000)}"
    
    # 清空佔位符
    clear_placeholder.empty()
    
    # 創建一個全新的容器來包裹所有內容
    main_container = st.container(key=f"dialog_main_{unique_key}")
    
    with main_container:
        # 使用兩欄布局顯示比對結果
        col_main_left, col_main_right = st.columns([1, 2], gap="large")
        
        # --- 左側：顯示選中的商品 ---
        with col_main_left:
            st.markdown("### 🎯 選中的商品")
            
            # 根據比對方向決定顯示的平台標籤
            match_direction = st.session_state.get('match_direction', 'momo_to_pchome')
            if match_direction == 'momo_to_pchome':
                platform_badge = "MOMO 購物網"
                badge_class = "badge-momo"
            else:
                platform_badge = "PChome 購物網"
                badge_class = "badge-pchome"
            
            # 顯示選中商品的詳細卡片
            price = selected_product_row.get('price')
            if pd.isna(price) or price is None:
                price_str = "價格未提供"
            else:
                price_str = f"NT$ {price:,.0f}"
            
            st.markdown(f"""
            <div class="product-card">
                <div class="badge {badge_class}">{platform_badge}</div>
                <div class="img-container">
                    <img src="{selected_product_row.get('image', '')}" 
                         alt="{selected_product_row['title'][:50]}" 
                         loading="lazy"
                         onerror="this.onerror=null; this.src='https://via.placeholder.com/200x200?text=無法載入圖片';">
                </div>
                <h4 style="margin-top:15px; line-height:1.4;">{selected_product_row['title']}</h4>
                <div class="price-tag"><span class="price-symbol">NT$</span> {price_str}</div>
                <div style="color:#718096; font-size:0.9rem; margin-top:10px;">
                    <strong>ID:</strong> {selected_product_row.get('id', 'N/A')}<br>
                    <strong>SKU:</strong> {selected_product_row.get('sku', 'N/A')}
                </div>
                <a href="{selected_product_row.get('url', '#')}" target="_blank" 
                   style="display:block; text-align:center; margin-top:20px; background:#f7f9fc; color:#4a5568; padding:8px; border-radius:8px; text-decoration:none; font-weight:bold; font-size:0.9rem;">
                   開啟商品頁面 ↗
                </a>
            </div>
            """, unsafe_allow_html=True)
        
        # 設定變數以便後續比對邏輯使用
        is_valid_selection = True
        is_new_selection = True  # 每次進入比對頁面都視為新選擇
        
        # --- 右側：Action & Results ---
        with col_main_right:
            # 根據比對方向顯示不同的標題
            match_direction = st.session_state.get('match_direction', 'momo_to_pchome')
            target_platform = "PChome" if match_direction == 'momo_to_pchome' else "MOMO"
            
            # 建立固定的標題
            st.markdown(f"### ⚡ 在 {target_platform} 尋找相同商品")
            progress_container = st.empty()
            
            # 清空區域標記
            clear_marker = st.empty()
            with clear_marker:
                st.markdown("")  # 空白標記用於分隔
            
            # 自動開始比對（當選擇新商品時）
            if is_valid_selection and is_new_selection:
                
                product_id = str(selected_product_row['id'])
                
                # 直接使用預計算的相似度資料
                stage1_matches_list = []
                
                if st.session_state.similarities and product_id in st.session_state.similarities:
                    stage1_matches_list = st.session_state.similarities[product_id]
                
                # 檢查第一階段結果，如果沒有找到則立即顯示
                if not stage1_matches_list:
                    st.warning(f"⚠️ 在 {target_platform} 沒有找到相似的商品")
                    st.info(f"💡 建議：\n- 選擇其他商品再試一次\n- 或直接到 {target_platform} 網站手動搜尋")
                else:
                    candidates_to_verify = stage1_matches_list
                    
                    # 一次性處理所有候選商品
                    verified_results = []
                    
                    # 建立進度條顯示比對進度
                    overall_progress = st.progress(0, text="正在使用 AI 分析所有候選商品...")
                    
                    # 檢查候選商品數量，設定最大限制
                    MAX_CANDIDATES_PER_CALL = 50
                    
                    if len(candidates_to_verify) > MAX_CANDIDATES_PER_CALL:
                        st.warning(f"⚠️ 找到 {len(candidates_to_verify)} 個候選商品，數量較多，將使用前 {MAX_CANDIDATES_PER_CALL} 個進行比對")
                        candidates_to_verify = candidates_to_verify[:MAX_CANDIDATES_PER_CALL]
                    
                    # 準備所有配對資料（包含價格資訊）
                    all_pairs = [
                        {
                            'momo_title': selected_product_row['title'],
                            'momo_price': float(selected_product_row.get('price', 0)),
                            'pchome_title': match['target_title'],
                            'pchome_price': float(match.get('target_price', 0)),
                            'similarity': match['similarity']
                        }
                        for match in candidates_to_verify
                    ]
                    
                    # 記錄開始時間
                    stage2_start_time = time.time()
                    
                    # 單次 API 呼叫處理所有配對，傳入比對方向
                    all_results = gemini_verify_batch(all_pairs, direction=match_direction)
                    
                    # 記錄結束時間
                    stage2_end_time = time.time()
                    stage2_duration = stage2_end_time - stage2_start_time
                    
                    # 將結果與商品配對
                    for match, result in zip(candidates_to_verify, all_results):
                        verified_results.append({
                            'match': match,
                            'result': result,
                            'is_match': result.get('is_match', False)
                        })
                    
                    # 統計配對成功數量
                    matched_count = sum(1 for r in verified_results if r['is_match'])
                    
                    # 記錄性能數據到 JSON
                    performance_log = {
                        "timestamp": datetime.now().isoformat(),
                        "source_product_id": str(selected_product_row.get('id', 'N/A')),
                        "source_product_title": selected_product_row['title'],
                        "stage2_duration_seconds": round(stage2_duration, 3),
                        "total_candidates_tested": len(candidates_to_verify),
                        "matched_count": matched_count
                    }
                    
                    # 寫入 JSON 文件（追加模式）
                    performance_file = "stage2_performance.json"
                    try:
                        if os.path.exists(performance_file):
                            try:
                                with open(performance_file, 'r', encoding='utf-8') as f:
                                    content = f.read().strip()
                                    if content:
                                        performance_logs = json.loads(content)
                                    else:
                                        performance_logs = []
                            except (json.JSONDecodeError, ValueError):
                                # 文件損壞或為空，重新創建
                                performance_logs = []
                        else:
                            performance_logs = []
                        
                        performance_logs.append(performance_log)
                        
                        with open(performance_file, 'w', encoding='utf-8') as f:
                            json.dump(performance_logs, f, ensure_ascii=False, indent=2)
                    except Exception as e:
                        print(f"❌ 記錄性能數據失敗: {e}")
                    
                    overall_progress.empty()
                    
                    # 使用快速排序算法自動按價格排序（低到高）
                    def quicksort_by_price(items):
                        """快速排序：按價格從低到高排序，配對成功的商品優先"""
                        if len(items) <= 1:
                            return items
                        
                        # 先分離配對成功和未配對的商品
                        matched = [item for item in items if item['is_match']]
                        unmatched = [item for item in items if not item['is_match']]
                        
                        # 分別對兩組進行快速排序
                        def quicksort(arr):
                            if len(arr) <= 1:
                                return arr
                            pivot = arr[len(arr) // 2]
                            pivot_price = pivot['match'].get('target_price', float('inf'))
                            if pd.isna(pivot_price):
                                pivot_price = float('inf')
                                
                            left = [x for x in arr if (x['match'].get('target_price', float('inf')) if not pd.isna(x['match'].get('target_price')) else float('inf')) < pivot_price]
                            middle = [x for x in arr if (x['match'].get('target_price', float('inf')) if not pd.isna(x['match'].get('target_price')) else float('inf')) == pivot_price]
                            right = [x for x in arr if (x['match'].get('target_price', float('inf')) if not pd.isna(x['match'].get('target_price')) else float('inf')) > pivot_price]
                            
                            return quicksort(left) + middle + quicksort(right)
                        
                        return quicksort(matched) + quicksort(unmatched)
                    
                    # 自動排序
                    verified_results = quicksort_by_price(verified_results)
                    
                    # 統計配對成功數量
                    verified_count = sum(1 for r in verified_results if r['is_match'])
                    
                    # 逐一輸出排序後的結果
                    for idx, item in enumerate(verified_results):
                        match = item['match']
                        result = item['result']
                        
                        # 更新固定在上方的進度條
                        with progress_container:
                            st.progress((idx + 1) / len(verified_results), text=f"正在顯示結果 ({idx + 1}/{len(verified_results)})...")
                        
                        # 根據結果顯示不同樣式
                        if result.get('is_match'):
                            card_style = "border-left: 6px solid #48bb78; background: #f0fff4;" # Green match
                            icon = "✅ 配對成功 (MATCH)"
                            text_color = "#2f855a"
                        else:
                            card_style = "border-left: 6px solid #f56565; background: #fff5f5;" # Red mismatch
                            icon = "❌ 未配對 (Mismatch)"
                            text_color = "#c53030"

                        # 結果卡片渲染
                        st.markdown(f"""
                        <div class="product-card" style="{card_style} padding: 20px; display: flex; align-items: start; gap: 20px; margin-bottom: 15px;">
                            <div style="width: 120px; flex-shrink: 0; text-align: center;">
                                <div class="badge badge-pchome" style="margin-bottom: 5px;">{target_platform}</div>
                                <img src="{match.get('target_image', '')}" 
                                     alt="{match['target_title'][:30]}"
                                     loading="lazy"
                                     style="width: 100%; height: auto; max-height: 120px; border-radius: 4px; object-fit: contain; display: block;" 
                                     onerror="this.onerror=null; this.src='https://via.placeholder.com/120x120?text=無法載入圖片';">
                            </div>
                            <div style="flex-grow: 1;">
                                <div style="display: flex; justify-content: space-between; align-items: start;">
                                    <h4 style="margin: 0; font-size: 1.1rem; color: #2d3748;">{match['target_title']}</h4>
                                    <span style="font-weight: bold; color: {text_color}; white-space: nowrap; margin-left: 10px;">{icon}</span>
                                </div>
                                <div style="margin-top: 8px; display: flex; gap: 15px; font-size: 0.9rem; color: #4a5568;">
                                    <span>💰 <strong>NT$ {match.get('target_price', 0) if match.get('target_price') and not pd.isna(match.get('target_price')) else '價格未提供'}</strong></span>
                                </div>
                                <div class="ai-reasoning-box">
                                    <strong>💡 判斷理由：</strong>{result.get('reasoning', '無詳細理由')}
                                </div>
                                <div style="margin-top: 8px; text-align: right;">
                                    <a href="{match.get('target_url', '#')}" target="_blank" style="color: #3182ce; text-decoration: none; font-size: 0.85rem;">查看商品詳情 &rarr;</a>
                                </div>
                            </div>
                        </div>
                        """, unsafe_allow_html=True)
                        
                        # 每輸出一個商品後延遲，讓用戶看到逐一輸出的效果
                        time.sleep(2.0)
                        
                    # 清除進度條
                    progress_container.empty()
                    
                    # 所有商品顯示完畢後顯示統計結果
                    st.markdown("---")
                    if verified_count == 0:
                        st.info("👀 已檢查所有候選商品，但沒有找到完全相同的商品。")
                    else:
                        st.success(f"🎉 完成！在 PChome 找到 {verified_count} 件相同商品（已按價格低到高排序）")

# ============= 主內容區 =============

# 顯示完整商品網格
# 根據比對方向決定顯示哪個平台的商品
match_direction = st.session_state.get('match_direction', 'momo_to_pchome')

if match_direction == 'momo_to_pchome':
    st.markdown("## 🛍️ MOMO 購物網商品列表")
    source_platform = "MOMO"
    target_platform = "PChome"
    display_df = momo_products_in_query
else:
    st.markdown("## 🛍️ PChome 購物網商品列表")
    source_platform = "PChome"
    target_platform = "MOMO"
    display_df = pchome_candidates_pool

# 根據是否有相似商品分類
if st.session_state.similarities:
    # 分類商品：有相似商品 vs 無相似商品
    products_with_matches = []
    products_without_matches = []
    
    for idx, row in display_df.iterrows():
        product_id = str(row['id'])
        if product_id in st.session_state.similarities and st.session_state.similarities[product_id]:
            products_with_matches.append((idx, row))
        else:
            products_without_matches.append((idx, row))
    
    # 顯示有相似商品的部分
    if products_with_matches:
        st.markdown(f"### ✅ 有找到相似商品 ({len(products_with_matches)} 件)")
        st.markdown(f"這些商品在 {target_platform} 找到了相似的商品，點擊查看詳細比價")
        
        cols_per_row = 4
        for i in range(0, len(products_with_matches), cols_per_row):
            row_products = products_with_matches[i:i+cols_per_row]
            cols = st.columns(cols_per_row)
            for col_idx, (prod_idx, row) in enumerate(row_products):
                with cols[col_idx]:
                    price = row.get('price')
                    if pd.isna(price) or price is None:
                        price_str = "價格未提供"
                    else:
                        price_str = f"NT$ {price:,.0f}"
                    
                    # 商品卡片 - 綠色邊框表示有匹配
                    card_html = f"""
                    <div class="momo-grid-card" style="border-color: #48bb78; min-height: 450px; display: flex; flex-direction: column;">
                        <div class="momo-grid-img-container">
                            <img src="{row.get('image', '')}" 
                                 class="momo-grid-img"
                                 onerror="this.onerror=null; this.src='https://via.placeholder.com/200x200?text=無法載入圖片';">
                        </div>
                        <div class="momo-grid-title" style="flex: 1; min-height: 60px;">{row['title']}</div>
                        <div class="momo-grid-price" style="color: #48bb78;">{price_str}</div>
                        <div class="momo-grid-info" style="margin-bottom: 10px;">
                            ID: {row.get('id', 'N/A')}
                        </div>
                    </div>
                    """
                    st.markdown(card_html, unsafe_allow_html=True)
                    
                    # 點擊按鈕
                    if st.button(
                        "🔍 查看比價",
                        key=f"view_comparison_{prod_idx}",
                        use_container_width=True,
                        type="primary"
                    ):
                        st.session_state.selected_product_index = prod_idx
                        st.session_state.dialog_open = True
                        st.session_state.dialog_key += 1
                        st.rerun()
        
        st.markdown("---")
    
    # 顯示無相似商品的部分
    if products_without_matches:
        st.markdown("### ⚠️ 未找到相似商品 ({} 件)".format(len(products_without_matches)))
        st.markdown("這些商品在 PChome 沒有找到相似的商品")
        
        cols_per_row = 4
        for i in range(0, len(products_without_matches), cols_per_row):
            row_products = products_without_matches[i:i+cols_per_row]
            cols = st.columns(cols_per_row)
            for col_idx, (prod_idx, row) in enumerate(row_products):
                with cols[col_idx]:
                    price = row.get('price')
                    if pd.isna(price) or price is None:
                        price_str = "價格未提供"
                    else:
                        price_str = f"NT$ {price:,.0f}"
                    
                    # 商品卡片 - 灰色邊框表示無匹配
                    card_html = f"""
                    <div class="momo-grid-card" style="border-color: #cbd5e0; opacity: 0.7; min-height: 450px; display: flex; flex-direction: column;">
                        <div class="momo-grid-img-container">
                            <img src="{row.get('image', '')}" 
                                 class="momo-grid-img"
                                 onerror="this.onerror=null; this.src='https://via.placeholder.com/200x200?text=無法載入圖片';">
                        </div>
                        <div class="momo-grid-title" style="flex: 1; min-height: 60px;">{row['title']}</div>
                        <div class="momo-grid-price" style="color: #718096;">{price_str}</div>
                        <div class="momo-grid-info" style="margin-bottom: 10px;">
                            ID: {row.get('id', 'N/A')}
                        </div>
                    </div>
                    """
                    st.markdown(card_html, unsafe_allow_html=True)
                    
                    # 點擊按鈕
                    if st.button(
                        "🔍 查看詳情",
                        key=f"view_comparison_{prod_idx}",
                        use_container_width=True
                    ):
                        st.session_state.selected_momo_index = prod_idx
                        st.session_state.dialog_open = True
                        st.session_state.dialog_key += 1
                        st.rerun()
else:
    # 如果還沒有相似度數據，顯示所有商品（初始狀態）
    st.markdown("點擊商品卡片查看 PChome 比價結果")
    
    cols_per_row = 4
    rows = [momo_products_in_query[i:i+cols_per_row] for i in range(0, len(momo_products_in_query), cols_per_row)]

    for row_products in rows:
        cols = st.columns(cols_per_row)
        for col_idx, (prod_idx, row) in enumerate(row_products.iterrows()):
            with cols[col_idx]:
                price = row.get('price')
                if pd.isna(price) or price is None:
                    price_str = "價格未提供"
                else:
                    price_str = f"NT$ {price:,.0f}"
                
                # 商品卡片
                card_html = f"""
                <div class="momo-grid-card" style="min-height: 450px; display: flex; flex-direction: column;">
                    <div class="momo-grid-badge">#{prod_idx+1}</div>
                    <div class="momo-grid-img-container">
                        <img src="{row.get('image', '')}" 
                             class="momo-grid-img"
                             onerror="this.onerror=null; this.src='https://via.placeholder.com/200x200?text=無法載入圖片';">
                    </div>
                    <div class="momo-grid-title" style="flex: 1; min-height: 60px;">{row['title']}</div>
                    <div class="momo-grid-price">{price_str}</div>
                    <div class="momo-grid-info" style="margin-bottom: 10px;">
                        ID: {row.get('id', 'N/A')}
                    </div>
                </div>
                """
                st.markdown(card_html, unsafe_allow_html=True)
                
                # 點擊按鈕
                if st.button(
                    "🔍 查看比價",
                    key=f"view_comparison_{prod_idx}",
                    use_container_width=True,
                    type="primary"
                ):
                    st.session_state.selected_product_index = prod_idx
                    st.session_state.dialog_open = True
                    st.session_state.dialog_key += 1
                    st.rerun()

# 檢查是否需要顯示 dialog
if st.session_state.dialog_open and st.session_state.selected_product_index is not None:
    # 根據比對方向選擇正確的商品資料源
    match_direction = st.session_state.get('match_direction', 'momo_to_pchome')
    if match_direction == 'momo_to_pchome':
        selected_product_row = momo_products_in_query.iloc[st.session_state.selected_product_index]
    else:
        selected_product_row = pchome_candidates_pool.iloc[st.session_state.selected_product_index]
    
    show_comparison_dialog(selected_product_row, st.session_state.dialog_key)
    # Dialog 關閉後清除狀態
    st.session_state.dialog_open = False
    st.session_state.selected_product_index = None