# Wallapop Profile Tracker

Fase 2: cliente asíncrono de solo lectura, modelos de dominio y parsers para datos públicos de Wallapop. Su uso debe limitarse a perfiles y volumen de consultas cubiertos por la autorización de Wallapop.

## Instalación de desarrollo

```bash
python -m pip install -e ".[dev]"
```

## Uso

```python
import asyncio

from wallapop_tracker import WallapopClient


async def main() -> None:
    async with WallapopClient() as client:
        user_id = await client.resolve_user_id(
            "https://es.wallapop.com/user/example-v4z4nyeyq8jy"
        )
        profile = await client.get_profile(user_id)
        stats = await client.get_profile_stats(user_id)
        reviews = await client.get_review_summary(user_id)
        items = await client.get_all_items(user_id)

        print(profile)
        print(stats)
        print(reviews)
        print(len(items))


asyncio.run(main())
```

Para conservar respuestas RAW de debugging:

```python
from pathlib import Path

async with WallapopClient(raw_data_dir=Path("data/raw")) as client:
    items = await client.get_all_items("v4z4nyeyq8jy")
```

Los tests normales no acceden a Internet:

```bash
pytest
pytest -m live  # reservado para tests live explícitamente autorizados
ruff check .
mypy src
```

SQLite, snapshots históricos, diff engine, tracker, scheduler y CLI quedan fuera de esta fase.

