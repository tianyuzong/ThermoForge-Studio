"""One process-global guard for Gmsh's process-global model state."""
from threading import RLock
GMSH_LOCK=RLock()
