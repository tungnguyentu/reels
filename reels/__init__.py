"""Turn long Minecraft recordings into 9:16 Reels from a text query."""


class ReelsError(Exception):
    """A failure the user can act on. The CLI prints these without a traceback."""
