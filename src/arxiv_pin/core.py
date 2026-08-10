"""arXiv HTML にピン留め機能を埋め込んだ HTML を出力する。

使い方:
    arxiv-pin paper.html
    arxiv-pin paper.html -o output.html

出力された HTML をブラウザで開くと、本文中の式番号をクリックできる。
クリックした式は右側のパネルに追加され、複数を並べたまま読める。
"""

from __future__ import annotations

import argparse
import re
import sys
import webbrowser
from pathlib import Path

from bs4 import BeautifulSoup, Tag

ARXIV = "https://arxiv.org"

# ピン留め対象の id。
#   S3.E1 / A1.Ex2 : 式 (S=本文セクション A=付録、E=番号付き Ex=番号なし)
#   Thmassumption1 : 仮定・定理・アルゴリズムなどの定理系ブロック
#   S3.F1 / S6.T1  : 図 (F) と表 (T)
TARGET_ID = re.compile(r"^([A-Za-z]+[\w.]*\.(Ex?|F|T)\d+|Thm[\w]+)$")


def attr(tag: Tag, name: str) -> str:
    """属性値を文字列として取り出す。

    BeautifulSoup は class のような複数値属性をリストで返すため、
    呼び出し側で毎回場合分けしなくて済むようここで正規化する。
    属性が無い場合は空文字を返す。
    """
    value = tag.get(name)
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return " ".join(value)


def resolve_base(soup: BeautifulSoup, html: str) -> str | None:
    """画像や CSS を読み込むための絶対 URL を決める。

    論文によって <base> がある場合と無い場合があるため、両方に対応する。
    """
    base = soup.find("base")
    if isinstance(base, Tag) and attr(base, "href"):
        href = attr(base, "href")
        if href.startswith("http"):
            return href
        # "/html/1608.00060v7/" のようなルート相対 → 絶対 URL にする
        return ARXIV + href

    # <base> が無い場合、画像パスが "2608.06361v1/x1.png" のように論文 ID を
    # 含む形なら、その 1 つ上 (/html/) を base にする。
    # なお /static/... のようなルート相対パスは base のパス部分を見ず
    # ドメインだけで解決されるため、CSS はこの base でも正しく読み込まれる。
    for img in soup.find_all("img"):
        src = attr(img, "src")
        if re.match(r"^\d+\.\d+v?\d*/", src):
            return f"{ARXIV}/html/"

    # それも無ければ、本文中の絶対リンクから論文 ID を拾う
    m = re.search(r"https://arxiv\.org/html/(\d+\.\d+v?\d*)", html)
    if m:
        return f"{ARXIV}/html/{m.group(1)}/"
    return None


def set_base(soup: BeautifulSoup, url: str) -> str:
    """<base> を絶対 URL に設定し、必ず <head> の先頭に移動する。

    HTML は上から順に解釈されるため、<base> が <link> より後ろにあると
    CSS のパスが解決されない。既存の <base> は位置ごと動かす必要がある。
    """
    head = soup.find("head")
    if head is None:
        return "失敗 (head なし)"

    base = soup.find("base")
    if isinstance(base, Tag):
        base["href"] = url
        base.extract()
        head.insert(0, base)
        return "書き換え＋先頭へ移動"

    head.insert(0, soup.new_tag("base", href=url))
    return "挿入"


STYLE = """
#pin-panel {
  position: fixed; top: 0; right: 0; width: 380px; height: 100vh;
  overflow-y: auto; background: #fff; border-left: 1px solid #ccc;
  padding: 12px; box-sizing: border-box; z-index: 9999; font-size: 15px;
}
#pin-head {
  display: flex; justify-content: space-between; align-items: center;
  margin-bottom: 8px; padding-bottom: 8px; border-bottom: 1px solid #ddd;
}
#pin-head strong { font-weight: 500; }
#pin-clear {
  border: 1px solid #bbb; background: #f5f5f5; border-radius: 4px;
  padding: 3px 10px; cursor: pointer; font-size: 13px;
}
#pin-empty { color: #888; font-size: 14px; padding: 12px 4px; }
.pin-item img { max-width: 100%; height: auto; }
.pin-item {
  border: 1px solid #ddd; border-radius: 6px; padding: 8px;
  margin-bottom: 10px; background: #fafafa; overflow-x: auto;
}
.pin-bar {
  display: flex; justify-content: space-between; align-items: center;
  margin-bottom: 4px; font-size: 13px; color: #666;
}
.pin-close {
  border: none; background: none; cursor: pointer;
  font-size: 16px; color: #999; line-height: 1; padding: 0 4px;
}
.pin-close:hover { color: #333; }
/* arXiv の本文は .ltx_page_main > .ltx_page_content > .ltx_document という
   入れ子で、内側の .ltx_document に読みやすさ用の固定幅が入っている。
   外側だけ縮めても文字列は動かないため、内側まで幅を連動させる。 */
html { --pin-w: 388px; }
/* パネル分の幅は body の右パディングで確保する。
   .ltx_page_main の左マージンは、ar5iv が目次サイドバーの開閉に合わせて
   動的に変えている。ここを上書きすると開閉時にレイアウトが崩れるため触らない。
   body のパディングを縮める方式なら、左マージンが何であっても本文はその内側に収まる。
   目次サイドバーは position:fixed で viewport 基準に配置されるので影響を受けない。 */
body {
  padding-right: var(--pin-w) !important;
  margin-right: 0 !important;
  box-sizing: border-box !important;
}
.ltx_page_main {
  width: auto !important;
  /* ar5iv の .ltx_page_main は inline-block (shrink-to-fit) なので、
     max-width を外すと中身の希望幅まで伸びて親からはみ出す。
     必ず親幅で頭打ちにする。 */
  max-width: 100% !important;
  padding-right: 0 !important;
  box-sizing: border-box !important;
}
/* 幅の上限だけ外して本文をパネルの手前まで広げる。
   左右のマージンには触らない。ar5iv は目次サイドバーぶんの余白を
   .ltx_page_content の左マージンで確保しており、ここを 0 にすると
   本文がサイドバーの下に潜り込んで左端が読めなくなる。 */
.ltx_page_content, .ltx_document {
  max-width: 100% !important;
  width: auto !important;
  box-sizing: border-box !important;
  padding-right: 0 !important;
}
#pin-resizer {
  position: fixed; top: 0; right: 380px; width: 6px; height: 100vh;
  cursor: col-resize; z-index: 10000; background: transparent;
}
#pin-resizer:hover, #pin-resizer.dragging { background: #9cc; }
.pin-btn {
  border: none; background: none; cursor: pointer;
  font-size: 13px; color: #999; line-height: 1; padding: 0 3px;
}
.pin-btn:hover { color: #333; }
.pin-btn:disabled { color: #ddd; cursor: default; }
.pin-tools { display: flex; align-items: center; gap: 2px; }
a.ltx_ref.pin-able { cursor: pointer; text-decoration: underline dotted; }
.pin-tag { cursor: pointer; }
.pin-tag:hover { background: #ffe9a8; border-radius: 3px; }
"""

SCRIPT = """
(function () {
  var TARGET = /^([A-Za-z]+[\\w.]*\\.(Ex?|F|T)\\d+|Thm\\w+)$/;
  var pinned = [];

  var panel = document.createElement('div');
  panel.id = 'pin-panel';
  panel.innerHTML =
    '<div id="pin-head"><strong>ピン留め</strong>' +
    '<button id="pin-clear">全消し</button></div><div id="pin-body"></div>';
  document.body.appendChild(panel);
  var body = panel.querySelector('#pin-body');

  // tbody や tr は table の外では表示できないため、table で包み直す
  function displayable(src) {
    var node = src.cloneNode(true);
    var name = src.nodeName.toLowerCase();
    if (name === 'tbody' || name === 'tr' || name === 'td' || name === 'th') {
      var t = document.createElement('table');
      t.className = src.closest('table') ? src.closest('table').className : '';
      t.appendChild(node);
      return t;
    }
    return node;
  }

  function labelOf(src, id) {
    if (src.classList && src.classList.contains('ltx_theorem')) {
      var th = src.querySelector('.ltx_title');
      if (th) return th.textContent.trim();
    }
    if (src.nodeName.toLowerCase() === 'figure') {
      var cp = src.querySelector('figcaption');
      if (cp) return cp.textContent.trim().split(/[:：]/)[0].slice(0, 40);
    }
    var tag = src.querySelector('.ltx_tag_equation');
    if (!tag) {
      var owner = document.getElementById(id);
      tag = owner ? owner.querySelector('.ltx_tag_equation') : null;
    }
    return tag ? tag.textContent.trim() : id;
  }

  function render() {
    body.innerHTML = '';
    if (pinned.length === 0) {
      body.innerHTML = '<div id="pin-empty">式番号をクリックすると、ここに式が並びます。</div>';
      return;
    }
    pinned.forEach(function (id, idx) {
      var src = document.getElementById(id);
      if (!src) return;
      var item = document.createElement('div');
      item.className = 'pin-item';

      var bar = document.createElement('div');
      bar.className = 'pin-bar';
      var label = document.createElement('span');
      label.textContent = labelOf(src, id);
      var close = document.createElement('button');
      close.className = 'pin-close';
      close.textContent = '\u00d7';
      close.title = '消す';
      close.onclick = function () {
        pinned = pinned.filter(function (x) { return x !== id; });
        render();
      };
      var tools = document.createElement('div');
      tools.className = 'pin-tools';

      var up = document.createElement('button');
      up.className = 'pin-btn'; up.textContent = '\u25b2'; up.title = '上へ';
      up.disabled = (idx === 0);
      up.onclick = function () { swap(idx, idx - 1); };

      var down = document.createElement('button');
      down.className = 'pin-btn'; down.textContent = '\u25bc'; down.title = '下へ';
      down.disabled = (idx === pinned.length - 1);
      down.onclick = function () { swap(idx, idx + 1); };

      tools.appendChild(up);
      tools.appendChild(down);
      tools.appendChild(close);
      bar.appendChild(label);
      bar.appendChild(tools);
      item.appendChild(bar);
      item.appendChild(displayable(src));
      body.appendChild(item);
    });
  }

  function swap(a, b) {
    if (b < 0 || b >= pinned.length) return;
    var t = pinned[a];
    pinned[a] = pinned[b];
    pinned[b] = t;
    render();
  }

  // 幅の調整（パネル左端をドラッグ）
  var resizer = document.createElement('div');
  resizer.id = 'pin-resizer';
  document.body.appendChild(resizer);

  function setWidth(w) {
    w = Math.max(240, Math.min(w, window.innerWidth - 320));
    panel.style.width = w + 'px';
    resizer.style.right = w + 'px';
    document.documentElement.style.setProperty('--pin-w', (w + 8) + 'px');
  }

  // 既定の 380px は広い画面向け。狭いウィンドウではパネルが場所を占めすぎて
  // 本文が潰れ、横スクロールが出て左端が隠れてしまうため、幅に応じて詰める。
  // ユーザーが自分でドラッグして決めた後は、その幅を尊重する。
  var userSized = false;

  function fitWidth() {
    if (!userSized) setWidth(Math.min(380, window.innerWidth * 0.4));
  }

  fitWidth();
  window.addEventListener('resize', fitWidth);

  resizer.addEventListener('mousedown', function (e) {
    e.preventDefault();
    userSized = true;
    resizer.classList.add('dragging');
    function move(ev) { setWidth(window.innerWidth - ev.clientX); }
    function stop() {
      resizer.classList.remove('dragging');
      document.removeEventListener('mousemove', move);
      document.removeEventListener('mouseup', stop);
    }
    document.addEventListener('mousemove', move);
    document.addEventListener('mouseup', stop);
  });

  function pin(id) {
    if (pinned.indexOf(id) === -1) {
      pinned.push(id);
      render();
    }
    var el = document.querySelector('.pin-item:last-child');
    if (el) {
      el.style.outline = '2px solid #6aa';
      setTimeout(function () { el.style.outline = ''; }, 600);
    }
  }

  // (1) 本文中の参照リンク
  document.querySelectorAll('a.ltx_ref').forEach(function (a) {
    var href = a.getAttribute('href') || '';
    if (href.indexOf('#') === -1) return;
    var id = href.split('#').pop();
    if (!TARGET.test(id)) return;
    if (!document.getElementById(id)) return;
    a.classList.add('pin-able');
    a.addEventListener('click', function (e) { e.preventDefault(); pin(id); });
  });

  // (2) 式の脇に表示されている番号そのもの
  document.querySelectorAll('.ltx_tag_equation').forEach(function (tag) {
    var owner = tag.closest('[id]');
    while (owner && !TARGET.test(owner.id)) {
      owner = owner.parentElement ? owner.parentElement.closest('[id]') : null;
    }
    if (!owner) return;
    var id = owner.id;
    tag.classList.add('pin-able', 'pin-tag');
    tag.addEventListener('click', function (e) { e.preventDefault(); pin(id); });
  });

  // (3) 図・表のキャプション
  document.querySelectorAll('figure').forEach(function (fig) {
    if (!fig.id || !TARGET.test(fig.id)) return;
    // サブ図は親をピン留めしたときに含まれるので、単独では対象にしない
    if (fig.id.indexOf('.sf') !== -1) return;
    var cap = fig.querySelector('figcaption');
    if (!cap) return;
    var id = fig.id;
    cap.classList.add('pin-able', 'pin-tag');
    cap.addEventListener('click', function (e) { e.preventDefault(); pin(id); });
  });

  // (4) 定理・仮定などの見出し
  document.querySelectorAll('.ltx_theorem').forEach(function (box) {
    if (!box.id || !TARGET.test(box.id)) return;
    var title = box.querySelector('.ltx_title');
    if (!title) return;
    var id = box.id;
    title.classList.add('pin-able', 'pin-tag');
    title.addEventListener('click', function (e) { e.preventDefault(); pin(id); });
  });

  // 本文が画面の左にはみ出す・目次サイドバーの下に潜るのを防ぐ。
  // ar5iv は画面幅で本文のレイアウト (inline-block / flex) を切り替えるうえ、
  // 内部のオフセットの持ち方もモードごとに違う。CSS の決め打ちでは
  // 追い切れないため、実際の描画位置を測って足りない分だけ押し戻す。
  (function () {
    var main = document.querySelector('.ltx_page_main');
    var content = document.querySelector('.ltx_page_content');
    if (!main || !content) return;

    // 左端に最低限確保する余白 (px)
    var GUTTER = 24;

    function fit() {
      main.style.paddingLeft = '';
      // 押し戻す量 = 「左端のはみ出し」と「サイドバーとの重なり」の大きい方。
      // 画面端にぴったり付くと読みにくいので、GUTTER 分の余白まで含めて確保する。
      // flex の中央寄せだと padding の半分しか効かないことがあるので、
      // 一度で決めず、測り直しながら数回で収束させる。
      for (var i = 0; i < 4; i++) {
        var left = content.getBoundingClientRect().left;
        var nav = document.querySelector('.ltx_page_navbar');
        var navRight = 0;
        if (nav) {
          var st = window.getComputedStyle(nav);
          if ((st.position === 'fixed' || st.position === 'absolute') &&
              st.display !== 'none' && nav.getBoundingClientRect().width > 0) {
            navRight = nav.getBoundingClientRect().right;
          }
        }
        var need = Math.max(GUTTER - left, navRight + GUTTER - left);
        if (need <= 0) break;
        var cur = parseFloat(main.style.paddingLeft) || 0;
        main.style.paddingLeft = Math.ceil(cur + need) + 'px';
      }
    }

    fit();
    window.addEventListener('resize', fit);
    if (window.ResizeObserver) new ResizeObserver(fit).observe(document.body);
  })();

  document.getElementById('pin-clear').onclick = function () { pinned = []; render(); };
  render();
})();
"""


def enhance(path: str | Path, out_path: str | Path) -> int:
    html = Path(path).read_text(encoding="utf-8")
    soup = BeautifulSoup(html, "lxml")

    base_url = resolve_base(soup, html)
    if base_url:
        how = set_base(soup, base_url)
        print(f"base            : {base_url} ({how})")
    else:
        print("base            : 特定できず (画像や数式が崩れる可能性があります)")

    # クリック可能になる参照の数を数えておく
    ids = {attr(el, "id") for el in soup.find_all(id=True)}
    n = 0
    for a in soup.select("a.ltx_ref"):
        href = attr(a, "href")
        if "#" not in href:
            continue
        target = href.split("#")[-1]
        if TARGET_ID.match(target) and target in ids:
            n += 1
    print(f"クリック可能な参照: {n} 箇所")

    if n == 0:
        print("  [警告] ピン留めできる参照がありません。")

    head = soup.find("head")
    style = soup.new_tag("style")
    style.string = STYLE
    (head or soup).append(style)

    script = soup.new_tag("script")
    script.string = SCRIPT
    (soup.find("body") or soup).append(script)

    out = Path(out_path).resolve()
    out.write_text(str(soup), encoding="utf-8")
    print(f"出力            : {out}")

    return n


def main() -> None:
    p = argparse.ArgumentParser(
        description="arXiv HTML にピン留め機能を埋め込む",
        epilog="例: arxiv-pin paper.html",
    )
    p.add_argument("input", nargs="+", help="手元の arXiv HTML ファイル (複数可)")
    p.add_argument("-o", "--output", help="出力先のファイル名 (入力が1件のときのみ)")
    p.add_argument("--no-open", action="store_true", help="ブラウザで開かない")
    args = p.parse_args()

    if args.output and len(args.input) > 1:
        print("-o は入力が1件のときだけ使えます")
        sys.exit(1)

    for name in args.input:
        src = Path(name)
        if not src.exists():
            print(f"ファイルが見つかりません: {src}")
            continue
        out = args.output or str(src.with_name(src.stem + "_pinned.html"))
        enhance(src, out)
        if not args.no_open:
            webbrowser.open(Path(out).resolve().as_uri())


if __name__ == "__main__":
    main()
