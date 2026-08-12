import argparse
import os
import random
import re
import secrets
import sys
import threading
import webbrowser

import spotipy
from dotenv import load_dotenv
from flask import Flask, redirect, render_template_string, request
from spotipy.oauth2 import SpotifyOAuth

load_dotenv()

HOST = "127.0.0.1"
PORT = 5000
REDIRECT_URI = f"http://{HOST}:{PORT}/callback"  # has to match the spotify dashboard exactly
SCOPE = (
    "playlist-read-private playlist-read-collaborative "
    "playlist-modify-private playlist-modify-public"
)


# ---------------------------------------------------------------------------
# auth
# ---------------------------------------------------------------------------

def make_auth_manager(open_browser: bool) -> SpotifyOAuth:
    client_id = os.getenv("SPOTIPY_CLIENT_ID") or os.getenv("CLIENT_ID")
    client_secret = os.getenv("SPOTIPY_CLIENT_SECRET") or os.getenv("CLIENT_SECRET")
    if not client_id or not client_secret:
        sys.exit("no credentials found, .env is missing or empty")
    return SpotifyOAuth(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=REDIRECT_URI,
        scope=SCOPE,
        cache_path=".cache",  # token lives here so I only log in once
        open_browser=open_browser,
    )


# ---------------------------------------------------------------------------
# logic
# ---------------------------------------------------------------------------

def parse_playlist_id(value: str) -> str:
    # let me paste whatever - bare id, spotify: uri, or the share link
    value = value.strip()
    match = re.search(r"playlist[/:]([A-Za-z0-9]+)", value)
    return match.group(1) if match else value


def get_own_playlists(sp: spotipy.Spotify) -> list[dict]:
    # only playlists I can actually modify (mine or collaborative)
    user_id = sp.current_user()["id"]
    playlists = []
    results = sp.current_user_playlists(limit=50)
    while results:
        for pl in results["items"]:
            if pl and (pl.get("owner", {}).get("id") == user_id or pl.get("collaborative")):
                playlists.append(pl)
        results = sp.next(results) if results["next"] else None
    return playlists


def shuffle_playlist(sp: spotipy.Spotify, playlist_id: str) -> tuple[str, int]:
    # fisher-yates but every swap is an api call to the reorder endpoint.
    # slow on big playlists but the playlist is never missing tracks at any
    # point, so a crash mid-shuffle can't nuke anything
    info = sp.playlist(playlist_id, fields="name,snapshot_id,tracks.total")
    name = info.get("name", playlist_id)
    total = (info.get("tracks") or {}).get("total")
    if total is None:  # spotify sometimes just doesn't send this
        total = sp.playlist_items(playlist_id, fields="total")["total"]
    snapshot = info["snapshot_id"]

    for i in range(total - 1):
        j = random.randint(i, total - 1)
        if j == i:
            continue
        result = sp.playlist_reorder_items(
            playlist_id, range_start=j, insert_before=i, snapshot_id=snapshot
        )
        snapshot = result["snapshot_id"]

    return name, total


# ---------------------------------------------------------------------------
# web mode
# ---------------------------------------------------------------------------

app = Flask(__name__)
app.secret_key = secrets.token_hex(32)

auth_manager: SpotifyOAuth | None = None  # set in web_mode()

PAGE = """
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Spotify Playlist Shuffler</title>
<style>
  body { font-family: system-ui, sans-serif; background: #121212; color: #eee;
         max-width: 640px; margin: 3rem auto; padding: 0 1rem; }
  h1 { color: #1db954; font-size: 1.5rem; }
  label { display: block; padding: .5rem .75rem; border-radius: 8px; cursor: pointer; }
  label:hover { background: #1e1e1e; }
  button { background: #1db954; color: #000; border: 0; border-radius: 999px;
           padding: .75rem 2rem; font-size: 1rem; font-weight: 600;
           cursor: pointer; margin-top: 1rem; }
  button:hover { background: #1ed760; }
  li { margin: .5rem 0; }
  .ok { color: #1db954; } .err { color: #f15e6c; }
  a { color: #1db954; }
</style>
</head>
<body>
<h1>Spotify Playlist Shuffler</h1>
{{ body|safe }}
</body>
</html>
"""


def render(body: str):
    return render_template_string(PAGE, body=body)


@app.errorhandler(Exception)
def show_error(exc):
    # show the traceback in the browser instead of flask's useless 500 page
    import traceback

    from markupsafe import escape

    tb = escape(traceback.format_exc())
    return (
        render(
            "<p class='err'>Something went wrong:</p>"
            f"<pre style='white-space:pre-wrap;color:#f15e6c'>{tb}</pre>"
            "<p>usual fixes: delete <code>.cache</code>, check <code>.env</code>, "
            "check the redirect uri in the spotify dashboard is "
            "<code>http://127.0.0.1:5000/callback</code></p>"
        ),
        500,
    )


@app.route("/")
def index():
    token = auth_manager.validate_token(auth_manager.cache_handler.get_cached_token())
    if not token:
        return redirect(auth_manager.get_authorize_url())
    return redirect("/playlists")


@app.route("/callback")
def callback():
    error = request.args.get("error")
    if error:
        return render(f"<p class='err'>Spotify authorization failed: {error}</p>")
    code = request.args.get("code")
    auth_manager.get_access_token(code)
    return redirect("/playlists")


@app.route("/playlists")
def playlists():
    token = auth_manager.validate_token(auth_manager.cache_handler.get_cached_token())
    if not token:
        return redirect("/")
    sp = spotipy.Spotify(auth_manager=auth_manager, requests_timeout=15)
    items = get_own_playlists(sp)
    if not items:
        return render("<p>No playlists found that you can modify.</p>")
    boxes = "\n".join(
        f"<label><input type='checkbox' name='playlist' value='{pl['id']}'> "
        f"{pl.get('name', pl['id'])}</label>"
        for pl in items
    )
    return render(
        "<p>Select the playlists to shuffle:</p>"
        f"<form method='post' action='/shuffle'>{boxes}"
        "<button type='submit'>Shuffle selected</button></form>"
    )


@app.route("/shuffle", methods=["POST"])
def shuffle():
    token = auth_manager.validate_token(auth_manager.cache_handler.get_cached_token())
    if not token:
        return redirect("/")
    ids = request.form.getlist("playlist")
    if not ids:
        return redirect("/playlists")
    sp = spotipy.Spotify(auth_manager=auth_manager)
    lines = []
    for pid in ids:
        try:
            name, total = shuffle_playlist(sp, pid)
            lines.append(f"<li class='ok'>&#10003; {name} — {total} tracks shuffled</li>")
        except Exception as exc:  # one dead playlist shouldn't kill the rest
            lines.append(f"<li class='err'>&#10007; {pid} — failed: {exc}</li>")
    return render(
        f"<ul>{''.join(lines)}</ul>"
        "<p>Done! <a href='/playlists'>shuffle more playlists</a></p>"
        "\n(ctrl+c in terminal)"
    )


def web_mode():
    global auth_manager
    auth_manager = make_auth_manager(open_browser=False)
    threading.Timer(1.0, webbrowser.open, [f"http://{HOST}:{PORT}"]).start()
    print(f"Opening http://{HOST}:{PORT} in your browser... (Ctrl+C to quit)")
    app.run(host=HOST, port=PORT)


# ---------------------------------------------------------------------------
# cli mode
# ---------------------------------------------------------------------------

def cli_mode(args):
    # spotipy spins up its own throwaway server for the first login,
    # after that .cache handles everything so this runs unattended
    manager = make_auth_manager(open_browser=True)
    sp = spotipy.Spotify(auth_manager=manager)

    if args.list:
        for pl in get_own_playlists(sp):
            print(f"{pl['id']}  {pl.get('name', '?')}")
        return

    failures = 0
    for raw in args.playlists:
        pid = parse_playlist_id(raw)
        try:
            name, total = shuffle_playlist(sp, pid)
            print(f"Shuffled '{name}' ({total} tracks)")
        except Exception as exc:
            failures += 1
            print(f"Failed to shuffle '{raw}': {exc}", file=sys.stderr)
    if failures:
        sys.exit(1)


# ---------------------------------------------------------------------------
# entry
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="actually shuffle spotify playlists. no args = web ui, "
        "or pass playlist ids/urls to shuffle them directly."
    )
    parser.add_argument(
        "playlists",
        nargs="*",
        help="playlist ids, spotify: uris, or open.spotify.com urls",
    )
    parser.add_argument(
        "--list", action="store_true", help="print my playlists and their ids"
    )
    args = parser.parse_args()

    if args.list or args.playlists:
        cli_mode(args)
    else:
        web_mode()


if __name__ == "__main__":
    main()