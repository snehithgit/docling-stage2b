from __future__ import annotations

import threading

# Shared by equipment-index readers, publishers, and lifecycle deletion.
EQUIPMENT_INDEX_SWAP_LOCK = threading.RLock()
