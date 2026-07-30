"""GOT 节点模块"""

from src.got.nodes.market_analysis import MarketAnalysisNode
from src.got.nodes.sector_analysis import SectorAnalysisNode
from src.got.nodes.capital_analysis import CapitalAnalysisNode
from src.got.nodes.news_analysis import NewsAnalysisNode
from src.got.nodes.sentiment_analysis import SentimentAnalysisNode
from src.got.nodes.aggregator import AggregatorNode

__all__ = [
    "MarketAnalysisNode",
    "SectorAnalysisNode",
    "CapitalAnalysisNode",
    "NewsAnalysisNode",
    "SentimentAnalysisNode",
    "AggregatorNode",
]
