"""Full-text product search with filtering, sorting, and pagination.

Performs case-insensitive substring matching on product name and description,
then applies category / brand / price / stock filters before sorting and
paginating the results.
"""

from __future__ import annotations

from ucp_merchant.catalog.products import ProductCatalog
from ucp_merchant.models import Product, ProductCategory, SearchResult


class CatalogSearch:
    """Search and filter engine over an in-memory :class:`ProductCatalog`."""

    def __init__(self, catalog: ProductCatalog) -> None:
        self._catalog = catalog

    def search(
        self,
        query: str | None = None,
        category: str | None = None,
        min_price: float | None = None,
        max_price: float | None = None,
        brand: str | None = None,
        in_stock: bool | None = None,
        sort_by: str = "relevance",
        limit: int = 20,
        offset: int = 0,
    ) -> SearchResult:
        """Search the catalog with optional filters.

        Parameters
        ----------
        query:
            Free-text search applied to product name and description.
        category:
            Filter by :class:`ProductCategory` value (case-insensitive).
        min_price / max_price:
            Price range filter (inclusive).
        brand:
            Case-insensitive brand filter.
        in_stock:
            If ``True``, only include products with ``stock > 0``.
        sort_by:
            One of ``"relevance"``, ``"price_asc"``, ``"price_desc"``, ``"name"``, ``"rating"``.
        limit:
            Maximum number of results to return.
        offset:
            Number of results to skip for pagination.

        Returns
        -------
        SearchResult
            Paginated results with the total count before pagination.
        """
        products = self._catalog.list_all()

        # -- full-text search ------------------------------------------------
        if query:
            products = self._text_filter(products, query)

        # -- categorical filters ---------------------------------------------
        if category:
            products = self._category_filter(products, category)
        if brand:
            products = self._brand_filter(products, brand)
        if min_price is not None:
            products = [p for p in products if p.price.amount >= min_price]
        if max_price is not None:
            products = [p for p in products if p.price.amount <= max_price]
        if in_stock is True:
            products = [p for p in products if p.stock > 0]
        elif in_stock is False:
            products = [p for p in products if p.stock == 0]

        # -- sort ------------------------------------------------------------
        products = self._sort(products, sort_by, query)

        # -- paginate --------------------------------------------------------
        total = len(products)
        page = products[offset : offset + limit]

        return SearchResult(
            products=page,
            total=total,
            offset=offset,
            limit=limit,
            query=query,
        )

    # -- private helpers -----------------------------------------------------

    @staticmethod
    def _text_filter(products: list[Product], query: str) -> list[Product]:
        """Case-insensitive substring match on name + description."""
        terms = query.lower().split()
        results: list[Product] = []
        for p in products:
            haystack = f"{p.name} {p.description} {p.brand}".lower()
            if all(term in haystack for term in terms):
                results.append(p)
        return results

    @staticmethod
    def _category_filter(
        products: list[Product], category: str
    ) -> list[Product]:
        """Filter by category value (case-insensitive)."""
        try:
            cat = ProductCategory(category.lower())
        except ValueError:
            # Accept partial / display-name matches
            cat_lower = category.lower()
            for c in ProductCategory:
                if cat_lower in c.value:
                    cat = c
                    break
            else:
                return []
        return [p for p in products if p.category == cat]

    @staticmethod
    def _brand_filter(products: list[Product], brand: str) -> list[Product]:
        """Case-insensitive brand match."""
        brand_lower = brand.lower()
        return [p for p in products if p.brand.lower() == brand_lower]

    @staticmethod
    def _sort(
        products: list[Product],
        sort_by: str,
        query: str | None,
    ) -> list[Product]:
        """Sort the product list according to *sort_by*."""
        if sort_by == "price_asc":
            return sorted(products, key=lambda p: p.price.amount)
        if sort_by == "price_desc":
            return sorted(products, key=lambda p: p.price.amount, reverse=True)
        if sort_by == "name":
            return sorted(products, key=lambda p: p.name.lower())
        if sort_by == "rating":
            return sorted(products, key=lambda p: p.rating, reverse=True)
        # "relevance" -- score by query-term hit density
        if query:
            terms = query.lower().split()

            def relevance(p: Product) -> float:
                haystack = f"{p.name} {p.description}".lower()
                return sum(haystack.count(t) for t in terms)

            return sorted(products, key=relevance, reverse=True)
        return products
