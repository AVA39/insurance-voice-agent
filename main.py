"""
保険営業サポートAI — FastAPI バックエンド

自動更新フロー:
  - サーバー起動後 1 分で初回 Web 更新を実行
  - 以降 24 時間ごとに自動更新（APScheduler BackgroundScheduler）
  - POST /api/update で手動トリガー可能
  - GET  /api/update-status で進捗確認可能
"""

import asyncio
import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

from anthropic import Anthropic, APIError
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import os

import updater

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")
logger = logging.getLogger(__name__)

KNOWLEDGE_PATH = Path(__file__).parent / "knowledge" / "insurance_faq.json"

# ──────────────────────────────────────────────────────────────
# System prompt — スレッドセーフに保持・更新する
# ──────────────────────────────────────────────────────────────
_prompt_lock = threading.Lock()
_system_prompt: str = ""


def _build_system_prompt(knowledge: dict) -> str:
    faq_text = ""
    for category in knowledge["categories"]:
        faq_text += f"\n## {category['name']}\n"
        for item in category["items"]:
            faq_text += f"Q: {item['question']}\nA: {item['answer']}\n\n"

    last_updated = knowledge.get("last_updated", "不明")

    return f"""あなたは保険営業担当者をサポートする専門 AI アシスタントです。
営業担当者が顧客との商談中や移動中にハンズフリーで質問できるよう設計されています。
知識ベース最終更新日: {last_updated}

【回答ルール】
- 回答は必ず 3 文以内に収めること（音声で聞き取りやすい長さ）
- 数字・期間・金額は具体的に述べること
- 不明・確認が必要な点は「確認が必要です」と正直に伝えること
- 法的リスクのある断言は避けること
- 敬体（です・ます調）を使うこと
- 箇条書きは使わず文章で回答すること（音声読み上げ対応）

【保険知識データベース（最新）】
{faq_text}
【重要な注意事項】
上記データベースに記載のない詳細・個別商品の情報は「詳細は約款または社内資料をご確認ください」と案内すること。
ハルシネーション（根拠のない情報の提供）は厳禁です。"""


def reload_system_prompt():
    """知識ベース JSON を再読み込みして system prompt を更新する。"""
    global _system_prompt
    with open(KNOWLEDGE_PATH, encoding="utf-8") as f:
        knowledge = json.load(f)
    new_prompt = _build_system_prompt(knowledge)
    with _prompt_lock:
        _system_prompt = new_prompt
    logger.info("system prompt を更新しました（知識ベース: %s）", knowledge.get("last_updated"))


def get_system_prompt() -> str:
    with _prompt_lock:
        return _system_prompt


# 初回ロード
reload_system_prompt()

# ──────────────────────────────────────────────────────────────
# Anthropic クライアント
# ──────────────────────────────────────────────────────────────
_api_key = os.environ.get("ANTHROPIC_API_KEY")
if not _api_key:
    raise RuntimeError(".env ファイルに ANTHROPIC_API_KEY が設定されていません")

client = Anthropic(api_key=_api_key)

# ──────────────────────────────────────────────────────────────
# スケジューラー & ライフサイクル
# ──────────────────────────────────────────────────────────────
_scheduler = BackgroundScheduler(timezone="Asia/Tokyo")
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="updater")


def _scheduled_update():
    logger.info("スケジュール更新を開始します")
    updater.run_update(client, reload_callback=reload_system_prompt)


@asynccontextmanager
async def lifespan(app_: FastAPI):
    # 起動 1 分後に初回自動更新、以降 24 時間おき
    _scheduler.add_job(
        _scheduled_update,
        "interval",
        hours=24,
        id="knowledge_update",
        next_run_time=__import__("datetime").datetime.now() + __import__("datetime").timedelta(minutes=1),
    )
    _scheduler.start()
    logger.info("スケジューラー起動（24h 間隔、初回は 1 分後）")
    yield
    _scheduler.shutdown(wait=False)
    _executor.shutdown(wait=False)
    logger.info("スケジューラー停止")


# ──────────────────────────────────────────────────────────────
# FastAPI アプリ
# ──────────────────────────────────────────────────────────────
app = FastAPI(title="保険営業サポートAI", lifespan=lifespan)


# ─── チャット ────────────────────────────────────────────────
class Message(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[Message]


@app.post("/api/chat")
async def chat(req: ChatRequest):
    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=300,
            system=get_system_prompt(),
            messages=[{"role": m.role, "content": m.content} for m in req.messages],
        )
        return {"reply": response.content[0].text}
    except APIError as e:
        raise HTTPException(status_code=502, detail=f"AI API エラー: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"サーバーエラー: {e}")


# ─── 知識ベース更新 ──────────────────────────────────────────
@app.post("/api/update")
async def trigger_update():
    """手動で知識ベース更新をトリガーする。"""
    status = updater.get_status()
    if status["status"] == "updating":
        return {"ok": False, "message": "既に更新中です。完了をお待ちください。"}

    loop = asyncio.get_event_loop()

    async def _run():
        await loop.run_in_executor(
            _executor,
            lambda: updater.run_update(client, reload_callback=reload_system_prompt),
        )

    asyncio.create_task(_run())
    return {"ok": True, "message": "知識ベースの更新を開始しました（バックグラウンドで実行中）"}


@app.get("/api/update-status")
async def get_update_status():
    """更新の進捗・最終更新日時を返す。"""
    return updater.get_status()


# ─── 例外ハンドラー ──────────────────────────────────────────
@app.exception_handler(Exception)
async def global_error_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"detail": f"予期しないエラーが発生しました: {exc}"},
    )


# ─── 静的ファイル（catch-all は API ルートの後に置くこと）──
app.mount(
    "/",
    StaticFiles(directory=str(Path(__file__).parent / "static"), html=True),
    name="static",
)
