"""Stock pools (round 3): named lists of stocks a strategy can copy as its scope."""

from wequant.pools.book import POOLS, NoPool, PoolBook, StockPool
from wequant.pools.parse import REASONS, Parsed, Unparsed, parse

__all__ = ["POOLS", "REASONS", "NoPool", "Parsed", "PoolBook", "StockPool", "Unparsed", "parse"]
