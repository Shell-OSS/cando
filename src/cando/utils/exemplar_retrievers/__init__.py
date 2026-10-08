# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Exemplar retrieval implementations used by GeneratorZero."""

from .base import BaseExemplarRetriever
from .nearest_neighbors import NearestNeighborsExemplarRetriever

__all__ = ["BaseExemplarRetriever", "NearestNeighborsExemplarRetriever"]
