# spotify playlist shuffler

Spotify's shuffle kept annoying me for not being random, so I made this flask web app that shuffles your playlist in a random order by moving the track with the reorder endpoint. This is far better than deleting and re-adding a new playlist, so if it crashes mid way I don't lose a playlist. I found out the hard way :(


Web mode — opens a page where I tick which playlists to shuffle:

```
python app.py
```

CLI mode — shuffles directly, this is what Task Scheduler runs:

```
python app.py --list        # dump my playlists + ids
python app.py <id or url>   # shuffle it
```

First run opens a browser to log in to Spotify, after that the token sits in `.cache` and refreshes itself, so scheduled runs are fully hands-off.

## setup notes for future

- client id and secret: (https://developer.spotify.com/dashboard) `make env file` and fill it in
- the app's redirect URI: `http://127.0.0.1:5000/callback` (`127.0.0.1`, not `localhost`, spotify rejects that now)
- `pip install -r requirements.txt`
- `python app.py`
- auth got weird, delete `.cache` and logging in again worked
- big playlists take a while (~one API call per track) — the price of never being able to lose the playlist

