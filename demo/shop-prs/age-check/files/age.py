"""Age checks for restricted products."""

LEGAL_AGE = 18


def can_buy_alcohol(age: int) -> bool:
    """Customers aged 18 or older can buy alcohol."""
    return age > LEGAL_AGE
