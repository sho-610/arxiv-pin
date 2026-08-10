"""arxiv_pin.core の単体テスト。"""

from __future__ import annotations

from pathlib import Path

import pytest
from bs4 import BeautifulSoup, Tag

from arxiv_pin.core import TARGET_ID, enhance, resolve_base, set_base

DATA = Path(__file__).parent / "data" / "sample.html"


@pytest.mark.parametrize(
    "target",
    ["S3.E1", "A1.Ex2", "S3.F1", "S6.T1", "Thmassumption1"],
)
def test_target_id_matches_pinnable_anchors(target: str) -> None:
    """式・図・表・定理の id はピン留め対象として認識される。"""
    assert TARGET_ID.match(target)


@pytest.mark.parametrize("target", ["S1", "bib.bib1", "footnote3", ""])
def test_target_id_rejects_other_anchors(target: str) -> None:
    """節・参考文献・脚注へのリンクはピン留め対象にしない。"""
    assert TARGET_ID.match(target) is None


def test_resolve_base_expands_root_relative_base_tag() -> None:
    """ルート相対の <base> は arxiv.org を補って絶対 URL にする。"""
    html = '<html><head><base href="/html/1608.00060v7/"></head><body></body></html>'
    soup = BeautifulSoup(html, "lxml")
    assert resolve_base(soup, html) == "https://arxiv.org/html/1608.00060v7/"


def test_resolve_base_keeps_absolute_base_tag() -> None:
    """既に絶対 URL の <base> はそのまま使う。"""
    html = '<html><head><base href="https://arxiv.org/html/2401.00001v1/"></head></html>'
    soup = BeautifulSoup(html, "lxml")
    assert resolve_base(soup, html) == "https://arxiv.org/html/2401.00001v1/"


def test_resolve_base_falls_back_to_image_path() -> None:
    """<base> が無くても、論文 ID を含む画像パスから base を推定できる。"""
    html = DATA.read_text(encoding="utf-8")
    soup = BeautifulSoup(html, "lxml")
    assert resolve_base(soup, html) == "https://arxiv.org/html/"


def test_resolve_base_returns_none_when_undeterminable() -> None:
    """手がかりが無い HTML では None を返す。"""
    html = "<html><head></head><body><p>no hints</p></body></html>"
    soup = BeautifulSoup(html, "lxml")
    assert resolve_base(soup, html) is None


def test_set_base_moves_existing_base_to_head_top() -> None:
    """<base> が <link> より後ろにあると CSS が解決できないため、先頭へ移動する。"""
    html = '<html><head><link rel="stylesheet" href="a.css"><base href="x"></head></html>'
    soup = BeautifulSoup(html, "lxml")
    assert set_base(soup, "https://arxiv.org/html/") == "書き換え＋先頭へ移動"

    head = soup.find("head")
    assert isinstance(head, Tag)
    first = next(c for c in head.contents if isinstance(c, Tag))
    assert first.name == "base"
    assert first["href"] == "https://arxiv.org/html/"


def test_set_base_inserts_when_absent() -> None:
    """<base> が無い HTML には新しく挿入する。"""
    soup = BeautifulSoup("<html><head></head></html>", "lxml")
    assert set_base(soup, "https://arxiv.org/html/") == "挿入"
    assert soup.find("base") is not None


def test_set_base_reports_failure_without_head() -> None:
    """<head> が無ければ何もせず失敗を返す。"""
    soup = BeautifulSoup("<p>fragment</p>", "html.parser")
    assert set_base(soup, "https://arxiv.org/html/") == "失敗 (head なし)"


def test_enhance_counts_only_resolvable_references(tmp_path: Path) -> None:
    """参照は4件あるが、実在する式・図の id を指すのは2件だけ。"""
    assert enhance(DATA, tmp_path / "out.html") == 2


def test_enhance_injects_pin_panel(tmp_path: Path) -> None:
    """出力 HTML にパネルの CSS と JS が埋め込まれている。"""
    out = tmp_path / "out.html"
    enhance(DATA, out)
    result = out.read_text(encoding="utf-8")
    assert "pin-panel" in result
    assert "<script" in result


def test_enhance_preserves_original_content(tmp_path: Path) -> None:
    """本文と id は変換後も保持される。"""
    out = tmp_path / "out.html"
    enhance(DATA, out)
    soup = BeautifulSoup(out.read_text(encoding="utf-8"), "lxml")
    assert "を参照。" in soup.get_text()
    assert soup.find(id="S1.E1") is not None
    assert soup.find(id="S1.F1") is not None
