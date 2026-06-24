#!/usr/bin/env python3
"""
GitHub Actions から実行される知識ベース更新スクリプト。

実行方法:
  ANTHROPIC_API_KEY=sk-ant-... python scripts/update_knowledge.py
"""

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

KNOWLEDGE_PATH = Path(__file__).parent.parent / "knowledge" / "insurance_faq.json"

SEARCH_QUERIES = [
    ("がん保険",          "がん保険 待機期間 免責 最新 2026"),
    ("がん保険",          "がん保険 診断一時金 上皮内新生物 抗がん剤特約 2026"),
    ("先進医療特約",      "先進医療 対象技術 一覧 2026 厚生労働省"),
    ("先進医療特約",      "重粒子線治療 陽子線治療 費用 最新 2026"),
    ("医療保険",          "医療保険 入院日額 相場 三大疾病 最新 2026"),
    ("死亡保険・生命保険", "定期保険 終身保険 収入保障保険 最新 2026"),
    ("保険全般・契約",    "生命保険料控除 クーリングオフ 告知義務 最新 2026"),
    ("個人年金・老後保障", "個人年金保険 iDeCo 老後資金 2026"),
]


def search_web(query: str, max_results: int = 4) -> list[dict]:
    try:
        from duckduckgo_search import DDGS
        return list(DDGS().text(query, region="jp-ja", timelimit="y", max_results=max_results))
    except Exception as e:
        print(f"  [WARN] 検索失敗 [{query}]: {e}", file=sys.stderr)
        return []


def collect_results() -> list[dict]:
    all_results = []
    for category, query in SEARCH_QUERIES:
        print(f"  検索: {query}")
        hits = search_web(query, max_results=4)
        for h in hits:
            if h.get("body"):
                all_results.append({
                    "category": category,
                    "title":    h.get("title", ""),
                    "snippet":  h.get("body", ""),
                    "url":      h.get("href", ""),
                })
    print(f"  合計 {len(all_results)} 件の検索結果を取得しました")
    return all_results


def update_with_claude(client, results: list[dict], existing: dict, today: str) -> dict:
    context = "\n\n".join(
        f"[{r['category']}] {r['title']}\n{r['snippet']}\n出典: {r['url']}"
        for r in results if r["snippet"]
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


def validate(data: dict):
    assert "categories" in data and isinstance(data["categories"], list), \
        "'categories' が見つかりません"
    for cat in data["categories"]:
        assert "items" in cat and isinstance(cat["items"], list), \
            f"カテゴリ '{cat.get('name')}' に items がありません"


def main():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: 環境変数 ANTHROPIC_API_KEY が設定されていません", file=sys.stderr)
        sys.exit(1)

    from anthropic import Anthropic
    client = Anthropic(api_key=api_key)

    today = datetime.now().strftime("%Y-%m-%d")
    print(f"=== 保険知識ベース更新 ({today}) ===")

    # 1. Web 検索
    print("\n[1] Web 検索中...")
    results = collect_results()
    if not results:
        print("ERROR: 検索結果が 0 件でした。処理を中断します。", file=sys.stderr)
        sys.exit(1)

    # 2. 既存 JSON 読み込み
    print("\n[2] 既存の知識ベースを読み込み中...")
    with open(KNOWLEDGE_PATH, encoding="utf-8") as f:
        existing = json.load(f)
    total_before = sum(len(c.get("items", [])) for c in existing["categories"])
    print(f"  現在: {len(existing['categories'])} カテゴリ / {total_before} 問")

    # 3. Anthropic で更新
    print("\n[3] AI で知識ベースを更新中...")
    updated = update_with_claude(client, results, existing, today)

    # 4. 検証
    print("\n[4] スキーマ検証中...")
    validate(updated)
    total_after = sum(len(c.get("items", [])) for c in updated["categories"])
    print(f"  更新後: {len(updated['categories'])} カテゴリ / {total_after} 問")

    # 5. バックアップ → 保存
    backup = KNOWLEDGE_PATH.with_suffix(".json.bak")
    backup.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")

    with open(KNOWLEDGE_PATH, "w", encoding="utf-8") as f:
        json.dump(updated, f, ensure_ascii=False, indent=2)

    print(f"\n✅ 完了！knowledge/insurance_faq.json を更新しました（{today}）")


if __name__ == "__main__":
    main()
