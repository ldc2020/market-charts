# 全球市场 · 近一年走势

一个静态网页，展示股指、黄金、原油、BTC/ETH、人民币汇率近一年的日线走势。

- `fetch_history.py`：每天由 GitHub Actions 运行，从多个公开接口抓取近一年收盘价，统一写入 `data.json`。
- `index.html`：读取 `data.json` 画图，不依赖任何外部库。通过 GitHub Pages 发布。
- 增删品种：改 `fetch_history.py` 里的 `ITEMS`，每个品种可以配多个数据源，按顺序尝试。

数据来自东方财富、新浪财经、CoinGecko、Frankfurter（欧洲央行汇率）、FRED 等公开接口，仅供参考。
