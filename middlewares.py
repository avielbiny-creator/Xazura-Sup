import asyncio
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Message


class AlbumMiddleware(BaseMiddleware):
    """Собирает альбом (несколько фото/файлов одним сообщением) и передаёт его в хендлер
    один раз в аргументе `album`, вместо отдельного вызова на каждую часть."""

    def __init__(self, latency: float = 0.6) -> None:
        self.latency = latency
        self.albums: dict[str, list[Message]] = {}

    async def __call__(
        self,
        handler: Callable[[Message, dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: dict[str, Any],
    ) -> Any:
        if not event.media_group_id:
            return await handler(event, data)
        album = self.albums.setdefault(event.media_group_id, [])
        album.append(event)
        if len(album) > 1:
            return None  # остальные части обработает первая
        await asyncio.sleep(self.latency)
        data["album"] = sorted(self.albums.pop(event.media_group_id), key=lambda m: m.message_id)
        return await handler(event, data)
