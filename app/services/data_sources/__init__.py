"""
Data sources subpackage.
Expose adapters and manager for backward-compatible imports.
"""
from .base import DataSourceAdapter
from .a_stock_data_adapter import AStockDataAdapter
from .tushare_adapter import TushareAdapter
from .akshare_adapter import AKShareAdapter
from .baostock_adapter import BaoStockAdapter
from .manager import DataSourceManager
