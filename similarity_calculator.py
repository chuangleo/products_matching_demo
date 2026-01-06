"""
預先計算第一階段相似度模組
在爬蟲完成後自動計算所有 MOMO 與 PChome 商品之間的相似度
"""
import pandas as pd
import numpy as np
import torch
from sentence_transformers import SentenceTransformer
import os
import json
from datetime import datetime


def prepare_text(title, platform):
    """準備文本用於編碼"""
    return ("query: " if platform == 'momo' else "passage: ") + str(title)


def get_batch_embeddings(model, texts, batch_size=32):
    """批次計算 embeddings"""
    return model.encode(texts, convert_to_tensor=True, batch_size=batch_size).cpu()


def calculate_similarities_for_all(model, momo_df, pchome_df, threshold=0.739465):
    """
    計算所有商品的相似度
    
    Args:
        model: SentenceTransformer 模型
        momo_df: MOMO 商品 DataFrame
        pchome_df: PChome 商品 DataFrame
        threshold: 相似度門檻
    
    Returns:
        dict: {momo_id: [{pchome_id, similarity, ...}]} 格式的相似度結果
    """
    print(f"\n🔄 開始計算所有商品的相似度...")
    
    momo_products = momo_df.reset_index(drop=True)
    pchome_products = pchome_df.reset_index(drop=True)
    
    if momo_products.empty or pchome_products.empty:
        print(f"⚠️ 商品數據不足，跳過")
        return {}
    
    print(f"  MOMO 商品數: {len(momo_products)}, PChome 商品數: {len(pchome_products)}")
    
    # 準備文本
    momo_texts = [prepare_text(row['title'], 'momo') for _, row in momo_products.iterrows()]
    pchome_texts = [prepare_text(row['title'], 'pchome') for _, row in pchome_products.iterrows()]
    
    # 計算 embeddings
    print("  📊 計算 MOMO 商品特徵向量...")
    momo_embs = get_batch_embeddings(model, momo_texts)
    print("  📊 計算 PChome 商品特徵向量...")
    pchome_embs = get_batch_embeddings(model, pchome_texts)
    
    # 正規化
    momo_embs = torch.nn.functional.normalize(momo_embs, p=2, dim=1)
    pchome_embs = torch.nn.functional.normalize(pchome_embs, p=2, dim=1)
    
    # 計算相似度矩陣
    print("  🔍 計算相似度矩陣...")
    similarity_matrix = torch.mm(momo_embs, pchome_embs.T).numpy()
    
    # 整理結果：只保存超過門檻的配對
    results = {}
    total_matches = 0
    
    for momo_idx, momo_row in momo_products.iterrows():
        momo_id = str(momo_row['id'])
        similarities = similarity_matrix[momo_idx]
        
        # 找出超過門檻的 PChome 商品
        matches = []
        for pchome_idx, similarity in enumerate(similarities):
            if similarity >= threshold:
                pchome_row = pchome_products.iloc[pchome_idx]
                matches.append({
                    'pchome_id': str(pchome_row['id']),
                    'pchome_title': pchome_row['title'],
                    'pchome_price': float(pchome_row.get('price', 0)),
                    'pchome_image': pchome_row.get('image', ''),
                    'pchome_url': pchome_row.get('url', ''),
                    'pchome_sku': pchome_row.get('sku', ''),
                    'similarity': float(similarity)
                })
        
        # 按相似度排序
        matches = sorted(matches, key=lambda x: x['similarity'], reverse=True)
        
        if matches:
            results[momo_id] = matches
            total_matches += len(matches)
    
    print(f"  ✅ 完成！找到 {len(results)} 個 MOMO 商品有配對，共 {total_matches} 組配對")
    return results


def calculate_all_similarities(momo_csv='momo.csv', pchome_csv='pchome.csv', 
                               model_path=None, output_file='similarities.json',
                               threshold=0.739465):
    """
    計算所有類別的商品相似度並保存
    
    Args:
        momo_csv: MOMO 商品 CSV 檔案路徑
        pchome_csv: PChome 商品 CSV 檔案路徑
        model_path: 模型路徑
        output_file: 輸出的 JSON 檔案路徑
        threshold: 相似度門檻
    """
    print("=" * 60)
    print("🚀 開始預先計算商品相似度...")
    print("=" * 60)
    
    # 載入資料
    print("\n📂 載入商品資料...")
    try:
        # 直接讀取 CSV，使用第一行作為表頭
        momo_df = pd.read_csv(momo_csv)
        pchome_df = pd.read_csv(pchome_csv)
        
        # 確保價格是數值型
        momo_df['price'] = pd.to_numeric(momo_df['price'], errors='coerce')
        pchome_df['price'] = pd.to_numeric(pchome_df['price'], errors='coerce')
        
        print(f"  ✅ MOMO: {len(momo_df)} 件商品")
        print(f"  ✅ PChome: {len(pchome_df)} 件商品")
    except Exception as e:
        print(f"  ❌ 資料載入失敗: {e}")
        return False
    
    # 載入模型
    print("\n🤖 載入 SentenceTransformer 模型...")
    if model_path is None:
        model_path = os.path.join("models", "models20-multilingual-e5-large_fold_1")
    
    if not os.path.exists(model_path):
        print(f"  ❌ 找不到模型路徑: {model_path}")
        return False
    
    try:
        model = SentenceTransformer(model_path)
        print(f"  ✅ 模型載入成功")
    except Exception as e:
        print(f"  ❌ 模型載入失敗: {e}")
        return False
    
    # 計算所有商品的相似度（不分類別）
    print(f"\n📋 開始計算所有 MOMO 與 PChome 商品的配對")
    
    all_results = calculate_similarities_for_all(
        model, momo_df, pchome_df, threshold
    )
    
    # 保存結果
    print(f"\n💾 保存相似度結果到 {output_file}...")
    try:
        output_data = {
            'metadata': {
                'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'threshold': threshold,
                'total_momo_products': len(momo_df),
                'total_pchome_products': len(pchome_df),
                'total_matches': len(all_results),
                'momo_csv': momo_csv,
                'pchome_csv': pchome_csv
            },
            'similarities': all_results
        }
        
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(output_data, f, ensure_ascii=False, indent=2)
        
        print(f"  ✅ 成功保存！")
        print("\n" + "=" * 60)
        print("✨ 所有相似度計算完成！")
        print("=" * 60)
        return True
        
    except Exception as e:
        print(f"  ❌ 保存失敗: {e}")
        return False


if __name__ == "__main__":
    # 可以直接執行此腳本來計算相似度
    calculate_all_similarities()
