"""
Spotify 再生情報表示アプリ（Flask）

機能:
- Spotify OAuth認証
- 現在再生中の曲取得
- トークン自動更新
- API提供（フロントエンド用）

Mac / Raspberry Pi 両対応
"""

import base64
import json
import logging
import os
import re
import time
from pathlib import Path
from urllib.parse import urlencode

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, render_template, request, url_for

# .env読み込み
load_dotenv()

app = Flask(__name__)

# 正常レスポンス（2xx）のアクセスログを抑制する
class _SuppressSuccess(logging.Filter):
    def filter(self, record):
        m = re.search(r'" (\d{3}) ', record.getMessage())
        if m:
            return int(m.group(1)) >= 400  # 4xx/5xx のみ出力
        return True  # 起動メッセージ・例外など他のログは通す

logging.getLogger("werkzeug").addFilter(_SuppressSuccess())
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "change-me")

# 環境変数
CLIENT_ID = os.environ["SPOTIFY_CLIENT_ID"]
CLIENT_SECRET = os.environ["SPOTIFY_CLIENT_SECRET"]
REDIRECT_URI = os.environ["SPOTIFY_REDIRECT_URI"]

# トークン保存ファイル
TOKEN_FILE = Path("token_store.json")

# Spotify API エンドポイント
AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
CURRENTLY_PLAYING_URL = "https://api.spotify.com/v1/me/player/currently-playing"
AUDIO_FEATURES_URL = "https://api.spotify.com/v1/audio-features/{}"

# 必要な権限
SCOPES = "user-read-currently-playing"

# キー名（Pitch Class 0〜11）
KEY_NAMES = ["C", "C♯", "D", "D♯", "E", "F", "F♯", "G", "G♯", "A", "A♯", "B"]


# -------------------------------
# トークン管理
# -------------------------------

def load_tokens():
    """保存済みトークンを読み込む"""
    if TOKEN_FILE.exists():
        return json.loads(TOKEN_FILE.read_text())
    return None


def save_tokens(tokens):
    """トークンをファイル保存"""
    TOKEN_FILE.write_text(json.dumps(tokens, indent=2))


def basic_auth_header():
    """Client ID / Secret をBase64エンコード"""
    raw = f"{CLIENT_ID}:{CLIENT_SECRET}".encode()
    encoded = base64.b64encode(raw).decode()
    return {"Authorization": f"Basic {encoded}"}


def token_expired(tokens):
    """トークン有効期限チェック"""
    return time.time() > tokens.get("expires_at", 0) - 60


def refresh_access_token(tokens):
    """refresh_token を使ってアクセストークン更新"""
    response = requests.post(
        TOKEN_URL,
        headers=basic_auth_header(),
        data={
            "grant_type": "refresh_token",
            "refresh_token": tokens["refresh_token"],
        },
    )
    response.raise_for_status()
    data = response.json()

    new_tokens = {
        "access_token": data["access_token"],
        "expires_at": time.time() + data["expires_in"],
        "refresh_token": data.get("refresh_token", tokens["refresh_token"]),
    }

    save_tokens(new_tokens)
    return new_tokens


def get_valid_tokens():
    """常に有効なトークンを取得"""
    tokens = load_tokens()
    if not tokens:
        return None

    if token_expired(tokens):
        return refresh_access_token(tokens)

    return tokens


# -------------------------------
# OAuth処理
# -------------------------------

def build_auth_url():
    """SpotifyログインURL生成"""
    params = {
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
    }
    return f"{AUTH_URL}?{urlencode(params)}"


def exchange_code_for_token(code):
    """認可コード → アクセストークン変換"""
    response = requests.post(
        TOKEN_URL,
        headers=basic_auth_header(),
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
        },
    )
    response.raise_for_status()
    data = response.json()

    tokens = {
        "access_token": data["access_token"],
        "refresh_token": data["refresh_token"],
        "expires_at": time.time() + data["expires_in"],
    }

    save_tokens(tokens)


# -------------------------------
# Spotify API
# -------------------------------

def fetch_audio_features(track_id, access_token):
    """Audio Features からキー情報を取得（取得できない場合は空dict）"""
    try:
        res = requests.get(
            AUDIO_FEATURES_URL.format(track_id),
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if not res.ok:
            return {}
        data = res.json()
        key_num = data.get("key", -1)
        mode = data.get("mode", -1)
        if key_num == -1 or mode == -1:
            return {}
        mode_name = "Major" if mode == 1 else "minor"
        return {"key": f"{KEY_NAMES[key_num]} {mode_name}"}
    except Exception:
        return {}


def fetch_now_playing(access_token):
    """現在再生中の曲を取得"""
    res = requests.get(
        CURRENTLY_PLAYING_URL,
        headers={"Authorization": f"Bearer {access_token}"},
    )

    # 再生していない場合
    if res.status_code == 204:
        return {"is_playing": False}

    res.raise_for_status()
    data = res.json()

    item = data.get("item")
    if not item:
        return {"is_playing": False}

    item_type = item.get("type", "track")  # "track" or "episode"

    if item_type == "episode":
        title = item["name"]
        artists = item.get("show", {}).get("name", "")
        album = ""
        images = item.get("images") or item.get("show", {}).get("images", [])
    else:
        title = item["name"]
        artists = ", ".join(a["name"] for a in item["artists"])
        album_obj = item["album"]
        album = album_obj["name"]
        album_type = album_obj.get("album_type", "")   # "album" / "single" / "compilation"
        release_date = album_obj.get("release_date", "")
        album_artists = ", ".join(a["name"] for a in album_obj.get("artists", []))
        images = album_obj.get("images", [])

    image_url = images[0]["url"] if images else None

    # 曲の長さ（分:秒）
    duration_ms = item.get("duration_ms", 0)
    minutes, seconds = divmod(duration_ms // 1000, 60)
    duration = f"{minutes}:{seconds:02d}"

    display_text = f"{title}  —  {artists}"

    result = {
        "is_playing": data["is_playing"],
        "type": item_type,
        "track_id": item["id"],
        "title": title,
        "artists": artists,
        "album": album,
        "image_url": image_url,
        "display_text": display_text,
        "duration": duration,
    }

    if item_type != "episode":
        result["album_type"] = album_type
        result["release_date"] = release_date
        result["album_artists"] = album_artists

    return result


# -------------------------------
# Flask Routes
# -------------------------------

@app.route("/")
def index():
    """メイン画面"""
    logged_in = load_tokens() is not None
    return render_template("index.html", logged_in=logged_in)


@app.route("/login")
def login():
    """Spotifyログインへリダイレクト"""
    return redirect(build_auth_url())


@app.route("/callback")
def callback():
    """OAuthコールバック"""
    code = request.args.get("code")
    exchange_code_for_token(code)
    return redirect("/")


@app.route("/api/now-playing")
def now_playing():
    """フロントエンド用API"""
    tokens = get_valid_tokens()

    if not tokens:
        return jsonify({"error": "not authenticated", "login_url": "/login"}), 401

    data = fetch_now_playing(tokens["access_token"])

    # 再生中のトラックのみキー情報を追加取得
    if data.get("is_playing") and data.get("type") == "track":
        features = fetch_audio_features(data["track_id"], tokens["access_token"])
        data.update(features)

    return jsonify(data)


# -------------------------------
# 起動
# -------------------------------

if __name__ == "__main__":
    # 開発用サーバ起動
    app.run(host="0.0.0.0", port=5000, debug=True)

