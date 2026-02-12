from .base import BaseSkill
import asyncio
import os
from winsdk.windows.media.control import GlobalSystemMediaTransportControlsSessionManager


class MediaSkill(BaseSkill):
    """
    Control Media (Spotify, etc.) via Windows System Media Transport Controls.
    Zero Configuration required (No API Keys).
    """
    name = "Media"

    def __init__(self):
        super().__init__()
        self.manager = None
        self.register_method("play", self.play)
        self.register_method("pause", self.pause)
        self.register_method("next", self.next_track)
        self.register_method("previous", self.previous_track)
        self.register_method("status", self.get_status)

    async def _get_session(self):
        if not self.manager:
            self.manager = await GlobalSystemMediaTransportControlsSessionManager.request_async()

        session = self.manager.get_current_session()
        return session

    async def _get_spotify_session(self):
        if not self.manager:
            self.manager = await GlobalSystemMediaTransportControlsSessionManager.request_async()

        sessions = self.manager.get_sessions()
        for session in sessions:
            if "spotify" in session.source_app_user_model_id.lower():
                return session

        return self.manager.get_current_session()

    def play(self, query=None):
        """
        Play media. If query is provided, opens Spotify search (Hybrid mode).
        """
        if query:
            try:
                cleaned_query = query.replace(" ", "%20")
                os.startfile(f"spotify:search:{cleaned_query}")
                return f"Opened Spotify search for '{query}'. Please click play manually or ask me to click it."
            except Exception as e:
                return f"Error opening Spotify URI: {e}"

        async def _resume():
            session = await self._get_session()
            if session:
                await session.try_play_async()
                return "Resumed playback (System)"
            return "No active media session found."

        try:
            return asyncio.run(_resume())
        except Exception as e:
            return f"Error resuming: {e}"

    def pause(self):
        async def _pause():
            session = await self._get_session()
            if session:
                await session.try_pause_async()
                return "Paused playback"
            return "No active media session found."

        try:
            return asyncio.run(_pause())
        except Exception as e:
            return f"Error pausing: {e}"

    def next_track(self):
        async def _next():
            session = await self._get_session()
            if session:
                await session.try_skip_next_async()
                return "Skipped to next track"
            return "No active media session found."

        try:
            return asyncio.run(_next())
        except Exception as e:
            return f"Error skipping: {e}"

    def previous_track(self):
        async def _prev():
            session = await self._get_session()
            if session:
                await session.try_skip_previous_async()
                return "Skipped to previous track"
            return "No active media session found."

        try:
            return asyncio.run(_prev())
        except Exception as e:
            return f"Error skipping: {e}"

    def get_status(self):
        async def _status():
            session = await self._get_session()
            if session:
                props = await session.try_get_media_properties_async()
                if props:
                    return f"Now Playing: {props.title} by {props.artist}"
            return "No media playing."

        try:
            return asyncio.run(_status())
        except Exception as e:
            return f"Error getting status: {e}"
