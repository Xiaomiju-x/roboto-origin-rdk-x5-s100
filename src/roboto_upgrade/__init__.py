"""Project-owned, device-free RoboParty X5 upgrade candidates.

Submodules are intentionally not imported here: the X5 trust-guard replay only
needs NumPy and must not acquire the host-only PyTorch training dependency.
"""

__all__ = ["synthetic_bev", "temporal_occ_flow", "trust_guard"]
