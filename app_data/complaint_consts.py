# app_data/complaint_consts.py

"""
投诉业务常量。
"""

# 批次大小：按 (分类, 书籍) 分组后，每组每 N 条切一批
COMPLAINT_BATCH_SIZE = 200

# 指定哪些平台需要【单条打包】（一条生成一个独立批次）
SINGLE_ITEM_PLATFORMS = {
    "wenxin",
    "weibo",
    # "其他需要单条的平台代号",
}

# 暂未在 PLATFORM_CONFIG 注册，但需要识别为网盘的域名
MISC_NETDISK_DOMAINS = []

# ==========================================
# ★ 证据材料契约配置（新增）
# ==========================================

# 1. 默认的书籍材料（比如夸克、UC，只要 PDF 维权书）
DEFAULT_BOOK_EVIDENCE_TYPES = ["auth_pdf"]

# 2. 特殊平台要求的书籍材料
PLATFORM_SPECIFIC_BOOK_EVIDENCES = {
    # ★ 请确保这里的 4 个名字，和你数据库 evidences 表里的 ev_type 完全一致！
    # 比如截图里存的可能是 site_backend, site_backend_doc, site_home, auth_pdf(如果是图的话)
    "wenxin": ["site_backend", "site_backend_doc", "site_home", "book_auth"],
}

# 3. 权利人必须的材料（身份证正反面）
HOLDER_EVIDENCE_TYPES = ["id_card_front", "id_card_back"]