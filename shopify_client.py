"""Minimal Shopify GraphQL Admin API client for pulling product data."""

import html
import logging
import re
from dataclasses import dataclass, field

import httpx

from config import Settings

logger = logging.getLogger(__name__)

PRODUCTS_QUERY = """
query TopProducts($first: Int!) {
  products(first: $first, sortKey: UPDATED_AT, reverse: true, query: "status:active") {
    edges {
      node {
        id
        title
        handle
        descriptionHtml
        onlineStoreUrl
        priceRangeV2 {
          minVariantPrice { amount currencyCode }
        }
        images(first: 3) {
          edges { node { url altText width height } }
        }
      }
    }
  }
}
"""

_TAG_RE = re.compile(r"<[^>]+>")
_BLOCK_TAG_RE = re.compile(r"</?(p|div|br|li|ul|ol|h[1-6])[^>]*>", re.IGNORECASE)
_SCRIPT_STYLE_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_WS_RE = re.compile(r"\s+")


class ShopifyError(RuntimeError):
    """Raised when Shopify returns an error or an unusable response."""


@dataclass
class Product:
    id: str
    title: str
    handle: str
    description: str
    url: str | None
    price: str | None
    currency: str | None
    image_urls: list[str] = field(default_factory=list)

    @property
    def price_label(self) -> str:
        if not self.price:
            return ""
        amount = float(self.price)
        text = f"{amount:,.0f}" if amount.is_integer() else f"{amount:,.2f}"
        return f"{text} {self.currency}".strip() if self.currency else text


def strip_html(raw: str | None) -> str:
    if not raw:
        return ""
    text = _SCRIPT_STYLE_RE.sub(" ", raw)
    text = _BLOCK_TAG_RE.sub(" ", text)
    text = _TAG_RE.sub("", text)
    return _WS_RE.sub(" ", html.unescape(text)).strip()


class ShopifyClient:
    def __init__(self, settings: Settings, timeout: float = 20.0) -> None:
        self._endpoint = settings.shopify_graphql_endpoint
        self._limit = settings.shopify_product_limit
        self._headers = {
            "X-Shopify-Access-Token": settings.shopify_access_token,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        self._timeout = timeout

    async def _execute(self, query: str, variables: dict) -> dict:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    self._endpoint, headers=self._headers, json={"query": query, "variables": variables}
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ShopifyError(f"Shopify HTTP {exc.response.status_code}: {exc.response.text[:300]}") from exc
        except httpx.HTTPError as exc:
            raise ShopifyError(f"Shopify request failed: {exc}") from exc

        payload = response.json()
        if payload.get("errors"):
            raise ShopifyError(f"Shopify GraphQL errors: {payload['errors']}")
        return payload.get("data") or {}

    async def fetch_top_products(self) -> list[Product]:
        data = await self._execute(PRODUCTS_QUERY, {"first": self._limit})
        edges = ((data.get("products") or {}).get("edges")) or []
        products = [self._parse(edge.get("node") or {}) for edge in edges]
        products = [p for p in products if p.image_urls]
        logger.info("Fetched %d products with images from Shopify", len(products))
        return products

    @staticmethod
    def _parse(node: dict) -> Product:
        price_info = ((node.get("priceRangeV2") or {}).get("minVariantPrice")) or {}
        image_edges = ((node.get("images") or {}).get("edges")) or []
        image_urls = [
            (edge.get("node") or {}).get("url")
            for edge in image_edges
            if (edge.get("node") or {}).get("url")
        ]
        return Product(
            id=node.get("id", ""),
            title=(node.get("title") or "Untitled product").strip(),
            handle=node.get("handle") or "product",
            description=strip_html(node.get("descriptionHtml")),
            url=node.get("onlineStoreUrl"),
            price=price_info.get("amount"),
            currency=price_info.get("currencyCode"),
            image_urls=image_urls,
        )
