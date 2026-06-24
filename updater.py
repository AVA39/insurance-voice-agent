"""
Web検索でAnthropicを使い、保険知識ベースを自動更新するモジュール。

フロー:
  1. DuckDuckGoで保険関連の最新情報を検索
  2. 検索スニペットをAnthropicへ送り、既存JSONを更新させる
  3. JSONを検証してファイルに保存
  4. 呼び出し元が reload_callback() で system prompt を再構築する
"""

import json
import logging
import re
import threading
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

KNOWLEDGE_PATH = Path(__file__).parent / "knowledge" / "insurance_faq.json"

# 各カテゴリについて検索するクエリ
SEARCH_QUERIES: list[tuple[str, str]] = [
    ("がん保険",          "がん保険 待機期間 免責 最新 2026"),
    ("がん保険",          "がん保険 診断一時金 上皮内新生物 抗がん剤特約 2026"),
    ("先進医療特約",      "先進医療 対象技術 一覧 2026 厚生労働省"),
    ("先進医療特約",      "重粒子線治療 陽子線治療 費用 最新 2026"),
    ("医療保険",          "医療保険 入院日額 相場 三大疾病 最新 2026"),
    ("死亡保険・生命保険", "定期保険 終身保険 収入保障保険 最新 2026"),
    ("保険全般・契約",    "生命保険料控除 クーリングオフ 告知義務 最新 2026"),
    ("個人年金・老後保障", "個人年金保険 iDeCo 老後資金 2026"),
]

_lock = threading.Lock()
_status: dict = {
    "status":       "idle",      # idle | updating | success | error
    "last_updated": None,        # ISO 8601 string or None
    "message":      "まだ自動更新が実行されていません",
}


# ──────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────

def get_status() -> dict:
    with _lock:
        return _status.copy()


def run_update(client, reload_callback=None) -> bool:
    """
    知識ベースを Web 検索 + AI で更新する（同期・スレッドセーフ）。

    Parameters
    ----------
    client : anthropic.Anthropic
        呼び出し元が生成済みのクライアントを渡す。
    reload_callback : callable | None
        更新成功後に呼ぶ関数（system prompt の再構築など）。
    """
    with _lock:
        if _status["status"] == "updating":
            return False  # 多重起動を防ぐ

    _set_status("updating", "Web 検索を開始しています...")
    logger.info("知識ベースの自動更新を開始します")

    try:
        # ── 1. Web 検索 ───────────────────────────────────────
        results = _collect_search_results()
        if not results:
            _set_status("error", "Web 検索結果が取得できませんでした（ネットワークを確認してください）")
            return False

        # ── 2. 既存 JSON 読み込み ─────────────────────────────
        with open(KNOWLEDGE_PATH, encoding="utf-8") as f:
            existing = json.load(f)

        # ── 3. Anthropic に更新を依頼 ────────────────────────
        _set_status("updating", "AI が知識ベースを更新中...")
        today = datetime.now().strftime("%Y-%m-%d")
        updated = _ask_claude_to_update(client, results, existing, today)

        # ── 4. 検証・保存 ─────────────────────────────────────
        _validate_schema(updated)
        _save(existing, updated)

        total = sum(len(c.get("items", [])) for c in updated["categories"])
        _set_status(
            "success",
            f"更新完了 — {total} 問 / {len(updated['categories'])} カテゴリ（{today}）",
            last_updated=datetime.now().isoformat(),
        )
        logger.info(f"知識ベース更新完了: {total} 問")

        if reload_callback:
            reload_callback()

        return True

    except json.JSONDecodeError as e:
        _set_status("error", f"AI の応答を JSON に変換できませんでした: {e}")
        logger.error(f"JSON 解析エラー: {e}")
    except Exception as e:
        _set_status("error", f"更新エラー: {e}")
        logger.error(f"更新失敗: {e}", exc_info=True)

    return False


# ──────────────────────────────────────────────────────────────
# Internal helpers
# ──────────────────────────────────────────────────────────────

def _set_status(status: str, message: str, *, last_updated=None):
    with _lock:
        _status["status"]  = status
        _status["message"] = message
        if last_updated is not None:
            _status["last_updated"] = last_updated


def _collect_search_results() -> list[dict]:
    """DuckDuckGo でクエリを順番に実行し、スニペットを収集する。"""
    from duckduckgo_search import DDGS

    all_results: list[dict] = []
    for category, query in SEARCH_QUERIES:
        _set_status("updating", f"「{category}」の最新情報を検索中...")
        try:
            hits = list(DDGS().text(query, region="jp-ja", timelimit="y", max_results=4))
            for h in hits:
                snippet = h.get("body", "").strip()
                if snippet:
                    all_results.append({
                        "category": category,
                        "title":    h.get("title", ""),
                        "snippet":  snippet,
                        "url":      h.get("href", ""),
                    })
        except Exception as e:
            logger.warning(f"検索スキップ [{query}]: {e}")

    logger.info(f"Web 検索結果: {len(all_results)} 件")
    return all_results


def _ask_claude_to_update(client, results: list[dict], existing: dict, today: str) -> dict:
    """Claude に知識ベース更新 JSON を生成させる。"""
    context = "\n\n".join(
        f"[{r['category']}] {r['title']}\n{r['snippet']}\n出典: {r['url']}"
        for r in results
        if r["snippet"]
    )

    prompt = f"""あなたは保険知識データベース管理 AI です。
以下の Web 検索結果（{today} 時点）をもとに、保険営業担当者向け知識ベース JSON を更新してください。

【Web 検索結果】
{context}

【現在の知識ベース JSON】
{json.dumps(existing, ensure_ascii=False, indent=2)}

【更新ルール】
- 既存の正確な情報はそのまま維持する
- 検索結果で確認できた新しい数値・制度変更を反映する
- 新しい重要な Q&A があれば追加（1 カテゴリ最大 8 問まで）
- 確認できない情報は追加しない（ハルシネーション禁止）
- "last_updated" を "{today}" に設定する
- スキーマは絶対に変更しない（categories[].name と categories[].items[].id は変えない）
- JSON のみ返答すること。説明文・```マーカーは不要"""

    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=8000,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = resp.content[0].text.strip()

    # markdown コードブロックを除去
    if "```" in raw:
        m = re.search(r"```(?:json)?\s*([\s\S]+?)```", raw)
        raw = m.group(1).strip() if m else raw

    return json.loads(raw)


def _validate_schema(data: dict):
    if "categories" not in data or not isinstance(data["categories"], list):
        raise ValueError("スキーマ検証失敗: 'categories' が見つかりません")
    for cat in data["categories"]:
        if "items" not in cat or not isinstance(cat["items"], list):
            raise ValueError(f"スキーマ検証失敗: カテゴリ '{cat.get('name')}' に items がありません")


def _save(existing: dict, updated: dict):
    # バックアップ保存
    backup = KNOWLEDGE_PATH.with_suffix(".json.bak")
    backup.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")

    with open(KNOWLEDGE_PATH, "w", encoding="utf-8") as f:
        json.dump(updated, f, ensure_ascii=False, indent=2)
