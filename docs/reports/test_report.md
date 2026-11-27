# 全量测试报告

- 生成时间：2026-11-25T00:11:29
- 环境：external / cache=redis / nlu=auto
- 用例：22 条，通过 22，跳过 0，失败 0
- 安全动态：合计 36 项，PASS 36，FAIL 0
- 静态扫描：scanned files: 110，FAIL 0，WARN 3
- 前端构建：exit 0

| # | 工具 | 话术 | 意图 | 出卡 | 耗时 | 结果 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | search_manual | KB-60HE 怎么切换连接模式 | presale | 否 | 2576ms | PASS |
| 2 | search_driver | KB-60HE 的驱动在哪里下载 | presale | 否 | 395ms | PASS |
| 3 | search_products | 有没有 60% 配列的键盘 | presale | 否 | 7541ms | PASS |
| 4 | query_spec | KB-60HE 支持 macOS 吗 | presale | 否 | 1678ms | PASS |
| 5 | query_stock | KB-60HE 还有货吗 | presale | 否 | 8078ms | PASS |
| 6 | query_price | KB-60HE 多少钱 | presale | 否 | 1390ms | PASS |
| 7 | query_shipping | 发到广东一般几天到 | chitchat | 否 | 1709ms | PASS |
| 8 | web_search | 帮我搜一下 2026 年国产机械键盘的新品 | chitchat | 否 | 2198ms | PASS |
| 9 | compare_benchmarks | KB-60HE 和竞品的跑分对比 | presale | 否 | 5878ms | PASS |
| 10 | get_bench_facts | 跑分库里 KB-60HE 的延迟数据是多少 | presale | 否 | 4687ms | PASS |
| 11 | search_bench_products | 跑分库里有哪些 60% 键盘 | presale | 否 | 4486ms | PASS |
| 12 | compare_products | KB-60HE 和 KB-PRO87 有什么区别 | presale | 否 | 386ms | PASS |
| 13 | check_compatibility | KB-60HE 和 DOCK-TB4 能一起用吗 | chitchat | 否 | 6656ms | PASS |
| 14 | recommend_config | 预算 3000 配一套键盘和耳机 | presale | 否 | 395ms | PASS |
| 15 | query_policy | 保修多久 | aftersale | 否 | 374ms | PASS |
| 16 | query_order | 查一下我的订单 | insale | 否 | 1118ms | PASS |
| 17 | query_logistics | 查一下订单 AA42 的物流 | insale | 否 | 371ms | PASS |
| 18 | aftersale_accept | 键盘按键失灵了，申请寄修 | aftersale | 是 | 425ms | PASS |
| 19 | compensation | 物流太慢了，能补偿吗 | aftersale | 是 | 8078ms | PASS |
| 20 | diagnose_issue | 键盘连不上电脑怎么办 | aftersale | 否 | 1581ms | PASS |
| 21 | change_address | 把订单 P420260001FF8B 的收货地址改成：山东省烟台市莱山区 | insale | 是 | 391ms | PASS |
| 22 | cancel_order | 取消我的待支付订单 | insale | 是 | 384ms | PASS |
