#!/usr/bin/env python3
"""抓取各品种近一年日线收盘价，统一成 data.json。只用标准库。

统一格式:
  {"updated": "2026-11-12 08:30", "items": [
     {"id": "spx", "group": "股指", "name": "标普500", "unit": "点",
      "source": "eastmoney", "points": [["2025-11-12", 6850.92], ...]}, ...]}

每个品种按顺序尝试多个数据源，第一个拿到足够数据的胜出；
全部失败时保留 data.json 里上一次的数据。
"""
import csv
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/126 Safari/537.36"}
TODAY = date.today()
START = TODAY - timedelta(days=370)
MIN_POINTS = 100
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data.json")

# 每个品种: 基本信息 + 按优先级排列的数据源 [(来源名, 参数), ...]
ITEMS = [
    dict(id="spx", group="股指", name="标普500", unit="点",
         src=[("eastmoney", "100.SPX"), ("sina_us", ".inx"), ("tencent", "us.INX"), ("fred", "SP500"), ("yahoo", "^GSPC"), ("stooq", "^spx")]),
    dict(id="ixic", group="股指", name="纳斯达克", unit="点",
         src=[("eastmoney", "100.NDX"), ("sina_us", ".ixic"), ("tencent", "us.IXIC"), ("fred", "NASDAQCOM"), ("yahoo", "^IXIC"), ("stooq", "^ndq")]),
    dict(id="dji", group="股指", name="道琼斯", unit="点",
         src=[("eastmoney", "100.DJIA"), ("sina_us", ".dji"), ("tencent", "us.DJI"), ("fred", "DJIA"), ("yahoo", "^DJI"), ("stooq", "^dji")]),
    dict(id="hsi", group="股指", name="恒生指数", unit="点",
         src=[("tencent", "hkHSI"), ("eastmoney", "100.HSI"), ("yahoo", "^HSI"), ("stooq", "^hsi")]),
    dict(id="sh", group="股指", name="上证指数", unit="点",
         src=[("eastmoney", "1.000001"), ("sina_cn", "sh000001"), ("tencent", "sh000001"), ("yahoo", "000001.SS"), ("stooq", "^shc")]),
    dict(id="sz", group="股指", name="深证成指", unit="点",
         src=[("eastmoney", "0.399001"), ("sina_cn", "sz399001"), ("tencent", "sz399001"), ("yahoo", "399001.SZ")]),
    dict(id="n225", group="股指", name="日经225", unit="点",
         src=[("sina_global", "NKY"), ("eastmoney", "100.N225"), ("fred", "NIKKEI225"), ("yahoo", "^N225"), ("stooq", "^nkx")]),
    dict(id="dax", group="股指", name="德国DAX", unit="点",
         src=[("eastmoney", "100.GDAXI"), ("sina_global", "DAX"), ("investing_stooq", "^dax"),
              ("yahoo", "^GDAXI"), ("stooq", "^dax")]),
    dict(id="gold-ny", group="商品", name="黄金(纽约)", unit="美元/盎司",
         src=[("eastmoney", "101.GC00Y"), ("sina_fut", "GC"), ("yahoo", "GC=F"), ("stooq", "gc.f")]),
    dict(id="gold-sh", group="商品", name="黄金(上海)", unit="人民币/克",
         src=[("eastmoney", "118.AU9999"), ("sina_cn_fut", "AU0")]),
    dict(id="wti", group="商品", name="WTI原油", unit="美元/桶",
         src=[("eastmoney", "102.CL00Y"), ("sina_fut", "CL"), ("fred", "DCOILWTICO"), ("yahoo", "CL=F"), ("stooq", "cl.f")]),
    dict(id="brent", group="商品", name="布伦特原油", unit="美元/桶",
         src=[("eastmoney", "112.B00Y"), ("sina_fut", "OIL"), ("fred", "DCOILBRENTEU"), ("yahoo", "BZ=F")]),
    dict(id="btc", group="加密货币", name="BTC", unit="美元",
         src=[("coingecko", "bitcoin"), ("yahoo", "BTC-USD")]),
    dict(id="eth", group="加密货币", name="ETH", unit="美元",
         src=[("coingecko", "ethereum"), ("yahoo", "ETH-USD")]),
    dict(id="usd", group="汇率", name="1美元", unit="人民币",
         src=[("frankfurter", "USD"), ("yahoo", "CNY=X")]),
    dict(id="hkd", group="汇率", name="1港币", unit="人民币",
         src=[("frankfurter", "HKD"), ("yahoo", "HKDCNY=X")]),
    dict(id="jpy", group="汇率", name="100日元", unit="人民币", scale=100,
         src=[("frankfurter", "JPY"), ("yahoo", "JPYCNY=X")]),
]


def get(url, headers=None, timeout=20):
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    for attempt in range(2):   # 偶发的超时/断连重试一次；429 之类的 HTTP 错误不重试
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError:
            raise
        except Exception:  # noqa: BLE001
            if attempt == 1:
                raise
            time.sleep(2)


def get_json(url, headers=None):
    return json.loads(get(url, headers))


# ---- 各数据源：都返回 [(YYYY-MM-DD, 收盘价), ...] ----

def src_eastmoney(secid):
    url = ("https://push2his.eastmoney.com/api/qt/stock/kline/get"
           f"?secid={secid}&ut=fa5fd1943c7b386f172d6893dbfba10b"
           "&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f53&klt=101&fqt=1&end=20500101&lmt=260")
    for attempt in range(3):   # 东方财富经常直接断开连接，重试几次
        try:
            data = get_json(url, {"Referer": "https://quote.eastmoney.com/"})["data"]
            break
        except Exception:  # noqa: BLE001
            if attempt == 2:
                raise
            time.sleep(3)
    return [(k.split(",")[0], float(k.split(",")[1])) for k in data["klines"]]


def _sina_rows(url):
    text = get(url, {"Referer": "https://finance.sina.com.cn"}).decode("utf-8", "replace")
    rows = json.loads(text[text.index("["):text.rindex("]") + 1])
    out = []
    for r in rows:
        d = r.get("d") or r.get("day") or r.get("date")
        c = r.get("c") or r.get("close")
        if d and c:
            out.append((str(d)[:10], float(c)))
    return out


def src_sina_us(sym):
    return _sina_rows("https://stock.finance.sina.com.cn/usstock/api/jsonp.php/x/"
                      f"US_MinKService.getDailyK?symbol={sym}")


def src_sina_cn(sym):
    return _sina_rows("https://quotes.sina.cn/cn/api/jsonp_v2.php/x/"
                      f"CN_MarketDataService.getKLineData?symbol={sym}&scale=240&ma=no&datalen=260")


def src_sina_fut(sym):
    return _sina_rows("https://stock2.finance.sina.com.cn/futures/api/jsonp.php/x/"
                      f"GlobalFuturesService.getGlobalFuturesDailyKLine?symbol={sym}")


def src_sina_cn_fut(sym):
    return _sina_rows("https://stock2.finance.sina.com.cn/futures/api/jsonp.php/x/"
                      f"InnerFuturesNewService.getDailyKLine?symbol={sym}")


def src_sina_global(sym):
    return _sina_rows("https://stock.finance.sina.com.cn/usstock/api/jsonp.php/x/"
                      f"Global_IndexService.getDayLine?symbol={sym}&num=260")


def src_investing_stooq(sym):
    # stooq 的另一个入口（不带日期范围）
    text = get(f"https://stooq.com/q/d/l/?s={urllib.parse.quote(sym)}&i=d").decode("utf-8", "replace")
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows or "Close" not in rows[0]:
        raise ValueError("stooq 返回: " + text[:80].replace("\n", " "))
    return [(r["Date"], float(r["Close"])) for r in rows if r.get("Close")]


def src_tencent(code):
    j = get_json(f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={code},day,,,260,qfq")
    d = j["data"][code]
    rows = d.get("day") or d.get("qfqday")
    return [(r[0], float(r[2])) for r in rows]   # [日期, 开, 收, 高, 低, 量]


def src_yahoo(sym):
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(sym)}"
           "?range=1y&interval=1d")
    r = get_json(url)["chart"]["result"][0]
    closes = r["indicators"]["quote"][0]["close"]
    return [(datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d"), c)
            for t, c in zip(r["timestamp"], closes) if c is not None]


def src_stooq(sym):
    text = get(f"https://stooq.com/q/d/l/?s={urllib.parse.quote(sym)}&i=d"
               f"&d1={START:%Y%m%d}&d2={TODAY:%Y%m%d}").decode("utf-8", "replace")
    rows = list(csv.DictReader(io.StringIO(text)))
    return [(r["Date"], float(r["Close"])) for r in rows if r.get("Close")]


def src_fred(series):
    text = get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}&cosd={START}").decode()
    out = []
    for row in list(csv.reader(io.StringIO(text)))[1:]:
        try:
            out.append((row[0], float(row[1])))
        except (ValueError, IndexError):
            continue  # FRED 用 "." 表示休市日
    return out


def src_coingecko(coin):
    j = get_json(f"https://api.coingecko.com/api/v3/coins/{coin}/market_chart"
                 "?vs_currency=usd&days=365&interval=daily")
    return [(datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d"), p)
            for ms, p in j["prices"]]


def src_frankfurter(base):
    j = get_json(f"https://api.frankfurter.app/{START}..{TODAY}?from={base}&to=CNY")
    return [(d, v["CNY"]) for d, v in j["rates"].items()]


SOURCES = {name[4:]: fn for name, fn in globals().items() if name.startswith("src_")}


LOG = []


def log(msg):
    print(msg)
    LOG.append(msg)


def clean(points, scale):
    """去重、排序、截到近一年、统一小数位。"""
    by_date = {d: p for d, p in points if p and START.isoformat() <= d <= TODAY.isoformat()}
    return [[d, round(by_date[d] * scale, 4)] for d in sorted(by_date)]


def fetch_item(item):
    # 新浪在 GitHub 的机器上最稳定，优先用；其余按表里的顺序
    ordered = sorted(item["src"], key=lambda t: not t[0].startswith(("sina", "tencent")))
    for name, arg in ordered:
        try:
            points = clean(SOURCES[name](arg), item.get("scale", 1))
            if len(points) >= MIN_POINTS:
                log(f"[ok]   {item['name']}: {name}，{len(points)} 个点，"
                      f"{points[0][0]} ~ {points[-1][0]}")
                return name, points
            log(f"[skip] {item['name']}: {name} 只有 {len(points)} 个点")
        except Exception as e:  # noqa: BLE001
            log(f"[fail] {item['name']}: {name}: {type(e).__name__}: {str(e)[:120]}")
    return None, None


def main():
    old = {}
    if os.path.exists(OUT):
        with open(OUT, encoding="utf-8") as f:
            old = {i["id"]: i for i in json.load(f).get("items", [])}
    items, missing = [], []
    for item in ITEMS:
        source, points = fetch_item(item)
        base = {k: item[k] for k in ("id", "group", "name", "unit")}
        if points:
            items.append({**base, "source": source, "points": points})
        elif item["id"] in old:
            log(f"[keep] {item['name']}: 全部失败，沿用上次数据")
            items.append({**base, "source": old[item["id"]].get("source"),
                          "points": old[item["id"]]["points"], "stale": True})
        else:
            missing.append(item["name"])
            items.append({**base, "source": None, "points": []})
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"updated": f"{now:%Y-%m-%d %H:%M}", "items": items}, f,
                  ensure_ascii=False, separators=(",", ":"))
    with open(os.path.join(os.path.dirname(OUT), "log.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(LOG) + "\n")
    print(f"\n写入 {OUT}；无数据的品种: {missing or '无'}")
    if len(missing) == len(ITEMS):
        sys.exit("所有品种都失败")


if __name__ == "__main__":
    main()
