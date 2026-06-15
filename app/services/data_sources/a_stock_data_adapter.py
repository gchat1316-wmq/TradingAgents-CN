"""
a-stock-data compatible data source adapter.

This adapter follows the a-stock-data approach: direct HTTP calls to public
A-share endpoints, with EastMoney requests routed through a small throttled
session. BaoStock and AKShare remain fallback sources in DataSourceManager.
"""
from __future__ import annotations

import json
import logging
import random
import re
import threading
import time
import uuid
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from urllib.parse import quote

import pandas as pd
import requests

from .base import DataSourceAdapter

logger = logging.getLogger(__name__)

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
EM_MIN_INTERVAL = 1.0


class AStockDataAdapter(DataSourceAdapter):
    """a-stock-data direct HTTP adapter for A-share data."""

    def __init__(self):
        super().__init__()
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": UA})
        self._em_last_call = 0.0
        self._em_lock = threading.Lock()
        self._cninfo_orgid_map: Dict[str, str] = {}

    @property
    def name(self) -> str:
        return "a-stock-data"

    def _get_default_priority(self) -> int:
        return 3

    def is_available(self) -> bool:
        return True

    def _em_get(self, url: str, params: Optional[dict] = None, headers: Optional[dict] = None, timeout: int = 15):
        """EastMoney request helper with a-stock-data style throttling."""
        with self._em_lock:
            wait = EM_MIN_INTERVAL - (time.time() - self._em_last_call)
            if wait > 0:
                time.sleep(wait + random.uniform(0.1, 0.5))
            try:
                return self._session.get(url, params=params, headers=headers, timeout=timeout)
            finally:
                self._em_last_call = time.time()

    def _safe_float(self, value) -> Optional[float]:
        try:
            if value in (None, "", "-", "None"):
                return None
            return float(value)
        except (ValueError, TypeError):
            return None

    def _normalize_code(self, code: str) -> str:
        digits = "".join(ch for ch in str(code or "") if ch.isdigit())
        return digits[-6:].zfill(6) if digits else ""

    def _market_id(self, code: str) -> int:
        return 1 if code.startswith(("6", "9")) else 0

    def _ts_code(self, code: str, market_id: Optional[int] = None) -> str:
        if market_id == 1 or code.startswith(("6", "9")):
            return f"{code}.SH"
        if market_id == 0 or code.startswith(("0", "2", "3")):
            return f"{code}.SZ"
        if code.startswith(("4", "8")):
            return f"{code}.BJ"
        return f"{code}.SZ"

    def _market_name(self, code: str) -> str:
        if code.startswith("688"):
            return "科创板"
        if code.startswith("300"):
            return "创业板"
        if code.startswith(("4", "8")):
            return "北交所"
        if code.startswith(("002", "003")):
            return "中小板"
        return "主板"

    def _clist(self, page_size: int = 6000) -> List[dict]:
        url = "https://push2.eastmoney.com/api/qt/clist/get"
        params = {
            "pn": "1",
            "pz": str(page_size),
            "po": "1",
            "np": "1",
            "fltt": "2",
            "invt": "2",
            "fid": "f3",
            "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81,m:0+t:82",
            "fields": "f2,f3,f5,f6,f8,f9,f12,f13,f14,f20,f21,f23,f100",
        }
        headers = {"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"}
        response = self._em_get(url, params=params, headers=headers, timeout=20)
        response.raise_for_status()
        return ((response.json().get("data") or {}).get("diff") or [])

    def get_stock_list(self) -> Optional[pd.DataFrame]:
        try:
            rows = []
            for item in self._clist():
                code = self._normalize_code(item.get("f12"))
                name = str(item.get("f14") or "")
                if not code or not name:
                    continue
                market_id = item.get("f13")
                rows.append({
                    "symbol": code,
                    "name": name,
                    "ts_code": self._ts_code(code, market_id),
                    "area": "",
                    "industry": str(item.get("f100") or ""),
                    "market": self._market_name(code),
                    "list_date": "",
                })
            if not rows:
                return None
            df = pd.DataFrame(rows)
            logger.info("a-stock-data: fetched %s stocks from EastMoney clist", len(df))
            return df
        except Exception as e:
            logger.error("a-stock-data: failed to fetch stock list: %s", e)
            return None

    def get_daily_basic(self, trade_date: str) -> Optional[pd.DataFrame]:
        try:
            rows = []
            for item in self._clist():
                code = self._normalize_code(item.get("f12"))
                if not code:
                    continue
                market_id = item.get("f13")
                rows.append({
                    "ts_code": self._ts_code(code, market_id),
                    "trade_date": trade_date,
                    "name": str(item.get("f14") or ""),
                    "close": self._safe_float(item.get("f2")),
                    "total_mv": self._safe_float(item.get("f20")),
                    "circ_mv": self._safe_float(item.get("f21")),
                    "turnover_rate": self._safe_float(item.get("f8")),
                    "pe": self._safe_float(item.get("f9")),
                    "pb": self._safe_float(item.get("f23")),
                })
            return pd.DataFrame(rows) if rows else None
        except Exception as e:
            logger.error("a-stock-data: failed to fetch daily basic data: %s", e)
            return self._daily_basic_from_tencent(trade_date)

    def find_latest_trade_date(self) -> Optional[str]:
        # Direct HTTP realtime endpoints do not expose an exchange calendar.
        return (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")

    def get_realtime_quotes(self) -> Optional[Dict[str, Dict[str, Optional[float]]]]:
        try:
            result: Dict[str, Dict[str, Optional[float]]] = {}
            for item in self._clist():
                code = self._normalize_code(item.get("f12"))
                if not code:
                    continue
                result[code] = {
                    "close": self._safe_float(item.get("f2")),
                    "pct_chg": self._safe_float(item.get("f3")),
                    "volume": self._safe_float(item.get("f5")),
                    "amount": self._safe_float(item.get("f6")),
                    "turnover_rate": self._safe_float(item.get("f8")),
                    "pe": self._safe_float(item.get("f9")),
                    "total_mv": self._safe_float(item.get("f20")),
                    "circ_mv": self._safe_float(item.get("f21")),
                    "pb": self._safe_float(item.get("f23")),
                }
            logger.info("a-stock-data: fetched realtime quotes for %s stocks", len(result))
            return result or None
        except Exception as e:
            logger.error("a-stock-data: failed to fetch realtime quotes: %s", e)
            return self._realtime_quotes_from_tencent()

    def _get_baostock_universe(self) -> List[str]:
        """Use BaoStock only as a free stock universe when EastMoney clist is unavailable."""
        try:
            import baostock as bs
        except ImportError:
            return []

        login = bs.login()
        if login.error_code != "0":
            logger.warning("a-stock-data: BaoStock universe login failed: %s", login.error_msg)
            return []
        try:
            rs = bs.query_stock_basic()
            if rs.error_code != "0":
                logger.warning("a-stock-data: BaoStock universe query failed: %s", rs.error_msg)
                return []
            codes = []
            while (rs.error_code == "0") & rs.next():
                row = rs.get_row_data()
                if len(row) > 5 and row[4] == "1" and row[5] == "1":
                    codes.append(row[0].replace("sh.", "").replace("sz.", ""))
            return codes
        finally:
            bs.logout()

    def _tencent_quote(self, codes: List[str]) -> Dict[str, Dict[str, Optional[float]]]:
        result: Dict[str, Dict[str, Optional[float]]] = {}
        if not codes:
            return result

        for offset in range(0, len(codes), 120):
            chunk = codes[offset:offset + 120]
            prefixed = []
            for code in chunk:
                code6 = self._normalize_code(code)
                if not code6:
                    continue
                if code6.startswith(("6", "9")):
                    prefixed.append(f"sh{code6}")
                elif code6.startswith("8"):
                    prefixed.append(f"bj{code6}")
                else:
                    prefixed.append(f"sz{code6}")

            url = "https://qt.gtimg.cn/q=" + quote(",".join(prefixed), safe=",")
            response = self._session.get(url, headers={"User-Agent": UA}, timeout=10)
            response.raise_for_status()
            text = response.content.decode("gbk", errors="ignore")
            for line in text.strip().split(";"):
                if not line.strip() or "=" not in line or '"' not in line:
                    continue
                key = line.split("=")[0].split("_")[-1]
                vals = line.split('"')[1].split("~")
                if len(vals) < 53:
                    continue
                code = self._normalize_code(key)
                if not code:
                    continue
                result[code] = {
                    "name": vals[1],
                    "close": self._safe_float(vals[3]),
                    "pre_close": self._safe_float(vals[4]),
                    "open": self._safe_float(vals[5]),
                    "pct_chg": self._safe_float(vals[32]),
                    "high": self._safe_float(vals[33]),
                    "low": self._safe_float(vals[34]),
                    "amount": (self._safe_float(vals[37]) or 0) * 10000,
                    "turnover_rate": self._safe_float(vals[38]),
                    "pe": self._safe_float(vals[39]),
                    "total_mv": self._safe_float(vals[44]),
                    "circ_mv": self._safe_float(vals[45]),
                    "pb": self._safe_float(vals[46]),
                }
        return result

    def _realtime_quotes_from_tencent(self) -> Optional[Dict[str, Dict[str, Optional[float]]]]:
        codes = self._get_baostock_universe()
        if not codes:
            return None
        try:
            result = self._tencent_quote(codes)
            logger.info("a-stock-data: fetched Tencent realtime quotes for %s stocks", len(result))
            return result or None
        except Exception as e:
            logger.error("a-stock-data: Tencent realtime fallback failed: %s", e)
            return None

    def _daily_basic_from_tencent(self, trade_date: str) -> Optional[pd.DataFrame]:
        quotes = self._realtime_quotes_from_tencent()
        if not quotes:
            return None
        rows = []
        for code, item in quotes.items():
            rows.append({
                "ts_code": self._ts_code(code),
                "trade_date": trade_date,
                "name": item.get("name") or "",
                "close": item.get("close"),
                "total_mv": item.get("total_mv"),
                "circ_mv": item.get("circ_mv"),
                "turnover_rate": item.get("turnover_rate"),
                "pe": item.get("pe"),
                "pb": item.get("pb"),
            })
        return pd.DataFrame(rows) if rows else None

    def get_kline(self, code: str, period: str = "day", limit: int = 120, adj: Optional[str] = None):
        try:
            code6 = self._normalize_code(code)
            if not code6:
                return None
            period_map = {
                "day": "101",
                "week": "102",
                "month": "103",
                "5m": "5",
                "15m": "15",
                "30m": "30",
                "60m": "60",
            }
            klt = period_map.get(period)
            if not klt:
                return None
            fqt_map = {None: "0", "": "0", "qfq": "1", "hfq": "2"}
            url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
            params = {
                "secid": f"{self._market_id(code6)}.{code6}",
                "fields1": "f1,f2,f3,f4,f5,f6",
                "fields2": "f51,f52,f53,f54,f55,f56,f57",
                "klt": klt,
                "fqt": fqt_map.get(adj, "0"),
                "end": "20500101",
                "lmt": str(limit),
            }
            headers = {"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"}
            response = self._em_get(url, params=params, headers=headers, timeout=15)
            response.raise_for_status()
            klines = (response.json().get("data") or {}).get("klines") or []
            items = []
            for line in klines[-limit:]:
                parts = str(line).split(",")
                if len(parts) < 7:
                    continue
                items.append({
                    "time": parts[0],
                    "open": self._safe_float(parts[1]),
                    "close": self._safe_float(parts[2]),
                    "high": self._safe_float(parts[3]),
                    "low": self._safe_float(parts[4]),
                    "volume": self._safe_float(parts[5]),
                    "amount": self._safe_float(parts[6]),
                })
            return items or None
        except Exception as e:
            logger.error("a-stock-data: failed to fetch kline for %s: %s", code, e)
            return None

    def get_news(self, code: str, days: int = 2, limit: int = 50, include_announcements: bool = True):
        code6 = self._normalize_code(code)
        if not code6:
            return None
        items = []
        try:
            items.extend(self._stock_news(code6, limit=limit))
        except Exception as e:
            logger.warning("a-stock-data: stock news failed for %s: %s", code6, e)
        if include_announcements and len(items) < limit:
            try:
                items.extend(self._announcements(code6, limit=limit - len(items)))
            except Exception as e:
                logger.warning("a-stock-data: announcements failed for %s: %s", code6, e)
        return items[:limit] if items else None

    def _stock_news(self, code: str, limit: int = 20) -> List[dict]:
        cb = "jQuery_news"
        url = "https://search-api-web.eastmoney.com/search/jsonp"
        inner_params = json.dumps({
            "uid": "",
            "keyword": code,
            "type": ["cmsArticleWebOld"],
            "client": "web",
            "clientType": "web",
            "clientVersion": "curr",
            "param": {"cmsArticleWebOld": {"searchScope": "default", "sort": "default",
                      "pageIndex": 1, "pageSize": limit, "preTag": "", "postTag": ""}},
        }, separators=(",", ":"))
        params = {"cb": cb, "param": inner_params}
        headers = {"User-Agent": UA, "Referer": "https://so.eastmoney.com/"}
        response = self._em_get(url, params=params, headers=headers, timeout=15)
        text = response.text
        json_str = text[text.index("(") + 1:text.rindex(")")]
        data = json.loads(json_str)
        rows = []
        for article in (data.get("result", {}).get("cmsArticleWebOld", []) or []):
            rows.append({
                "title": re.sub(r"<[^>]+>", "", article.get("title", "")),
                "source": article.get("mediaName", "") or "eastmoney",
                "time": article.get("date", ""),
                "url": article.get("url", ""),
                "type": "news",
            })
        return rows

    def _cninfo_ts_to_date(self, value) -> str:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value / 1000).strftime("%Y-%m-%d")
        return str(value)[:10] if value else ""

    def _cninfo_orgid(self, code: str) -> str:
        if not self._cninfo_orgid_map:
            try:
                response = self._session.get(
                    "http://www.cninfo.com.cn/new/data/szse_stock.json",
                    headers={"User-Agent": UA},
                    timeout=15,
                )
                self._cninfo_orgid_map = {
                    item["code"]: item["orgId"]
                    for item in response.json().get("stockList", [])
                    if item.get("code") and item.get("orgId")
                }
            except Exception as e:
                logger.warning("a-stock-data: cninfo orgId map fetch failed: %s", e)
        org_id = self._cninfo_orgid_map.get(code)
        if org_id:
            return org_id
        if code.startswith("6"):
            return f"gssh0{code}"
        if code.startswith(("4", "8")):
            return f"gsbj0{code}"
        return f"gssz0{code}"

    def _announcements(self, code: str, limit: int = 30) -> List[dict]:
        url = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
        payload = {
            "stock": f"{code},{self._cninfo_orgid(code)}",
            "tabName": "fulltext",
            "pageSize": str(limit),
            "pageNum": "1",
            "column": "",
            "category": "",
            "plate": "",
            "seDate": "",
            "searchkey": "",
            "secid": "",
            "sortName": "",
            "sortType": "",
            "isHLtitle": "true",
            "random": str(uuid.uuid4()),
        }
        headers = {
            "User-Agent": UA,
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": "https://www.cninfo.com.cn/new/disclosure",
            "Origin": "https://www.cninfo.com.cn",
        }
        response = self._session.post(url, data=payload, headers=headers, timeout=15)
        data = response.json()
        rows = []
        for item in data.get("announcements", []) or []:
            rows.append({
                "title": item.get("announcementTitle", ""),
                "source": "cninfo",
                "time": self._cninfo_ts_to_date(item.get("announcementTime")),
                "url": f"https://www.cninfo.com.cn/new/disclosure/detail?annoId={item.get('announcementId', '')}",
                "type": "announcement",
            })
        return rows
