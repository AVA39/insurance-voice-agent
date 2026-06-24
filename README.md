# 保険営業サポートAI 🛡️

保険営業担当者が**音声でハンズフリー**に保険知識を確認できるWebアプリケーション（MVP）。  
マイクボタンを押して話しかけると、AIが音声とテキストで回答します。

---

## 機能

- **音声入力**：マイクボタン（または `Space` キー）で話すだけで質問できる
- **AI回答**：Claude（claude-sonnet-4-6）が保険知識ベースを参照して回答
- **音声読み上げ**：回答を日本語で自動読み上げ（ブラウザ標準TTS）
- **テキスト入力**：音声が使えない環境でもチャット入力に対応
- **会話継続**：直前のやりとりを踏まえた回答が可能

---

## セットアップ

### 前提条件

- Python 3.9 以上
- Anthropic API キー（[console.anthropic.com](https://console.anthropic.com) で取得）
- Chrome または Edge（Web Speech API 対応ブラウザ）

### 1. APIキーの設定

```bash
cd insurance-voice-agent
cp .env.example .env
```

`.env` ファイルをテキストエディタで開き、`ANTHROPIC_API_KEY` に取得したキーを貼り付けてください：

```
ANTHROPIC_API_KEY=sk-ant-xxxxxxxxxxxxxxxxxxxxx
```

### 2. 依存パッケージのインストール（初回のみ）

```bash
pip3 install -r requirements.txt
```

### 3. サーバー起動

```bash
python3 -m uvicorn main:app --reload --port 8000
```

ターミナルに以下が表示されたら起動成功です：

```
INFO:     Uvicorn running on http://127.0.0.1:8000
```

### 4. ブラウザでアクセス

**Chrome** または **Edge** で以下を開いてください：

```
http://localhost:8000
```

---

## 使い方

### 音声入力

1. **🎤 ボタン** をクリック（またはキーボードの `Space` キー）
2. マイクが赤くなったら話しかけてください
3. 発話が終わると自動的にAIへ送信され、回答が音声で読み上げられます

### テキスト入力

テキストボックスに入力して **送信ボタン** または `Enter` キーを押してください。

### マイク許可の設定（初回のみ）

Chromeでマイクを使用する際、アドレスバーにポップアップが表示されたら **「許可」** をクリックしてください。  
許可が表示されない場合は以下を確認してください：

1. Chromeアドレスバーの鍵アイコン → サイトの設定 → マイク → 許可
2. macOS: システム環境設定 → プライバシーとセキュリティ → マイク → Chrome にチェック

---

## 対応している質問例

| カテゴリ | 質問例 |
|---|---|
| がん保険 | がん保険の待機期間は何日ですか？ |
| がん保険 | 上皮内新生物は保険の対象ですか？ |
| 先進医療特約 | 先進医療特約はどんな治療が対象ですか？ |
| 先進医療特約 | 重粒子線治療の費用はいくらですか？ |
| 医療保険 | 入院日額の相場を教えてください |
| 生命保険 | 定期保険と終身保険の違いは何ですか？ |
| 契約全般 | クーリングオフの期間は何日ですか？ |
| 契約全般 | 保険料控除の仕組みを教えてください |

---

## 動作確認（トラブルシューティング）

### APIが応答しない

```bash
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"がん保険の待機期間は？"}]}'
```

正常なら `{"reply":"..."}` が返ります。

### よくあるエラー

| エラー | 原因と対処 |
|---|---|
| `RuntimeError: ANTHROPIC_API_KEY が設定されていません` | `.env` ファイルにAPIキーが設定されていない |
| マイクボタンが非表示 | Chrome/Edge 以外のブラウザを使用している |
| 音声認識エラー: not-allowed | マイクの許可が必要。ブラウザ設定を確認 |
| 音声認識エラー: no-speech | 静かな場所で再度お試しください |
| 音声が読み上げられない | ブラウザの音量設定を確認 |

---

## ファイル構成

```
insurance-voice-agent/
├── main.py                  FastAPI サーバー（AIロジック）
├── requirements.txt         Python依存パッケージ
├── .env.example             APIキー設定テンプレート
├── .env                     ★APIキーをここに設定（Gitに含めない）
├── knowledge/
│   └── insurance_faq.json   保険知識ベース（6カテゴリ・25問）
├── static/
│   └── index.html           チャットUI（音声機能組み込み）
└── README.md                このファイル
```

---

## 知識ベースの拡張

`knowledge/insurance_faq.json` に以下の形式でQ&Aを追加するだけで、AIの回答範囲を拡張できます：

```json
{
  "id": "xxx-01",
  "question": "○○について教えてください",
  "answer": "○○は〜です。"
}
```

サーバーを再起動すると反映されます（`--reload` オプション使用時は自動反映）。

---

## 技術スタック

| 要素 | 技術 |
|---|---|
| AI モデル | Claude claude-sonnet-4-6（Anthropic） |
| バックエンド | FastAPI + Python 3.9 |
| 音声認識（STT） | Web Speech API（webkitSpeechRecognition） |
| 音声合成（TTS） | Web Speech API（speechSynthesis）|
| フロントエンド | Vanilla HTML/CSS/JS |
| 知識ベース | JSON（RAG-liteとしてシステムプロンプトに注入） |
