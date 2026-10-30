"""
Workspace Context Cache Module.
Caches customer/profile tree structures to prevent heavy recursive I/O scans on every UI tab render.
Automatically invalidated on customer/profile CRUD operations.
"""
import threading
from typing import Any, Callable, Dict, List, Optional


class WorkspaceContextCache:
    """Thread-safe cache for workspace tree structures and active context resolution."""

    def __init__(self):
        self._lock = threading.RLock()
        self._customer_tree_cache: Optional[List[Dict[str, Any]]] = None
        self._active_context_cache: Optional[Dict[str, Any]] = None

    def get_customer_tree(self, fetcher_fn: Callable[[], List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
        """Return cached customer tree or fetch and cache if invalidated/absent."""
        with self._lock:
            if self._customer_tree_cache is not None:
                return self._customer_tree_cache

        # Fetch outside or under RLock safely
        tree = fetcher_fn()
        with self._lock:
            self._customer_tree_cache = tree
            return tree

    def get_active_context(self, resolver_fn: Callable[[], Dict[str, Any]]) -> Dict[str, Any]:
        """Return cached active context (customer/profile) or resolve if invalidated/absent."""
        with self._lock:
            if self._active_context_cache is not None:
                return self._active_context_cache

        # Resolve outside or under RLock safely
        context = resolver_fn()
        with self._lock:
            self._active_context_cache = context
            return context

    def invalidate(self):
        """Invalidate all cached tree and context data."""
        with self._lock:
            self._customer_tree_cache = None
            self._active_context_cache = None


# Global singleton instance
workspace_cache = WorkspaceContextCache()
