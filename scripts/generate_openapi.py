#!/usr/bin/env python3
"""Generate OpenAPI JSON from the FastAPI app."""

import json
import sys
from pathlib import Path

# Add src to path so imports work
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ad_buyer.interfaces.api.main import app

openapi = app.openapi()
output = Path(__file__).resolve().parent.parent / "docs" / "api" / "openapi.json"
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(json.dumps(openapi, indent=2))
print(f"OpenAPI JSON written to {output}")
