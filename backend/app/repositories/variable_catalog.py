"""Compatibility facade for legacy variable-catalog repository imports."""

from ..adapters.persistence.variable_catalog import SQLVariableCatalogRepository


VariableCatalogRepository = SQLVariableCatalogRepository
