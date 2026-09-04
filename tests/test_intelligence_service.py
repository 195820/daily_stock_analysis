# -*- coding: utf-8 -*-
"""
Intelligence 情报服务测试（§6 远端整合）

覆盖：
- IntelligenceSourceRecord / IntelligenceItemRecord ORM
- IntelligenceService 源注册 / 采集 / 查询 / list_sources
- 安全工具（SSRF URL 检查 + DNS 解析 / ID 生成 / pubdate 解析）
- NewsNow 响应解析（C3：{"items": [...]}）+ 内存缓存（W4）
- RSS pubdate 解析（W5）
- 便捷函数字段对齐（C4）/ 不短路（W6）/ 幂等初始化（W1）
"""

import json
import socket
from datetime import datetime
from unittest.mock import patch, MagicMock

import pytest


def _fake_response(body: bytes) -> MagicMock:
    """构造一个 stream=True 的假 requests.Response。"""
    resp = MagicMock()
    resp.iter_content.return_value = [body]
    resp.raise_for_status.return_value = None
    resp.close.return_value = None
    return resp


def _addrinfo(ip: str, port: int = 443):
    """构造 socket.getaddrinfo 的返回值（5 元组列表）。"""
    return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, port))]


# =============================================================================
# ORM 测试
# =============================================================================

class TestIntelligenceORM:
    """IntelligenceSourceRecord / IntelligenceItemRecord ORM 测试"""

    def test_source_record_schema(self):
        """验证 IntelligenceSourceRecord 表结构"""
        from src.storage import IntelligenceSourceRecord
        assert IntelligenceSourceRecord.__tablename__ == "intelligence_sources"
        # 关键字段存在性
        assert hasattr(IntelligenceSourceRecord, "source_id")
        assert hasattr(IntelligenceSourceRecord, "source_type")
        assert hasattr(IntelligenceSourceRecord, "template_id")
        assert hasattr(IntelligenceSourceRecord, "enabled")

    def test_item_record_schema(self):
        """验证 IntelligenceItemRecord 表结构"""
        from src.storage import IntelligenceItemRecord
        assert IntelligenceItemRecord.__tablename__ == "intelligence_items"
        # 关键字段存在性
        assert hasattr(IntelligenceItemRecord, "item_id")
        assert hasattr(IntelligenceItemRecord, "source_id")
        assert hasattr(IntelligenceItemRecord, "title")
        assert hasattr(IntelligenceItemRecord, "scope_type")

    def test_item_record_extra_data(self):
        """验证 IntelligenceItemRecord extra_data JSON 序列化"""
        from src.storage import IntelligenceItemRecord
        record = IntelligenceItemRecord()
        record.set_extra_data({"hot_score": 100, "comments": 50})
        assert record.extra_data is not None
        data = record.get_extra_data()
        assert data["hot_score"] == 100
        assert data["comments"] == 50

    def test_item_record_extra_data_empty(self):
        """验证空 extra_data 处理"""
        from src.storage import IntelligenceItemRecord
        record = IntelligenceItemRecord()
        assert record.get_extra_data() == {}
        record.set_extra_data(None)
        assert record.extra_data is None


# =============================================================================
# 安全工具测试
# =============================================================================

class TestSecurityUtils:
    """安全工具测试（SSRF / DNS / item_id / pubdate）"""

    def test_is_safe_url_public(self):
        """公共域名（DNS 解析到公网 IP）应通过"""
        import src.got.intelligence_service as mod
        with patch.object(mod.socket, "getaddrinfo", return_value=_addrinfo("93.184.216.34")):
            assert mod._is_safe_url("https://newsnow.busiyi.world/api/s") is True
            assert mod._is_safe_url("https://example.com/rss") is True

    def test_is_safe_url_private_literal(self):
        """字面量私有 IP 应拒绝（无需 DNS，离线安全）"""
        from src.got.intelligence_service import _is_safe_url
        assert _is_safe_url("http://192.168.1.1/secret") is False
        assert _is_safe_url("http://10.0.0.1/internal") is False
        assert _is_safe_url("http://127.0.0.1/local") is False

    def test_is_safe_url_dns_rebinding(self):
        """W2：公网域名解析到内网 IP 时应拒绝（字面量正则无法覆盖此场景）"""
        import src.got.intelligence_service as mod
        with patch.object(mod.socket, "getaddrinfo", return_value=_addrinfo("127.0.0.1")):
            assert mod._is_safe_url("https://evil.example.com/rss") is False
        with patch.object(mod.socket, "getaddrinfo", return_value=_addrinfo("169.254.169.254")):
            assert mod._is_safe_url("https://evil.example.com/meta") is False

    def test_is_safe_url_dns_failure(self):
        """W2：DNS 解析失败应拒绝"""
        import src.got.intelligence_service as mod
        with patch.object(mod.socket, "getaddrinfo", side_effect=socket.gaierror("NXDOMAIN")):
            assert mod._is_safe_url("https://nonexistent.invalid/rss") is False

    def test_is_safe_url_scheme(self):
        """非 http/https 协议应拒绝"""
        from src.got.intelligence_service import _is_safe_url
        assert _is_safe_url("ftp://example.com/file") is False
        assert _is_safe_url("file:///etc/passwd") is False

    def test_is_private_ip(self):
        """W2：ipaddress 覆盖各类内网/保留段"""
        from src.got.intelligence_service import _is_private_ip
        assert _is_private_ip("10.0.0.1") is True
        assert _is_private_ip("172.16.5.4") is True
        assert _is_private_ip("192.168.0.1") is True
        assert _is_private_ip("169.254.169.254") is True  # 云元数据
        assert _is_private_ip("::1") is True
        assert _is_private_ip("8.8.8.8") is False
        assert _is_private_ip("not-an-ip") is True  # 无法解析 → 保守拒绝

    def test_make_item_id(self):
        """验证 item_id 生成"""
        from src.got.intelligence_service import _make_item_id
        id1 = _make_item_id("测试标题", "newsnow-cls-hot", datetime(2026, 9, 4))
        id2 = _make_item_id("测试标题", "newsnow-cls-hot", datetime(2026, 9, 4))
        id3 = _make_item_id("不同标题", "newsnow-cls-hot", datetime(2026, 9, 4))
        id4 = _make_item_id("测试标题", "newsnow-cls-hot", datetime(2026, 9, 5))
        # 相同输入应生成相同 ID
        assert id1 == id2
        # 不同标题应生成不同 ID
        assert id1 != id3
        # W5：不同发布日期应生成不同 ID（同标题跨日不冲突）
        assert id1 != id4
        # ID 格式：32 字符 MD5
        assert len(id1) == 32

    def test_parse_pubdate(self):
        """W5：RFC 822 / ISO 8601 发布时间解析"""
        from src.got.intelligence_service import _parse_pubdate
        rfc = _parse_pubdate("Mon, 01 Sep 2025 12:00:00 GMT")
        assert rfc is not None and rfc.year == 2025 and rfc.month == 9
        iso = _parse_pubdate("2025-09-01T12:00:00Z")
        assert iso is not None and iso.month == 9
        assert _parse_pubdate("") is None
        assert _parse_pubdate(None) is None
        assert _parse_pubdate("not-a-date") is None


# =============================================================================
# NewsNow 模板测试
# =============================================================================

class TestNewsNowTemplates:
    """NewsNow 模板测试"""

    def test_templates_exist(self):
        """验证内建模板"""
        from src.got.intelligence_service import NEWSNOW_TEMPLATES
        assert "cls-hot" in NEWSNOW_TEMPLATES
        assert "xueqiu-hotstock" in NEWSNOW_TEMPLATES
        assert "weibo" in NEWSNOW_TEMPLATES
        assert "baidu" in NEWSNOW_TEMPLATES

    def test_template_structure(self):
        """验证模板结构"""
        from src.got.intelligence_service import NEWSNOW_TEMPLATES
        for template_id, template in NEWSNOW_TEMPLATES.items():
            assert "name" in template
            assert "scope_type" in template
            assert "scope_value" in template
            assert template["scope_type"] == "market"
            assert template["scope_value"] == "cn"


# =============================================================================
# NewsNow / RSS 采集解析测试（真实解析逻辑，非 mock 同义反复）
# =============================================================================

class TestFetchParsing:
    """C3 / W4 / W5：真实解析逻辑测试（mock HTTP 层，不 mock 被测函数本身）"""

    def test_newsnow_parses_items_dict(self):
        """C3：NewsNow 响应为 {"items": [...]}（dict），此前 isinstance(list) 恒 False 导致空返回"""
        import src.got.intelligence_service as mod
        mod._cache_clear()
        payload = json.dumps({
            "items": [
                {"title": "测试新闻A", "url": "https://a.com", "desc": "摘要A", "hot": 100},
                {"title": "   ", "url": "https://blank.com"},  # 空标题应跳过
                {"title": "测试新闻B", "url": "https://b.com"},
            ]
        }).encode("utf-8")

        with patch.object(mod, "_is_safe_url", return_value=True), \
             patch.object(mod, "_get_session") as mock_session:
            mock_session.return_value.get.return_value = _fake_response(payload)
            items = mod._fetch_newsnow_template("cls-hot")

        assert len(items) == 2  # 空标题被跳过
        assert items[0]["title"] == "测试新闻A"
        assert items[0]["url"] == "https://a.com"
        assert items[0]["summary"] == "摘要A"
        assert items[0]["extra"]["hot_score"] == 100
        assert items[0]["extra"]["template_id"] == "cls-hot"
        mod._cache_clear()

    def test_newsnow_parses_bare_list(self):
        """C3：兼容裸 list 响应（向后兼容）"""
        import src.got.intelligence_service as mod
        mod._cache_clear()
        payload = json.dumps([{"title": "X", "url": "https://x"}]).encode("utf-8")
        with patch.object(mod, "_is_safe_url", return_value=True), \
             patch.object(mod, "_get_session") as mock_session:
            mock_session.return_value.get.return_value = _fake_response(payload)
            items = mod._fetch_newsnow_template("weibo")
        assert len(items) == 1 and items[0]["title"] == "X"
        mod._cache_clear()

    def test_newsnow_cache_hit(self):
        """W4：成功结果缓存，第二次调用不再发 HTTP"""
        import src.got.intelligence_service as mod
        mod._cache_clear()
        payload = json.dumps({"items": [{"title": "T", "url": "u"}]}).encode("utf-8")
        with patch.object(mod, "_is_safe_url", return_value=True), \
             patch.object(mod, "_get_session") as mock_session:
            mock_session.return_value.get.return_value = _fake_response(payload)
            first = mod._fetch_newsnow_template("baidu")
            second = mod._fetch_newsnow_template("baidu")
            assert first == second
            assert mock_session.return_value.get.call_count == 1  # 命中缓存
        mod._cache_clear()

    def test_newsnow_unsafe_url_short_circuits(self):
        """W2：不安全 URL 直接返回空，不发 HTTP"""
        import src.got.intelligence_service as mod
        mod._cache_clear()
        with patch.object(mod, "_is_safe_url", return_value=False), \
             patch.object(mod, "_get_session") as mock_session:
            items = mod._fetch_newsnow_template("cls-hot")
            assert items == []
            mock_session.return_value.get.assert_not_called()
        mod._cache_clear()

    def test_rss_parses_pubdate(self):
        """W5：RSS <pubDate> 应被解析为 published_at"""
        import src.got.intelligence_service as mod
        mod._cache_clear()
        xml = (
            b"<rss><channel>"
            b"<item><title>News A</title><link>https://a</link>"
            b"<description>desc A</description>"
            b"<pubDate>Mon, 01 Sep 2025 12:00:00 GMT</pubDate></item>"
            b"</channel></rss>"
        )
        with patch.object(mod, "_is_safe_url", return_value=True), \
             patch.object(mod, "_get_session") as mock_session:
            mock_session.return_value.get.return_value = _fake_response(xml)
            items = mod._fetch_rss("https://feed.example.com/rss")
        assert len(items) == 1
        assert items[0]["title"] == "News A"
        assert items[0]["summary"] == "desc A"
        assert items[0]["published_at"] is not None
        assert items[0]["published_at"].year == 2025
        mod._cache_clear()


# =============================================================================
# IntelligenceService 测试
# =============================================================================

class TestIntelligenceService:
    """IntelligenceService 主类测试"""

    def test_service_init(self):
        """验证服务初始化"""
        from src.got.intelligence_service import IntelligenceService
        service = IntelligenceService()
        assert service is not None

    def test_list_items_empty(self):
        """验证空数据库查询"""
        from src.got.intelligence_service import IntelligenceService
        mock_db = MagicMock()
        mock_session = MagicMock()
        mock_db.session_scope.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_db.session_scope.return_value.__exit__ = MagicMock(return_value=False)
        mock_session.query.return_value.filter.return_value.all.return_value = []

        service = IntelligenceService(db=mock_db)
        result = service.list_items(days=1)
        assert result["items"] == []
        assert result["total"] == 0

    def test_list_sources_shape(self):
        """W9：list_sources 返回 {"sources": [...], "total": int}"""
        from src.got.intelligence_service import IntelligenceService
        mock_db = MagicMock()
        mock_session = MagicMock()
        mock_db.session_scope.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_db.session_scope.return_value.__exit__ = MagicMock(return_value=False)
        mock_session.query.return_value.order_by.return_value.all.return_value = []

        service = IntelligenceService(db=mock_db)
        result = service.list_sources()
        assert result == {"sources": [], "total": 0}

    def test_register_newsnow_source_invalid(self):
        """验证注册无效 NewsNow 模板"""
        from src.got.intelligence_service import IntelligenceService
        service = IntelligenceService()
        with pytest.raises(ValueError, match="未知 NewsNow 模板"):
            service.register_newsnow_source("invalid-template")


# =============================================================================
# 便捷函数测试
# =============================================================================

class TestConvenienceFunctions:
    """便捷函数测试"""

    def test_get_intelligence_service_singleton(self):
        """验证全局实例单例"""
        from src.got.intelligence_service import get_intelligence_service
        import src.got.intelligence_service as module
        module._service_instance = None
        service1 = get_intelligence_service()
        service2 = get_intelligence_service()
        assert service1 is service2

    @patch("src.got.intelligence_service.get_intelligence_service")
    def test_fetch_intelligence_items_empty(self, mock_get_service):
        """采集 0 + 查询空 → 返回空列表"""
        from src.got.intelligence_service import fetch_intelligence_items
        mock_service = MagicMock()
        mock_service.fetch_enabled_sources.return_value = {}
        mock_service.list_items.return_value = {"items": [], "total": 0}
        mock_get_service.return_value = mock_service
        assert fetch_intelligence_items(days=1) == []

    @patch("src.got.intelligence_service.get_intelligence_service")
    def test_fetch_intelligence_items_no_short_circuit(self, mock_get_service):
        """W6：本次采集 0 新增，仍返回窗口内已持久化条目（不 total==0 短路）"""
        from src.got.intelligence_service import fetch_intelligence_items
        mock_service = MagicMock()
        mock_service.fetch_enabled_sources.return_value = {}  # 本次 0 新增
        mock_service.list_items.return_value = {
            "items": [{
                "title": "历史情报", "summary": "摘要", "url": "https://x",
                "source_id": "newsnow-cls-hot", "published_at": "2026-09-04T00:00:00",
            }],
            "total": 1,
        }
        mock_get_service.return_value = mock_service
        items = fetch_intelligence_items(days=1)
        assert len(items) == 1  # 未被短路

    @patch("src.got.intelligence_service.get_intelligence_service")
    def test_fetch_intelligence_items_field_alignment(self, mock_get_service):
        """C4：输出字段对齐 news_feeds（snippet / published_date，不再用 content / datetime）"""
        from src.got.intelligence_service import fetch_intelligence_items
        mock_service = MagicMock()
        mock_service.fetch_enabled_sources.return_value = {"newsnow-cls-hot": 1}
        mock_service.list_items.return_value = {
            "items": [{
                "title": "标题", "summary": "摘要内容", "url": "https://x",
                "source_id": "newsnow-cls-hot", "published_at": "2026-09-04T08:00:00",
            }],
            "total": 1,
        }
        mock_get_service.return_value = mock_service
        items = fetch_intelligence_items(days=1)
        assert len(items) == 1
        item = items[0]
        # C4：字段名对齐 news_feeds，_format_news_for_prompt 才能读到摘要
        assert item["snippet"] == "摘要内容"
        assert item["published_date"] == "2026-09-04T08:00:00"
        assert item["source_type"] == "intelligence"
        assert item["source"] == "intelligence_newsnow-cls-hot"
        assert "content" not in item
        assert "datetime" not in item

    @patch("src.got.intelligence_service.get_intelligence_service")
    def test_initialize_builtin_sources_registers_missing(self, mock_get_service):
        """W1：无既有源时注册全部内建模板"""
        from src.got.intelligence_service import initialize_builtin_sources, NEWSNOW_TEMPLATES
        mock_service = MagicMock()
        mock_service.list_sources.return_value = {"sources": [], "total": 0}
        mock_get_service.return_value = mock_service
        registered = initialize_builtin_sources()
        assert mock_service.register_newsnow_source.call_count == len(NEWSNOW_TEMPLATES)
        assert registered == len(NEWSNOW_TEMPLATES)

    @patch("src.got.intelligence_service.get_intelligence_service")
    def test_initialize_builtin_sources_skips_existing(self, mock_get_service):
        """W1：已存在的源被跳过，不重新注册（保留用户 enabled 设置，不被强制启用）"""
        from src.got.intelligence_service import initialize_builtin_sources, NEWSNOW_TEMPLATES
        mock_service = MagicMock()
        existing = [{"source_id": f"newsnow-{tid}"} for tid in NEWSNOW_TEMPLATES]
        mock_service.list_sources.return_value = {"sources": existing, "total": len(existing)}
        mock_get_service.return_value = mock_service
        registered = initialize_builtin_sources()
        assert mock_service.register_newsnow_source.call_count == 0
        assert registered == 0


# =============================================================================
# 集成测试（mock DB 写入）
# =============================================================================

class TestIntegration:
    """集成测试"""

    def test_full_flow_with_mock_db(self):
        """完整流程测试（mock DB）"""
        from src.got.intelligence_service import IntelligenceService

        mock_db = MagicMock()
        mock_session = MagicMock()
        mock_db.session_scope.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_db.session_scope.return_value.__exit__ = MagicMock(return_value=False)
        mock_session.query.return_value.filter_by.return_value.first.return_value = None

        service = IntelligenceService(db=mock_db)

        # 测试源注册
        source_id = service.register_newsnow_source("cls-hot", enabled=True)
        assert source_id == "newsnow-cls-hot"
        mock_session.add.assert_called()

    def test_persist_items_dedup_batch(self):
        """S(N+1)：_persist_items 批量查询去重，已存在的 item_id 不重复插入"""
        from src.got.intelligence_service import IntelligenceService, _make_item_id

        mock_db = MagicMock()
        mock_session = MagicMock()
        mock_db.session_scope.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_db.session_scope.return_value.__exit__ = MagicMock(return_value=False)

        items = [
            {"title": "新闻1", "url": "u1", "summary": "s1", "published_at": datetime(2026, 9, 4)},
            {"title": "新闻2", "url": "u2", "summary": "s2", "published_at": datetime(2026, 9, 4)},
        ]
        # 让 "新闻1" 已存在
        existing_id = _make_item_id("新闻1", "newsnow-cls-hot", datetime(2026, 9, 4))
        mock_session.query.return_value.filter.return_value.all.return_value = [(existing_id,)]

        service = IntelligenceService(db=mock_db)
        inserted = service._persist_items("newsnow-cls-hot", items, {"market": "cn"})
        # 只应新增 "新闻2"
        assert inserted == 1
        assert mock_session.add.call_count == 1
