"""Money formatting for the storefront."""


def format_price(amount: float) -> str:
    """Like $1,234.50. Refunds put the minus sign first: -$5.00."""
    return f"${amount:,.2f}"
