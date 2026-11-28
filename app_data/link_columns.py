# app_data/link_columns.py

"""盗版链接表格列索引——page_links / links_row_builder / complaint_service 共用，改列只需改这里。

★ 调整列顺序时必须同步：
  1. 下面的 COL_* 编号与 TABLE_HEADERS 顺序
  2. links_row_builder.build_link_row 的行构造顺序
  3. page_links 里按列号持久化的 QSettings key（筛选器、列宽）要升版本，
     否则旧配置会套到新列上。
"""


COL_CHK           = 0
COL_BOOK          = 1
COL_CAT           = 2
COL_URL           = 3
COL_PUBLISHER     = 4
COL_ATTR          = 5
COL_CODE          = 6   # 特征码（紧跟属性，便于对照录入）
COL_HANDLE        = 7   # 处理状况（只读，派生列）
COL_RES_STATUS    = 8
COL_COMP_TIME     = 9
COL_CRAWL_STATUS  = 10
COL_PICKER_STATUS = 11
COL_TITLE         = 12

# 表头文案：顺序必须与上面的列索引一致（page_links 建模型、links_row_builder 建行都用它）
TABLE_HEADERS = [
    "勾选", "关联作品(必填)", "资源分类(必填)", "资源链接(必填，右键预览)", "发布方",
    "属性", "特征码", "处理状况", "资源状态", "投诉时间", "抓取状态", "拾取状态", "标题",
]

# 条目重复时追加在 URL 文本后的后缀（录入/显示时被追加，读写单元格前要先剥掉）
DUP_URL_SUFFIX = " (重复资源链接)"
