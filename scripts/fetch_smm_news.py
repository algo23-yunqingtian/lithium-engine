#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SMM新闻搜索脚本 - 通过临时文件接收关键词
用法: python fetch_smm_news.py <temp_file_path>
"""
import sys
import json
import os

def main():
    # 调试：记录脚本开始执行
    import sys as _sys
    print(f"DEBUG: Script started, args={sys.argv}", file=_sys.stderr)
    
    if len(sys.argv) != 2:
        print(json.dumps({'items': [], 'error': 'Usage: fetch_smm_news.py <temp_file_path>'}, ensure_ascii=False))
        sys.exit(1)
    
    temp_file = sys.argv[1]
    print(f"DEBUG: temp_file={temp_file}", file=_sys.stderr)
    
    # 从临时文件读取关键词
    try:
        with open(temp_file, 'r', encoding='utf-8') as f:
            keyword = f.read().strip()
        print(f"DEBUG: keyword='{keyword}', len={len(keyword)}", file=_sys.stderr)
    except Exception as e:
        print(f"DEBUG: Failed to read temp file: {e}", file=_sys.stderr)
        print(json.dumps({'items': [], 'error': f'读取关键词文件失败: {str(e)}'}, ensure_ascii=False))
        sys.exit(1)
    
    if not keyword:
        print(json.dumps({'items': [], 'error': '关键词为空'}, ensure_ascii=False))
        sys.exit(0)
    
    # 搜索新闻
    try:
        import akshare as ak
        news_df = ak.futures_news_shmet(symbol="锌")
        
        # 按关键词过滤
        mask = news_df['内容'].str.contains(keyword, case=False, na=False)
        filtered_df = news_df[mask].head(50)
        
        # 转换为JSON格式
        items = []
        for _, row in filtered_df.iterrows():
            items.append({
                'date': str(row['发布时间'])[:16],
                'title': str(row['内容'])[:200]
            })
        
        print(json.dumps({'items': items}, ensure_ascii=False))
        
    except Exception as e:
        print(json.dumps({'items': [], 'error': f'搜索新闻失败: {str(e)}'}, ensure_ascii=False))
        sys.exit(1)

if __name__ == '__main__':
    main()
