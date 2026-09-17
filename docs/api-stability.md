# Estrategia de estabilidad de la API

Los endpoints observados de Wallapop son endpoints internos/no documentados usados por el frontend público. Pueden cambiar sin mantener compatibilidad, por lo que el proyecto conserva respuestas RAW fechadas y separa su interpretación en parsers.

```text
Wallapop API response
        ↓
RAW historical fixture
        ↓
parser contract tests
        ↓
normalized domain model
```

Las fixtures de `tests/fixtures/raw/` son una referencia histórica de estructura. Las fixtures de `tests/fixtures/` son casos mínimos controlados para tests unitarios. Los contract tests ejercitan los parsers contra la referencia RAW sin exigir que todos los tests conozcan todos los campos de Wallapop.

Para comparar dos respuestas sin considerar cambios de precio, nombre o rating:

```bash
python scripts/compare_api_fixture.py tests/fixtures/raw/2026-09/stats.json tests/fixtures/raw/2026-09/stats.json
```

El script informa de campos añadidos, eliminados y cambios de tipo, incluyendo cambios entre objetos y listas y la presencia de `null`.

