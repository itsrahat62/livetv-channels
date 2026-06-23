# livetv-channels

Auto-generated worldwide free-IPTV channel list for the Live TV app.

- `build_channels.py` aggregates public IPTV sources, keeps only working servers, blocks adult content.
- A GitHub Action rebuilds `channels.json` every 3 hours.
- The app fetches `channels.json` from this public repo.
